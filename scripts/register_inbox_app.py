#!/usr/bin/env python3
"""Put YantrAI Web (the website's lead inbox) on the YantrAI platform as a tile
for platform admins, and keep it there. Run by the owner; prints no secret.

  python scripts/register_inbox_app.py --owner-env-file <platform .env holding DB_URL>
  python scripts/register_inbox_app.py --owner-env-file <...> --enable     (last step: show the tile)
  python scripts/register_inbox_app.py --owner-env-file <...> --disable    (hide it again)
  (or set PLATFORM_OWNER_DB_URL instead of --owner-env-file; add --dry-run to change nothing)

Register (the default), in order:
  1. checks gcloud first: the website's Cloud Run address, and that the inbox
     login's secret (website-admin-db-url, from set_website_login.py --role
     website_admin) exists. Nothing changes if any of this fails;
  2. in ONE transaction on the platform database:
     - refuses if Supabase's API roles (anon, authenticated) could read or change
       the app table, which would expose the signing key;
     - finds the platform's own org (the one org whose members are super_admin
       accounts; --org picks it if there are several) and reports how many
       platform admins will see the tile;
     - writes the store_agents row 'yantrai-web': a first-party remote app with no
       owner org (so the Developer Portal cannot reach it), visibility 'sadmin',
       remote_url = <the website's Cloud Run address>/admin. A NEW row gets a new
       client id and signing key; an existing one KEEPS them (re-running never
       breaks sign-ins) and has its settings put back (visibility, address, name);
     - adds the app to the platform org's installs switched OFF, so nobody sees a
       tile before the website can answer it (an existing install is left as is);
  3. stores the signing key in Secret Manager (website-inbox-key) when it is
     missing or differs, and makes the inbox's session secret
     (website-inbox-session-key) if there is none, both through gcloud's stdin;
     lets the Cloud Run service account read both (every run, so a re-run
     repairs a grant that failed);
  4. prints ONE command that points the website at all of it.

--enable: checks that the website's /_status says inbox: true, then switches the
install on: every platform admin whose active company is in the platform org sees
the tile. --disable switches it off everywhere; it needs neither gcloud nor a
clear platform org, so it works when other things do not (the inbox itself stays
reachable to anyone already signed in until their session ends).
--new-session-key: a new session secret; its printed command signs everyone out.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

try:                                           # run as a script or imported by the tests
    from set_website_login import env_value
except ImportError:                            # pragma: no cover
    from scripts.set_website_login import env_value

PROJECT = "gen-lang-client-0024674990"
REGION = "asia-south1"
SERVICE = "yantrai-website"
RUNTIME_SA = "916641724782-compute@developer.gserviceaccount.com"
PLATFORM_ORIGIN = "https://workspace.yantrailabs.com"

SLUG = "yantrai-web"
NAME = "YantrAI Web"
ICON = "\U0001F310"                             # globe
TAGLINE = "Website leads inbox"
DESCRIPTION = "Leads from yantrailabs.com: read, triage and export them. Platform admins only."

DB_SECRET = "website-admin-db-url"
KEY_SECRET = "website-inbox-key"
SESSION_SECRET = "website-inbox-session-key"

_CLIENT_ID = re.compile(r"cid_[0-9a-f]{16}")
_SIGNING_KEY = re.compile(r"sk_[0-9a-f]{48}")
_RUN_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.run\.app")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# --- the platform database -------------------------------------------------------------------
class Db:
    """One connection, in a transaction, through psycopg2 or pg8000. SQL uses :name
    parameters (lower case, and CAST, never ::) and no literal %."""

    def __init__(self, url):
        self._pg8000 = False
        try:
            import psycopg2
            self._con = psycopg2.connect(url, connect_timeout=10, application_name="register-inbox-app")
            self._con.autocommit = False
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
                ssl_context=ctx, timeout=10, application_name="register-inbox-app")
            self._pg8000 = True
            self._con.run("START TRANSACTION")

    def q(self, sql, **params):
        if self._pg8000:
            return self._con.run(sql, **params)
        cur = self._con.cursor()
        cur.execute(re.sub(r"(?<![:\w]):([a-z_]+)", r"%(\1)s", sql), params)
        return cur.fetchall() if cur.description else None

    def commit(self):
        if self._pg8000:
            self._con.run("COMMIT")
        else:
            self._con.commit()

    def close(self):
        try:
            self._con.close()                  # anything not committed is rolled back
        except Exception:
            pass


def _columns(db, table):
    return {r[0] for r in db.q("SELECT column_name FROM information_schema.columns "
                               "WHERE table_schema = 'public' AND table_name = :t", t=table)}


def api_role_exposure(db):
    """Supabase API roles that could read or change store_agents (and so see or
    replace the signing key through the Data API). Empty is the only safe answer."""
    rows = db.q("SELECT r.rolname FROM pg_catalog.pg_roles r WHERE r.rolname IN ('anon', 'authenticated') "
                "AND (has_any_column_privilege(r.oid, 'public.store_agents', 'SELECT') "
                "OR has_any_column_privilege(r.oid, 'public.store_agents', 'UPDATE') "
                "OR has_any_column_privilege(r.oid, 'public.store_agents', 'INSERT')) ORDER BY 1")
    return [r[0] for r in rows]


def platform_org(db, chosen=None):
    """(org id, None) for the platform's own org, or (None, why not)."""
    orgs = [r[0] for r in db.q("SELECT DISTINCT CAST(m.org_id AS text) FROM memberships m "
                                "JOIN accounting_users au ON au.users_id = m.user_id "
                                "WHERE au.role = 'super_admin'")]
    if chosen:
        if chosen in orgs:
            return chosen, None
        return None, f"--org {chosen} has no super_admin members"
    if len(orgs) != 1:
        return None, (f"expected exactly one platform org (an org with super_admin members), found "
                      f"{len(orgs)}: {', '.join(orgs) or 'none'}. Pick it with --org <id>")
    return orgs[0], None


def admins_report(db, org):
    (total,), = db.q("SELECT count(*) FROM accounting_users WHERE role = 'super_admin'")
    (members,), = db.q("SELECT count(*) FROM accounting_users au JOIN memberships m ON m.user_id = au.users_id "
                       "WHERE au.role = 'super_admin' AND CAST(m.org_id AS text) = :org", org=org)
    (no_uid,), = db.q("SELECT count(*) FROM accounting_users WHERE role = 'super_admin' AND users_id IS NULL")
    return total, members, no_uid


def manifest_for(remote_url, client_id, features=None):
    """What the platform's create_app writes for a remote app (its db.py), so the
    shell opens it the same way: its first view 'remote-<slug>' shows remote_url
    in the iframe."""
    view = "remote-" + SLUG
    m = {"version": 1, "agent_kind": "remote", "remote_url": remote_url, "client_id": client_id,
         "nav_groups": [{"label": NAME, "items": [{"view": view, "label": NAME, "icon": ICON}]}]}
    if features:
        m["features"] = features
    return m


def register_row(db, remote_url):
    """Write or re-assert the store_agents row. Returns (client_id, signing_key, created, has_v574)."""
    cols = _columns(db, "store_agents")
    has_v574 = {"kind", "stage", "remote_url"} <= cols
    rows = db.q("SELECT client_id, signing_key, CAST(owner_org_id AS text), publisher, manifest "
                "FROM store_agents WHERE slug = :slug FOR UPDATE", slug=SLUG)
    created = not rows
    if rows:
        cid, key, owner_org, publisher, manifest = rows[0]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        manifest = manifest or {}
        if owner_org is not None or publisher != "first-party" or manifest.get("agent_kind") != "remote" \
                or not _CLIENT_ID.fullmatch(cid or "") or not _SIGNING_KEY.fullmatch(key or ""):
            raise RuntimeError(f"store_agents already has a '{SLUG}' row that this tool did not make; "
                               "look at it before going on")
        features = manifest.get("features")
    else:
        cid, key, features = "cid_" + secrets.token_hex(8), "sk_" + secrets.token_hex(24), None
    manifest = json.dumps(manifest_for(remote_url, cid, features))
    values = dict(slug=SLUG, name=NAME, tagline=TAGLINE, description=DESCRIPTION, icon=ICON,
                  manifest=manifest, cid=cid, key=key, url=remote_url,
                  policy=json.dumps({"chargeable": False}))
    extra_cols = ", kind, stage, remote_url" if has_v574 else ""
    extra_vals = ", 'remote', 'internal', :url" if has_v574 else ""
    extra_set = ", kind = 'remote', stage = 'internal', remote_url = :url" if has_v574 else ""
    if created:
        db.q("INSERT INTO store_agents (slug, name, tagline, description, icon, category, status, publisher, "
             "token_policy, manifest, sort_order, owner_org_id, owner_user_id, visibility, client_id, signing_key"
             + extra_cols + ") VALUES (:slug, :name, :tagline, :description, :icon, 'custom', 'published', "
             "'first-party', CAST(:policy AS jsonb), CAST(:manifest AS jsonb), 500, NULL, NULL, 'sadmin', :cid, :key"
             + extra_vals + ")", **values)
    else:
        values.pop("key")
        db.q("UPDATE store_agents SET name = :name, tagline = :tagline, description = :description, icon = :icon, "
             "status = 'published', publisher = 'first-party', token_policy = CAST(:policy AS jsonb), "
             "manifest = CAST(:manifest AS jsonb), visibility = 'sadmin', client_id = :cid"
             + extra_set + " WHERE slug = :slug", **values)
    return cid, key, created, has_v574


def registered_address(db):
    """The website address the app row points at (without /admin), or None."""
    rows = db.q("SELECT manifest FROM store_agents WHERE slug = :slug", slug=SLUG)
    if not rows:
        return None
    manifest = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else (rows[0][0] or {})
    url = str(manifest.get("remote_url") or "")
    return url[:-len("/admin")] if url.endswith("/admin") else None


def ensure_install(db, org, enabled=None):
    """Make sure the platform org has the app. enabled=None leaves an existing install
    as it is (a new one starts off); True/False switches it."""
    rows = db.q("SELECT enabled FROM org_agent_installs WHERE CAST(org_id AS text) = :org AND agent_slug = :slug",
                org=org, slug=SLUG)
    if not rows:
        db.q("INSERT INTO org_agent_installs (org_id, agent_slug, enabled) VALUES (CAST(:org AS uuid), :slug, :on)",
             org=org, slug=SLUG, on=bool(enabled))
        return bool(enabled)
    if enabled is None:
        return bool(rows[0][0])
    db.q("UPDATE org_agent_installs SET enabled = :on WHERE CAST(org_id AS text) = :org AND agent_slug = :slug",
         org=org, slug=SLUG, on=enabled)
    return enabled


def disable_everywhere(db):
    """Hide the tile in every org that has it: always safe, needs no platform org."""
    rows = db.q("UPDATE org_agent_installs SET enabled = FALSE WHERE agent_slug = :slug AND enabled "
                "RETURNING CAST(org_id AS text)", slug=SLUG)
    return len(rows or [])


# --- gcloud ----------------------------------------------------------------------------------------
def _gcloud(args, *cmd, data=None):
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not exe:
        raise RuntimeError("gcloud not found on PATH")
    return subprocess.run([exe, *cmd, "--project", args.project], input=data, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _last(output):
    lines = output.strip().splitlines()
    return lines[-1].strip() if lines else ""


def _not_found(r):
    return "NOT_FOUND" in r.stderr or "not found" in r.stderr.lower()


def service_url(args):
    r = _gcloud(args, "run", "services", "describe", args.service, "--region", args.region,
                "--format=value(status.url)")
    url = _last(r.stdout).rstrip("/") if r.returncode == 0 else ""
    if not _RUN_URL.fullmatch(url):
        raise RuntimeError("could not read the website's Cloud Run address: "
                           + (r.stderr.strip()[-300:] or url or "empty answer"))
    return url


def secret_state(args, name):
    """(exists, latest version or None). A secret can exist with no version yet."""
    r = _gcloud(args, "secrets", "describe", name, "--format=value(name)")
    if r.returncode != 0:
        if _not_found(r):
            return False, None
        raise RuntimeError(f"gcloud cannot read secret {name}: " + r.stderr.strip()[-300:])
    v = _gcloud(args, "secrets", "versions", "describe", "latest", "--secret", name, "--format=value(name)")
    if v.returncode != 0:
        if _not_found(v):
            return True, None
        raise RuntimeError(f"gcloud cannot read secret {name}: " + v.stderr.strip()[-300:])
    tail = _last(v.stdout).rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise RuntimeError(f"could not read the version of secret {name}")
    return True, tail


def secret_value(args, name):
    r = _gcloud(args, "secrets", "versions", "access", "latest", "--secret", name)
    return r.stdout.strip() if r.returncode == 0 else None


def store_secret(args, name, value, exists):
    """Add `value` as a new version (creating the secret if needed); return the version."""
    if exists:
        r = _gcloud(args, "secrets", "versions", "add", name, "--data-file=-", "--format=value(name)", data=value)
    else:
        r = _gcloud(args, "secrets", "create", name, "--replication-policy=automatic", "--data-file=-", data=value)
    if r.returncode != 0:
        raise RuntimeError(f"storing secret {name} failed: " + r.stderr.strip()[-300:].replace(value, "***"))
    return secret_state(args, name)[1]


def let_service_read(args, name):
    """Idempotent: the Cloud Run service account may read the secret."""
    grant = _gcloud(args, "secrets", "add-iam-policy-binding", name,
                    f"--member=serviceAccount:{args.service_account}",
                    "--role=roles/secretmanager.secretAccessor", "--format=none")
    if grant.returncode != 0:
        raise RuntimeError(f"letting the service account read {name} failed: " + grant.stderr.strip()[-300:])


def website_inbox_state(base_url):
    """(True, '') when the website's /_status says inbox: true; else (False, why)."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/_status", timeout=15) as r:
            status = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"{base_url}/_status answered HTTP {exc.code}"
    except Exception as exc:
        return False, f"{base_url}/_status could not be read ({type(exc).__name__}: {str(exc)[:120]})"
    if status.get("inbox") is True:
        return True, ""
    return False, (f"{base_url}/_status says inbox: {json.dumps(status.get('inbox'))}. Deploy the website "
                   "and run the command the registration printed, then try again.")


# --- the run -----------------------------------------------------------------------------------------
def _owner_url(args):
    if args.owner_env_file:
        url = env_value(args.owner_env_file, args.owner_env_key)
        if not url:
            sys.exit(f"{args.owner_env_key} not found in {args.owner_env_file}")
        return url
    url = os.getenv("PLATFORM_OWNER_DB_URL", "").strip()
    if not url:
        sys.exit("give --owner-env-file, or set PLATFORM_OWNER_DB_URL")
    return url


def main(argv=None):
    ap = argparse.ArgumentParser(description=" ".join(__doc__.split("\n\n")[0].split()))
    ap.add_argument("--owner-env-file", help="the platform's .env file holding the owner connection URL")
    ap.add_argument("--owner-env-key", default="DB_URL")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--region", default=REGION)
    ap.add_argument("--service", default=SERVICE)
    ap.add_argument("--service-account", default=RUNTIME_SA)
    ap.add_argument("--platform-origin", default=PLATFORM_ORIGIN)
    ap.add_argument("--remote-url", help="register only: the website's https://...run.app address "
                                         "(default: asked of gcloud). To move the tile, register again with it")
    ap.add_argument("--org", help="the platform org's id, when the tool cannot tell which org it is")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--enable", action="store_true", help="show the tile (after the website is set up)")
    mode.add_argument("--disable", action="store_true", help="hide the tile")
    mode.add_argument("--new-session-key", action="store_true",
                      help="make a new session secret: signs everyone out once the printed command is run")
    ap.add_argument("--dry-run", action="store_true", help="do everything in a transaction, then roll it back")
    args = ap.parse_args(argv)
    if args.org and not _UUID.fullmatch(args.org):
        print("FAILED: --org must be an org id (a uuid)")
        return 1
    if args.remote_url and (args.enable or args.disable or args.new_session_key):
        print("FAILED: --remote-url is for registering only. To move the tile to a new address, run the "
              "registration with --remote-url first, then --enable.")
        return 1
    if args.new_session_key:                      # gcloud only: no database password needed
        return _new_session_key(args)

    owner_url = _owner_url(args)
    try:
        owner_secret = unquote(urlsplit(owner_url).password or "")
    except ValueError:
        owner_secret = ""
    hide = [owner_url, owner_secret]

    def fail(step, exc):
        text = f"{type(exc).__name__}: {exc}".splitlines()[0]
        for h in hide:
            if h:
                text = text.replace(h, "***")
        print(f"FAILED {step}: {text}")
        return 1

    if args.enable or args.disable:
        return _switch(args, owner_url, fail)

    # 1. gcloud first: nothing changes if it cannot be used
    try:
        base = args.remote_url.rstrip("/") if args.remote_url else service_url(args)
        if not _RUN_URL.fullmatch(base):
            raise RuntimeError(f"{base} is not a https://...run.app address")
        db_exists, db_version = secret_state(args, DB_SECRET)
        if db_version is None:
            print(f"FAILED before changing anything: secret {DB_SECRET} does not exist yet. Run "
                  "scripts/set_website_login.py --role website_admin first.")
            return 1
        key_exists, key_version = secret_state(args, KEY_SECRET)
        session_exists, session_version = secret_state(args, SESSION_SECRET)
    except Exception as exc:
        return fail("before changing anything", exc)
    remote_url = base + "/admin"

    db = None
    try:
        db = Db(owner_url)
        for table in ("store_agents", "org_agent_installs", "memberships", "accounting_users"):
            if not _columns(db, table):
                raise RuntimeError(f"table {table} not found: is this the platform database?")
        exposed = api_role_exposure(db)
        if exposed:
            print(f"FAILED before changing anything: {', '.join(exposed)} can read or change "
                  "public.store_agents through Supabase's Data API, which would expose the inbox's signing key. "
                  "The platform owner must revoke that first (REVOKE ALL ON public.store_agents FROM anon, "
                  "authenticated).")
            return 1
        org, problem = platform_org(db, args.org)
        if problem:
            print("FAILED before changing anything: " + problem)
            return 1
        total, members, no_uid = admins_report(db, org)
        cid, key, created, has_v574 = register_row(db, remote_url)
        hide.append(key)
        on = ensure_install(db, org)
        if args.dry_run:
            print(f"--dry-run: would {'create' if created else 'refresh'} '{SLUG}' -> {remote_url} "
                  f"(client id {cid}); install {'on' if on else 'off'}; nothing changed")
            return 0
        db.commit()
    except Exception as exc:
        return fail("on the platform database", exc)
    finally:
        if db is not None:
            db.close()

    print(f"1. '{SLUG}' is {'registered' if created else 'up to date'}: {remote_url}, visibility sadmin, "
          f"client id {cid}" + ("" if has_v574 else " (no kind/stage/remote_url columns on this database)"))
    print(f"   the platform org's install is {'ON' if on else 'OFF (switch it on last, with --enable)'}")
    print(f"   {total} platform admin account(s); {members} in the platform org"
          + (f"; {no_uid} without a user id, who cannot sign in to the inbox" if no_uid else ""))

    # 3. the secrets
    try:
        if key_version is None or secret_value(args, KEY_SECRET) != key:
            key_version = store_secret(args, KEY_SECRET, key, key_exists)
            print(f"2. signing key stored as {KEY_SECRET} version {key_version}")
        else:
            print(f"2. {KEY_SECRET} version {key_version} already holds the signing key")
        if session_version is None:
            session_version = store_secret(args, SESSION_SECRET, secrets.token_hex(32), session_exists)
            print(f"   session secret stored as {SESSION_SECRET} version {session_version}")
        for name in (KEY_SECRET, SESSION_SECRET):
            let_service_read(args, name)
        print("   the Cloud Run service account may read both")
        if not key_version or not session_version:
            raise RuntimeError("a secret's version number could not be read; see gcloud secrets versions list")
    except Exception as exc:
        return fail("storing the secrets", exc)

    print("3. point the website at them (one command, PowerShell or bash):")
    print(f'   gcloud run services update {args.service} --region {args.region} --project {args.project} '
          f'--update-secrets "INBOX_DB_URL={DB_SECRET}:{db_version},INBOX_SIGNING_KEY={KEY_SECRET}:{key_version},'
          f'INBOX_SESSION_KEY={SESSION_SECRET}:{session_version}" '
          f'--update-env-vars "INBOX_CLIENT_ID={cid},INBOX_PLATFORM_ORIGIN={args.platform_origin}"')
    print("   (this switches the inbox on: do not run it while the inbox is meant to be off, e.g. after "
          "--remove-secrets INBOX_SIGNING_KEY)")
    if not on:
        print("4. then show the tile: python scripts/register_inbox_app.py --owner-env-file <same file> --enable")
    return 0


def _switch(args, owner_url, fail):
    """--enable / --disable: only the platform org's install changes."""
    db = None
    try:
        db = Db(owner_url)
        if not db.q("SELECT 1 FROM store_agents WHERE slug = :slug", slug=SLUG):
            print(f"FAILED: '{SLUG}' is not registered yet; run this tool without --enable first")
            return 1
        if args.disable:
            n = disable_everywhere(db)
            if args.dry_run:
                print("--dry-run: would switch the tile off; nothing changed")
                return 0
            db.commit()
            print("The YantrAI Web tile is now OFF" + (f" (in {n} org(s))" if n else "") + ".")
            return 0
        # the address the tile will open is the registered one, so that is what is checked
        base = registered_address(db)
        if not base or not _RUN_URL.fullmatch(base):
            print("FAILED: the registered address could not be read; run the registration again first")
            return 1
        ready, why = website_inbox_state(base)
        if not ready:
            print("FAILED before changing anything: " + why)
            return 1
        org, problem = platform_org(db, args.org)
        if problem:
            print("FAILED before changing anything: " + problem)
            return 1
        total, members, _ = admins_report(db, org)
        ensure_install(db, org, enabled=True)
        if args.dry_run:
            print("--dry-run: would switch the tile on; nothing changed")
            return 0
        db.commit()
        print("The YantrAI Web tile is now ON for the platform org.")
        print(f"   {members} of {total} platform admin account(s) are in the platform org and see it "
              "while their active company is in that org.")
        return 0
    except Exception as exc:
        return fail("on the platform database", exc)
    finally:
        if db is not None:
            db.close()


def _new_session_key(args):
    """A new session secret, and a command that changes only that setting."""
    def fail(step, exc):
        print(f"FAILED {step}: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
        return 1
    try:
        exists, version = secret_state(args, SESSION_SECRET)
        if args.dry_run:
            print("--dry-run: would add a new session secret; nothing changed")
            return 0
        version = store_secret(args, SESSION_SECRET, secrets.token_hex(32), exists)
        let_service_read(args, SESSION_SECRET)
        if not version:
            raise RuntimeError("the new version number could not be read; see gcloud secrets versions list")
    except Exception as exc:
        return fail("making a new session secret", exc)
    print(f"1. new session secret stored as {SESSION_SECRET} version {version}")
    print("2. sign everyone out (changes only the session secret):")
    print(f'   gcloud run services update {args.service} --region {args.region} --project {args.project} '
          f'--update-secrets "INBOX_SESSION_KEY={SESSION_SECRET}:{version}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
