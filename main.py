"""yantrailabs.com — AiFA, AI Teams for Finance.

Everything served to the browser lives in public/. This file only routes and
handles the form; nothing else in the repo is reachable over HTTP, except the
lead inbox the YantrAI platform opens at /admin (inbox.py, admin/), which answers
404 until it is configured and then only to a platform admin.
"""
import os
import re
import smtplib
import threading
from email.message import EmailMessage

from flask import Flask, jsonify, redirect, request, send_from_directory
from werkzeug.utils import secure_filename

import inbox
import intake

# static_folder is off on purpose — Flask's built-in static route would be
# registered ahead of ours and serve the repo root, source files included.
app = Flask(__name__, static_folder=None)

PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

MAX_CV_BYTES = 10 * 1024 * 1024
CV_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": ("application/"
              "vnd.openxmlformats-officedocument.wordprocessingml.document"),
}

# reject an oversized body at the edge rather than reading it into memory
app.config["MAX_CONTENT_LENGTH"] = MAX_CV_BYTES + (1 << 20)
# the savings check is a small JSON form: the page caps the note at 4000
# characters (at most 12 KB even in a 3-byte script), so a real visitor stays
# far under 256 KB (the other inputs have no maxlength, but the server cuts them
# to 120-300 characters). Applied only when storing (mail-only keeps the
# app-wide limit, as before).
SAVINGS_MAX_BYTES = 256 * 1024
# a 512 MiB instance holds at most two CVs (and their mail encoding) at once
_cv_slots = threading.BoundedSemaphore(2)

# The page declares yantrailabs.com canonical — <link rel="canonical">, og:url,
# the sitemap and robots.txt all name it. Both hostnames are mapped to this
# service, so without this the same site answers on two of them and they
# compete as duplicates. www is sent to the apex instead.
#
# Navigations only. A form POST redirected across hostnames is a cross-origin
# request the browser would block, and www is a perfectly good origin to answer
# the API on — only the page the user lands on needs to settle on one host.
CANONICAL_HOST = "yantrailabs.com"

# --- locales ------------------------------------------------------------
# English is served from the root, French from /fr/. Both are fully built
# static trees, so a crawler sees real French HTML at a real French URL.
SUPPORTED_LOCALES = ("en", "fr")
# Firebase Hosting fronts yantrailabs.com and strips every cookie except
# __session before forwarding to the backend, so a cookie by any other name
# reaches the app on run.app and never through the domain. This slot is shared
# — if anything else ever needs a cookie here, it has to become a structured
# value rather than a bare locale code.
LANG_COOKIE = "__session"
# a year: the choice is a preference, not a session
LANG_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
# paths that are locale-neutral and must never be redirected
LOCALE_EXEMPT_PREFIXES = ("/api/", "/assets/", "/brand/", "/admin/")
LOCALE_EXEMPT_PATHS = ("/site.css", "/page.css", "/app.js", "/admin",
                       "/robots.txt", "/sitemap.xml", "/_status", "/favicon.ico", "/apple-touch-icon.png")


def _path_locale(path):
    if path == "/fr" or path.startswith("/fr/"):
        return "fr"
    return "en"


def _locale_path(path, locale):
    """The same page under another locale. Slugs are identical across locales."""
    rest = path[3:] if _path_locale(path) == "fr" else path
    if not rest.startswith("/"):
        rest = "/" + rest
    return rest if locale == "en" else "/fr" + (rest if rest != "/" else "/")


@app.before_request
def redirect_to_canonical_host():
    if request.method not in ("GET", "HEAD"):
        return None
    if request.host.split(":")[0].lower() != "www." + CANONICAL_HOST:
        return None
    # full_path always appends "?"; drop it when there was no query string.
    return redirect(
        "https://" + CANONICAL_HOST + request.full_path.rstrip("?"), code=301
    )


def _clean(value, max_len=1000):
    # form fields are text; anything else (a number, a list) counts as empty.
    # NUL bytes are dropped and lone surrogates become "?": the database cannot
    # store either. Line breaks count as one character, as the page's maxlength
    # counts them (a browser sends each as CRLF).
    text = intake.safe_text(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()[:max_len]


def _send_mail(subject, body, reply_to="", attachment=None):
    """Send one plain-text mail. Returns (ok, error_message, http_status)."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    # Google shows a 16-character app password in four spaced groups, and a
    # secret created with `echo` carries a trailing newline. Both get pasted in
    # as-is and both make login fail with an error indistinguishable from a
    # genuinely wrong password, so normalise rather than trust the stored form.
    password = "".join(os.getenv("SMTP_PASS", "").split())
    port = int(os.getenv("SMTP_PORT", "587"))
    recipient = os.getenv("DEMO_TO_EMAIL", "rohit@yantrailabs.com")
    sender = os.getenv("DEMO_FROM_EMAIL") or user

    if not host or not user or not password or not sender:
        missing = [
            n for n, v in (
                ("SMTP_HOST", host), ("SMTP_USER", user),
                ("SMTP_PASS", password), ("DEMO_FROM_EMAIL/SMTP_USER", sender),
            ) if not v
        ]
        app.logger.error("Mail not configured; unset: %s", ", ".join(missing))
        return False, "Email service not configured", 500

    # Visitor text reaches the subject (name, company) and Reply-To. Collapse
    # every kind of line break, or the email package refuses the header and the
    # mail is lost (a failed mail for a stored lead, a 502 for a CV or mail-only).
    subject = " ".join(str(subject).split())
    # Reply-To only when the visitor's address is one plain ASCII address a
    # header can carry (a group, a bracket, a list or a non-ASCII local part
    # would be refused or garbled): otherwise the sender. The address is in
    # the body either way.
    reply_to = intake.header_address(reply_to)

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        try:
            msg["Reply-To"] = reply_to or sender
        except Exception:
            # an address the header parser chokes on must not cost the mail
            # (and a careers CV with it): the address is in the body anyway
            del msg["Reply-To"]
            msg["Reply-To"] = sender
        msg.set_content(body)
        if attachment:
            filename, mime, data = attachment
            maintype, _, subtype = mime.partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception:
        # The visitor only ever sees "that did not go through", which is right —
        # but without this the reason (rejected app password, blocked port, TLS
        # failure) is discarded too, and the failure cannot be diagnosed from
        # the logs at all. Never log `password`.
        app.logger.exception(
            "SMTP send failed: host=%s port=%s user=%s to=%s", host, port, user, recipient
        )
        return False, "Failed to send email", 502

    return True, "", 200


@app.before_request
def offer_preferred_locale():
    """Send a first-time visitor to the locale their browser asks for.

    Only when they have never expressed a preference. Once the switcher has
    set the cookie, the URL is taken at face value in both directions — so a
    shared /fr/ link stays French for an English speaker, and someone who
    chose English is never bounced out of a page they clicked deliberately.
    """
    if request.method not in ("GET", "HEAD"):
        return None
    path = request.path
    if path.startswith(LOCALE_EXEMPT_PREFIXES) or path in LOCALE_EXEMPT_PATHS:
        return None
    if request.cookies.get(LANG_COOKIE) in SUPPORTED_LOCALES:
        return None
    if _path_locale(path) != "en":
        return None
    preferred = request.accept_languages.best_match(SUPPORTED_LOCALES, default="en")
    if preferred == "en":
        return None
    target = _locale_path(path, preferred)
    if not os.path.isdir(os.path.join(PUBLIC, target.strip("/"))) and target != "/fr/":
        return None                      # no translated page — leave them here
    query = request.query_string.decode()
    return redirect(target + ("?" + query if query else ""), code=302)


@app.after_request
def vary_on_language(response):
    """Both the cookie and Accept-Language change what this returns, so a shared
    cache must not hand one visitor's redirect to another."""
    if request.path.startswith(LOCALE_EXEMPT_PREFIXES) or request.path in LOCALE_EXEMPT_PATHS:
        return response
    existing = response.headers.get("Vary")
    parts = [p.strip() for p in existing.split(",")] if existing else []
    for h in ("Accept-Language", "Cookie"):
        if h not in parts:
            parts.append(h)
    response.headers["Vary"] = ", ".join(parts)
    return response


# the lead inbox (YantrAI Web): its page and API under /admin, their headers, and
# JSON errors under /admin/api/. Registered before the catch-all below is used,
# though Flask would prefer its fixed paths anyway.
inbox.register(app)


@app.get("/")
def home():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/<path:path>")
def static_files(path):
    # content pages are directories holding index.html — /security and
    # /security/ both have to resolve to public/security/index.html
    clean = path.rstrip("/")
    if os.path.isdir(os.path.join(PUBLIC, clean)):
        return send_from_directory(os.path.join(PUBLIC, clean), "index.html")
    return send_from_directory(PUBLIC, path)


def _store_then_notify(form, fields, subject, body, reply_to, attachment=None):
    """The intake path (intake.py): store the submission first, then mail it.

    A stored lead is safe whatever the mailbox does, so the visitor is told it
    went through even when the mail fails, except a careers application with a
    CV (502: the CV exists only in the mail). A lead that could not be stored is
    logged in full and mailed as [NOT SAVED] with replay JSON within the hourly
    caps (intake.py); past them the visitor gets 503 or 429."""
    ip = intake.client_ip(request)
    row = intake.build_row(form, fields, request, ip, cv=attachment,
                           cookie_value=request.cookies.get(LANG_COOKIE))
    subject = f"{subject} [#{intake.short_id(row['id'])}]"
    body = f"{body}\n\nLead id: {row['id']}"

    rate = intake.check_rate(ip)
    if rate == "ip":
        enforced = intake.ip_limit_enforced()
        intake.log("WARNING", "intake_ip_over_limit", id=row["id"], form=form, host=request.host,
                   xff_depth=intake.xff_depth(request), enforced=enforced)
        if enforced:
            # one visitor over its limit: refused outright, without drawing on
            # the mails and log lines kept for database outages
            return jsonify({"ok": False, "error": "Too many requests"}), 429
    elif rate == "global":
        return _unstored(row, "rate_limited", subject, body, reply_to, attachment)

    try:
        outcome = intake.insert_submission(row)
    except intake.IntakeError as exc:
        return _unstored(row, exc.reason, subject, body, reply_to, attachment, exc.detail)

    intake.log("INFO", "intake_stored", id=row["id"], form=form, outcome=outcome,
               host=request.host, xff_depth=intake.xff_depth(request))

    # a CV exists nowhere but in this mail, so it goes whatever NOTIFY_EMAIL says
    if intake.notify_email_on() or attachment:
        sent, _, _ = _send_mail(subject, body, reply_to=reply_to, attachment=attachment)
        intake.record_event(row["id"], "mail_sent" if sent else "mail_failed")
        if not sent:
            # stored, so not lost -- but nobody was told: worth an alert
            intake.log("ERROR", "intake_mail_failed", id=row["id"], form=form)
            if attachment:
                intake.log("ERROR", "careers_cv_undelivered", id=row["id"])
                return jsonify({"ok": False, "error": "Failed to send email"}), 502
    else:
        intake.record_event(row["id"], "mail_disabled")
    return jsonify({"ok": True})


def _unstored(row, reason, subject, body, reply_to, attachment, detail=""):
    """The submission could not be stored: log it, mail it as [NOT SAVED] with the
    JSON scripts/replay_intake.py needs, and tell the visitor honestly."""
    intake.log_unstored(row, reason, detail)
    sent = False
    if intake.take_fallback_slot(reason):
        sent, _, _ = _send_mail(
            "[NOT SAVED] " + subject,
            f"{body}\n\n---\nThis submission was NOT saved in the database ({reason}).\n"
            "Replay it with scripts/replay_intake.py once the database is reachable:\n"
            + intake.REPLAY_MARKER + " " + intake.replay_json(row) + "\n",
            reply_to=reply_to,
            attachment=attachment,
        )
        intake.log("WARNING" if sent else "ERROR", "intake_fallback_mail",
                   id=row["id"], reason=reason, sent=sent)
    else:
        intake.log("ERROR", "intake_fallback_mail_capped", id=row["id"], reason=reason)
    if sent:
        # the submission reached the mailbox, which is what the visitor asked for
        return jsonify({"ok": True})
    if reason in ("throttled", "rate_limited"):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    return jsonify({"ok": False, "error": "Please try again"}), 503


def _email_refused(form, email):
    # logged without the address itself, so a refusal is visible and countable
    intake.log("WARNING", "intake_email_refused", form=form, length=len(email),
               has_at=email.count("@"))
    return jsonify({"ok": False, "error": "Please check the email address"}), 400


@app.post("/api/savings-check")
def savings_check():
    storing = intake.enabled("savings_check")
    if storing:
        if (request.content_length or 0) > SAVINGS_MAX_BYTES:
            intake.log("WARNING", "intake_body_too_large", form="savings_check",
                       bytes=request.content_length)
            return jsonify({"ok": False, "error": "Request too large"}), 413
        # also bounds a chunked body, whose length is not declared up front
        request.max_content_length = SAVINGS_MAX_BYTES

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    if _clean(payload.get("website")):          # honeypot
        return jsonify({"ok": True})

    name = _clean(payload.get("name"), 120)
    email = intake.normalise_email(_clean(payload.get("email"), 1000))[:180]
    company = _clean(payload.get("company"), 180)
    role = _clean(payload.get("role"), 120)
    erp = _clean(payload.get("erp"), 120)
    outflow = _clean(payload.get("outflow"), 120)
    note = _clean(payload.get("note"), 4000)
    page = _clean(payload.get("page"), 300)

    if not name or not email or not company:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    subject = f"AiFA savings check: {company} ({name})"
    body = "\n".join(
        [
            "New AiFA savings-check request",
            "",
            f"Name: {name}",
            f"Email: {email}",
            f"Company: {company}",
            f"Role: {role or 'Not provided'}",
            f"ERP: {erp or 'Not provided'}",
            f"Annual outflow: {outflow or 'Not provided'}",
            "",
            "Notes:",
            note or "Not provided",
            "",
            f"From: {page or 'Not provided'}",
        ]
    )
    # visitor text cannot pose as the replay line a [NOT SAVED] mail ends with
    body = intake.defuse_markers(body)

    if not storing:
        # no database configured for this form: mail only, as before
        ok, error, status = _send_mail(subject=subject, body=body, reply_to=email)
        if not ok:
            return jsonify({"ok": False, "error": error}), status
        return jsonify({"ok": True})

    if not intake.looks_like_email(email):
        return _email_refused("savings_check", email)
    fields = {"name": name, "email": email, "company": company, "role": role, "erp": erp,
              "outflow": outflow, "note": note, "page": page}
    return _store_then_notify("savings_check", fields, subject, body, reply_to=email)


@app.post("/api/careers")
def careers():
    cv = request.files.get("resume")
    if not (cv and cv.filename):
        return _careers()
    if not _cv_slots.acquire(timeout=30):
        return jsonify({"ok": False, "error": "Busy, please try again"}), 503
    try:
        return _careers()
    finally:
        _cv_slots.release()


def _careers():
    """Open applications. The CV rides along as a mail attachment rather than
    landing in a bucket — at this hiring volume an inbox is the right store,
    and it means no storage to secure or clean up."""
    if _clean(request.form.get("website")):          # honeypot
        return jsonify({"ok": True})

    name = _clean(request.form.get("name"), 120)
    email = intake.normalise_email(_clean(request.form.get("email"), 1000))[:180]
    linkedin = _clean(request.form.get("linkedin"), 300)
    work = _clean(request.form.get("work"), 300)
    area = _clean(request.form.get("area"), 120)
    note = _clean(request.form.get("note"), 6000)
    page = _clean(request.form.get("page"), 300)

    if not name or not email:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    attachment = None
    cv = request.files.get("resume")
    if cv and cv.filename:
        ext = os.path.splitext(cv.filename)[1].lower()
        if ext not in CV_EXTENSIONS:
            return jsonify({"ok": False, "error": "Unsupported file type"}), 400
        data = cv.read(MAX_CV_BYTES + 1)
        if len(data) > MAX_CV_BYTES:
            return jsonify({"ok": False, "error": "File too large"}), 413
        if data:
            attachment = (secure_filename(cv.filename) or "cv" + ext,
                          CV_EXTENSIONS[ext], data)

    subject = f"Careers: {name}" + (f" — {area}" if area else "")
    body = "\n".join(
        [
            "Open application",
            "",
            f"Name: {name}",
            f"Email: {email}",
            f"LinkedIn: {linkedin or 'Not provided'}",
            f"Something they made: {work or 'Not provided'}",
            f"Area: {area or 'Not provided'}",
            f"CV: {attachment[0] if attachment else 'Not attached'}",
            "",
            "What they'd want to own:",
            note or "Not provided",
            "",
            f"From: {page or 'Not provided'}",
        ]
    )
    body = intake.defuse_markers(body)

    if not intake.enabled("careers"):
        # applications are stored only once INTAKE_FORMS lists careers
        ok, error, status = _send_mail(subject=subject, body=body, reply_to=email,
                                       attachment=attachment)
        if not ok:
            return jsonify({"ok": False, "error": error}), status
        return jsonify({"ok": True})

    if not intake.looks_like_email(email):
        return _email_refused("careers", email)
    fields = {"name": name, "email": email, "linkedin": linkedin, "work": work,
              "area": area, "note": note, "page": page}
    return _store_then_notify("careers", fields, subject, body, reply_to=email,
                              attachment=attachment)


# not /healthz — Cloud Run reserves that path and answers it before the
# request reaches the container
@app.get("/_status")
def status():
    # "mail", "db" and "inbox" say whether each is configured, not that it works:
    # a live check here would let anyone open database connections at will
    return jsonify({
        "ok": True,
        "mail": bool(os.getenv("SMTP_PASS")),
        "db": intake.configured(),
        "intake_forms": sorted(intake.forms()) if intake.configured() else [],
        "inbox": inbox.configured(),
        "version": _version(),
    })


def _version():
    """What is running: the commit scripts/deploy.sh recorded (GIT_COMMIT) and
    the Cloud Run revision (K_REVISION, set by Cloud Run). The footer shows it."""
    commit = (os.getenv("GIT_COMMIT") or "").strip().lower()
    return {
        "commit": commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else None,
        "revision": (os.getenv("K_REVISION") or "").strip()[:100] or None,
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
