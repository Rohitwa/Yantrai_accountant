#!/usr/bin/env python3
"""Give one of the website's two database logins a new password and store its
connection URL in Secret Manager, without the password ever being shown, typed,
written or logged.

  python scripts/set_website_login.py --role website_app   --owner-env-file <.env holding DB_URL>
  python scripts/set_website_login.py --role website_admin --owner-env-file <.env holding DB_URL>
  (or set PLATFORM_OWNER_DB_URL to the project's postgres session-pooler URL)

The two logins (the role is required, so a run never changes the wrong one):
  website_app    the forms' INSERT-only login (db/001_intake.sql)
                 secret website-db-url, service setting WEBSITE_DB_URL
  website_admin  YantrAI Web's inbox login: reads leads (db/002_inbox.sql)
                 secret website-admin-db-url, service setting INBOX_DB_URL

What it does, in order:
  1. makes a random password in memory;
  2. sets it on the login as a SCRAM-SHA-256 verifier, as psql's \\password
     does, so the password itself never reaches the server or its logs;
  3. proves the login works and has exactly its rights: website_app still
     cannot read a lead; website_admin can read one but cannot change or
     delete any directly;
  4. adds the URL as a new version of the login's secret (creating the secret
     if needed) through gcloud's stdin, and lets the Cloud Run service account
     read it;
  5. prints the new version number, to pin with --update-secrets.

It prints neither the password nor the URL. Every run sets a NEW password:
the live site keeps using the old one, and so FAILS to log in, until it is
pointed at the new version. For website_app that means the forms stop saving
leads until the printed --update-secrets line is run: run the two back to back. If it fails while logging in, wait two minutes before running
it again (the pooler blocks an address after repeated failed logins). If a
leak is suspected, do the README's first steps (NOLOGIN, end the sessions)
before running this. Only the SCRAM verifier, never the password, can appear
in the database's statement statistics or logs.
Needs psycopg2 or pg8000, and gcloud signed in (unless --no-secret); gcloud and
Secret Manager are checked before anything changes.
"""
import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit

PROJECT = "gen-lang-client-0024674990"
RUNTIME_SA = "916641724782-compute@developer.gserviceaccount.com"
# each login: its secret, the service setting that holds it, the file that sets it up
ROLES = {
    "website_app": {"secret": "website-db-url", "env": "WEBSITE_DB_URL", "sql": "db/001_intake.sql"},
    "website_admin": {"secret": "website-admin-db-url", "env": "INBOX_DB_URL", "sql": "db/002_inbox.sql"},
}
_VERIFIER = re.compile(r"SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+")


def scram_verifier(password, iterations=4096, salt=None):
    """The SCRAM-SHA-256 verifier PostgreSQL stores (RFC 5802 / 7677)."""
    salt = salt or os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(b):
        return base64.b64encode(b).decode("ascii")
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def app_url(owner_url, password, role="website_app"):
    """The login's URL on the same host, port and database as the owner's.
    On Supabase the pooler needs the project ref after the role name. Only
    sslmode=require is carried, never the owner's other parameters (a laptop's
    sslrootcert path, sslmode=disable)."""
    try:
        parts = urlsplit(owner_url)
        owner, host, port = unquote(parts.username or ""), parts.hostname or "", parts.port
    except ValueError:
        raise ValueError("the owner URL could not be read: percent-encode any ?, # or @ "
                         "in its password") from None
    if not owner or not host:
        raise ValueError("the owner URL has no user or host")
    user = role + (owner[owner.index("."):] if "." in owner else "")
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}" + (f":{port}" if port else "")
    query = "" if host in ("localhost", "127.0.0.1", "::1") else "sslmode=require"
    return urlunsplit(("postgresql", netloc, parts.path or "/postgres", query, ""))


class _Db:
    """One connection through psycopg2 or pg8000, whichever is installed."""

    def __init__(self, url):
        try:
            import psycopg2
            self._con = psycopg2.connect(url, connect_timeout=10, application_name="set-website-login")
            self._con.autocommit = True
            self._pg8000 = False
        except ImportError:
            import pg8000.native
            import ssl
            p = urlsplit(url)
            local = p.hostname in ("localhost", "127.0.0.1", "::1")
            mode = dict(q.split("=", 1) for q in p.query.split("&") if "=" in q).get(
                "sslmode", "disable" if local else "require")
            ctx = None
            if mode != "disable":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._con = pg8000.native.Connection(
                user=unquote(p.username or ""), password=unquote(p.password or ""), host=p.hostname,
                port=p.port or 5432, database=(p.path or "/postgres").lstrip("/") or "postgres",
                ssl_context=ctx, timeout=10, application_name="set-website-login")
            self._pg8000 = True

    def run(self, sql):
        if self._pg8000:
            return self._con.run(sql)
        cur = self._con.cursor()
        cur.execute(sql)
        return cur.fetchall() if cur.description else None

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass


def env_value(path, key):
    """`key` from a .env file: a BOM, `export `, spaces around '=', quotes and
    a trailing # comment are all allowed. None when the key is not there."""
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, sep, value = line.partition("=")
            if not sep or name.strip() != key:
                continue
            value = value.strip()
            if value[:1] in ("'", '"'):
                end = value.find(value[0], 1)
                return value[1:end] if end > 0 else value[1:]
            return value.split(" #", 1)[0].strip()
    return None


def _owner_url(args):
    if args.owner_env_file:
        try:
            url = env_value(args.owner_env_file, args.owner_env_key)
        except (OSError, UnicodeDecodeError) as exc:
            sys.exit(f"could not read {args.owner_env_file}: {type(exc).__name__} (it must be UTF-8)")
        if not url:
            sys.exit(f"{args.owner_env_key} not found in {args.owner_env_file}")
        return url
    url = os.getenv("PLATFORM_OWNER_DB_URL", "").strip()
    if not url:
        sys.exit("give --owner-env-file, or set PLATFORM_OWNER_DB_URL")
    return url


def _say_error(step, exc, *hide):
    text = f"{type(exc).__name__}: {exc}".splitlines()[0]
    for h in hide:
        if h:
            text = text.replace(h, "***")
    print(f"FAILED at {step}: {text}")


def _secret_state(args):
    """'exists', 'missing', or a reason gcloud cannot be used (checked before
    anything changes, so a gcloud problem never costs a password)."""
    try:
        r = _gcloud(args, "secrets", "describe", args.secret, "--format=value(name)")
    except Exception as exc:
        return f"gcloud could not run ({type(exc).__name__}: {exc})"
    if r.returncode == 0:
        return "exists"
    if "NOT_FOUND" in r.stderr or "not found" in r.stderr.lower():
        return "missing"
    return "gcloud cannot read Secret Manager: " + r.stderr.strip()[-300:]


def _version_of(output):
    tail = output.strip().splitlines()[-1].rsplit("/", 1)[-1] if output.strip() else ""
    return tail if tail.isdigit() else None


def _gcloud(args, *cmd, data=None):
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not exe:
        raise RuntimeError("gcloud not found on PATH")
    return subprocess.run([exe, *cmd, "--project", args.project], input=data, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=" ".join(__doc__.split("\n\n")[0].split()))
    ap.add_argument("--role", required=True, choices=sorted(ROLES),
                    help="which login: website_app (the forms) or website_admin (the inbox)")
    ap.add_argument("--owner-env-file", help="a .env file holding the owner connection URL")
    ap.add_argument("--owner-env-key", default="DB_URL", help="its key (default DB_URL)")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--secret", help="default: the role's own secret")
    ap.add_argument("--service-account", default=RUNTIME_SA)
    ap.add_argument("--no-secret", action="store_true", help="set and prove the password only (tests)")
    args = ap.parse_args(argv)
    role = args.role
    spec = ROLES[role]
    args.secret = args.secret or spec["secret"]
    # the inbox's URL in the forms' secret would hand the public form path a login
    # that reads leads; the forms' URL in the inbox's would break the inbox
    others = {r["secret"] for name, r in ROLES.items() if name != role}
    if args.secret in others:
        print(f"FAILED before changing anything: {args.secret} holds another login's URL")
        return 1
    print(f"Changing the password of {role}; the new URL goes to secret {args.secret}.")
    if role == "website_app" and not args.no_secret:
        print("   The live forms stop saving leads until the service is pointed at the new version:")
        print("   run the --update-secrets line printed at the end straight away.")

    owner_url = _owner_url(args)
    try:
        owner_secret = unquote(urlsplit(owner_url).password or "")
    except ValueError:
        owner_secret = ""
    password = secrets.token_urlsafe(32)
    try:
        url = app_url(owner_url, password, role)
    except ValueError as exc:
        print(f"FAILED before changing anything: {exc}")
        return 1
    verifier = scram_verifier(password)
    assert _VERIFIER.fullmatch(verifier)          # only base64, digits, $ and : go into the SQL
    hide = (password, url, owner_url, owner_secret)

    state = None
    if not args.no_secret:
        state = _secret_state(args)
        if state not in ("exists", "missing"):
            print(f"FAILED before changing anything: {state}")
            return 1

    owner = None
    try:
        owner = _Db(owner_url)
        # role comes from the fixed ROLES choices, so it is safe to put in the SQL
        exists = owner.run(f"SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname = '{role}'")[0][0]
        if not exists:
            print(f"FAILED: role {role} does not exist; apply {spec['sql']} first")
            return 1
        owner.run(f"ALTER ROLE {role} PASSWORD '{verifier}'")
        print(f"1. {role} has a new password (stored as a SCRAM verifier only)")
        # after a suspected leak the README turns logins off first; the new
        # password is in place now, so they can come back on
        if not owner.run(f"SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname = '{role}'")[0][0]:
            owner.run(f"ALTER ROLE {role} LOGIN")
            print("   logins were off; turned back on now that the password is new")
        # settings the login stored on itself (after a leak, say) survive a new
        # password: only its SQL file clears them
        stored = owner.run("SELECT s.setdatabase, s.setconfig FROM pg_catalog.pg_db_role_setting s "
                           f"WHERE s.setrole = '{role}'::regrole")
        expected = {"statement_timeout=5s", "idle_in_transaction_session_timeout=10s"}
        for db, config in stored or []:
            extra = set(config) - expected - {'search_path=""', "search_path="}
            if db != 0 or extra or not expected <= set(config):
                print(f"   warning: {role} has stored settings {spec['sql']} did not set; "
                      f"run {spec['sql'].split('/')[-1][:3]} now, then this tool again if the next step fails")
                break
    except Exception as exc:
        _say_error("setting the password", exc, *hide)
        return 1
    finally:
        if owner is not None:
            owner.close()

    app = None
    try:
        for attempt in range(4):                  # the pooler may take a moment to see it
            try:
                app = _Db(url)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(5)
        who = app.run("SELECT current_user")[0][0]
        if who != role:
            print(f"FAILED: logged in as {who}, not {role}")
            return 1
        if role == "website_app":
            if not _refused(app, "SELECT 1 FROM website_intake.submissions LIMIT 1", "reading leads", hide):
                return 1
            print(f"2. logged in as {who}; reading leads is refused, as it should be")
        else:
            app.run("SELECT 1 FROM website_intake.submissions LIMIT 1")
            for sql, what in (("UPDATE website_intake.submissions SET status = status WHERE false",
                               "changing a lead directly"),
                              ("DELETE FROM website_intake.submissions WHERE false", "deleting a lead"),
                              ("INSERT INTO website_intake.submissions (id) VALUES (NULL)", "adding a lead")):
                if not _refused(app, sql, what, hide):
                    return 1
            print(f"2. logged in as {who}; it can read leads, and changing, deleting or adding "
                  "one directly is refused, as it should be")
    except Exception as exc:
        _say_error("logging in as " + role, exc, *hide)
        return 1
    finally:
        if app is not None:
            app.close()

    if args.no_secret:
        print("3. --no-secret: nothing stored in Secret Manager")
        return 0
    try:
        if state == "exists":
            r = _gcloud(args, "secrets", "versions", "add", args.secret, "--data-file=-",
                        "--format=value(name)", data=url)
            version = _version_of(r.stdout) if r.returncode == 0 else None
        else:
            r = _gcloud(args, "secrets", "create", args.secret, "--replication-policy=automatic",
                        "--data-file=-", data=url)
            version = None
            if r.returncode == 0:
                listed = _gcloud(args, "secrets", "versions", "list", args.secret, "--limit=1",
                                 "--format=value(name)")
                version = _version_of(listed.stdout) if listed.returncode == 0 else None
        if r.returncode != 0:
            text = r.stderr.strip()
            for h in hide:
                if h:
                    text = text.replace(h, "***")
            print("FAILED storing the secret: " + text[-400:])
            return 1
        if version:
            print(f"3. stored as {args.secret} version {version}")
        else:
            print(f"3. stored in {args.secret}, but its version number could not be read: "
                  f"see gcloud secrets versions list {args.secret}")
        grant = _gcloud(args, "secrets", "add-iam-policy-binding", args.secret,
                        f"--member=serviceAccount:{args.service_account}",
                        "--role=roles/secretmanager.secretAccessor", "--format=none")
        if grant.returncode != 0:
            print("FAILED letting the service account read it: " + grant.stderr.strip()[-400:])
            return 1
    except Exception as exc:
        _say_error("storing the secret", exc, *hide)
        return 1
    print("   the Cloud Run service account may read it")
    if not version:
        return 1
    print(f"   point the website at it with: --update-secrets {spec['env']}={args.secret}:{version}")
    return 0


def _refused(con, sql, what, hide):
    """True when the statement is refused for lack of rights (and only that)."""
    try:
        con.run(sql)
    except Exception as exc:
        arg = exc.args[0] if exc.args else None
        code = getattr(exc, "pgcode", None) or (arg.get("C") if isinstance(arg, dict) else None)
        if code == "42501":
            return True
        _say_error(f"checking that {what} is refused", exc, *hide)
        return False
    print(f"FAILED: {what} is allowed for this login; run the db/verify_*.sql scripts")
    return False


if __name__ == "__main__":
    sys.exit(main())
