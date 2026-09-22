"""YantrAI Web: the lead inbox, served by the website at /admin and shown by the
YantrAI platform as a tile to its platform admins (backlog LEAD-03).

Who gets in is inbox_auth.py's job. This module is the rest: the page, the JSON
API it calls, and the database access, as website_admin (db/002_inbox.sql), a
login separate from the forms' INSERT-only website_app. It can read the leads,
changes one only through website_intake.set_status() (which logs the change in
the same transaction), and can delete nothing.

Rules the routes keep:
  * everything under /admin answers 404 until the inbox is fully configured
    (inbox_auth.configured() and INBOX_DB_URL), so a half-set service shows nothing;
  * a platform sign-in token is used once: the sign-in is recorded (and a second
    use of the same token refused by the database) BEFORE a session is issued,
    and any database trouble means no session;
  * opening a lead and exporting are logged first, and no data leaves if the log
    entry could not be written;
  * search terms and filters travel in POST bodies, never in URLs, so they stay
    out of Cloud Run's request logs; nothing a visitor typed is ever logged;
  * every /admin response is no-store, no-referrer, nosniff, noindex, and may be
    framed only by the platform (CSP frame-ancestors);
  * the inbox has its own database slots, so it can never hold up the forms.

Settings: see inbox_auth.py, plus
  INBOX_DB_URL  postgresql://website_admin.<project>:<password>@<pooler>:5432/postgres?sslmode=require
                (Secret Manager: website-admin-db-url; made by
                 scripts/set_website_login.py --role website_admin)
"""
import base64
import csv
import html
import io
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Response, abort, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import inbox_auth
import intake

ADMIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin")
SLUG = "yantrai-web"
STATUSES = ("new", "contacted", "qualified", "closed", "spam")
FORMS = {"savings_check": "Demo request", "careers": "Careers"}
PAGE_SIZE = 50
EXPORT_MAX = 5000
MAX_BODY = 16 * 1024
MAX_QUERY = 100
MAX_NOTE = 2000

# its own slots: at most 2 inbox connections per instance (website_admin allows 5
# in all), and a request waits briefly rather than hold one of gunicorn's threads
SLOTS = 2
SLOT_WAIT = 2
_slots = threading.BoundedSemaphore(SLOTS)

bp = Blueprint("inbox", __name__)


def db_configured():
    return bool(os.getenv("INBOX_DB_URL", "").strip())


def configured():
    return inbox_auth.configured() and db_configured()


class InboxError(Exception):
    def __init__(self, status, message, detail=""):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


def _err(status, message):
    return jsonify({"ok": False, "error": message}), status


# --- the database ------------------------------------------------------------------------
@contextmanager
def _db():
    """One short connection as website_admin, holding one of the inbox's slots."""
    if not _slots.acquire(timeout=SLOT_WAIT):
        raise InboxError(503, "The inbox is busy. Try again in a moment.", "busy")
    con = None
    try:
        try:
            import pg8000.native
            con = pg8000.native.Connection(**intake.connect_args_for("INBOX_DB_URL", "yantrai-website-inbox"))
        except intake.IntakeError as exc:
            raise InboxError(503, "The inbox database is not set up.", exc.detail or exc.reason) from None
        except Exception as exc:
            raise InboxError(503, "The inbox database could not be reached. Try again in a moment.",
                             intake._why(exc)) from None
        yield con
    finally:
        if con is not None:
            try:
                con.close()              # an open transaction is rolled back with it
            except Exception:
                pass
        _slots.release()


def _sqlstate(exc):
    return intake._sqlstate(exc)


def _is_db_error(exc):
    try:
        from pg8000.exceptions import DatabaseError, InterfaceError
    except Exception:
        return False
    return isinstance(exc, (DatabaseError, InterfaceError, OSError))


_LOG_EVENT = (
    "INSERT INTO website_intake.inbox_events "
    "(action, actor_uid, actor, actor_org, submission_id, detail, token_sha256) "
    "VALUES (:action, CAST(:uid AS uuid), :actor, CAST(:org AS uuid), CAST(:sid AS uuid), "
    "CAST(:detail AS jsonb), :tok)"
)


def _log_event(con, action, who, submission_id=None, detail=None, token_sha256=None):
    uid, name, org = who
    con.run(_LOG_EVENT, action=action, uid=uid, actor=name or None, org=org,
            sid=str(submission_id) if submission_id else None,
            detail=json.dumps(detail, separators=(",", ":")) if detail is not None else None,
            tok=token_sha256)


_COUNTS = (
    "SELECT count(*) FILTER (WHERE status = 'new'), "
    "count(*) FILTER (WHERE received_at > now() - interval '7 days'), "
    "count(*) FILTER (WHERE status = 'qualified'), count(*) "
    "FROM website_intake.submissions"
)
_FILTERS = (
    " WHERE (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))"
    " AND (CAST(:form AS text) IS NULL OR form = CAST(:form AS text))"
    " AND (CAST(:pat AS text) IS NULL OR name ILIKE CAST(:pat AS text) ESCAPE '!'"
    "      OR company ILIKE CAST(:pat AS text) ESCAPE '!' OR email ILIKE CAST(:pat AS text) ESCAPE '!')"
)
_LIST = (
    "SELECT id, form, received_at, name, company, email, status FROM website_intake.submissions"
    + _FILTERS +
    " AND (CAST(:before_at AS timestamptz) IS NULL"
    "      OR (received_at, id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid)))"
    " ORDER BY received_at DESC, id DESC LIMIT :lim"
)
DETAIL_COLUMNS = ("id", "form", "received_at", "locale", "page", "name", "email", "company", "role",
                  "erp", "outflow", "note", "linkedin", "work", "area", "cv_filename", "cv_mime",
                  "cv_bytes", "status", "status_note", "triaged_at", "triaged_by")
_DETAIL = ("SELECT " + ", ".join(DETAIL_COLUMNS)
           + " FROM website_intake.submissions WHERE id = CAST(:id AS uuid)")
_HISTORY = (
    "SELECT at, actor, from_status, to_status, note FROM website_intake.inbox_events"
    " WHERE submission_id = CAST(:id AS uuid) AND action = 'set_status' ORDER BY at DESC LIMIT 50"
)
_NOTIFY = ("SELECT event, at FROM website_intake.notify_events"
           " WHERE submission_id = CAST(:id AS uuid) ORDER BY at")
_SET_STATUS = (
    "SELECT new_status, new_note, changed_at FROM website_intake.set_status("
    "CAST(:id AS uuid), CAST(:status AS text), CAST(:note AS text), CAST(:expected AS text), "
    "CAST(:uid AS uuid), CAST(:actor AS text), CAST(:org AS uuid))"
)
EXPORT_COLUMNS = ("received_at", "form", "status", "name", "email", "company", "role", "erp", "outflow",
                  "note", "linkedin", "work", "area", "page", "locale", "status_note", "triaged_at",
                  "triaged_by", "id")
_EXPORT = ("SELECT " + ", ".join(EXPORT_COLUMNS) + " FROM website_intake.submissions" + _FILTERS
           + " ORDER BY received_at DESC, id DESC LIMIT :lim")
_EXPORT_COUNT = "SELECT count(*) FROM website_intake.submissions" + _FILTERS


def _iso(value):
    """Timestamps go out in UTC, whatever the database session's time zone."""
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).isoformat()
    return value


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    return _iso(value)


def _counts(con):
    (new, week, qualified, total), = con.run(_COUNTS)
    return {"new": new, "week": week, "qualified": qualified, "all": total}


# --- request helpers -----------------------------------------------------------------------
def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise InboxError(400, "The request could not be read.")
    return data


def _text(value, limit, what):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InboxError(400, f"The {what} could not be read.")
    text = intake.safe_text(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > limit:
        raise InboxError(400, f"The {what} is too long (at most {limit} characters).")
    return text


def _choice(value, allowed, what):
    if value in (None, ""):
        return None
    if value not in allowed:
        raise InboxError(400, f"Unknown {what}.")
    return value


def _filters(body):
    q = _text(body.get("q"), MAX_QUERY, "search")
    pat = None
    if q:
        pat = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    return {"status": _choice(body.get("status"), STATUSES, "status"),
            "form": _choice(body.get("form"), FORMS, "form"),
            "pat": pat}


def _cursor_out(row):
    raw = json.dumps([_iso(row[2]), str(row[0])], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_in(value):
    if value in (None, ""):
        return None, None
    try:
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError
        at, lead = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        return datetime.fromisoformat(at).isoformat(), str(uuid.UUID(lead))
    except Exception:
        raise InboxError(400, "The list position could not be read. Reload the list.") from None


def requires_admin(view):
    """A live inbox session in the Authorization header, else 401."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        claims = inbox_auth.verify_session(header[7:].strip()) if header.startswith("Bearer ") else None
        if claims is None:
            return _err(401, "Your session has ended. Open YantrAI Web again from the platform.")
        g.inbox_who = (claims["uid"], claims.get("u") or "", claims.get("o"))
        return view(*args, **kwargs)
    return wrapped


def guarded(view):
    """Turn the inbox's own and the database's failures into JSON answers."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except InboxError as exc:
            if exc.status >= 500:
                intake.log("ERROR", "inbox_unavailable", route=request.endpoint, detail=exc.detail)
            return _err(exc.status, exc.message)
        except HTTPException:
            raise
        except Exception as exc:
            code = _sqlstate(exc)
            intake.log("ERROR", "inbox_failed", route=request.endpoint,
                       detail=code or intake._why(exc))
            if _is_db_error(exc):
                return _err(503, "The inbox database could not be reached. Try again in a moment.")
            return _err(500, "Something went wrong in the inbox.")
    return wrapped


# --- the page -----------------------------------------------------------------------------------
@bp.before_request
def _only_when_configured():
    if not configured():
        abort(404)
    if request.method == "POST":
        if (request.content_length or 0) > MAX_BODY:
            abort(413)
        request.max_content_length = MAX_BODY


@bp.get("/admin")
def page():
    with open(os.path.join(ADMIN_DIR, "index.html"), encoding="utf-8") as f:
        text = f.read()
    text = text.replace("__PLATFORM_URL__", html.escape(inbox_auth.platform_origin(), quote=True))
    return Response(text, mimetype="text/html")


@bp.get("/admin/inbox.js")
def script():
    return send_from_directory(ADMIN_DIR, "inbox.js", mimetype="text/javascript", max_age=0)


@bp.get("/admin/inbox.css")
def style():
    return send_from_directory(ADMIN_DIR, "inbox.css", mimetype="text/css", max_age=0)


# --- the API ----------------------------------------------------------------------------------------
@bp.post("/admin/api/session")
@guarded
def session():
    """Swap the platform's one-time sign-in token for an inbox session."""
    token = _json_body().get("token")
    claims = inbox_auth.verify_platform_token(token)
    if claims is None:
        intake.log("WARNING", "inbox_sign_in_refused", reason="invalid_or_expired")
        return _err(401, "This sign-in has expired. Open YantrAI Web again from the platform.")
    who = inbox_auth.admin_identity(claims)
    if who is None:
        uid = claims.get("uid")
        intake.log("WARNING", "inbox_sign_in_refused", reason="not_a_platform_admin",
                   uid=uid if isinstance(uid, str) and len(uid) <= 64 else None)
        return _err(403, "YantrAI Web is for platform admins only.")
    try:
        with _db() as con:
            _log_event(con, "sign_in", who, token_sha256=inbox_auth.token_digest(token))
    except Exception as exc:
        if _sqlstate(exc) == "23505":
            intake.log("WARNING", "inbox_token_replay", uid=who[0])
            return _err(401, "This sign-in was already used. Open YantrAI Web again from the platform.")
        raise
    token_out, expires = inbox_auth.issue_session(*who)
    intake.log("INFO", "inbox_sign_in", uid=who[0], org=who[2])
    return jsonify({"ok": True, "token": token_out, "expires_at": expires, "user": who[1]})


@bp.post("/admin/api/leads/search")
@guarded
@requires_admin
def search():
    body = _json_body()
    params = _filters(body)
    before_at, before_id = _cursor_in(body.get("before"))
    with _db() as con:
        rows = con.run(_LIST, lim=PAGE_SIZE + 1, before_at=before_at, before_id=before_id, **params)
        counts = _counts(con)
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    leads = [{"id": str(r[0]), "form": r[1], "received_at": _iso(r[2]), "name": r[3],
              "company": r[4], "email": r[5], "status": r[6]} for r in rows]
    return jsonify({"ok": True, "leads": leads, "counts": counts,
                    "next": _cursor_out(rows[-1]) if more and rows else None})


@bp.get("/admin/api/leads/<uuid:lead_id>")
@guarded
@requires_admin
def lead(lead_id):
    with _db() as con:
        con.run("START TRANSACTION")
        rows = con.run(_DETAIL, id=str(lead_id))
        if not rows:
            con.run("ROLLBACK")
            return _err(404, "That lead is not in the inbox.")
        # logged first: if the entry cannot be written, nothing is shown
        _log_event(con, "view_lead", g.inbox_who, submission_id=lead_id)
        history = con.run(_HISTORY, id=str(lead_id))
        notify = con.run(_NOTIFY, id=str(lead_id))
        con.run("COMMIT")
    row = {c: _plain(v) for c, v in zip(DETAIL_COLUMNS, rows[0])}
    return jsonify({
        "ok": True, "lead": row,
        "history": [{"at": _iso(h[0]), "actor": h[1], "from": h[2], "to": h[3], "note": h[4]}
                    for h in history],
        "notify": [{"event": n[0], "at": _iso(n[1])} for n in notify],
    })


@bp.post("/admin/api/leads/<uuid:lead_id>/status")
@guarded
@requires_admin
def set_status(lead_id):
    body = _json_body()
    status = _choice(body.get("status"), STATUSES, "status")
    if status is None:
        raise InboxError(400, "Pick a status.")
    expected = _choice(body.get("expected"), STATUSES, "status")
    note = _text(body.get("note"), MAX_NOTE, "note") or None
    uid, name, org = g.inbox_who
    try:
        with _db() as con:
            (new_status, new_note, changed_at), = con.run(
                _SET_STATUS, id=str(lead_id), status=status, note=note, expected=expected,
                uid=uid, actor=name or None, org=org)
            counts = _counts(con)
    except Exception as exc:
        code = _sqlstate(exc)
        if code == "WI404":
            return _err(404, "That lead is not in the inbox.")
        if code == "WI409":
            return _err(409, "Someone else changed this lead a moment ago. Nothing was saved.")
        if code and code[:2] == "22":
            return _err(400, "That change could not be saved.")
        raise
    intake.log("INFO", "inbox_status_changed", id=str(lead_id), status=new_status, uid=uid)
    return jsonify({"ok": True, "status": new_status, "note": new_note,
                    "changed_at": _iso(changed_at), "counts": counts})


def csv_cell(value):
    """A cell a spreadsheet shows as text: visitors typed most of these, and a
    cell starting with = + - @ (or a tab or CR) would otherwise run as a formula."""
    if value is None:
        return ""
    text = _plain(value)
    text = text if isinstance(text, str) else str(text)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") or text.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


@bp.post("/admin/api/export")
@guarded
@requires_admin
def export():
    params = _filters(_json_body())
    with _db() as con:
        con.run("START TRANSACTION")
        rows = con.run(_EXPORT, lim=EXPORT_MAX + 1, **params)
        # past the cap the file holds the newest EXPORT_MAX, and says so (the page
        # shows it): never a file that silently looks complete
        matching = len(rows)
        if matching > EXPORT_MAX:
            rows = rows[:EXPORT_MAX]
            (matching,), = con.run(_EXPORT_COUNT, **params)
        # logged first: if the entry cannot be written, nothing leaves
        _log_event(con, "export", g.inbox_who,
                   detail={"rows": len(rows), "matching": matching, "status": params["status"],
                           "form": params["form"], "searched": params["pat"] is not None})
        con.run("COMMIT")
    out = io.StringIO()
    out.write("\ufeff")                      # so spreadsheet apps read it as UTF-8
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        values = list(r)
        values[1] = FORMS.get(values[1], values[1])
        writer.writerow([csv_cell(v) for v in values])
    intake.log("INFO", "inbox_export", rows=len(rows), matching=matching, uid=g.inbox_who[0])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="yantrai-leads.csv"',
                             "X-Export-Rows": str(len(rows)), "X-Export-Matching": str(matching)})


# --- wiring into the site ---------------------------------------------------------------------------
def is_admin_path(path):
    return path == "/admin" or path.startswith("/admin/")


def admin_headers(response):
    if is_admin_path(request.path):
        origin = inbox_auth.platform_origin()
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
            f"frame-ancestors {origin}")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers.pop("X-Frame-Options", None)
    return response


def _api_errors(exc):
    """JSON for any HTTP error under /admin/api/ (a mistyped path, a wrong method, a
    body too large); everywhere else the site's usual pages, unchanged."""
    if isinstance(exc, HTTPException) and request.path.startswith("/admin/api/"):
        messages = {404: "Not found.", 405: "Not allowed.", 413: "The request is too large."}
        return _err(exc.code or 500, messages.get(exc.code, "Something went wrong in the inbox."))
    return exc


def register(app):
    app.register_blueprint(bp)
    app.after_request(admin_headers)
    for code in (400, 404, 405, 413, 500):
        app.register_error_handler(code, _api_errors)
