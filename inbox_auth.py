"""Who may open YantrAI Web, the lead inbox the YantrAI platform shows as a tile.

The platform opens /admin in an iframe with ?token=<a sign-in token it mints for
this app>. The token is the platform's own format, not a JWT:

    base64url(compact JSON) + "." + base64url(HMAC-SHA256(key, <that base64url text>))

both halves unpadded, where key is the UTF-8 bytes of this app's signing key
(store_agents.signing_key, "sk_" + 48 hex) and the JSON carries kid = this app's
client id, exp (300 s ahead) and the user: u, uid, o, su, ... (platform
server.py _sso_sign / GET /api/agents/sso-token; docs/REMOTE_AGENT_SPEC.md).

The rule: only a platform admin gets in, which on the platform means an account
whose role is super_admin: the claim su is exactly true. Nothing else in the
token is trusted for access (r, ga, w and f describe a customer's own grants).
The platform's own gates for a 'sadmin' app fail open on a database error, and a
customer group admin can grant any app to themselves, so this check is the one
that matters, and it fails closed: no key or client id configured = no access.

A verified token is exchanged once for this app's own session token, in a
different format and signed with a separate secret (INBOX_SESSION_KEY), so
neither can ever pass for the other, holding the platform's copy of the signing
key is not enough to make a session, and changing INBOX_SESSION_KEY signs
everyone out without touching the platform.

Settings (all required; without them every /admin path answers 404):
  INBOX_SIGNING_KEY   this app's store_agents.signing_key (secret website-inbox-key)
  INBOX_CLIENT_ID     this app's store_agents.client_id (not secret)
  INBOX_SESSION_KEY   64 hex characters, for the inbox's own sessions (secret website-inbox-session-key)
  INBOX_DB_URL        website_admin's connection URL (secret website-admin-db-url; see inbox.py)
  INBOX_PLATFORM_ORIGIN  optional, default https://workspace.yantrailabs.com
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid

PLATFORM_TOKEN_MAX_AHEAD = 330        # the platform mints 300 s tokens; allow a little clock skew
SESSION_TTL = 2 * 60 * 60             # an inbox session ends 2 hours after sign-in
SESSION_PREFIX = "yw1"
MAX_TOKEN_LENGTH = 4096
_B64U = re.compile(r"[A-Za-z0-9_-]+")
_CLIENT_ID = re.compile(r"cid_[0-9a-f]{16}")
_SIGNING_KEY = re.compile(r"sk_[0-9a-f]{48}")
_SESSION_SECRET = re.compile(r"[0-9a-f]{64}")


def signing_key():
    key = os.getenv("INBOX_SIGNING_KEY", "").strip()
    return key if _SIGNING_KEY.fullmatch(key) else ""


def client_id():
    cid = os.getenv("INBOX_CLIENT_ID", "").strip()
    return cid if _CLIENT_ID.fullmatch(cid) else ""


def _session_secret():
    key = os.getenv("INBOX_SESSION_KEY", "").strip().lower()
    return bytes.fromhex(key) if _SESSION_SECRET.fullmatch(key) else b""


def platform_origin():
    """Where the platform runs: the only page allowed to frame the inbox, and where
    "Home" and "open it again" point."""
    origin = os.getenv("INBOX_PLATFORM_ORIGIN", "https://workspace.yantrailabs.com").strip().rstrip("/")
    if re.fullmatch(r"https://[a-z0-9.-]+(:\d{1,5})?", origin) or \
       re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?", origin):
        return origin
    return "https://workspace.yantrailabs.com"


def configured():
    """The platform's credentials for this app and the session secret are all
    present and well formed (the database URL is inbox.py's to check)."""
    return bool(signing_key() and client_id() and _session_secret())


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _split(token):
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    parts = token.split(".")
    if len(parts) != 2 or not all(_B64U.fullmatch(p) for p in parts):
        return None
    return parts


def _payload(body):
    try:
        payload = json.loads(_b64u_decode(body))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _int(value):
    # bool is an int in Python; a token saying exp=true is not a time
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def verify_platform_token(token, now=None):
    """The token's claims if it is a valid sign-in token for THIS app, else None.

    Checks: the HMAC over the base64url body with this app's signing key (compared
    in constant time), kid == this app's client id, exp an integer that has not
    passed and is not implausibly far ahead, and not a provisioning token (the
    platform signs those, sent to other apps when someone is granted one, with the
    same key but no kid; they never sign anyone in)."""
    key, cid = signing_key(), client_id()
    parts = _split(token)
    if not key or not cid or parts is None:
        return None
    body, sig = parts
    expected = _b64u(hmac.new(key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig.encode("ascii"), expected.encode("ascii")):
        return None
    payload = _payload(body)
    if payload is None:
        return None
    now = int(time.time()) if now is None else now
    exp = _int(payload.get("exp"))
    if exp is None or exp < now or exp > now + PLATFORM_TOKEN_MAX_AHEAD:
        return None
    if "provision" in payload or payload.get("kid") != cid:
        return None
    return payload


def admin_identity(claims):
    """(uid, username, org) for a platform admin, else None. su must be exactly true
    and uid a UUID: the platform sets both from the account itself."""
    if not claims or claims.get("su") is not True or not _is_uuid(claims.get("uid")):
        return None
    name = claims.get("u")
    name = name.strip()[:120] if isinstance(name, str) else ""
    org = claims.get("o")
    return (claims["uid"].lower(), name, org.lower() if _is_uuid(org) else None)


def token_digest(token):
    """What the sign-in record keeps of a platform token: enough to refuse it a
    second time, not enough to use it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- this app's own session --------------------------------------------------------------
def _session_key():
    return _session_secret()


def issue_session(uid, name, org, now=None):
    if not _session_key():
        raise RuntimeError("INBOX_SESSION_KEY is not set")
    now = int(time.time()) if now is None else now
    claims = {"uid": uid, "u": name, "o": org, "iat": now, "exp": now + SESSION_TTL,
              "sid": _b64u(os.urandom(12))}
    body = _b64u(json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("ascii"))
    signed = SESSION_PREFIX + "." + body
    sig = _b64u(hmac.new(_session_key(), signed.encode("ascii"), hashlib.sha256).digest())
    return signed + "." + sig, claims["exp"]


def verify_session(token, now=None):
    """The session's claims if it is a live session this app issued, else None."""
    key = _session_key()
    if not key or not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != SESSION_PREFIX or not all(_B64U.fullmatch(p) for p in parts[1:]):
        return None
    signed = parts[0] + "." + parts[1]
    expected = _b64u(hmac.new(key, signed.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(parts[2].encode("ascii"), expected.encode("ascii")):
        return None
    claims = _payload(parts[1])
    if claims is None:
        return None
    now = int(time.time()) if now is None else now
    exp, iat = _int(claims.get("exp")), _int(claims.get("iat"))
    if exp is None or iat is None or exp < now or exp - iat > SESSION_TTL or iat > now + 60:
        return None
    if not _is_uuid(claims.get("uid")):
        return None
    return claims
