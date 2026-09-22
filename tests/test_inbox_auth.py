"""inbox_auth.py: who may open YantrAI Web. Only a platform admin (su exactly
true), only with a token minted for this app, and only while it is fresh."""
import time

import pytest

import inbox_auth
from inboxkit import ADMIN_UID, CLIENT, KEY, PLATFORM_ORG, SESSION_KEY, b64u, configure, mint


@pytest.fixture
def conf(env):
    configure(env)
    return env


def test_a_super_admins_token_for_this_app_is_accepted(conf):
    claims = inbox_auth.verify_platform_token(mint())
    assert claims["kid"] == CLIENT
    assert inbox_auth.admin_identity(claims) == (ADMIN_UID, "sadmin", PLATFORM_ORG)


# tokens are minted inside the test, never at collection: a slow run must not
# make every case "refused" merely because the token expired
@pytest.mark.parametrize("make", [
    lambda: mint(key="sk_" + "f" * 48),                                 # another app's key
    lambda: mint(kid="cid_ffffffffffffffff"),                           # another app's id
    lambda: mint(kid=...),                                              # no kid: the platform's global secret
    lambda: mint(exp=int(time.time()) - 1),                             # expired
    lambda: mint(exp=int(time.time()) + 3600),                          # implausibly far ahead
    lambda: mint(exp=True),                                             # a boolean is not a time
    lambda: mint(exp=str(int(time.time()) + 300)),
    lambda: mint(exp=...),
    lambda: mint(provision=True),                                       # a grant's provisioning token
    lambda: mint(provision=False),
    lambda: mint()[:-2] + "xx",                                         # tampered signature
    lambda: mint().split(".")[0] + "." + mint(u="someone-else").split(".")[1],   # another body's signature
    lambda: "", lambda: None, lambda: 42, lambda: "a.b.c", lambda: "only-one-part", lambda: "é.é",
    lambda: "x" * 5000,
])
def test_anything_else_is_refused(conf, make):
    assert inbox_auth.verify_platform_token(mint()) is not None      # the baseline is accepted...
    assert inbox_auth.verify_platform_token(make()) is None          # ...this variant is not


@pytest.mark.parametrize("claims", [{"kid": "cid_ffffffffffffffff"}, {"kid": ...}, {"provision": True}])
def test_a_fresh_token_is_refused_for_its_kid_or_provision_alone(conf, claims):
    """Fresh, correctly signed, from a super admin: only the named claim is wrong."""
    token = mint(**claims)
    assert inbox_auth.verify_platform_token(token) is None


def test_a_signed_body_that_is_not_an_object_is_refused(conf):
    import hashlib
    import hmac
    body = b64u(b'["u","sadmin"]')
    sig = b64u(hmac.new(KEY.encode(), body.encode(), hashlib.sha256).digest())
    assert inbox_auth.verify_platform_token(f"{body}.{sig}") is None


@pytest.mark.parametrize("claims", [
    {"su": False}, {"su": "true"}, {"su": 1}, {"su": ...},
    {"uid": ...}, {"uid": None}, {"uid": "sadmin"}, {"uid": 12},
])
def test_only_a_platform_admin_with_a_user_id_gets_in(conf, claims):
    verified = inbox_auth.verify_platform_token(mint(**claims))
    assert verified is not None                         # a valid token...
    assert inbox_auth.admin_identity(verified) is None  # ...but not an inbox admin


def test_a_customer_group_admin_with_an_app_grant_is_refused(conf):
    # what a customer owner gets after granting the app to themselves
    token = mint(su=False, ga=True, r="admin", o="11111111-2222-4333-8444-555555555555")
    assert inbox_auth.admin_identity(inbox_auth.verify_platform_token(token)) is None


def test_nothing_is_accepted_without_the_settings(env):
    token = mint()
    configure(env)
    assert inbox_auth.configured() and inbox_auth.verify_platform_token(token)
    for name in ("INBOX_SIGNING_KEY", "INBOX_CLIENT_ID", "INBOX_SESSION_KEY"):
        configure(env)
        env.delenv(name)
        assert not inbox_auth.configured()
    configure(env)
    env.setenv("INBOX_SIGNING_KEY", "not-a-key")
    assert not inbox_auth.configured() and inbox_auth.verify_platform_token(token) is None


def test_settings_pasted_with_a_trailing_newline_still_work(env):
    configure(env)
    env.setenv("INBOX_SIGNING_KEY", KEY + "\r\n")
    env.setenv("INBOX_CLIENT_ID", " " + CLIENT + "\n")
    env.setenv("INBOX_SESSION_KEY", SESSION_KEY.upper() + "\n")
    assert inbox_auth.configured()
    assert inbox_auth.verify_platform_token(mint()) is not None


# --- the inbox's own session -------------------------------------------------------------------
def test_a_session_round_trips_and_lasts_two_hours(conf):
    now = int(time.time())
    token, exp = inbox_auth.issue_session(ADMIN_UID, "sadmin", PLATFORM_ORG, now=now)
    assert exp == now + 2 * 3600
    claims = inbox_auth.verify_session(token, now=now + 2 * 3600)
    assert (claims["uid"], claims["u"], claims["o"]) == (ADMIN_UID, "sadmin", PLATFORM_ORG)
    assert inbox_auth.verify_session(token, now=now + 2 * 3600 + 1) is None


def test_sessions_and_platform_tokens_never_pass_for_each_other(conf):
    session, _ = inbox_auth.issue_session(ADMIN_UID, "sadmin", PLATFORM_ORG)
    assert inbox_auth.verify_platform_token(session) is None
    assert inbox_auth.verify_session(mint()) is None


def test_the_platform_key_alone_cannot_make_a_session(conf):
    """Holding the signing key (which the platform also stores) is not enough."""
    import hashlib
    import hmac
    import json
    body = b64u(json.dumps({"uid": ADMIN_UID, "u": "x", "o": None, "iat": int(time.time()),
                            "exp": int(time.time()) + 60, "sid": "a"}).encode())
    signed = "yw1." + body
    for key in (KEY.encode(), hmac.new(KEY.encode(), b"yantrai-web inbox session v1", hashlib.sha256).digest()):
        forged = signed + "." + b64u(hmac.new(key, signed.encode(), hashlib.sha256).digest())
        assert inbox_auth.verify_session(forged) is None


def test_a_new_session_key_signs_everyone_out(conf):
    token, _ = inbox_auth.issue_session(ADMIN_UID, "sadmin", None)
    assert inbox_auth.verify_session(token)
    conf.setenv("INBOX_SESSION_KEY", "7a" * 32)
    assert inbox_auth.verify_session(token) is None


@pytest.mark.parametrize("mangle", [
    lambda t: t + "x", lambda t: t.replace("yw1.", "yw2.", 1), lambda t: t.rsplit(".", 1)[0],
    lambda t: t.split(".")[0] + "." + b64u(b'{"uid":"x"}') + "." + t.split(".")[2],
])
def test_a_tampered_session_is_refused(conf, mangle):
    token, _ = inbox_auth.issue_session(ADMIN_UID, "sadmin", None)
    assert inbox_auth.verify_session(mangle(token)) is None


def test_a_session_claiming_a_longer_life_is_refused(conf):
    import hashlib
    import hmac
    import json
    now = int(time.time())
    body = b64u(json.dumps({"uid": ADMIN_UID, "u": "x", "o": None, "iat": now,
                            "exp": now + 3 * 3600, "sid": "a"}).encode())
    signed = "yw1." + body
    forged = signed + "." + b64u(hmac.new(bytes.fromhex(SESSION_KEY), signed.encode(),
                                          hashlib.sha256).digest())
    assert inbox_auth.verify_session(forged) is None


def test_platform_origin_is_validated(env):
    assert inbox_auth.platform_origin() == "https://workspace.yantrailabs.com"
    env.setenv("INBOX_PLATFORM_ORIGIN", "https://workspace.yantrailabs.com/")
    assert inbox_auth.platform_origin() == "https://workspace.yantrailabs.com"
    env.setenv("INBOX_PLATFORM_ORIGIN", "http://localhost:8000")
    assert inbox_auth.platform_origin() == "http://localhost:8000"
    for bad in ("javascript:alert(1)", "https://a.com https://b.com", "http://evil.example",
                "https://evil.example/path", "https://x.com;", "*"):
        env.setenv("INBOX_PLATFORM_ORIGIN", bad)
        assert inbox_auth.platform_origin() == "https://workspace.yantrailabs.com", bad
