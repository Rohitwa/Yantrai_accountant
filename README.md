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
