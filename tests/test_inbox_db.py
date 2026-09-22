"""db/002_inbox.sql, the inbox API and the registration tool against a real,
throwaway, LOCAL Postgres (tests/pgsim.py). Skipped unless INTAKE_TEST_PG_ADMIN
and INTAKE_TEST_PSQL are set. Never point it at the platform database."""
import csv
import io
import json
import os
import secrets
import time
import uuid

import pytest

import inbox
import inbox_auth
import intake
import main
from inboxkit import ADMIN_UID, CLIENT, KEY, PLATFORM_ORG, configure, mint
from pgsim import ROOT, SKIP, _connect, a_row, pg, sqlstate  # noqa: F401  (pg is a fixture)

pytestmark = SKIP
SQL_VERIFY = os.path.join(ROOT, "db", "verify_inbox.sql")
SQL_VERIFY_INTAKE = os.path.join(ROOT, "db", "verify_intake.sql")
N_CHECKS = 21
OTHER_UID = "0b8e3f7a-5c2d-4e1f-9a0b-c1d2e3f4a5b6"


def verify(conn, path=SQL_VERIFY):
    sql = open(path, encoding="utf-8").read().split("-- Information only")[0]
    return {name: ok for name, ok in conn.run(sql)}


def all_ok(checks):
    return [name for name, ok in checks.items() if not ok]


@pytest.fixture
def client(pg, env, fresh_intake):
    configure(env, db_url=pg["inbox_dsn"])
    main.app.config["TESTING"] = True
    return main.app.test_client()


def store(pg, **fields):
    """A lead as the website stores it (as website_app)."""
    row = a_row(**fields)
    con = pg["app"]()
    try:
        con.run("INSERT INTO website_intake.submissions (" + ", ".join(intake.COLUMNS) + ") VALUES ("
                + ", ".join(":" + c for c in intake.COLUMNS) + ")", **row)
    finally:
        con.close()
    return row


def sign_in(client, **claims):
    r = client.post("/admin/api/session", json={"token": mint(**claims)})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["token"]}


def events(pg, **where):
    sql = "SELECT action, CAST(actor_uid AS text), actor, CAST(submission_id AS text), from_status, to_status, " \
          "note, detail, token_sha256 FROM website_intake.inbox_events"
    if where:
        sql += " WHERE " + " AND ".join(f"CAST({k} AS text) = :{k}" for k in where)
    return pg["sim"].run(sql + " ORDER BY at, id", **where)


# --- the migration and its proof --------------------------------------------------------------------
def test_verify_inbox_passes_through_psql(pg):
    out = pg["psql"]("-At", "-F", "|", "-f", SQL_VERIFY)
    assert out.returncode == 0, out.stderr
    checks = [line.rsplit("|", 1) for line in out.stdout.splitlines() if "|" in line]
    assert len(checks) == N_CHECKS and len(verify(pg["sim"])) == N_CHECKS
    assert all(ok == "t" for _, ok in checks), [name for name, ok in checks if ok != "t"]


def test_002_leaves_001s_proof_intact(pg):
    assert all_ok(verify(pg["sim"], SQL_VERIFY_INTAKE)) == []


@pytest.mark.parametrize("mistake, check", [
    ("ALTER ROLE website_admin CONNECTION LIMIT 50", "website_admin connection limit is 5"),
    ("GRANT website_app TO website_admin", "website_admin is a member of no other role"),
    ("ALTER ROLE website_admin SET work_mem = '1GB'",
     "website_admin stores exactly the three 002 settings, and none per database"),
    ("ALTER ROLE website_admin IN DATABASE {db} SET statement_timeout = 0",
     "website_admin stores exactly the three 002 settings, and none per database"),
    ("GRANT UPDATE (status) ON website_intake.submissions TO website_admin",
     "website_admin cannot add, change or delete leads or mail records"),
    ("GRANT DELETE ON website_intake.notify_events TO website_admin",
     "website_admin cannot add, change or delete leads or mail records"),
    ("GRANT INSERT (id, form, name, email) ON website_intake.submissions TO website_admin",
     "website_admin cannot add, change or delete leads or mail records"),
    ("GRANT DELETE ON website_intake.inbox_events TO website_admin",
     "website_admin may only append to the inbox log, never edit it"),
    ("GRANT UPDATE (note) ON website_intake.inbox_events TO website_admin",
     "website_admin may only append to the inbox log, never edit it"),
    ("GRANT INSERT (to_status) ON website_intake.inbox_events TO website_admin",
     "website_admin cannot set a log entry's id, time or status change directly"),
    ("GRANT INSERT (at) ON website_intake.inbox_events TO website_admin",
     "website_admin cannot set a log entry's id, time or status change directly"),
    ("GRANT SELECT ON public.leaky TO website_admin", "website_admin has no rights on any public table"),
    ("GRANT CREATE ON SCHEMA website_intake TO website_admin", "website_admin cannot create objects in any schema"),
    ("CREATE FUNCTION public.leak() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
     "the only owner-rights function website_admin may call is set_status"),
    ("GRANT EXECUTE ON FUNCTION website_intake._throttle() TO website_admin",
     "the only owner-rights function website_admin may call is set_status"),
    ("GRANT EXECUTE ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid) TO PUBLIC",
     "only website_admin (and the owner) may call set_status"),
    ("ALTER FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid) RESET search_path",
     "set_status exists once and runs as its owner with a pinned search_path"),
    ("ALTER TABLE website_intake.inbox_events DISABLE ROW LEVEL SECURITY",
     "row-level security is on for the inbox log"),
    ("ALTER POLICY inbox_log ON website_intake.inbox_events WITH CHECK (true)",
     "website_admin's policies: read the three tables; log only sign-ins, views and exports"),
    ("CREATE POLICY sneaky ON website_intake.submissions FOR UPDATE TO website_admin USING (true)",
     "website_admin's policies: read the three tables; log only sign-ins, views and exports"),
    ("DROP INDEX website_intake.inbox_events_token_once", "a platform sign-in token can be used once"),
    ("GRANT SELECT ON website_intake.inbox_events TO anon",
     "PUBLIC, anon, authenticated and service_role hold nothing on the inbox log"),
    ("GRANT SELECT ON website_intake.inbox_events TO PUBLIC",
     "PUBLIC, anon, authenticated and service_role hold nothing on the inbox log"),
    ("GRANT INSERT (action, actor_uid) ON website_intake.inbox_events TO website_app",
     "website_app has nothing on the inbox log and cannot call set_status"),
    ("GRANT EXECUTE ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid) TO website_app",
     "website_app has nothing on the inbox log and cannot call set_status"),
])
def test_verify_inbox_catches_each_mistake(pg, mistake, check):
    sim = pg["sim"]
    sim.run("BEGIN")
    try:
        sim.run(mistake.format(db=pg["db"]))
        assert verify(sim)[check] is False
    finally:
        sim.run("ROLLBACK")
    assert verify(sim)[check] is True


def test_a_policy_for_website_admin_does_not_break_001s_check(pg):
    """verify_intake check 22 now looks only at website_app's policies."""
    checks = verify(pg["sim"], SQL_VERIFY_INTAKE)
    assert checks["website_app's only policies are the two insert ones, and none applies to everyone"] is True
    sim = pg["sim"]
    sim.run("BEGIN")
    try:
        sim.run("CREATE POLICY open ON website_intake.submissions FOR SELECT USING (true)")   # TO PUBLIC
        assert verify(sim, SQL_VERIFY_INTAKE)[
            "website_app's only policies are the two insert ones, and none applies to everyone"] is False
    finally:
        sim.run("ROLLBACK")


def test_rerunning_002_takes_back_a_grant_and_a_setting(pg):
    sim = pg["sim"]
    sim.run("GRANT UPDATE (status) ON website_intake.submissions TO website_admin")
    sim.run("ALTER ROLE website_admin SET statement_timeout = 0")
    assert all_ok(verify(sim))
    done = pg["psql"]("-q", "-f", pg["sql_002"])
    assert done.returncode == 0, done.stderr
    assert all_ok(verify(sim)) == []


def test_002_refuses_to_run_before_001(pg):
    admin = pg["admin"]()
    name = "inbox_empty_" + secrets.token_hex(3)
    try:
        admin.run(f"CREATE DATABASE {name} OWNER postgres")
        import subprocess
        env = dict(os.environ, PGPASSWORD=pg["owner_url"].split(":")[2].split("@")[0])
        done = subprocess.run([os.getenv("INTAKE_TEST_PSQL"), "-h", pg["host"], "-p", str(pg["port"]),
                               "-U", "postgres", "-d", name, "-v", "ON_ERROR_STOP=1", "-q", "-f", pg["sql_002"]],
                              env=env, capture_output=True, text=True, encoding="utf-8")
        assert done.returncode != 0 and "apply db/001_intake.sql first" in done.stderr
    finally:
        admin.run(f"DROP DATABASE IF EXISTS {name}")
        admin.close()


# --- what website_admin can and cannot do --------------------------------------------------------------
def test_website_admin_reads_leads(pg):
    row = store(pg)
    con = pg["inbox"]()
    try:
        assert con.run("SELECT name FROM website_intake.submissions WHERE id = CAST(:id AS uuid)",
                       id=row["id"]) == [["Asha Rao"]]
        assert con.run("SHOW statement_timeout") == [["5s"]]
    finally:
        con.close()


@pytest.mark.parametrize("sql", [
    "UPDATE website_intake.submissions SET status = 'spam'",
    "UPDATE website_intake.submissions SET name = 'x'",
    "DELETE FROM website_intake.submissions",
    "TRUNCATE website_intake.submissions",
    "INSERT INTO website_intake.submissions (id, form, name, email, company) "
    "VALUES (gen_random_uuid(), 'savings_check', 'x', 'x@y.co', 'c')",
    "DELETE FROM website_intake.inbox_events",
    "UPDATE website_intake.inbox_events SET actor = 'someone else'",
    "INSERT INTO website_intake.inbox_events (action, actor_uid, submission_id, to_status) "
    "VALUES ('set_status', gen_random_uuid(), gen_random_uuid(), 'spam')",
    "INSERT INTO website_intake.inbox_events (action, actor_uid, at, token_sha256) "
    "VALUES ('sign_in', gen_random_uuid(), now() - interval '1 year', repeat('a', 64))",
    "SELECT * FROM public.leaky",
    "SELECT website_intake._throttle()",
])
def test_website_admin_is_refused(pg, sql):
    from pg8000.exceptions import DatabaseError
    con = pg["inbox"]()
    try:
        with pytest.raises(DatabaseError) as info:
            con.run(sql)
        assert sqlstate(info.value) == "42501"
    finally:
        con.close()


def test_website_admin_cannot_log_a_status_change_itself(pg):
    """Only set_status writes 'set_status' entries (the row-level rule refuses it
    even through the columns website_admin may fill)."""
    from pg8000.exceptions import DatabaseError
    con = pg["inbox"]()
    try:
        with pytest.raises(DatabaseError) as info:
            con.run("INSERT INTO website_intake.inbox_events (action, actor_uid, submission_id) "
                    "VALUES ('set_status', gen_random_uuid(), gen_random_uuid())")
        assert sqlstate(info.value) == "42501"
    finally:
        con.close()


def test_website_app_cannot_touch_the_inbox(pg):
    from pg8000.exceptions import DatabaseError
    con = pg["app"]()
    try:
        for sql in ("SELECT * FROM website_intake.inbox_events",
                    "SELECT * FROM website_intake.set_status(gen_random_uuid(), 'spam', NULL, NULL, "
                    "gen_random_uuid(), 'x', NULL)"):
            with pytest.raises(DatabaseError) as info:
                con.run(sql)
            assert sqlstate(info.value) == "42501"
    finally:
        con.close()


def call_set_status(pg, lead_id, status, note=None, expected=None, uid=ADMIN_UID, actor="sadmin"):
    con = pg["inbox"]()
    try:
        return con.run("SELECT new_status, new_note, changed_at FROM website_intake.set_status("
                       "CAST(:id AS uuid), CAST(:s AS text), CAST(:n AS text), CAST(:e AS text), "
                       "CAST(:u AS uuid), CAST(:a AS text), CAST(:o AS uuid))",
                       id=lead_id, s=status, n=note, e=expected, u=uid, a=actor, o=PLATFORM_ORG)
    finally:
        con.close()


def test_set_status_changes_the_lead_and_logs_it_in_one_go(pg):
    row = store(pg)
    (new, note, at), = call_set_status(pg, row["id"], "contacted", note="  Called; demo on Friday  ",
                                       expected="new")
    assert (new, note) == ("contacted", "Called; demo on Friday")
    (status, snote, triaged_at, by_uid, by), = pg["sim"].run(
        "SELECT status, status_note, triaged_at, CAST(triaged_by_uid AS text), triaged_by "
        "FROM website_intake.submissions WHERE id = CAST(:id AS uuid)", id=row["id"])
    assert (status, snote, triaged_at, by_uid, by) == ("contacted", "Called; demo on Friday", at, ADMIN_UID, "sadmin")
    assert events(pg, submission_id=row["id"]) == [
        ["set_status", ADMIN_UID, "sadmin", row["id"], "new", "contacted", "Called; demo on Friday", None, None]]


@pytest.mark.parametrize("args, code", [
    ({"status": "qualified", "expected": "contacted"}, "WI409"),     # someone changed it meanwhile
    ({"status": "archived"}, "22023"),
    ({"status": None}, "22023"),
    ({"status": "spam", "expected": "gone"}, "22023"),
    ({"status": "spam", "note": "n" * 2001}, "22001"),
    ({"status": "spam", "uid": None}, "22023"),
])
def test_set_status_refuses(pg, args, code):
    from pg8000.exceptions import DatabaseError
    row = store(pg)
    with pytest.raises(DatabaseError) as info:
        call_set_status(pg, row["id"], **args)
    assert sqlstate(info.value) == code
    assert events(pg, submission_id=row["id"]) == []
    assert pg["sim"].run("SELECT status FROM website_intake.submissions WHERE id = CAST(:id AS uuid)",
                         id=row["id"]) == [["new"]]


def test_set_status_on_an_unknown_lead_is_wi404(pg):
    from pg8000.exceptions import DatabaseError
    with pytest.raises(DatabaseError) as info:
        call_set_status(pg, str(uuid.uuid4()), "spam")
    assert sqlstate(info.value) == "WI404"


def test_a_sign_in_token_is_recorded_once(pg):
    from pg8000.exceptions import DatabaseError
    con = pg["inbox"]()
    digest = secrets.token_hex(32)
    try:
        sql = ("INSERT INTO website_intake.inbox_events (action, actor_uid, token_sha256) "
               "VALUES ('sign_in', CAST(:u AS uuid), :t)")
        con.run(sql, u=ADMIN_UID, t=digest)
        with pytest.raises(DatabaseError) as info:
            con.run(sql, u=OTHER_UID, t=digest)
        assert sqlstate(info.value) == "23505"
    finally:
        con.close()


# --- the API end to end ---------------------------------------------------------------------------------
def test_sign_in_is_recorded_and_a_token_works_once(client, pg, capsys):
    token = mint()
    r = client.post("/admin/api/session", json={"token": token})
    assert r.status_code == 200
    body = r.get_json()
    assert body["user"] == "sadmin" and inbox_auth.verify_session(body["token"])["uid"] == ADMIN_UID
    import hashlib
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert [e[0] for e in events(pg, token_sha256=digest)] == ["sign_in"]
    again = client.post("/admin/api/session", json={"token": token})
    assert again.status_code == 401 and "already used" in again.get_json()["error"]
    out = capsys.readouterr().out
    assert "inbox_token_replay" in out and token not in out and body["token"] not in out


def test_a_customer_admins_token_records_nothing(client, pg):
    before = len(events(pg))
    r = client.post("/admin/api/session", json={"token": mint(su=False, r="admin", ga=True)})
    assert r.status_code == 403
    assert len(events(pg)) == before


def test_the_list_pages_searches_and_filters(client, pg):
    marker = secrets.token_hex(3)
    rows = [store(pg, name=f"P{marker} {i}", email=f"p{i}-{marker}@example.com") for i in range(3)]
    career = store(pg, form="careers", name=f"C{marker}", email=f"c-{marker}@example.com")
    odd = store(pg, name=f"50%_off!{marker}", email=f"odd-{marker}@example.com")
    # five rows received in the very same instant: paging must neither skip nor repeat one
    same = pg["sim"].run("SELECT now()")[0][0]
    for r in rows + [career, odd]:
        pg["sim"].run("UPDATE website_intake.submissions SET received_at = :t WHERE id = CAST(:id AS uuid)",
                      t=same, id=r["id"])
    headers = sign_in(client)
    seen, before = [], None
    old_page = inbox.PAGE_SIZE
    inbox.PAGE_SIZE = 2
    try:
        while True:
            r = client.post("/admin/api/leads/search", json={"q": marker, "before": before}, headers=headers)
            assert r.status_code == 200, r.get_json()
            data = r.get_json()
            seen += [lead["id"] for lead in data["leads"]]
            before = data["next"]
            if not before:
                break
    finally:
        inbox.PAGE_SIZE = old_page
    assert sorted(seen) == sorted(r["id"] for r in rows + [career, odd]) and len(seen) == len(set(seen))
    only = client.post("/admin/api/leads/search", json={"q": marker, "form": "careers"}, headers=headers)
    assert [lead["id"] for lead in only.get_json()["leads"]] == [career["id"]]
    # % and _ are searched for literally
    lit = client.post("/admin/api/leads/search", json={"q": "50%_off!"}, headers=headers).get_json()["leads"]
    assert [lead["id"] for lead in lit] == [odd["id"]]
    assert client.post("/admin/api/leads/search", json={"q": "5_%"}, headers=headers).get_json()["leads"] == []
    counts = only.get_json()["counts"]
    assert set(counts) == {"new", "week", "qualified", "all"} and counts["all"] >= 5
    first = data["leads"][0] if data["leads"] else None
    if first:
        assert "T" in first["received_at"] and first["received_at"].endswith("+00:00")


def test_opening_a_lead_logs_it_first(client, pg):
    row = store(pg, note="Please call after 5pm", role="CFO", erp="SAP", outflow="$50M+", page="/fr/",
                locale="fr", ip_hash="c" * 64, user_agent="UA")
    headers = sign_in(client)
    r = client.get(f"/admin/api/leads/{row['id']}", headers=headers)
    assert r.status_code == 200
    lead = r.get_json()["lead"]
    assert (lead["id"], lead["note"], lead["role"], lead["status"]) == (row["id"], "Please call after 5pm", "CFO", "new")
    assert "ip_hash" not in lead and "user_agent" not in lead
    assert [e[0] for e in events(pg, submission_id=row["id"])] == ["view_lead"]
    missing = client.get(f"/admin/api/leads/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404


def test_no_lead_is_shown_or_exported_when_the_log_cannot_be_written(client, pg):
    row = store(pg)
    headers = sign_in(client)
    sim = pg["sim"]
    sim.run("REVOKE INSERT ON website_intake.inbox_events FROM website_admin")
    try:
        r = client.get(f"/admin/api/leads/{row['id']}", headers=headers)
        assert r.status_code == 503 and "lead" not in r.get_json()
        x = client.post("/admin/api/export", json={}, headers=headers)
        assert x.status_code == 503 and x.mimetype == "application/json"
    finally:
        done = pg["psql"]("-q", "-f", pg["sql_002"])
        assert done.returncode == 0, done.stderr
    assert events(pg, submission_id=row["id"]) == []


def test_changing_a_status_through_the_api(client, pg, capsys):
    row = store(pg)
    headers = sign_in(client, u="asha.admin")
    url = f"/admin/api/leads/{row['id']}/status"
    r = client.post(url, json={"status": "qualified", "note": "Good fit", "expected": "new"}, headers=headers)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "qualified" and r.get_json()["counts"]["qualified"] >= 1
    stale = client.post(url, json={"status": "spam", "expected": "new"}, headers=headers)
    assert stale.status_code == 409
    detail = client.get(f"/admin/api/leads/{row['id']}", headers=headers).get_json()
    assert detail["lead"]["status"] == "qualified" and detail["lead"]["triaged_by"] == "asha.admin"
    assert detail["history"][0]["from"] == "new" and detail["history"][0]["to"] == "qualified"
    assert detail["history"][0]["actor"] == "asha.admin" and detail["history"][0]["note"] == "Good fit"
    gone = client.post(f"/admin/api/leads/{uuid.uuid4()}/status", json={"status": "spam"}, headers=headers)
    assert gone.status_code == 404
    assert "Good fit" not in capsys.readouterr().out


def test_the_export_is_a_safe_csv_and_is_logged(client, pg):
    marker = secrets.token_hex(3)
    evil = store(pg, name=f"=HYPERLINK(\"http://evil\",\"{marker}\")", company="+cmd|' /C calc'!A0",
                 note="line one\nline two, with \"quotes\"", email=f"x-{marker}@example.com")
    headers = sign_in(client)
    r = client.post("/admin/api/export", json={"q": marker}, headers=headers)
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.headers["Content-Disposition"] == 'attachment; filename="yantrai-leads.csv"'
    assert r.headers["Cache-Control"] == "no-store"
    text = r.get_data(as_text=True)
    assert text.startswith("﻿")
    table = list(csv.reader(io.StringIO(text[1:])))
    header, body = table[0], table[1:]
    assert header == list(inbox.EXPORT_COLUMNS) and len(body) == 1
    got = dict(zip(header, body[0]))
    assert got["name"].startswith("'=") and got["company"].startswith("'+")
    assert got["note"] == "line one\nline two, with \"quotes\"" and got["id"] == evil["id"]
    assert got["form"] == "Demo request"
    logged = [e for e in events(pg) if e[0] == "export"][-1]
    assert logged[7] == {"rows": 1, "matching": 1, "status": None, "form": None, "searched": True}
    assert marker not in json.dumps(logged[7])


def test_an_export_past_the_cap_says_how_many_it_left_out(client, pg, monkeypatch):
    marker = secrets.token_hex(3)
    for i in range(3):
        store(pg, name=f"Cap {marker} {i}")
    headers = sign_in(client)
    monkeypatch.setattr(inbox, "EXPORT_MAX", 2)
    r = client.post("/admin/api/export", json={"q": marker}, headers=headers)
    assert r.status_code == 200 and r.get_data(as_text=True).count(f"Cap {marker}") == 2
    assert (r.headers["X-Export-Rows"], r.headers["X-Export-Matching"]) == ("2", "3")
    assert [e for e in events(pg) if e[0] == "export"][-1][7]["matching"] == 3
    monkeypatch.setattr(inbox, "EXPORT_MAX", 3)
    ok = client.post("/admin/api/export", json={"q": marker}, headers=headers)
    assert ok.get_data(as_text=True).count(f"Cap {marker}") == 3
    assert (ok.headers["X-Export-Rows"], ok.headers["X-Export-Matching"]) == ("3", "3")


def test_the_login_tool_sets_up_the_inbox_login_without_touching_the_forms(pg, env, monkeypatch, capsys):
    import scripts.set_website_login as tool
    known = "known-" + secrets.token_hex(8)
    monkeypatch.setattr(tool.secrets, "token_urlsafe", lambda n: known)
    env.setenv("PLATFORM_OWNER_DB_URL", pg["owner_url"])
    try:
        assert tool.main(["--role", "website_admin", "--no-secret"]) == 0
        out = capsys.readouterr().out
        assert "it can read leads" in out and known not in out
        _connect("website_admin", known, pg["db"], pg["host"], pg["port"]).close()
        _connect("website_app", pg["app_pw"], pg["db"], pg["host"], pg["port"]).close()   # the forms' login untouched
        # a website_admin that could change leads directly is caught
        pg["sim"].run("GRANT UPDATE (status) ON website_intake.submissions TO website_admin")
        assert tool.main(["--role", "website_admin", "--no-secret"]) == 1
        assert "changing a lead directly is allowed" in capsys.readouterr().out
    finally:
        done = pg["psql"]("-q", "-f", pg["sql_002"])
        assert done.returncode == 0, done.stderr
        pg["sim"].run(f"ALTER ROLE website_admin PASSWORD '{pg['inbox_pw']}'")


def test_the_inbox_uses_its_own_login(client, pg, env):
    """The forms' login is not enough for the inbox, and the inbox's is the one it uses."""
    env.setenv("INBOX_DB_URL", pg["dsn"])               # website_app's URL by mistake
    headers = {"Authorization": "Bearer " + inbox_auth.issue_session(ADMIN_UID, "sadmin", None)[0]}
    r = client.post("/admin/api/leads/search", json={}, headers=headers)
    assert r.status_code == 503


# --- the registration tool ------------------------------------------------------------------------------------
PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS public.store_agents (
  slug text PRIMARY KEY, name text NOT NULL, tagline text, description text, icon text, category text,
  status text DEFAULT 'published', publisher text DEFAULT 'first-party', token_policy jsonb DEFAULT '{}',
  manifest jsonb NOT NULL DEFAULT '{}', sort_order int DEFAULT 100, created_at timestamp DEFAULT now(),
  owner_org_id uuid, owner_user_id uuid, visibility text DEFAULT 'public', client_id text, signing_key text,
  kind text CHECK (kind IN ('remote', 'inapp')),
  stage text CHECK (stage IN ('dev', 'pilot', 'live', 'dormant', 'internal', 'retired')),
  remote_url text, CHECK ((kind = 'remote') = (remote_url IS NOT NULL)));
CREATE UNIQUE INDEX IF NOT EXISTS store_agents_client_id_uq ON public.store_agents (client_id)
  WHERE client_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.org_agent_installs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL,
  agent_slug text NOT NULL REFERENCES public.store_agents(slug), installed_by_user_id uuid,
  installed_at timestamp DEFAULT now(), enabled boolean DEFAULT true, settings jsonb DEFAULT '{}',
  UNIQUE (org_id, agent_slug));
CREATE TABLE IF NOT EXISTS public.memberships (user_id uuid UNIQUE, org_id uuid, role text);
CREATE TABLE IF NOT EXISTS public.accounting_users (username text PRIMARY KEY, role text, users_id uuid);
"""
CUSTOMER_ORG = "11111111-2222-4333-8444-555555555555"
RUN_URL = "https://yantrai-website-abc123-el.a.run.app"


class FakeGcloud:
    """gcloud for the tool: a service address and an in-memory Secret Manager.
    `fail` answers a command (its first three words) with an error; `fail_once`
    does so only the first time."""

    def __init__(self, url=RUN_URL, secrets_=None, fail=None):
        self.url, self.store, self.fail, self.calls = url, dict(secrets_ or {}), fail or {}, []
        self.fail_once, self.grants = {}, []

    def __call__(self, args, *cmd, data=None):
        import subprocess
        self.calls.append((cmd, data))
        key = " ".join(cmd[:3])
        if key in self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", self.fail[key])
        if key in self.fail_once:
            return subprocess.CompletedProcess(cmd, 1, "", self.fail_once.pop(key))
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")        # noqa: E731
        missing = subprocess.CompletedProcess(cmd, 1, "", "ERROR: NOT_FOUND: Secret not found")
        if cmd[:3] == ("run", "services", "describe"):
            return ok(self.url + "\n")
        if cmd[:2] == ("secrets", "describe"):
            return ok(f"projects/9/secrets/{cmd[2]}\n") if cmd[2] in self.store else missing
        if cmd[:3] == ("secrets", "versions", "describe"):
            name = cmd[cmd.index("--secret") + 1]
            vs = self.store.get(name)
            return ok(f"projects/9/secrets/{name}/versions/{len(vs)}\n") if vs else missing
        if cmd[:3] == ("secrets", "versions", "access"):
            vs = self.store.get(cmd[cmd.index("--secret") + 1])
            return ok(vs[-1]) if vs else missing
        if cmd[:3] == ("secrets", "versions", "add"):
            self.store[cmd[3]].append(data)
            return ok(f"projects/9/secrets/{cmd[3]}/versions/{len(self.store[cmd[3]])}\n")
        if cmd[:2] == ("secrets", "create"):
            if cmd[2] in self.store:                            # as gcloud does
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: ALREADY_EXISTS: Secret already exists")
            self.store[cmd[2]] = [data]
            return ok()
        if cmd[:2] == ("secrets", "add-iam-policy-binding"):
            self.grants.append(cmd[2])
            return ok()
        raise AssertionError(cmd)


@pytest.fixture
def platform(pg, env, monkeypatch):
    """Stand-in platform tables in the throwaway database, reset for each test."""
    import scripts.register_inbox_app as tool
    sim = pg["sim"]
    sim.run("BEGIN")
    for stmt in [s for s in PLATFORM_SCHEMA.split(";") if s.strip()]:
        sim.run(stmt)
    sim.run("DELETE FROM public.org_agent_installs")
    sim.run("DELETE FROM public.store_agents")
    sim.run("DELETE FROM public.memberships")
    sim.run("DELETE FROM public.accounting_users")
    sim.run("COMMIT")
    admins = [("sadmin", "super_admin", ADMIN_UID), ("asha", "super_admin", OTHER_UID),
              ("customer_owner", "admin", "22222222-3333-4444-8555-666666666666")]
    for name, role, uid in admins:
        sim.run("INSERT INTO public.accounting_users VALUES (:n, :r, CAST(:u AS uuid))", n=name, r=role, u=uid)
    for uid, org in ((ADMIN_UID, PLATFORM_ORG), (OTHER_UID, PLATFORM_ORG),
                     ("22222222-3333-4444-8555-666666666666", CUSTOMER_ORG)):
        sim.run("INSERT INTO public.memberships VALUES (CAST(:u AS uuid), CAST(:o AS uuid), 'owner')", u=uid, o=org)
    gcloud = FakeGcloud(secrets_={"website-admin-db-url": ["postgresql://website_admin:pw@h/db"]})
    monkeypatch.setattr(tool, "_gcloud", gcloud)
    env.setenv("PLATFORM_OWNER_DB_URL", pg["owner_url"])
    return {"tool": tool, "gcloud": gcloud, "sim": sim}


def the_row(sim):
    rows = sim.run("SELECT client_id, signing_key, visibility, CAST(owner_org_id AS text), publisher, status, "
                   "kind, stage, remote_url, manifest, token_policy, icon, name FROM public.store_agents "
                   "WHERE slug = 'yantrai-web'")
    return dict(zip(("cid", "key", "visibility", "owner_org", "publisher", "status", "kind", "stage",
                     "remote_url", "manifest", "policy", "icon", "name"), rows[0])) if rows else None


def install(sim):
    return sim.run("SELECT CAST(org_id AS text), enabled FROM public.org_agent_installs WHERE agent_slug = 'yantrai-web'")


def test_registration_makes_a_locked_tile_that_starts_hidden(platform, capsys):
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    assert tool.main([]) == 0
    out = capsys.readouterr().out
    row = the_row(sim)
    assert row["visibility"] == "sadmin" and row["owner_org"] is None and row["publisher"] == "first-party"
    assert (row["kind"], row["stage"], row["remote_url"]) == ("remote", "internal", RUN_URL + "/admin")
    assert row["status"] == "published" and row["policy"] == {"chargeable": False} and row["name"] == "YantrAI Web"
    m = row["manifest"]
    assert m["agent_kind"] == "remote" and m["remote_url"] == RUN_URL + "/admin" and m["client_id"] == row["cid"]
    assert m["nav_groups"][0]["items"][0]["view"] == "remote-yantrai-web"
    assert install(sim) == [[PLATFORM_ORG, False]]                           # hidden until --enable
    assert gcloud.store["website-inbox-key"] == [row["key"]]
    session_key = gcloud.store["website-inbox-session-key"][0]
    assert len(session_key) == 64 and int(session_key, 16) >= 0
    assert row["key"] not in out and session_key not in out and "pw@" not in out
    assert ('--update-secrets "INBOX_DB_URL=website-admin-db-url:1,INBOX_SIGNING_KEY=website-inbox-key:1,'
            'INBOX_SESSION_KEY=website-inbox-session-key:1"') in out
    assert f'--update-env-vars "INBOX_CLIENT_ID={row["cid"]},INBOX_PLATFORM_ORIGIN=https://workspace.yantrailabs.com"' in out
    assert "2 platform admin account(s); 2 in the platform org" in out


def test_a_rerun_keeps_the_keys_and_puts_the_settings_back(platform, capsys):
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    assert tool.main([]) == 0
    first = the_row(sim)
    sim.run("UPDATE public.store_agents SET visibility = 'public' WHERE slug = 'yantrai-web'")   # an Agent Manager edit
    sim.run("UPDATE public.org_agent_installs SET enabled = true")
    gcloud.url = "https://yantrai-website-916641724782.asia-south1.run.app"
    assert tool.main([]) == 0
    again = the_row(sim)
    assert (again["cid"], again["key"]) == (first["cid"], first["key"])
    assert again["visibility"] == "sadmin" and again["remote_url"] == gcloud.url + "/admin"
    assert again["manifest"]["remote_url"] == gcloud.url + "/admin"
    assert install(sim) == [[PLATFORM_ORG, True]]                            # an existing install is left alone
    assert len(gcloud.store["website-inbox-key"]) == 1 and len(gcloud.store["website-inbox-session-key"]) == 1
    assert "already holds the signing key" in capsys.readouterr().out


def test_enable_waits_for_the_website_and_disable_hides_it(platform, monkeypatch, capsys):
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    assert tool.main([]) == 0
    capsys.readouterr()
    monkeypatch.setattr(tool, "website_inbox_state", lambda base: (False, "it says inbox: false"))
    assert tool.main(["--enable"]) == 1
    assert "it says inbox: false" in capsys.readouterr().out
    assert install(sim) == [[PLATFORM_ORG, False]]
    asked = []
    monkeypatch.setattr(tool, "website_inbox_state", lambda base: (asked.append(base) or True, ""))
    # --enable and --disable read the address from the registration, not from gcloud
    gcloud.fail["run services describe"] = "ERROR: Reauthentication required"
    assert tool.main(["--enable", "--remote-url", "https://elsewhere-abc-el.a.run.app"]) == 1
    assert asked == [] and install(sim) == [[PLATFORM_ORG, False]]
    assert tool.main(["--enable"]) == 0
    assert asked == [RUN_URL] and install(sim) == [[PLATFORM_ORG, True]]
    assert "tile is now ON" in capsys.readouterr().out
    assert tool.main(["--disable"]) == 0
    assert install(sim) == [[PLATFORM_ORG, False]]


def test_disable_works_when_the_platform_org_is_unclear_and_gcloud_is_gone(platform, monkeypatch):
    tool, sim = platform["tool"], platform["sim"]
    assert tool.main([]) == 0
    sim.run("UPDATE public.org_agent_installs SET enabled = true")
    sim.run("UPDATE public.accounting_users SET role = 'super_admin' WHERE username = 'customer_owner'")

    def no_gcloud(*a, **k):
        raise RuntimeError("gcloud not found on PATH")
    monkeypatch.setattr(tool, "_gcloud", no_gcloud)
    assert tool.main(["--disable"]) == 0
    assert install(sim) == [[PLATFORM_ORG, False]]
    # with two orgs holding super admins, --org names the platform one
    monkeypatch.setattr(tool, "website_inbox_state", lambda base: (True, ""))
    assert tool.main(["--enable"]) == 1
    assert tool.main(["--enable", "--org", PLATFORM_ORG]) == 0
    assert install(sim) == [[PLATFORM_ORG, True]]


def test_a_new_session_key_changes_only_the_session_setting(platform, env, capsys):
    tool, gcloud = platform["tool"], platform["gcloud"]
    assert tool.main([]) == 0
    capsys.readouterr()
    env.delenv("PLATFORM_OWNER_DB_URL")                     # an off switch: no database password needed
    assert tool.main(["--new-session-key"]) == 0
    first, second = gcloud.store["website-inbox-session-key"]
    assert first != second
    out = capsys.readouterr().out
    assert '--update-secrets "INBOX_SESSION_KEY=website-inbox-session-key:2"' in out
    assert "INBOX_SIGNING_KEY" not in out and "INBOX_DB_URL" not in out      # never re-attaches the key
    assert second not in out


def test_a_rerun_repairs_a_permission_grant_that_failed(platform):
    tool, gcloud = platform["tool"], platform["gcloud"]
    gcloud.fail_once["secrets add-iam-policy-binding website-inbox-key"] = "ABORTED: concurrent policy changes"
    assert tool.main([]) == 1
    gcloud.grants.clear()
    assert tool.main([]) == 0
    assert set(gcloud.grants) == {"website-inbox-key", "website-inbox-session-key"}
    assert len(gcloud.store["website-inbox-key"]) == 1


def test_a_secret_created_without_a_version_gets_one(platform):
    tool, gcloud = platform["tool"], platform["gcloud"]
    gcloud.store["website-inbox-key"] = []                       # created, but its first version failed
    assert tool.main([]) == 0
    assert gcloud.store["website-inbox-key"] == [the_row(platform["sim"])["key"]]
    assert not [c for c, _ in gcloud.calls if c[:3] == ("secrets", "create", "website-inbox-key")]


def test_registration_refuses_when_the_data_api_could_read_the_key(platform, capsys):
    tool, sim = platform["tool"], platform["sim"]
    sim.run("GRANT SELECT (slug, signing_key) ON public.store_agents TO anon")
    try:
        assert tool.main([]) == 1
        assert "anon can read or change public.store_agents" in capsys.readouterr().out
        assert the_row(sim) is None and "website-inbox-key" not in platform["gcloud"].store
    finally:
        sim.run("REVOKE ALL ON public.store_agents FROM anon")


@pytest.mark.parametrize("setup, message", [
    ("UPDATE public.accounting_users SET role = 'super_admin' WHERE username = 'customer_owner'",
     "Pick it with --org"),
    ("INSERT INTO public.store_agents (slug, name, publisher, owner_org_id, manifest, client_id, signing_key) "
     "VALUES ('yantrai-web', 'Someone else', 'developer', CAST('11111111-2222-4333-8444-555555555555' AS uuid), "
     "'{\"agent_kind\": \"remote\"}', 'cid_aaaaaaaaaaaaaaaa', 'sk_' || repeat('a', 48))",
     "did not make"),
])
def test_registration_refuses_what_it_does_not_recognise(platform, capsys, setup, message):
    tool, sim = platform["tool"], platform["sim"]
    sim.run(setup)
    before = sim.run("SELECT count(*) FROM public.org_agent_installs")
    assert tool.main([]) == 1
    assert message in capsys.readouterr().out
    assert sim.run("SELECT count(*) FROM public.org_agent_installs") == before
    assert "website-inbox-key" not in platform["gcloud"].store


def test_nothing_changes_without_the_inbox_login_or_gcloud(platform, capsys):
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    del gcloud.store["website-admin-db-url"]
    assert tool.main([]) == 1
    assert "set_website_login.py --role website_admin first" in capsys.readouterr().out
    gcloud.store["website-admin-db-url"] = ["x"]
    gcloud.fail["run services describe"] = "ERROR: (gcloud.run.services.describe) You do not have permission"
    assert tool.main([]) == 1
    assert "before changing anything" in capsys.readouterr().out
    assert the_row(sim) is None and install(sim) == []


def test_a_dry_run_changes_nothing(platform, capsys):
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    assert tool.main(["--dry-run"]) == 0
    assert "nothing changed" in capsys.readouterr().out
    assert the_row(sim) is None and install(sim) == [] and "website-inbox-key" not in gcloud.store


def test_a_registered_app_signs_in_with_the_stored_key(platform, pg, env, fresh_intake):
    """What the tool stores is what the website verifies against: a token signed
    with the row's key and carrying its client id gets a session."""
    tool, sim, gcloud = platform["tool"], platform["sim"], platform["gcloud"]
    assert tool.main([]) == 0
    row = the_row(sim)
    env.setenv("INBOX_SIGNING_KEY", gcloud.store["website-inbox-key"][-1])
    env.setenv("INBOX_CLIENT_ID", row["cid"])
    env.setenv("INBOX_SESSION_KEY", gcloud.store["website-inbox-session-key"][-1])
    env.setenv("INBOX_DB_URL", pg["inbox_dsn"])
    main.app.config["TESTING"] = True
    r = main.app.test_client().post("/admin/api/session",
                                    json={"token": mint(key=row["key"], kid=row["cid"])})
    assert r.status_code == 200
    assert CLIENT != row["cid"] and KEY != row["key"]
