"""The inbox's page and API through Flask, without a database: what is served,
with which headers, and what is refused before any database is touched."""
import json
import os
import re
import time

import pytest

import inbox
import inbox_auth
import main
from inboxkit import ADMIN_UID, configure, mint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(env, fresh_intake):
    main.app.config["TESTING"] = True
    return main.app.test_client()


@pytest.fixture
def conf(env):
    configure(env)
    return env


def session_header(uid=ADMIN_UID, name="sadmin"):
    token, _ = inbox_auth.issue_session(uid, name, None)
    return {"Authorization": "Bearer " + token}


ADMIN_PATHS = [("GET", "/admin"), ("GET", "/admin/inbox.js"), ("GET", "/admin/inbox.css"),
               ("POST", "/admin/api/session"), ("POST", "/admin/api/leads/search"),
               ("GET", "/admin/api/leads/3f2b1c9e-8d7a-4b6c-9e0f-112233445566"),
               ("POST", "/admin/api/leads/3f2b1c9e-8d7a-4b6c-9e0f-112233445566/status"),
               ("POST", "/admin/api/export")]


# --- nothing until it is fully configured ----------------------------------------------------------
@pytest.mark.parametrize("missing", [None, "INBOX_SIGNING_KEY", "INBOX_CLIENT_ID", "INBOX_SESSION_KEY",
                                     "INBOX_DB_URL"])
def test_every_admin_path_is_404_until_fully_configured(client, env, missing):
    if missing:
        configure(env)
        env.delenv(missing)
    for method, path in ADMIN_PATHS:
        r = client.open(path, method=method, json={} if method == "POST" else None)
        assert r.status_code == 404, (method, path)
    assert client.get("/_status").get_json()["inbox"] is False


def test_status_reports_the_inbox_configured(client, conf):
    assert client.get("/_status").get_json()["inbox"] is True


# --- the page ------------------------------------------------------------------------------------------
def test_the_page_may_be_framed_only_by_the_platform(client, conf):
    r = client.get("/admin?token=abc", headers={"Accept-Language": "fr"})     # no locale redirect
    assert r.status_code == 200 and r.mimetype == "text/html"
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors https://workspace.yantrailabs.com" in csp
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "default-src 'none'" in csp
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "noindex" in r.headers["X-Robots-Tag"]
    assert "X-Frame-Options" not in r.headers
    assert "Accept-Language" not in r.headers.get("Vary", "")
    html = r.get_data(as_text=True)
    assert '<meta name="yw-platform" content="https://workspace.yantrailabs.com">' in html
    assert "__PLATFORM_URL__" not in html


def test_the_platform_origin_setting_reaches_the_frame_rule_and_the_page(client, conf):
    conf.setenv("INBOX_PLATFORM_ORIGIN", "http://localhost:8000")
    r = client.get("/admin")
    assert r.headers["Content-Security-Policy"].endswith("frame-ancestors http://localhost:8000")
    assert 'content="http://localhost:8000"' in r.get_data(as_text=True)


def test_script_and_style_are_served_with_the_same_rules(client, conf):
    js = client.get("/admin/inbox.js")
    css = client.get("/admin/inbox.css")
    assert js.status_code == 200 and js.mimetype == "text/javascript"
    assert css.status_code == 200 and css.mimetype == "text/css"
    for r in (js, css):
        assert r.headers["Cache-Control"] == "no-store" and "frame-ancestors" in r.headers["Content-Security-Policy"]


def test_the_page_never_parses_lead_text_as_html_or_talks_to_the_platform():
    """Visitors write the leads: the page only ever sets text, runs no inline
    script (the CSP would block it anyway) and sends the platform no messages
    (the platform acts on some with the viewing admin's rights)."""
    js = open(os.path.join(ROOT, "admin", "inbox.js"), encoding="utf-8").read()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function", "postMessage", "setTimeout('", 'setTimeout("'):
        assert sink not in js, sink
    page = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
    assert re.findall(r"<script(?![^>]*\bsrc=)", page) == []
    assert not re.search(r"<style\b|\sstyle\s*=|\son[a-z]+\s*=", page)


def test_the_page_keeps_search_terms_out_of_urls():
    js = open(os.path.join(ROOT, "admin", "inbox.js"), encoding="utf-8").read()
    assert "'/admin/api/leads/search'" in js and "'/admin/api/export'" in js
    assert "URLSearchParams(" in js and js.count("URLSearchParams(") == 1     # only to read ?token=


# --- sign-in ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make, code", [
    (lambda: {"token": mint(key="sk_" + "f" * 48)}, 401),
    (lambda: {"token": mint(exp=int(time.time()) - 5)}, 401),
    (lambda: {"token": mint(provision=True)}, 401),
    (lambda: {"token": mint(kid="cid_ffffffffffffffff")}, 401),
    (lambda: {"token": mint(su=False)}, 403),
    (lambda: {"token": mint(uid=...)}, 403),
    (lambda: {"token": 12}, 401),
    (lambda: {}, 401),
])
def test_sign_in_is_refused_before_the_database(client, conf, capsys, make, code):
    body = make()
    r = client.post("/admin/api/session", json=body)
    assert r.status_code == code
    assert r.get_json()["ok"] is False and "token" not in r.get_json()
    out = capsys.readouterr().out
    assert "inbox_sign_in_refused" in out
    if isinstance(body.get("token"), str):
        assert body["token"] not in out


def test_sign_in_needs_the_database_to_record_it(client, conf):
    """The database at 127.0.0.1:1 is unreachable: no record, so no session."""
    r = client.post("/admin/api/session", json={"token": mint()})
    assert r.status_code == 503 and "token" not in r.get_json()


def test_a_body_that_is_not_json_is_a_400(client, conf):
    r = client.post("/admin/api/session", data="token=x", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 400 and r.get_json()["ok"] is False


# --- every data route needs a session --------------------------------------------------------------------
@pytest.mark.parametrize("method, path", [p for p in ADMIN_PATHS if "/api/" in p[1] and "session" not in p[1]])
@pytest.mark.parametrize("auth", [None, "Bearer ", "Bearer nonsense", "Basic abc", "platform", "expired"])
def test_data_routes_refuse_without_a_live_session(client, conf, method, path, auth):
    headers = {}
    if auth == "platform":
        headers["Authorization"] = "Bearer " + mint()           # a platform token is not a session
    elif auth == "expired":
        token, _ = inbox_auth.issue_session(ADMIN_UID, "sadmin", None, now=int(time.time()) - 3 * 3600)
        headers["Authorization"] = "Bearer " + token
    elif auth:
        headers["Authorization"] = auth
    r = client.open(path, method=method, headers=headers, json={} if method == "POST" else None)
    assert r.status_code == 401 and r.get_json()["ok"] is False


@pytest.mark.parametrize("body", [
    {"status": "archived"}, {"form": "newsletter"}, {"q": "x" * 101}, {"q": 5},
    {"before": "not-a-cursor"}, {"before": "W10"},
])
def test_bad_filters_are_a_400_before_the_database(client, conf, body):
    r = client.post("/admin/api/leads/search", json=body, headers=session_header())
    assert r.status_code == 400 and r.get_json()["ok"] is False


@pytest.mark.parametrize("body", [{}, {"status": "archived"}, {"status": "new", "note": "n" * 2001},
                                  {"status": "new", "expected": "gone"}])
def test_bad_status_changes_are_a_400(client, conf, body):
    r = client.post("/admin/api/leads/3f2b1c9e-8d7a-4b6c-9e0f-112233445566/status", json=body,
                    headers=session_header())
    assert r.status_code == 400


def test_database_trouble_is_a_503_not_a_crash(client, conf, capsys):
    r = client.post("/admin/api/leads/search", json={}, headers=session_header())
    assert r.status_code == 503 and r.get_json()["ok"] is False
    line = [json.loads(x) for x in capsys.readouterr().out.splitlines() if "inbox_unavailable" in x][0]
    assert line["severity"] == "ERROR" and "x@" not in json.dumps(line)


# --- errors under /admin/api are JSON; the rest of the site is unchanged -----------------------------------------
@pytest.mark.parametrize("method, path, code", [
    ("GET", "/admin/api/nothing-here", 404),
    ("GET", "/admin/api/leads/not-a-uuid", 404),
    ("GET", "/admin/api/session", 404),          # GETs fall to the site's catch-all
    ("DELETE", "/admin/api/leads/3f2b1c9e-8d7a-4b6c-9e0f-112233445566", 405),
])
def test_api_errors_are_json(client, conf, method, path, code):
    r = client.open(path, method=method)
    assert r.status_code == code and r.is_json and r.get_json()["ok"] is False
    assert r.headers["Cache-Control"] == "no-store"


def test_an_oversized_body_is_refused(client, conf):
    r = client.post("/admin/api/leads/search", data=json.dumps({"q": "x" * 20000}),
                    content_type="application/json", headers=session_header())
    assert r.status_code == 413 and r.get_json()["ok"] is False


def test_the_rest_of_the_site_keeps_its_own_pages(client, conf):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and not r.is_json
    assert "Content-Security-Policy" not in r.headers
    home = client.get("/")
    assert home.status_code == 200 and "Content-Security-Policy" not in home.headers
    assert client.get("/administrator").status_code == 404        # not taken for /admin


# --- the export -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value, shown", [
    ("=HYPERLINK(\"http://x\",\"y\")", "'=HYPERLINK(\"http://x\",\"y\")"),
    ("+1-555", "'+1-555"), ("-2+3", "'-2+3"), ("@SUM(A1)", "'@SUM(A1)"),
    ("\tcmd", "'\tcmd"), ("\r=1", "'\r=1"), ("  =1+1", "'  =1+1"),
    ("Acme = best", "Acme = best"), ("asha@example.com", "asha@example.com"), (None, ""), (12, "12"),
])
def test_export_cells_cannot_run_as_formulas(value, shown):
    assert inbox.csv_cell(value) == shown
