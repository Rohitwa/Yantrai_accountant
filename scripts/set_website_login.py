#!/usr/bin/env python3
"""Give website_app a new password and store its connection URL in Secret
Manager, without the password ever being shown, typed, written or logged.

  python scripts/set_website_login.py --owner-env-file <.env holding DB_URL>
  (or set PLATFORM_OWNER_DB_URL to the project's postgres session-pooler URL)

What it does, in order:
  1. makes a random password in memory;
  2. sets it on website_app as a SCRAM-SHA-256 verifier, as psql's \\password
     does, so the password itself never reaches the server or its logs;
  3. proves the login works (connects as website_app) and that it still cannot
     read a lead;
  4. adds the URL as a new version of the website-db-url secret (creating the
     secret if needed) through gcloud's stdin, and lets the Cloud Run service
     account read it;
  5. prints the new version number, to pin with
     --update-secrets WEBSITE_DB_URL=website-db-url:<version>.

It prints neither the password nor the URL. Every run sets a NEW password:
a site already using website-db-url keeps the old one until it is pointed at
the new version. If it fails while logging in, wait two minutes before running
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
SECRET = "website-db-url"
RUNTIME_SA = "916641724782-compute@developer.gserviceaccount.com"
ROLE = "website_app"
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


def app_url(owner_url, password):
    """The website_app URL on the same host, port and database as the owner's.
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
    user = ROLE + (owner[owner.index("."):] if "." in owner else "")
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--owner-env-file", help="a .env file holding the owner connection URL")
    ap.add_argument("--owner-env-key", default="DB_URL", help="its key (default DB_URL)")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--secret", default=SECRET)
    ap.add_argument("--service-account", default=RUNTIME_SA)
    ap.add_argument("--no-secret", action="store_true", help="set and prove the password only (tests)")
    args = ap.parse_args(argv)

    owner_url = _owner_url(args)
    try:
        owner_secret = unquote(urlsplit(owner_url).password or "")
    except ValueError:
        owner_secret = ""
    password = secrets.token_urlsafe(32)
    try:
        url = app_url(owner_url, password)
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
        exists = owner.run(f"SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname = '{ROLE}'")[0][0]
        if not exists:
            print(f"FAILED: role {ROLE} does not exist; apply db/001_intake.sql first")
            return 1
        owner.run(f"ALTER ROLE {ROLE} PASSWORD '{verifier}'")
        print(f"1. {ROLE} has a new password (stored as a SCRAM verifier only)")
        # after a suspected leak the README turns logins off first; the new
        # password is in place now, so they can come back on
        if not owner.run(f"SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname = '{ROLE}'")[0][0]:
            owner.run(f"ALTER ROLE {ROLE} LOGIN")
            print("   logins were off; turned back on now that the password is new")
        # settings the login stored on itself (after a leak, say) survive a new
        # password: only db/001_intake.sql clears them
        stored = owner.run("SELECT s.setdatabase, s.setconfig FROM pg_catalog.pg_db_role_setting s "
                           f"WHERE s.setrole = '{ROLE}'::regrole")
        expected = {"statement_timeout=5s", "idle_in_transaction_session_timeout=10s"}
        for db, config in stored or []:
            extra = set(config) - expected - {'search_path=""', "search_path="}
            if db != 0 or extra or not expected <= set(config):
                print(f"   warning: {ROLE} has stored settings db/001_intake.sql did not set; "
                      "run 001 now, then this tool again if the next step fails")
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
        if who != ROLE:
            print(f"FAILED: logged in as {who}, not {ROLE}")
            return 1
        try:
            app.run("SELECT 1 FROM website_intake.submissions LIMIT 1")
            print(f"FAILED: {who} can read leads; run db/verify_intake.sql")
            return 1
        except Exception as exc:                  # only "permission denied" is the right answer
            arg = exc.args[0] if exc.args else None
            code = getattr(exc, "pgcode", None) or (arg.get("C") if isinstance(arg, dict) else None)
            if code != "42501":
                _say_error("checking that leads cannot be read", exc, *hide)
                return 1
        print(f"2. logged in as {who}; reading leads is refused, as it should be")
    except Exception as exc:
        _say_error("logging in as " + ROLE, exc, *hide)
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
    print(f"   switch the website on with: --update-secrets WEBSITE_DB_URL={args.secret}:{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
