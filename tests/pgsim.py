"""A throwaway, Supabase-like Postgres for the DB tests (test_intake_db.py,
test_inbox_db.py). Both migrations are applied the way production applies them:
with psql, as a non-superuser `postgres`, and each more than once, in both orders.

Skipped unless both are set:
  INTAKE_TEST_PG_ADMIN  superuser URL of a disposable local cluster, e.g.
                        postgresql://pgsuper:<pw>@127.0.0.1:55432/postgres
  INTAKE_TEST_PSQL      path to psql (the migrations are applied with psql, as in production)
Setting only one of them, or INTAKE_TEST_REQUIRE_DB=1 without both, is an error,
not a skip: a run meant to prove the database side must never pass without it.

The fixture makes a Supabase-like setup: a non-superuser `postgres` login with
CREATEROLE/CREATEDB/BYPASSRLS, the anon/authenticated/service_role/authenticator
roles, and a public table open to anon. It refuses to run against anything that
is not localhost. Never point it at the platform database.
"""
import os
import secrets
import subprocess
import uuid
from urllib.parse import urlsplit

import pytest

import intake

ADMIN = os.getenv("INTAKE_TEST_PG_ADMIN")
PSQL = os.getenv("INTAKE_TEST_PSQL")
if (ADMIN or PSQL or os.getenv("INTAKE_TEST_REQUIRE_DB") == "1") and not (ADMIN and PSQL):
    raise RuntimeError("the database tests need both INTAKE_TEST_PG_ADMIN and INTAKE_TEST_PSQL; missing: "
                       + ", ".join(n for n, v in (("INTAKE_TEST_PG_ADMIN", ADMIN), ("INTAKE_TEST_PSQL", PSQL))
                                   if not v))
SKIP = pytest.mark.skipif(
    not (ADMIN and PSQL),
    reason="set INTAKE_TEST_PG_ADMIN and INTAKE_TEST_PSQL to run against a throwaway local Postgres")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_001 = os.path.join(ROOT, "db", "001_intake.sql")
SQL_002 = os.path.join(ROOT, "db", "002_inbox.sql")


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
    sim_pw, app_pw, inbox_pw = secrets.token_hex(12), secrets.token_hex(12), secrets.token_hex(12)
    dbname = "intake_it_" + secrets.token_hex(4)

    # postgres, website_app and website_admin are cluster-wide: two runs at once
    # would reset each other's passwords and settings, so runs take turns. The
    # lock is released whatever happens below, so a failed setup never leaves the
    # next module waiting for it.
    lock = _connect(a.username, a.password, "postgres", host, port)     # one lock per cluster
    lock.run("SELECT pg_advisory_lock(815301)")
    try:
        yield from _database(a, host, port, sim_pw, app_pw, inbox_pw, dbname)
    finally:
        lock.run("SELECT pg_advisory_unlock(815301)")
        lock.close()


def _database(a, host, port, sim_pw, app_pw, inbox_pw, dbname):
    admin = _connect(a.username, a.password, (a.path or "/postgres").lstrip("/") or "postgres", host, port)
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

    # re-runnable, and in either order: 001, 001, 002, 002, 001 again
    for sql in (SQL_001, SQL_001, SQL_002, SQL_002, SQL_001):
        done = psql("-q", "-f", sql)
        assert done.returncode == 0, (sql, done.stderr)
    sim.run(f"ALTER ROLE website_app PASSWORD '{app_pw}'")
    sim.run(f"ALTER ROLE website_admin PASSWORD '{inbox_pw}'")

    try:
        yield {
            "host": host, "port": port, "db": dbname, "sim": sim, "psql": psql,
            "sql_001": SQL_001, "sql_002": SQL_002,
            "admin": lambda: _connect(a.username, a.password, dbname, host, port),
            "app": lambda: _connect("website_app", app_pw, dbname, host, port),
            "inbox": lambda: _connect("website_admin", inbox_pw, dbname, host, port),
            "dsn": f"postgresql://website_app:{app_pw}@{host}:{port}/{dbname}?sslmode=disable",
            "inbox_dsn": f"postgresql://website_admin:{inbox_pw}@{host}:{port}/{dbname}?sslmode=disable",
            "app_pw": app_pw, "inbox_pw": inbox_pw,
            "owner_url": f"postgresql://postgres:{sim_pw}@{host}:{port}/{dbname}",
        }
    finally:
        # leave no stored settings behind for the next run (re-applying clears them)
        try:
            for sql in (SQL_001, SQL_002):
                done = psql("-q", "-f", sql)
                assert done.returncode == 0, (sql, done.stderr)
        finally:
            sim.close()


def a_row(form="savings_check", **extra):
    row = {c: None for c in intake.COLUMNS}
    row.update(id=str(uuid.uuid4()), form=form, locale="en", name="Asha Rao",
               email="asha@example.com", company="Acme Pvt Ltd" if form == "savings_check" else None)
    row.update(extra)
    return row


def sqlstate(exc):
    return exc.args[0].get("C") if exc.args and isinstance(exc.args[0], dict) else None
