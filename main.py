"""yantrailabs.com — AiFA, AI Teams for Finance.

Everything served to the browser lives in public/. This file only routes and
handles the form; nothing else in the repo is reachable over HTTP.
"""
import os
import smtplib
from email.message import EmailMessage

from flask import Flask, jsonify, redirect, request, send_from_directory

# static_folder is off on purpose — Flask's built-in static route would be
# registered ahead of ours and serve the repo root, source files included.
app = Flask(__name__, static_folder=None)

PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

# The page declares yantrailabs.com canonical — <link rel="canonical">, og:url,
# the sitemap and robots.txt all name it. Both hostnames are mapped to this
# service, so without this the same site answers on two of them and they
# compete as duplicates. www is sent to the apex instead.
#
# Navigations only. A form POST redirected across hostnames is a cross-origin
# request the browser would block, and www is a perfectly good origin to answer
# the API on — only the page the user lands on needs to settle on one host.
CANONICAL_HOST = "yantrailabs.com"


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


def _send_mail(subject, body, reply_to=""):
    """Send one plain-text mail. Returns (ok, error_message, http_status)."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
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
@app.get("/_status")
def status():
    return jsonify({"ok": True, "mail": bool(os.getenv("SMTP_PASS"))})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
