"""yantrailabs.com — AiFA, AI Teams for Finance.

Everything served to the browser lives in public/. This file only routes and
handles the form; nothing else in the repo is reachable over HTTP.
"""
import os
import smtplib
from email.message import EmailMessage

from flask import Flask, jsonify, redirect, request, send_from_directory
from werkzeug.utils import secure_filename

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
LOCALE_EXEMPT_PREFIXES = ("/api/", "/assets/")
LOCALE_EXEMPT_PATHS = ("/site.css", "/page.css", "/app.js",
                       "/robots.txt", "/sitemap.xml", "/_status", "/favicon.ico")


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
    return (value or "").strip()[:max_len]


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

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = reply_to or sender
    msg.set_content(body)
    if attachment:
        filename, mime, data = attachment
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    try:
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


@app.post("/api/savings-check")
def savings_check():
    payload = request.get_json(silent=True) or {}

    if _clean(payload.get("website")):          # honeypot
        return jsonify({"ok": True})

    name = _clean(payload.get("name"), 120)
    email = _clean(payload.get("email"), 180)
    company = _clean(payload.get("company"), 180)
    role = _clean(payload.get("role"), 120)
    erp = _clean(payload.get("erp"), 120)
    outflow = _clean(payload.get("outflow"), 120)
    note = _clean(payload.get("note"), 4000)
    page = _clean(payload.get("page"), 300)

    if not name or not email or not company:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    ok, error, status = _send_mail(
        subject=f"AiFA savings check: {company} ({name})",
        body="\n".join(
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
        ),
        reply_to=email,
    )
    if not ok:
        return jsonify({"ok": False, "error": error}), status
    return jsonify({"ok": True})


# not /healthz — Cloud Run reserves that path and answers it before the
# request reaches the container
@app.post("/api/careers")
def careers():
    """Open applications. The CV rides along as a mail attachment rather than
    landing in a bucket — at this hiring volume an inbox is the right store,
    and it means no storage to secure or clean up."""
    if _clean(request.form.get("website")):          # honeypot
        return jsonify({"ok": True})

    name = _clean(request.form.get("name"), 120)
    email = _clean(request.form.get("email"), 180)
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

    ok, error, status = _send_mail(
        subject=f"Careers: {name}" + (f" — {area}" if area else ""),
        body="\n".join(
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
        ),
        reply_to=email,
        attachment=attachment,
    )
    if not ok:
        return jsonify({"ok": False, "error": error}), status
    return jsonify({"ok": True})


@app.get("/_status")
def status():
    return jsonify({"ok": True, "mail": bool(os.getenv("SMTP_PASS"))})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
