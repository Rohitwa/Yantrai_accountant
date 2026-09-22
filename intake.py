"""Store each submission of the forms listed in INTAKE_FORMS in the platform
Supabase before any mail goes out, so a broken mailbox no longer loses a lead.

The website connects as website_app, a login that can only INSERT into its own
closed schema, website_intake (db/001_intake.sql). It cannot read anything back:
that is why ids are made here rather than returned by the database, and why a
duplicate id (a retry of the same submission) counts as already stored.

Everything is switched on by configuration, not code:

  WEBSITE_DB_URL    postgresql://website_app.<project>:<password>@<pooler>:5432/postgres?sslmode=require
                    (Secret Manager: website-db-url; the password percent-encoded).
                    Unset = mail only, as before, with the same hardening (pasted
                    mailto:, quotes, brackets and trailing ; , . removed; Reply-To falls
                    back to the sender unless it is one plain ASCII address; non-text
                    fields count as empty). sslmode=verify-full plus
                    sslrootcert=<path to a CA file> also checks the certificate.
  WEBSITE_DB_SSLROOTCERT  optional; replaces the URL's sslrootcert path. For running the
                    replay script on a laptop against a secret that names the Cloud Run
                    mount path.
  INTAKE_FORMS      comma list of forms to store (default "savings_check"). Add "careers"
                    only once retention and consent for applicants are decided.
  NOTIFY_EMAIL      "1" (default) mails each stored lead as before; "0" stores it silently.
                    A careers CV is mailed regardless: the row holds no copy of the file.
  IP_HASH_SALT      optional; when set, rows carry an HMAC of the visitor's IP for abuse triage.
  TRUSTED_XFF_HOPS  how many proxies append to X-Forwarded-For in front of the app (default 1).
                    Above 1 the run.app address becomes spoofable: raise it only once that
                    address is closed or told apart (the logs carry `host`).
  INTAKE_IP_LIMIT   "log" (default) only logs a visitor sending more than 5 submissions in
                    10 minutes; "enforce" answers them 429. In "log" mode one busy client
                    can fill this instance's window (60 admitted in 10 minutes) and can
                    also use up the database's 300-an-hour savings guard, which then
                    throttles every instance. Until those drain, other visitors are not
                    stored: a few go out as [NOT SAVED] mails and the rest get 429.
                    Switch to "enforce" once the logs show real visitor addresses (see
                    TRUSTED_XFF_HOPS).

Logs are one JSON object per line on stdout, which Cloud Logging reads as
structured entries (severity, event, id, ...). A connection string is never logged.
"""
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import threading
import time
import uuid
from collections import deque
from urllib.parse import parse_qs, unquote, urlsplit

FORMS = ("savings_check", "careers")

# the only columns website_app may write (db/001_intake.sql grants exactly these)
COLUMNS = (
    "id", "form", "locale", "page", "name", "email", "company", "role", "erp",
    "outflow", "note", "linkedin", "work", "area", "cv_filename", "cv_mime",
    "cv_bytes", "cv_sha256", "ip_hash", "user_agent", "source_revision",
)
_INSERT_SUBMISSION = (
    "INSERT INTO website_intake.submissions (" + ", ".join(COLUMNS) + ") VALUES ("
    + ", ".join(":" + c for c in COLUMNS) + ")"
)
_INSERT_EVENT = (
    "INSERT INTO website_intake.notify_events (submission_id, event) VALUES (:submission_id, :event)"
)
EVENTS = ("mail_sent", "mail_failed", "mail_disabled")

SOCKET_TIMEOUT = 6          # connect + statement; the role's own statement_timeout is 5 s
MAX_CONCURRENT_WRITES = 3   # per instance; the role allows 10 connections in all
SLOT_WAIT = 5               # seconds a submission waits for a write slot
EVENT_SLOT_WAIT = 0.5       # a delivery record waits less, and is skipped when busy

IP_WINDOW, IP_LIMIT = 600, 5              # per visitor: 5 submissions / 10 minutes
# per instance: 60 submissions admitted to the database / 10 minutes (stored or
# not), enough for a burst of real visitors (after a webinar, say). Refused
# requests are not counted, so the window reopens as soon as the oldest admitted
# one ages out. During an outage the overflow past 60 is logged as rate_limited
# and uses the flood allowance. The hourly bound is the database's flood guard
# (db/001_intake.sql), shared by every instance.
GLOBAL_WINDOW, GLOBAL_LIMIT = 600, 60
# kept for database outages; rate-limited and throttled traffic has its own
# allowance: as many log lines (so a burst stays replayable), few mails
UNSTORED_PAYLOAD_PER_HOUR = 200
FALLBACK_MAILS_PER_HOUR = 30
RATE_LIMITED_PAYLOAD_PER_HOUR = 200
RATE_LIMITED_MAILS_PER_HOUR = 5


class IntakeError(Exception):
    """The row was not stored. `reason` is one of: unconfigured, busy,
    unavailable, throttled, rejected."""

    def __init__(self, reason, detail=""):
        super().__init__(reason if not detail else reason + ": " + detail)
        self.reason = reason
        self.detail = detail


# --- configuration ---------------------------------------------------------------
def configured():
    return bool(os.getenv("WEBSITE_DB_URL", "").strip())


def forms():
    raw = os.getenv("INTAKE_FORMS")
    listed = {"savings_check"} if raw is None else {f.strip() for f in raw.split(",") if f.strip()}
    return listed & set(FORMS)


def enabled(form):
    """True when this form's submissions are stored before mail goes out."""
    return configured() and form in forms()


def notify_email_on():
    return os.getenv("NOTIFY_EMAIL", "1").strip().lower() not in ("0", "false", "no", "off")


_NOT_IN_ADDRESS = set('[]()<>,;:"\\')
# what a mail header can carry: an unquoted ASCII local part (RFC 5322
# dot-atom) and a domain of plain letter-digit-hyphen labels, as IDNA gives
_DOT_ATOM = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
_LDH_DOMAIN = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                         r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
# Python's idna codec is IDNA 2003, which maps these differently from the IDNA
# 2008 that registries and mail use (straße.de would become strasse.de)
_IDNA_DEVIATIONS = set("\u00df\u03c2\u200c\u200d")
REPLAY_MARKER = "REPLAY-JSON:"


def normalise_email(value):
    """What people paste around an address, removed until nothing changes:
    quotes, brackets and parentheses (paired or not), a mailto: and anything
    from its '?', and trailing separators or a full stop (Outlook adds ';', a
    sentence adds '.')."""
    value = (value or "").strip()
    while True:
        before = value
        if value[:7].lower() == "mailto:":
            value = value[7:].split("?", 1)[0]
        value = value.strip().lstrip("<([\"'").rstrip(">)]\"',;:.").strip()
        if value == before:
            return value


def header_address(value):
    """`value` as one plain ASCII address a mail header can carry (the domain
    IDNA-encoded), or "" when it is not one."""
    value = " ".join(str(value or "").split())
    if not looks_like_email(value):
        return ""
    local, domain = value.rsplit("@", 1)
    if not _DOT_ATOM.fullmatch(local) or _IDNA_DEVIATIONS & set(domain):
        return ""
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    # IDNA maps full-width punctuation to ASCII, so check what came out
    if not _LDH_DOMAIN.fullmatch(domain):
        return ""
    return local + "@" + domain


def defuse_markers(text):
    """Visitor text going into a mail body: a line that starts with the replay
    marker is indented, so only the server's own line can be read for replay."""
    return "\n".join(" " + line if line.startswith(REPLAY_MARKER) else line
                     for line in str(text).split("\n"))


def looks_like_email(value):
    """one @, something on both sides, a dot in the domain, no whitespace, and
    none of the characters that make a mail header parse as a group or a
    quoted/bracketed address (Python's email package raises on some of those)"""
    value = value or ""
    if any(ch.isspace() or ch in _NOT_IN_ADDRESS for ch in value) or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain.strip(".") and not domain.startswith(".")


# --- logging -------------------------------------------------------------------------
def log(severity, event, **fields):
    """One structured line for Cloud Logging. Never pass a DSN or a password.
    ASCII-escaped, so no visitor text can split or break the line."""
    entry = {"severity": severity, "event": event, "message": event}
    entry.update(fields)
    try:
        line = json.dumps(entry, default=str, ensure_ascii=True)
    except Exception:
        line = json.dumps({"severity": severity, "event": event, "message": event,
                           "id": str(fields.get("id")), "log_error": "fields not serialisable"})
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


# --- request facts ----------------------------------------------------------------------
def client_ip(request):
    """The visitor's address: the entry TRUSTED_XFF_HOPS from the right of
    X-Forwarded-For (each trusted proxy appends one), else the socket peer."""
    xff = [p.strip() for p in (request.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
    try:
        hops = max(1, int(os.getenv("TRUSTED_XFF_HOPS", "1")))
    except ValueError:
        hops = 1
    if xff:
        return xff[-hops] if len(xff) >= hops else xff[0]
    return request.remote_addr or ""


def xff_depth(request):
    """How many entries X-Forwarded-For carries: logged (with the host) so
    TRUSTED_XFF_HOPS can be set from evidence. The addresses are not logged."""
    return len([p for p in (request.headers.get("X-Forwarded-For") or "").split(",") if p.strip()])


def hash_ip(ip):
    salt = os.getenv("IP_HASH_SALT", "")
    if not salt or not ip:
        return None
    return hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def locale_of(page, cookie_value):
    path = urlsplit(page or "").path
    if path == "/fr" or path.startswith("/fr/"):
        return "fr"
    return "fr" if cookie_value == "fr" else "en"


def new_id():
    return str(uuid.uuid4())


def short_id(submission_id):
    return str(submission_id)[:8]


def build_row(form, fields, request, ip, cv=None, cookie_value=None):
    """The row to store. `fields` are the handler's cleaned values; `cv` is
    (filename, mime, bytes) when a CV was attached. Only metadata about the CV is
    kept here; the file itself travels by mail."""
    if form not in FORMS:
        raise ValueError("unknown form: " + str(form))
    row = {c: None for c in COLUMNS}
    for key, value in fields.items():
        if key in row and key not in ("id", "form"):
            row[key] = value or None
    row["id"] = new_id()
    row["form"] = form
    row["locale"] = locale_of(fields.get("page"), cookie_value)
    row["user_agent"] = safe_text(request.headers.get("User-Agent"))[:300] or None
    row["source_revision"] = (os.getenv("K_REVISION") or "")[:100] or None
    row["ip_hash"] = hash_ip(ip)
    if cv:
        filename, mime, data = cv
        row["cv_filename"] = (filename or "")[:200] or None
        row["cv_mime"] = mime
        row["cv_bytes"] = len(data)
        row["cv_sha256"] = hashlib.sha256(data).hexdigest()
    return row


def safe_text(value):
    """Text Postgres will store: no NUL bytes, no lone surrogates."""
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def replay_json(row):
    """The row as one ASCII line of JSON: what scripts/replay_intake.py accepts."""
    return json.dumps({c: row.get(c) for c in COLUMNS}, ensure_ascii=True, separators=(",", ":"))


# --- limits ------------------------------------------------------------------------------
class _Window:
    """Thread-safe sliding-window counter per key."""

    def __init__(self, seconds, limit):
        self.seconds, self.limit = seconds, limit
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, count_refused=True):
        """Record one hit; True when the key is now over its limit. With
        count_refused=False a hit over the limit is not recorded, so the
        window holds only what it let through."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.seconds:
                q.popleft()
            over = len(q) >= self.limit
            if count_refused or not over:
                q.append(now)
            if len(self._hits) > 5000:          # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.seconds]:
                    del self._hits[k]
            return over


class _HourlyCap:
    """At most `limit` grants per rolling hour per instance. Refused attempts are
    not counted, so the allowance comes back an hour after the grants it made."""

    def __init__(self, limit, seconds=3600):
        self.limit, self.seconds = limit, seconds
        self._grants = deque()
        self._lock = threading.Lock()

    def take(self):
        now = time.monotonic()
        with self._lock:
            while self._grants and now - self._grants[0] > self.seconds:
                self._grants.popleft()
            if len(self._grants) >= self.limit:
                return False
            self._grants.append(now)
            return True


_per_ip = _Window(IP_WINDOW, IP_LIMIT)
_global = _Window(GLOBAL_WINDOW, GLOBAL_LIMIT)
_unstored_payloads = _HourlyCap(UNSTORED_PAYLOAD_PER_HOUR)
_fallback_mails = _HourlyCap(FALLBACK_MAILS_PER_HOUR)
_rate_limited_payloads = _HourlyCap(RATE_LIMITED_PAYLOAD_PER_HOUR)
_rate_limited_mails = _HourlyCap(RATE_LIMITED_MAILS_PER_HOUR)
_write_slots = threading.BoundedSemaphore(MAX_CONCURRENT_WRITES)


def ip_limit_enforced():
    return os.getenv("INTAKE_IP_LIMIT", "log").strip().lower() == "enforce"


def check_rate(ip):
    """'ok', 'ip' (this visitor is over its limit) or 'global' (this instance is).

    The visitor is checked first. When the per-visitor limit is enforced, a
    refused request is not counted against the instance, so one visitor cannot
    fill the instance's window and lock everyone else out. A visitor's refused
    requests do count against that visitor (retrying while over keeps them
    over); the instance's window counts only what it let through."""
    ip_over = bool(ip) and _per_ip.hit(ip)
    if ip_over and ip_limit_enforced():
        return "ip"
    if _global.hit("all", count_refused=False):
        return "global"
    return "ip" if ip_over else "ok"


# refused because of volume (this instance's window, or the database's flood
# guard), not because storage failed
FLOOD_REASONS = ("rate_limited", "throttled")


def take_fallback_slot(reason):
    """May a [NOT SAVED] mail go out now? Outage failures and flood traffic
    draw on separate allowances, so a flood cannot use up the mails kept for a
    database outage."""
    cap = _rate_limited_mails if reason in FLOOD_REASONS else _fallback_mails
    return cap.take()


def log_unstored(row, reason, detail=""):
    """The submission as a log line, so it can be replayed. Capped per hour (on
    separate allowances for outages and for floods); past the cap only the id
    and the reason are logged."""
    cap = _rate_limited_payloads if reason in FLOOD_REASONS else _unstored_payloads
    if cap.take():
        log("ERROR", "intake_unstored", id=row.get("id"), form=row.get("form"),
            reason=reason, detail=detail, row={c: row.get(c) for c in COLUMNS})
    else:
        log("ERROR", "intake_unstored", id=row.get("id"), form=row.get("form"),
            reason=reason, detail=detail, row_omitted="hourly payload cap reached")


# --- the database --------------------------------------------------------------------------
def _connect_args():
    url = os.getenv("WEBSITE_DB_URL", "").strip()
    if not url:
        raise IntakeError("unconfigured")
    try:
        parts = urlsplit(url)
        port = parts.port or 5432
    except ValueError:
        raise IntakeError("unconfigured", "WEBSITE_DB_URL could not be parsed") from None
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise IntakeError("unconfigured", "WEBSITE_DB_URL is not a postgresql:// URL")
    query = parse_qs(parts.query)
    sslmode = (query.get("sslmode") or ["require"])[0]
    rootcert = os.getenv("WEBSITE_DB_SSLROOTCERT") or (query.get("sslrootcert") or [None])[0]
    if sslmode == "disable":            # local tests only
        ctx = None
    elif sslmode in ("verify-ca", "verify-full"):
        ctx = ssl.create_default_context(cafile=rootcert) if rootcert else ssl.create_default_context()
        ctx.check_hostname = sslmode == "verify-full"
    else:
        # libpq's `require`: encrypted, certificate not checked. The Supabase
        # pooler's certificate is signed by Supabase's own CA; use verify-full
        # with sslrootcert= pointing at that CA to check it.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return {
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "host": parts.hostname,
        "port": port,
        "database": (parts.path or "/postgres").lstrip("/") or "postgres",
        "ssl_context": ctx,
        "timeout": SOCKET_TIMEOUT,
        "application_name": "yantrai-website-intake",
    }


def _why(exc):
    """The error class, and the one underneath when there is one: pg8000 wraps
    connect errors (InterfaceError/ConnectionRefusedError), while a refused
    certificate arrives as SSLCertVerificationError and a missing CA file as
    FileNotFoundError. Tells a certificate problem from an outage in the logs."""
    names = [type(exc).__name__]
    inner = exc.__cause__ or exc.__context__
    if inner is not None and type(inner).__name__ not in names:
        names.append(type(inner).__name__)
    return "/".join(names)


def _sqlstate(exc):
    arg = exc.args[0] if getattr(exc, "args", None) else None
    return arg.get("C") if isinstance(arg, dict) else None


def _run(sql, params, retry=True):
    """Open a short connection, run one statement, close. Retries once on a
    connection-level failure (the same id makes a retry harmless); SQL errors
    are raised to the caller."""
    import pg8000.native
    from pg8000.exceptions import InterfaceError

    args = _connect_args()
    attempts = 2 if retry else 1
    for attempt in range(1, attempts + 1):
        con = None
        try:
            con = pg8000.native.Connection(**args)
            con.run(sql, **params)
            return
        except (InterfaceError, OSError):
            if attempt == attempts:
                raise
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass


def insert_submission(row):
    """Store the row. Returns 'stored' or 'duplicate' (already there: a retry or a
    replay). Raises IntakeError when it was not stored."""
    try:
        from pg8000.exceptions import DatabaseError, InterfaceError
    except Exception as exc:                # a broken install must still fall back
        raise IntakeError("unavailable", _why(exc)) from None

    if not _write_slots.acquire(timeout=SLOT_WAIT):
        raise IntakeError("busy", "no free database write slot")
    try:
        _run(_INSERT_SUBMISSION, {c: row.get(c) for c in COLUMNS})
        return "stored"
    except IntakeError:
        raise
    except DatabaseError as exc:
        code = _sqlstate(exc)
        if code == "23505":                 # the same id is already stored
            return "duplicate"
        if code == "WI429":                 # the database's own flood guard
            raise IntakeError("throttled", code)
        if code and code[:2] in ("22", "23"):   # data the table refused
            raise IntakeError("rejected", code)
        raise IntakeError("unavailable", code or _why(exc))
    except (InterfaceError, OSError) as exc:
        raise IntakeError("unavailable", _why(exc))
    except Exception as exc:                # never let intake take the handler down
        raise IntakeError("unavailable", _why(exc))
    finally:
        _write_slots.release()


def connection_check():
    """Connect with WEBSITE_DB_URL and return the login's name, changing nothing:
    proves a new secret works before the service is switched on to use it."""
    try:
        import pg8000.native
        from pg8000.exceptions import DatabaseError
    except Exception as exc:
        raise IntakeError("unavailable", _why(exc)) from None
    con = None
    try:
        con = pg8000.native.Connection(**_connect_args())
        return str(con.run("SELECT current_user")[0][0])
    except IntakeError:
        raise
    except DatabaseError as exc:
        raise IntakeError("unavailable", _sqlstate(exc) or _why(exc))
    except Exception as exc:
        raise IntakeError("unavailable", _why(exc))
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def record_event(submission_id, event):
    """Best effort: note whether the notification mail went out. One attempt,
    only when a write slot is free at once-ish. Never raises."""
    if event not in EVENTS or not configured():
        return
    if not _write_slots.acquire(timeout=EVENT_SLOT_WAIT):
        log("WARNING", "intake_event_skipped", id=submission_id, mail_event=event,
            detail="no free database write slot")
        return
    try:
        _run(_INSERT_EVENT, {"submission_id": submission_id, "event": event}, retry=False)
    except Exception as exc:
        code = _sqlstate(exc)
        if code != "23505":
            log("WARNING", "intake_event_unrecorded", id=submission_id, mail_event=event,
                detail=code or _why(exc))
    finally:
        _write_slots.release()
