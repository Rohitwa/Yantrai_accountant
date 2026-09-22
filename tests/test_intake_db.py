"""db/001_intake.sql and intake.py against a real, throwaway, LOCAL Postgres 17.

Skipped unless both are set:
  INTAKE_TEST_PG_ADMIN  superuser URL of a disposable local cluster, e.g.
                        postgresql://pgsuper:<pw>@127.0.0.1:55432/postgres
  INTAKE_TEST_PSQL      path to psql (the migration is applied with psql, as in production)

The fixture makes a Supabase-like setup: a non-superuser `postgres` login with
CREATEROLE/CREATEDB/BYPASSRLS, the anon/authenticated/service_role/authenticator
roles, and a public table open to anon. It refuses to run against anything that
is not localhost. Never point it at the platform database.
"""
import json
import os
import secrets
import subprocess
import uuid
from urllib.parse import urlsplit

import pytest

import intake

ADMIN = os.getenv("INTAKE_TEST_PG_ADMIN")
PSQL = os.getenv("INTAKE_TEST_PSQL")
pytestmark = pytest.mark.skipif(
    not (ADMIN and PSQL),
    reason="set INTAKE_TEST_PG_ADMIN and INTAKE_TEST_PSQL to run against a throwaway local Postgres")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_001 = os.path.join(ROOT, "db", "001_intake.sql")
SQL_VERIFY = os.path.join(ROOT, "db", "verify_intake.sql")


def _connect(user, password, database, host, port):
    import pg8000.native
    return pg8000.native.Connection(user=user, password=password, database=database,
                                    host=host, port=port)


@pytest.fixture(scope="module")
def pg():
    a = urlsplit(ADMIN)
    assert a.hostname in ("127.0.0.1", "localhost", "::1"), \
        "integration tests only run against a local throwaway Postgres"
    assert a.username != "postgres", \
        "connect as the throwaway cluster's own superuser, not a role named postgres"
    host, port = a.hostname, a.port or 5432
    sim_pw, app_pw = secrets.token_hex(12), secrets.token_hex(12)
    dbname = "intake_it_" + secrets.token_hex(4)

    admin = _connect(a.username, a.password, (a.path or "/postgres").lstrip("/") or "postgres", host, port)
    # postgres and website_app are cluster-wide: two runs at once would reset each
    # other's passwords and settings, so runs take turns (released at teardown)
    lock = _connect(a.username, a.password, "postgres", host, port)     # one lock per cluster
    lock.run("SELECT pg_advisory_lock(815301)")
    admin.run("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN NOINHERIT; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN NOINHERIT; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticator') THEN CREATE ROLE authenticator LOGIN NOINHERIT; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='postgres') THEN CREATE ROLE postgres LOGIN CREATEROLE CREATEDB BYPASSRLS; END IF;
    END $$""")
    # the stand-in must look like Supabase's: not a superuser, but CREATEROLE + BYPASSRLS.
    # Checked before its password is touched, so a real `postgres` superuser is never changed.
    assert admin.run("SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles "
                     "WHERE rolname = 'postgres'") == [[False, True, True]]
    admin.run(f"ALTER ROLE postgres PASSWORD '{sim_pw}'")
    admin.run(f"CREATE DATABASE {dbname} OWNER postgres")
    admin.close()

    sim = _connect("postgres", sim_pw, dbname, host, port)
    sim.run("CREATE TABLE public.leaky (x int)")
    sim.run("GRANT ALL ON public.leaky TO anon, authenticated, service_role")

    env = dict(os.environ, PGPASSWORD=sim_pw)

    def psql(*args):
        return subprocess.run([PSQL, "-h", host, "-p", str(port), "-U", "postgres", "-d", dbname,
                               "-v", "ON_ERROR_STOP=1", *args],
                              env=env, capture_output=True, text=True, encoding="utf-8")

    for _ in range(2):                          # applying twice proves it is re-runnable
        done = psql("-q", "-f", SQL_001)
        assert done.returncode == 0, done.stderr
    sim.run(f"ALTER ROLE website_app PASSWORD '{app_pw}'")

    try:
        yield {
            "host": host, "port": port, "db": dbname, "sim": sim, "psql": psql, "sql_001": SQL_001,
            "admin": lambda: _connect(a.username, a.password, dbname, host, port),
            "app": lambda: _connect("website_app", app_pw, dbname, host, port),
            "dsn": f"postgresql://website_app:{app_pw}@{host}:{port}/{dbname}?sslmode=disable",
            "app_pw": app_pw,
            "owner_url": f"postgresql://postgres:{sim_pw}@{host}:{port}/{dbname}",
        }
    finally:
        # leave no stored website_app settings behind for the next run (re-applying 001 clears them)
        pg_psql = psql("-q", "-f", SQL_001)
        assert pg_psql.returncode == 0, pg_psql.stderr
        sim.close()
        lock.run("SELECT pg_advisory_unlock(815301)")
        lock.close()


@pytest.fixture
def as_app(pg, env):
    env.setenv("WEBSITE_DB_URL", pg["dsn"])
    return pg


def a_row(form="savings_check", **extra):
    row = {c: None for c in intake.COLUMNS}
    row.update(id=str(uuid.uuid4()), form=form, locale="en", name="Asha Rao",
               email="asha@example.com", company="Acme Pvt Ltd" if form == "savings_check" else None)
    row.update(extra)
    return row


def sqlstate(exc):
    return exc.args[0].get("C") if exc.args and isinstance(exc.args[0], dict) else None


def verify(conn):
    sql = open(SQL_VERIFY, encoding="utf-8").read().split("-- Information only")[0]
    return {name: ok for name, ok in conn.run(sql)}


# --- the migration and its proof -------------------------------------------------------
N_CHECKS = 32


def test_verify_script_passes_through_psql(pg):
    out = pg["psql"]("-At", "-F", "|", "-f", SQL_VERIFY)
    assert out.returncode == 0, out.stderr
    # the first result only; the ones after it are information
    checks = [line.rsplit("|", 1) for line in out.stdout.splitlines() if "|" in line][:N_CHECKS]
    assert len(checks) == N_CHECKS and len(verify(pg["sim"])) == N_CHECKS
    assert all(ok == "t" for _, ok in checks), [name for name, ok in checks if ok != "t"]


@pytest.mark.parametrize("mistake, check", [
    ("GRANT SELECT ON website_intake.submissions TO anon",
     "anon, authenticated and service_role hold no table rights"),
    ("GRANT USAGE ON SCHEMA website_intake TO authenticated",
     "anon, authenticated and service_role cannot use the schema"),
    ("GRANT SELECT ON website_intake.submissions TO website_app",
     "website_app cannot read, change or delete submissions"),
    ("GRANT INSERT (status) ON website_intake.submissions TO website_app",
     "website_app cannot set received_at, status or triage columns"),
    ("GRANT SELECT ON public.leaky TO website_app", "website_app has no rights on any public table"),
    ("ALTER TABLE website_intake.submissions DISABLE ROW LEVEL SECURITY",
     "row-level security is on for both tables"),
    ("GRANT EXECUTE ON FUNCTION website_intake._throttle() TO PUBLIC",
     "nobody but its owner may call the throttle directly"),
    ("ALTER ROLE website_app CONNECTION LIMIT 50", "website_app connection limit is 10"),
    ("ALTER ROLE website_app IN DATABASE {db} SET statement_timeout = 0",
     "website_app has no per-database setting overrides"),
    ("GRANT CREATE ON SCHEMA public TO website_app", "website_app cannot create objects in any schema"),
    ("CREATE FUNCTION public.leak() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
     "website_app cannot call any function that runs with its owner's rights"),
    ("ALTER TABLE website_intake.notify_events DROP CONSTRAINT notify_events_submission_fk",
     "notify_events rows must belong to a stored submission"),
    ("ALTER ROLE website_app SET default_transaction_read_only = on",
     "website_app stores no settings beyond the three 001 sets"),
    ("ALTER ROLE website_app RESET idle_in_transaction_session_timeout",
     "website_app stores no settings beyond the three 001 sets"),
    ("GRANT TRUNCATE ON public.leaky TO website_app", "website_app has no rights on any public table"),
    ("GRANT TRIGGER ON public.leaky TO website_app", "website_app has no rights on any public table"),
    ("GRANT TRUNCATE ON website_intake.submissions TO service_role",
     "anon, authenticated and service_role hold no table rights"),
    ("GRANT MAINTAIN ON public.leaky TO website_app", "website_app has no rights on any public table"),
    ("GRANT MAINTAIN ON website_intake.submissions TO service_role",
     "anon, authenticated and service_role hold no table rights"),
    ("GRANT MAINTAIN ON website_intake.submissions TO website_app",
     "website_app cannot read, change or delete submissions"),
    ("GRANT TRIGGER ON website_intake.notify_events TO website_app",
     "website_app cannot read, change or delete notify_events"),
])
def test_verify_script_catches_each_mistake(pg, mistake, check):
    sim = pg["sim"]
    sim.run("BEGIN")
    try:
        sim.run(mistake.format(db=pg["db"]))
        assert verify(sim)[check] is False
    finally:
        sim.run("ROLLBACK")
    assert verify(sim)[check] is True


def test_verify_script_catches_a_large_object_written_by_website_app(pg):
    check = "website_app owns no large objects"
    app = pg["app"]()
    try:
        (oid,), = app.run("SELECT lo_from_bytea(0, 'x')")       # a PUBLIC default, not closed by 001
        assert verify(pg["sim"])[check] is False
        app.run("SELECT lo_unlink(:oid)", oid=oid)
    finally:
        app.close()
    assert verify(pg["sim"])[check] is True


# --- what website_app can and cannot do ----------------------------------------------------
def test_website_app_inserts_and_a_retry_is_a_duplicate(as_app):
    row = a_row()
    assert intake.insert_submission(row) == "stored"
    assert intake.insert_submission(row) == "duplicate"
    admin = as_app["admin"]()
    (status, recent), = admin.run(
        "SELECT status, received_at > now() - interval '1 minute' FROM website_intake.submissions WHERE id = :id",
        id=row["id"])
    admin.close()
    assert status == "new" and recent is True


@pytest.mark.parametrize("sql", [
    "SELECT id FROM website_intake.submissions",
    "SELECT * FROM website_intake.notify_events",
    "UPDATE website_intake.submissions SET status = 'spam'",
    "DELETE FROM website_intake.submissions",
    "TRUNCATE website_intake.submissions",
    "SELECT * FROM public.leaky",
    "INSERT INTO public.leaky VALUES (1)",
    "INSERT INTO website_intake.submissions (id, form, name, email, company, status) "
    "VALUES (gen_random_uuid(), 'savings_check', 'x', 'x@y.co', 'c', 'qualified')",
    "INSERT INTO website_intake.submissions (id, form, name, email, company, received_at) "
    "VALUES (gen_random_uuid(), 'savings_check', 'x', 'x@y.co', 'c', now() - interval '1 year')",
    "INSERT INTO website_intake.submissions (id, form, name, email, company) "
    "VALUES (gen_random_uuid(), 'savings_check', 'x', 'x@y.co', 'c') RETURNING id",
])
def test_website_app_is_refused(as_app, sql):
    from pg8000.exceptions import DatabaseError
    con = as_app["app"]()
    try:
        with pytest.raises(DatabaseError) as info:
            con.run(sql)
        assert sqlstate(info.value) == "42501"
    finally:
        con.close()


def test_website_app_settings_apply(as_app):
    con = as_app["app"]()
    try:
        assert con.run("SHOW statement_timeout") == [["5s"]]
        assert con.run("SHOW search_path") == [['""']]
    finally:
        con.close()


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_api_roles_are_shut_out(pg, role):
    from pg8000.exceptions import DatabaseError
    con = pg["admin"]()
    try:
        con.run(f"SET ROLE {role}")
        with pytest.raises(DatabaseError) as info:
            con.run("SELECT count(*) FROM website_intake.submissions")
        assert sqlstate(info.value) == "42501"
    finally:
        con.close()


def test_notify_events_are_recorded_once(as_app):
    row = a_row()
    intake.insert_submission(row)
    intake.record_event(row["id"], "mail_sent")
    intake.record_event(row["id"], "mail_sent")       # duplicate: swallowed
    admin = as_app["admin"]()
    (n,), = admin.run("SELECT count(*) FROM website_intake.notify_events WHERE submission_id = :id",
                      id=row["id"])
    admin.close()
    assert n == 1


def test_data_the_table_refuses_is_rejected(as_app):
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission(a_row(cv_mime="text/plain"))
    assert info.value.reason == "rejected"


def test_wrong_password_is_unavailable(pg, env):
    env.setenv("WEBSITE_DB_URL", pg["dsn"].replace("website_app:", "website_app:wrong", 1))
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission(a_row())
    assert info.value.reason == "unavailable"


# --- end to end ------------------------------------------------------------------------------
def test_a_form_post_lands_as_a_row(as_app, fresh_intake, monkeypatch):
    import main
    monkeypatch.setattr(main, "_send_mail", lambda *a, **k: (True, "", 200))
    email = f"e2e-{secrets.token_hex(3)}@example.com"
    r = main.app.test_client().post("/api/savings-check", json={
        "name": "End To End", "email": email, "company": "E2E Ltd", "page": "https://yantrailabs.com/fr/"})
    assert r.status_code == 200
    admin = as_app["admin"]()
    rows = admin.run("SELECT form, locale, company, status FROM website_intake.submissions WHERE email = :e",
                     e=email)
    admin.close()
    assert rows == [["savings_check", "fr", "E2E Ltd", "new"]]


def test_over_long_input_on_every_field_is_stored_not_refused(as_app, fresh_intake, monkeypatch, env):
    """The handlers' caps against the real CHECKs, for both forms: a 200 alone
    could be the [NOT SAVED] fallback, so the rows themselves are looked up."""
    import io
    import main
    env.setenv("INTAKE_FORMS", "savings_check,careers")
    env.setenv("K_REVISION", "r" * 400)
    monkeypatch.setattr(main, "_send_mail", lambda *a, **k: (True, "", 200))
    client = main.app.test_client()
    ua = {"User-Agent": "U" * 400}
    email = f"xx{secrets.token_hex(3)}@" + "e." * 150 + "com"    # 180 after the cut, ending in a letter
    savings = {k: k[0] * 10000 for k in ("name", "company", "role", "erp", "outflow", "note")}
    savings.update(email=email, page="https://yantrailabs.com/" + "p" * 10000)
    assert client.post("/api/savings-check", json=savings, headers=ua).status_code == 200
    careers = {k: k[0] * 10000 for k in ("name", "linkedin", "work", "area", "note")}
    careers.update(email=email, page="https://yantrailabs.com/careers" + "p" * 10000,
                   resume=(io.BytesIO(b"%PDF-1.4\n%test\n"), "c" * 400 + ".pdf"))
    r = client.post("/api/careers", data=careers, content_type="multipart/form-data", headers=ua)
    assert r.status_code == 200
    admin = as_app["admin"]()
    rows = admin.run("SELECT form, char_length(email) FROM website_intake.submissions "
                     "WHERE email = :e ORDER BY form", e=email[:180])
    admin.close()
    assert rows == [["careers", 180], ["savings_check", 180]]


def test_replay_puts_a_not_saved_mail_back_once(as_app, tmp_path, capsys):
    import scripts.replay_intake as replay
    row = a_row(email=f"replay-{secrets.token_hex(3)}@example.com", locale="fr", page="/fr/",
                role="CFO", erp="SAP", outflow="$1M", note="note « é » 🙂", linkedin="https://l.in/x",
                work="https://w.x", area="Ops", ip_hash="b" * 64, user_agent="UA",
                source_revision="yantrai-website-00030-abc")
    mail = tmp_path / "notsaved.txt"
    mail.write_text("Subject: [NOT SAVED] ...\nFrom: rohit@yantrailabs.com\n\nREPLAY-JSON: "
                    + intake.replay_json(row) + "\n", encoding="utf-8")
    assert replay.main([str(mail)]) == 0
    assert replay.main([str(mail)]) == 0
    out = capsys.readouterr().out
    assert f"{row['id']}: stored" in out and f"{row['id']}: already stored" in out
    admin = as_app["admin"]()
    stored = admin.run("SELECT " + ", ".join(intake.COLUMNS)
                       + " FROM website_intake.submissions WHERE id = CAST(:id AS uuid)", id=row["id"])
    admin.close()
    got = dict(zip(intake.COLUMNS, stored[0]))
    got["id"] = str(got["id"])
    assert got == row


def test_connection_check_names_the_login(as_app):
    assert intake.connection_check() == "website_app"


def test_a_delivery_record_needs_a_stored_submission(as_app, capsys):
    orphan = str(uuid.uuid4())
    intake.record_event(orphan, "mail_sent")
    assert "intake_event_unrecorded" in capsys.readouterr().out
    admin = as_app["admin"]()
    (n,), = admin.run("SELECT count(*) FROM website_intake.notify_events "
                      "WHERE submission_id = CAST(:id AS uuid)", id=orphan)
    admin.close()
    assert n == 0


def test_website_app_can_store_defaults_on_itself_and_verify_sees_it(pg):
    app = pg["app"]()
    try:
        app.run("ALTER ROLE website_app SET default_transaction_read_only = on")
        app.run("ALTER ROLE website_app IN DATABASE postgres SET statement_timeout = 0")
    finally:
        app.close()
    checks = verify(pg["sim"])
    assert checks["website_app stores no settings beyond the three 001 sets"] is False
    assert checks["website_app has no per-database setting overrides"] is False
    done = pg["psql"]("-q", "-f", pg["sql_001"])
    assert done.returncode == 0, done.stderr
    checks = verify(pg["sim"])
    assert all(checks.values()), [name for name, ok in checks.items() if not ok]


def test_rerunning_001_takes_back_a_schema_grant(pg):
    sim = pg["sim"]
    sim.run("GRANT CREATE ON SCHEMA website_intake TO website_app")
    assert verify(sim)["website_app cannot create objects in any schema"] is False
    done = pg["psql"]("-q", "-f", pg["sql_001"])
    assert done.returncode == 0, done.stderr
    assert verify(sim)["website_app cannot create objects in any schema"] is True


@pytest.mark.parametrize("stored, where", [
    ("ALTER ROLE website_app SET log_min_duration_statement = 0", "role-wide"),
    ("ALTER ROLE website_app IN DATABASE template1 SET log_statement = 'all'", "IN DATABASE template1"),
])
def test_001_fails_loudly_on_a_setting_only_a_superuser_can_clear(pg, stored, where):
    admin = pg["admin"]()
    try:
        admin.run(stored)
        done = pg["psql"]("-q", "-f", pg["sql_001"])
        assert done.returncode != 0 and "could not reset" in done.stderr
        assert where in done.stderr and "IN DATABASE <name> RESET ALL" in done.stderr
    finally:
        admin.run("ALTER ROLE website_app RESET ALL")
        admin.run("ALTER ROLE website_app IN DATABASE template1 RESET ALL")
        admin.close()
    done = pg["psql"]("-q", "-f", pg["sql_001"])
    assert done.returncode == 0, done.stderr
    checks = verify(pg["sim"])
    assert all(checks.values()), [name for name, ok in checks.items() if not ok]


def test_the_leak_recovery_steps_shut_out_a_connected_session(pg):
    """README 'After any suspicion', step by step, while the leaked login keeps a
    session open: it must not be able to keep or retake the login."""
    sim, admin = pg["sim"], pg["admin"]()
    attacker = pg["app"]()
    new_pw = secrets.token_hex(12)
    try:
        sim.run("ALTER ROLE website_app NOLOGIN")                                   # 1
        admin.run("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE usename = 'website_app'")                                  # 2
        with pytest.raises(Exception):                                               # its session is gone
            attacker.run("ALTER ROLE website_app PASSWORD 'kept-by-attacker'")
        sim.run(f"ALTER ROLE website_app PASSWORD '{new_pw}'")                      # 3
        with pytest.raises(Exception):                                               # no login until 001
            _connect("website_app", new_pw, pg["db"], pg["host"], pg["port"])
        done = pg["psql"]("-q", "-f", pg["sql_001"])                               # 4: LOGIN again
        assert done.returncode == 0, done.stderr
        _connect("website_app", new_pw, pg["db"], pg["host"], pg["port"]).close()
        with pytest.raises(Exception):
            _connect("website_app", "kept-by-attacker", pg["db"], pg["host"], pg["port"])
    finally:
        try:
            attacker.close()
        except Exception:
            pass
        sim.run("ALTER ROLE website_app LOGIN")
        sim.run(f"ALTER ROLE website_app PASSWORD '{pg['app_pw']}'")
        admin.close()


def test_set_website_login_sets_a_password_nobody_sees(pg, env, monkeypatch, capsys):
    """The operator tool, run as the Supabase-like postgres: website_app gets a
    working password that is never printed, and still cannot read a lead."""
    import scripts.set_website_login as tool
    known = "known-" + secrets.token_hex(8)
    monkeypatch.setattr(tool.secrets, "token_urlsafe", lambda n: known)
    env.setenv("PLATFORM_OWNER_DB_URL", pg["owner_url"])
    try:
        assert tool.main(["--no-secret"]) == 0
        out = capsys.readouterr().out
        assert "logged in as website_app" in out and "refused" in out
        assert known not in out and "SCRAM-SHA-256$" not in out
        _connect("website_app", known, pg["db"], pg["host"], pg["port"]).close()
        with pytest.raises(Exception):                    # the old password is gone
            _connect("website_app", pg["app_pw"], pg["db"], pg["host"], pg["port"])
        stored = pg["admin"]()
        try:
            (verifier,), = stored.run("SELECT rolpassword FROM pg_authid WHERE rolname = 'website_app'")
        finally:
            stored.close()
        assert verifier.startswith("SCRAM-SHA-256$4096:")
        assert tool.app_url("postgresql://postgres.abc:x@pooler.example:5432/postgres", "p w") == \
            "postgresql://website_app.abc:p%20w@pooler.example:5432/postgres?sslmode=require"
        # the README's leak response: logins off, then the tool, which turns them
        # back on only once the new password is in place
        pg["sim"].run("ALTER ROLE website_app NOLOGIN")
        monkeypatch.setattr(tool.secrets, "token_urlsafe", lambda n: known + "-2")
        assert tool.main(["--no-secret"]) == 0
        assert "turned back on" in capsys.readouterr().out
        _connect("website_app", known + "-2", pg["db"], pg["host"], pg["port"]).close()
        with pytest.raises(Exception):
            _connect("website_app", known, pg["db"], pg["host"], pg["port"])
    finally:
        pg["sim"].run("ALTER ROLE website_app LOGIN")
        pg["sim"].run(f"ALTER ROLE website_app PASSWORD '{pg['app_pw']}'")


def test_rerunning_001_clears_per_database_overrides(pg):
    sim = pg["sim"]
    sim.run(f"ALTER ROLE website_app IN DATABASE {pg['db']} SET statement_timeout = 0")
    assert verify(sim)["website_app has no per-database setting overrides"] is False
    done = pg["psql"]("-q", "-f", pg["sql_001"])
    assert done.returncode == 0, done.stderr
    assert verify(sim)["website_app has no per-database setting overrides"] is True


# last: it fills the hour's savings-check quota
def test_the_flood_guard_trips_at_300_an_hour(as_app):
    sim = as_app["sim"]
    (n,), = sim.run("SELECT count(*) FROM website_intake.submissions "
                    "WHERE form = 'savings_check' AND received_at > now() - interval '1 hour'")
    sim.run("INSERT INTO website_intake.submissions (id, form, name, email, company) "
            "SELECT gen_random_uuid(), 'savings_check', 'x', 'x@y.co', 'c' FROM generate_series(1, :k)",
            k=300 - n)
    already = a_row()
    sim.run("ALTER TABLE website_intake.submissions DISABLE TRIGGER submissions_throttle")
    sim.run("INSERT INTO website_intake.submissions (id, form, name, email, company) "
            "VALUES (CAST(:id AS uuid), 'savings_check', 'x', 'x@y.co', 'c')", id=already["id"])
    sim.run("ALTER TABLE website_intake.submissions ENABLE TRIGGER submissions_throttle")
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission(a_row())
    assert info.value.reason == "throttled"
    # a retry of a row that is already stored is answered by the primary key
    assert intake.insert_submission(already) == "duplicate"
    # careers has its own allowance
    assert intake.insert_submission(a_row(form="careers")) == "stored"
