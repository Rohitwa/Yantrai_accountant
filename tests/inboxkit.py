"""Shared by the inbox tests: made-up credentials and a stand-in for the platform's
token mint (platform server.py _sso_sign: base64url(compact JSON) + "." +
base64url(HMAC-SHA256(<signing key as UTF-8>, <the base64url body>)), unpadded)."""
import base64
import hashlib
import hmac
import json
import time
import uuid

KEY = "sk_" + "0123456789abcdef" * 3
CLIENT = "cid_0123456789abcdef"
SESSION_KEY = "5e" * 32
ADMIN_UID = "7d9f3c1e-2b4a-4c6d-8e0f-1a2b3c4d5e6f"
PLATFORM_ORG = "6a489447-660c-47c3-944b-f35434b7f9d2"


def b64u(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint(claims=None, key=KEY, **overrides):
    """A platform sign-in token for the inbox, as the platform mints one for a
    super admin (claims per server.py GET /api/agents/sso-token)."""
    payload = {"u": "sadmin", "c": "YantrAI Platform Owner", "exp": int(time.time()) + 300,
               "o": PLATFORM_ORG, "uid": ADMIN_UID, "su": True, "ga": True,
               "w": str(uuid.uuid4()), "r": "admin", "kid": CLIENT}
    if claims is not None:
        payload = claims
    payload.update(overrides)
    for k in [k for k, v in payload.items() if v is ...]:
        del payload[k]                                  # overrides with ... remove a claim
    body = b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = b64u(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def configure(env, db_url="postgresql://website_admin:x@127.0.0.1:1/inbox?sslmode=disable"):
    env.setenv("INBOX_SIGNING_KEY", KEY)
    env.setenv("INBOX_CLIENT_ID", CLIENT)
    env.setenv("INBOX_SESSION_KEY", SESSION_KEY)
    env.setenv("INBOX_DB_URL", db_url)
