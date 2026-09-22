# AiFA — AI Teams for Finance

The site behind **yantrailabs.com**. Flask serves one static page and one form
endpoint; everything the browser can reach lives in `public/`.

## Layout

```
public/          the site — index.html, site.css, app.js, assets/, robots.txt, sitemap.xml
main.py          routing + POST /api/savings-check
design/          the pipeline that generates public/ (see design/README.md)
Procfile         entrypoint the Cloud Run Python buildpack runs
app.yaml         the same entrypoint, kept for App Engine builds
.github/         the workflow that deploys main to Cloud Run
```

`design/` is excluded by `.gcloudignore`, so it never reaches the container.

## Running locally

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python main.py          # http://localhost:8080
```

`GET /_status` reports whether mail is configured. (Not `/healthz` — Cloud Run
intercepts that path.)

## Deploying

Cloud Run service `yantrai-website`, project `gen-lang-client-0024674990`,
region `asia-south1`. `yantrailabs.com` is mapped to it, so whatever this
service serves is what the domain serves.

**Deploy with the script. Do not run `gcloud run deploy` by hand.**

```bash
scripts/deploy.sh              # deploy, after the checks below pass
scripts/deploy.sh --dry-run    # run the checks only; needs no gcloud
```

`gcloud run deploy --source .` uploads *the folder you are standing in*. It
never reads GitHub and it will not warn you that your checkout is stale. That
is not hypothetical: a deploy once went out from a checkout two commits behind
`main`, so the new hero and article went live while the explainer video and a
removed section did not, and nothing anywhere reported a problem. Every check
in the script exists because of that deploy.

The script refuses to run unless:

| Check | Why |
|---|---|
| on `main` | the site is deployed from `main` |
| working tree clean | otherwise what ships matches no commit |
| not behind `origin/main` | the stale-checkout failure above |
| not ahead of `origin/main` | the deployed commit must exist on the remote |
| `public/` matches `design/` | `public/` is served; a stale build ships old HTML |

Then it deploys, and afterwards compares the homepage the domain returns
against `public/index.html` in your checkout, so "it deployed" is verified
rather than assumed.

### Making a push deploy itself

`.github/workflows/deploy.yml` is meant to deploy every push to `main`, and
would remove the stale-checkout problem entirely. **It has never succeeded** —
the repo has no GCP credential, so every run dies at the auth step. Until one
is added, `main` being green says nothing about what the domain is serving.

To fix it, add either:

* **`GCP_SA_KEY`** — the deploy service account's JSON key, under
  Settings → Secrets and variables → Actions → *New repository secret*; or
* **`GCP_WORKLOAD_IDENTITY_PROVIDER`** and **`GCP_DEPLOY_SERVICE_ACCOUNT`** —
  repository *variables*, for keyless auth, which is the better option.

The workflow now fails immediately with an explanation when neither is set,
instead of failing inside the auth action.

The workflow deliberately passes no `--set-env-vars`. That flag replaces the
service's entire environment, which would drop the SMTP settings the form
needs; leaving it off keeps the existing configuration across deploys.

### Line endings

If `git config core.autocrlf` is `true`, the HTML you deploy carries CRLF and
the served bytes stop matching the repo. Nothing breaks, but it makes "is the
site on this commit?" harder to answer. `git config core.autocrlf false`
followed by a fresh checkout keeps them identical. The deploy script warns.

## Changing the site

`design/` is the source; `public/` is what Cloud Run serves. Both are
committed, which means they can drift — and a drifted `public/` ships old HTML
without any error. So after **any** change under `design/`:

```bash
scripts/build.sh          # build en + fr, stage into public/
git status --short public # review what changed
```

`scripts/check-build.sh` fails if `public/` is not what `design/` builds. It
runs in CI on every push, and inside `scripts/deploy.sh` before a deploy.

The build output that lands in `design/` (`design/index.html`, `design/fr/`,
and so on) is generated and git-ignored; only `public/` is tracked.

## Domains

`yantrailabs.com` is canonical; `www.yantrailabs.com` 301s to it. The redirect
lives in `main.py`, so it holds however the domain reaches the service. Only
navigations redirect — a form POST bounced across hostnames would be a
cross-origin request the browser blocks, so the API answers on either host.

Both hostnames still have to be pointed at the service. Which way depends on
what fronts the domain:

*If Cloud Run serves the domain directly*, map both and add the records it
returns at the registrar:

```bash
for HOST in yantrailabs.com www.yantrailabs.com; do
  gcloud beta run domain-mappings create --service=yantrai-website \
    --domain=$HOST --region=asia-south1 --project=gen-lang-client-0024674990
done
gcloud beta run domain-mappings describe --domain=www.yantrailabs.com \
  --region=asia-south1 --project=gen-lang-client-0024674990
```

At GoDaddy the apex needs the `A`/`AAAA` records the mapping reports (an apex
cannot be a CNAME), and `www` a `CNAME` to `ghs.googlehosted.com`. Certificates
are issued once DNS resolves, which takes up to ~24h.

*If Firebase Hosting fronts it* — a rewrite to the Cloud Run service — then add
`www.yantrailabs.com` as a second custom domain in the Firebase console and let
it publish the GoDaddy records. Don't also create a Cloud Run mapping for it;
one owner per hostname.

Check both resolve to the same revision once DNS settles:

```bash
curl -sI https://www.yantrailabs.com/ | head -2   # expect 301 → apex
curl -s  https://yantrailabs.com/_status          # expect {"ok":true,...}
```

### One-time setup for the workflow

The workflow needs credentials for a service account that can deploy. Create
one and give it the roles a source deploy touches — Cloud Run, Cloud Build, the
Artifact Registry it pushes the image to, the bucket the source is staged in,
and permission to act as the runtime service account:

```bash
PROJECT=gen-lang-client-0024674990
SA=github-deployer@$PROJECT.iam.gserviceaccount.com

gcloud iam service-accounts create github-deployer \
  --display-name='GitHub Actions deployer' --project=$PROJECT

for ROLE in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/storage.admin; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" --role="$ROLE"
done

gcloud iam service-accounts add-iam-policy-binding \
  916641724782-compute@developer.gserviceaccount.com \
  --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountUser --project=$PROJECT
```

Then give GitHub a way to authenticate as it. Either works; the workflow picks
whichever is configured.

*Keyless (preferred — no long-lived key exists to leak).* Set up a Workload
Identity Pool for the repo, then add two **repository variables** under
Settings → Secrets and variables → Actions → Variables:

| Variable | Value |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/916641724782/locations/global/workloadIdentityPools/POOL/providers/PROVIDER` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-deployer@gen-lang-client-0024674990.iam.gserviceaccount.com` |

*Key file (simpler).* Leave `GCP_WORKLOAD_IDENTITY_PROVIDER` unset, and add the
JSON key as the **repository secret** `GCP_SA_KEY`:

```bash
gcloud iam service-accounts keys create key.json --iam-account=$SA
# paste key.json into the GCP_SA_KEY secret, then delete the local copy
rm key.json
```

## The form

`POST /api/savings-check` emails the submission to `DEMO_TO_EMAIL`. It needs
these set **on the Cloud Run service** — `app.yaml` env vars do nothing here:

| Variable | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `rohit@yantrailabs.com` |
| `DEMO_FROM_EMAIL` | `rohit@yantrailabs.com` |
| `DEMO_TO_EMAIL` | `rohit@yantrailabs.com` |
| `SMTP_PASS` | from the `smtp-pass` secret |

`SMTP_PASS` is mounted with `--set-secrets SMTP_PASS=smtp-pass:latest`. That
requires the runtime service account
(`916641724782-compute@developer.gserviceaccount.com`) to hold
`roles/secretmanager.secretAccessor` on the secret — `roles/editor` does **not**
cover Secret Manager, which is deliberate on Google's part. Granting it needs a
project owner:

```bash
gcloud secrets add-iam-policy-binding smtp-pass \
  --member='serviceAccount:916641724782-compute@developer.gserviceaccount.com' \
  --role=roles/secretmanager.secretAccessor \
  --project=gen-lang-client-0024674990
```

Without it the endpoint returns 500 `Email service not configured` and the page
falls back to showing an email address.


### Stored submissions (website_intake)

When `WEBSITE_DB_URL` is set, each accepted submission of a form listed in
`INTAKE_FORMS` is **stored first, then mailed** (`intake.py`). A stored lead is
safe whatever the mailbox does, so the visitor sees success even if the mail
fails, except for a careers application with a CV, which answers 502 because the
CV exists only in that mail. A submission that cannot be stored is logged in full
(`intake_unstored`; up to 200 an hour per instance for outages and another 200
for floods, after that only its id) and mailed as `[NOT SAVED]` with a
`REPLAY-JSON:` line (up to 30 an hour per instance for outages, 5 for floods).
Past those caps the visitor is asked to try again (503 or 429).

With the variable unset the forms mail only, as described above, with the same
hardening: what people paste around an address (`mailto:`, quotes, `<...>`,
`(...)`, a trailing `;`, `,` or `.`) is removed, and Reply-To is the visitor
only when their address is one plain ASCII address (otherwise the sender; the
address is in the body either way).

Rows live in the platform Supabase (project `vxnflumpectzqdamjqsc`), schema
`website_intake`, written by `website_app`: a login that can only INSERT there.
It cannot read a row back or change or delete one, and it has no rights on any
table in `public.*` (verify check 16; 001 itself grants nothing there). Like
every login it keeps the platform-wide defaults: reading the system catalogs,
TEMP tables and large objects. The schema is closed to `anon`, `authenticated`
and `service_role`. Never add it to the Data API's exposed schemas.

| Variable | Meaning |
|---|---|
| `WEBSITE_DB_URL` | from the `website-db-url` secret. Unset = mail only, as before |
| `WEBSITE_DB_SSLROOTCERT` | optional; replaces the URL's `sslrootcert` path. For replaying from a laptop when the secret names the Cloud Run mount path |
| `INTAKE_FORMS` | forms to store, default `savings_check`. Add `careers` only once applicant retention and consent are decided |
| `NOTIFY_EMAIL` | `1` (default) mails each stored lead; `0` stores it silently. A careers CV is always mailed |
| `IP_HASH_SALT` | optional; rows then carry an HMAC of the visitor's IP |
| `TRUSTED_XFF_HOPS` | proxies that append to `X-Forwarded-For` (default `1`); set it from the `host` and `xff_depth` the logs report. Above `1` the run.app address becomes spoofable, so first close it (`--no-default-url`) or restrict its ingress |
| `INTAKE_IP_LIMIT` | `log` (default) or `enforce` the 5-per-10-minutes per-visitor limit. In `log` mode one busy client can fill an instance's window (60 admitted in 10 minutes) and also use up the database's 300-an-hour savings guard, which then throttles every instance; other visitors are not stored until those drain (a few go out as `[NOT SAVED]`, the rest get 429). Switch to `enforce` once the logs show real visitor addresses |

**Limits.** Per visitor: 5 submissions in 10 minutes (logged, or refused with
`enforce`). Per instance: 60 submissions sent to the database in 10 minutes,
stored or not; refused requests are not counted, so the window reopens as the
oldest ages out (during an outage the overflow past 60 is logged as rate
limited and uses the flood allowance). In the database: a flood guard of about
300 savings checks and 100 careers applications an hour, shared by every
instance. The guard counts committed rows, so transactions open at the same
moment can each add up to the cap; the website inserts one row per transaction
and overshoots by at most one row per concurrent insert (2 with one instance,
up to 9 under the login's 10-connection limit).

**One-time database setup** (the project owner, as `postgres` over the session
pooler; review the SQL first). `001` is idempotent and safe to run again, and it
re-asserts its own state: it clears `website_app`'s stored settings in every
database (and stops with an error naming any a superuser stored, which only a
superuser can clear); it revokes any rights granted since on the schema, both
tables and the flood guard to PUBLIC, `anon`, `authenticated`, `service_role`
and `website_app`; and it replaces the flood guard. Grant anything new (the
inbox app, say) in a later file, to a role of its own, or re-run that file
after every run of 001.

```bash
psql "<postgres session-pooler URL>" -v ON_ERROR_STOP=1 -f db/001_intake.sql
psql "<postgres session-pooler URL>" -c "\password website_app"
psql "<postgres session-pooler URL>" -f db/verify_intake.sql   # every row of the first result must say t
```

**The secret**: the URL is
`postgresql://website_app.vxnflumpectzqdamjqsc:<password>@aws-1-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require`,
with the password percent-encoded (`python -c "import urllib.parse,getpass;print(urllib.parse.quote(getpass.getpass(),safe=''))"`).
Type it without echo, so it stays out of shell history, then prove it before
anything uses it:

```bash
read -rs DSN && printf '%s' "$DSN" | gcloud secrets create website-db-url --data-file=- --project gen-lang-client-0024674990
WEBSITE_DB_URL="$DSN" python scripts/replay_intake.py --check   # says: connected as website_app
unset DSN
gcloud secrets add-iam-policy-binding website-db-url \
  --member='serviceAccount:916641724782-compute@developer.gserviceaccount.com' \
  --role=roles/secretmanager.secretAccessor --project gen-lang-client-0024674990
```

**The certificate**: `sslmode=require` encrypts the connection but does not
check who answers. Someone able to redirect the traffic could pose as the
pooler, and pg8000 would hand them the password. To have the certificate
checked, in this order:

1. Download Supabase's root certificate (dashboard: Project Settings, Database,
   SSL configuration).
2. Prove it on Python 3.13 or later: production runs 3.14, whose TLS checks are
   stricter than 3.12's, so a pass on 3.12 proves nothing. Type a URL ending
   `sslmode=verify-full` when this waits for it:
   `read -rs DSN && WEBSITE_DB_URL="$DSN" WEBSITE_DB_SSLROOTCERT=./prod-ca-2021.crt python scripts/replay_intake.py --check; unset DSN`.
   Or check the chain as 3.14 would: save the pooler's certificates with
   `openssl s_client -starttls postgres -connect aws-1-ap-south-1.pooler.supabase.com:5432 -showcerts </dev/null`,
   then run `openssl verify -x509_strict -CAfile prod-ca-2021.crt -untrusted intermediates.pem -verify_hostname aws-1-ap-south-1.pooler.supabase.com leaf.pem`.
   If either fails, stay on `require`.
3. Store the certificate as a secret and mount it as a file (a new revision;
   nothing reads the file yet).
4. Only then add the new URL, ending
   `sslmode=verify-full&sslrootcert=/secrets/supabase-ca/root.crt`, as a new
   version (instances that start from then on and read `:latest` pick it up),
   and pin that version so every instance uses it at once.
5. Watch for `intake_unstored` whose detail names `SSLCertVerificationError` (a
   refused certificate) or `FileNotFoundError` (the mount is missing), not an
   outage. Going back: point the service at the previous version again, e.g.
   `--update-secrets WEBSITE_DB_URL=website-db-url:1`. While the service is
   pinned, adding a new version changes nothing.

```bash
gcloud secrets create supabase-ca --data-file=prod-ca-2021.crt --project gen-lang-client-0024674990
gcloud secrets add-iam-policy-binding supabase-ca \
  --member='serviceAccount:916641724782-compute@developer.gserviceaccount.com' \
  --role=roles/secretmanager.secretAccessor --project gen-lang-client-0024674990
gcloud run services update yantrai-website --region asia-south1 --project gen-lang-client-0024674990 \
  --update-secrets /secrets/supabase-ca/root.crt=supabase-ca:latest
read -rs DSN && printf '%s' "$DSN" | gcloud secrets versions add website-db-url --data-file=- --project gen-lang-client-0024674990; unset DSN
# use the version number that command printed, e.g. 2:
gcloud run services update yantrai-website --region asia-south1 --project gen-lang-client-0024674990 \
  --update-secrets WEBSITE_DB_URL=website-db-url:2
```

**Switching it on and off**: never with `--set-env-vars`, which would wipe
`SMTP_*`. If you pinned a version for the certificate, use that number instead
of `:latest`:

```bash
gcloud run services update yantrai-website --region asia-south1 --project gen-lang-client-0024674990 \
  --update-secrets WEBSITE_DB_URL=website-db-url:latest
# off again (back to mail only):
gcloud run services update yantrai-website --region asia-south1 --project gen-lang-client-0024674990 \
  --remove-secrets WEBSITE_DB_URL
```

`/_status` then reports `"db": true` and the stored forms. It says what is
configured, not that the database answers; a live check there would let anyone
open connections.

**Reading leads** before the inbox tile exists: the Supabase table editor, or as
`postgres`: `SELECT * FROM website_intake.submissions ORDER BY received_at DESC;`
Each notification mail's subject ends in `[#<first 8 characters of the id>]`.

**Replaying** a `[NOT SAVED]` mail (saved as .eml, or its text with the
`REPLAY-JSON:` line) or the `intake_unstored` log entries. Each row keeps its
id, so replaying twice is harmless. Make log exports in Git Bash (with
`PYTHONUTF8=1`) or Cloud Shell, never in Windows PowerShell 5.1: its `>` and `|`
re-decode gcloud's output with the console code page, so non-English names are
already garbled in the file, and a replayed row cannot be corrected afterwards.
UTF-8 files (with or without a BOM) and UTF-16 files are read; any other
encoding (`Set-Content`'s ANSI, say) is refused. A JSON-lines file must hold nothing but JSON: one
line of other text and nothing in it is replayed. The flood guard counts replayed
rows with live ones, so replay at most about 250 savings checks or 90 careers
applications an hour, preferably when the site is quiet.

```bash
export PYTHONUTF8=1   # on Windows, so non-English names survive the pipe
export WEBSITE_DB_URL="$(gcloud secrets versions access latest --secret website-db-url --project gen-lang-client-0024674990)"
export WEBSITE_DB_SSLROOTCERT=./prod-ca-2021.crt   # only once the URL uses verify-full
python scripts/replay_intake.py notsaved-mail.eml
gcloud logging read 'jsonPayload.event="intake_unstored"' --freshness=30d --format=json \
  --project gen-lang-client-0024674990 | python scripts/replay_intake.py -
unset WEBSITE_DB_URL WEBSITE_DB_SSLROOTCERT
```

Log events worth alerting on: `intake_unstored`, `intake_fallback_mail` with
`sent: false`, `intake_fallback_mail_capped`, `intake_mail_failed` (stored, but
nobody was told), `careers_cv_undelivered`. Worth watching: `intake_ip_over_limit`,
`intake_email_refused` (a 400 the page shows as a generic error),
`intake_body_too_large`, `intake_event_skipped`. A `website_app` password that
stops working, or one you did not change, is a sign of compromise.

What a leaked `website_app` password can still do:

- add rows: the flood guard's cap per open transaction, so up to roughly 3000
  savings checks and 1000 careers applications an hour through its 10
  connections, plus up to three delivery records for each; a transaction held
  open stamps its rows with the time it began;
- change its own password (locking the website out), or store settings on
  itself: `default_transaction_read_only = on` or a tiny `statement_timeout`,
  say, makes every website insert fail. Verify checks 9, 10, 27 and 32 between
  them fail on any such setting, and re-running 001 clears them;
- read the platform database's catalogs (every schema, table, role and function
  source, as any login can) and run expensive queries on up to 10 connections:
  `statement_timeout` is only a default, which a session may set to 0 without
  any check seeing it;
- use two defaults every login on the platform database shares: TEMP tables
  (gone at disconnect) and large objects (kept until deleted; check 31 fails if
  `website_app` owns one).

After any suspicion, in this order (an open session can change the password
itself, so it must be shut out before the password changes):

1. Stop new logins: `ALTER ROLE website_app NOLOGIN;` (leads fall back to
   `[NOT SAVED]` until step 5).
2. End its open sessions:
   `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'website_app';`
   (if `postgres` is refused, restarting the project from the Supabase
   dashboard ends every session).
3. Set a new password: `\password website_app`.
4. Re-run 001, which turns logins back on, and the verify script.
5. Store the new URL as a `website-db-url` version, prove it with
   `replay_intake.py --check`, and point the service at it
   (`--update-secrets WEBSITE_DB_URL=website-db-url:<new version>`).

Keep a disk-usage alert on the Supabase project. Closing TEMP or large objects
is a platform decision (it affects every login); both are listed right after
the checks in the verify output.

**Tests**: `pip install -r requirements.txt pytest && python -m pytest tests`.
The database tests run only against a disposable **local** Postgres 17: set
`INTAKE_TEST_PG_ADMIN` to the URL of a superuser that is not named `postgres`
(a cluster made with `initdb -U pgsuper`, say) and `INTAKE_TEST_PSQL` to the
path of psql. The tests create Supabase-like roles, including a non-superuser
`postgres`, and leave `intake_it_*` databases behind, so use a cluster made only
for this. Runs on the same cluster take turns (an advisory lock in its
`postgres` database).

## Changing the page

The page is generated from a Claude Design canvas, not hand-edited. Edit
`design/src/*`, then:

```bash
cd design
AIFA_FORM_ENDPOINT=/api/savings-check AIFA_SITE_URL=https://yantrailabs.com python3 build.py
cp index.html site.css app.js ../public/
```

Full details, including how to re-capture after a canvas change, in
`design/README.md`.
