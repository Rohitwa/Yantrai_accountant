"""The owner's tools, where no database is needed: the login tool must be told
which login to change, and never puts one login's URL in the other's secret."""
import pytest

import scripts.set_website_login as login_tool


def test_the_login_tool_will_not_guess_which_login(env, monkeypatch, capsys):
    def no_touch(*a, **k):
        raise AssertionError("nothing may be touched")
    monkeypatch.setattr(login_tool, "_Db", no_touch)          # sandboxed even if --role ever gets a default
    monkeypatch.setattr(login_tool, "_gcloud", no_touch)
    with pytest.raises(SystemExit):
        login_tool.main([])
    assert "--role" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--role", "website_admin", "--secret", "website-db-url"],        # the inbox's URL in the forms' secret
    ["--role", "website_app", "--secret", "website-admin-db-url"],
])
def test_the_login_tool_refuses_the_other_logins_secret(env, monkeypatch, capsys, argv):
    env.setenv("PLATFORM_OWNER_DB_URL", "postgresql://postgres.ref:x@pooler.example:5432/postgres")

    def no_db(*a, **k):
        raise AssertionError("the database must not be touched")
    monkeypatch.setattr(login_tool, "_Db", no_db)
    monkeypatch.setattr(login_tool, "_gcloud", no_db)
    assert login_tool.main(argv) == 1
    assert "holds another login's URL" in capsys.readouterr().out


def test_each_login_gets_its_own_user_and_secret():
    owner = "postgresql://postgres.ref:x@pooler.example:5432/postgres"
    assert login_tool.app_url(owner, "pw", "website_admin").startswith("postgresql://website_admin.ref:pw@")
    assert login_tool.app_url(owner, "pw").startswith("postgresql://website_app.ref:pw@")
    assert login_tool.ROLES["website_admin"] == {"secret": "website-admin-db-url", "env": "INBOX_DB_URL",
                                                "sql": "db/002_inbox.sql"}
    assert login_tool.ROLES["website_app"]["secret"] == "website-db-url"


def test_the_registration_tool_translates_every_parameter_for_psycopg2():
    """The owner's machine may use psycopg2: every :name must become %(name)s and no
    bare % may remain (psycopg2 would read it as a placeholder)."""
    import re
    import scripts.register_inbox_app as tool

    seen = []

    class Cursor:
        description = ("column",)                                 # every statement returns rows (none)

        def execute(self, sql, params):
            seen.append(sql)
            assert not re.search(r"(?<![:\w]):[a-z_]+", sql), sql
            sql % {k: "x" for k in params}                      # raises on a stray %

        def fetchall(self):
            return []

    db = tool.Db.__new__(tool.Db)
    db._pg8000 = False
    db._con = type("C", (), {"cursor": lambda self: Cursor()})()
    db.q("SELECT 1 FROM org_agent_installs WHERE CAST(org_id AS text) = :org AND agent_slug = :slug",
         org="o", slug="s")
    tool.disable_everywhere(db)
    tool.api_role_exposure(db)
    tool.platform_org(db)
    assert len(seen) == 4
