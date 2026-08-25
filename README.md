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

**A push to `main` deploys.** `.github/workflows/deploy.yml` builds the source
with the Python buildpack and rolls out a new revision. `Run workflow` on the
Actions tab deploys any other branch by hand.

By hand, from a checkout:

```bash
gcloud run deploy yantrai-website --source . \
  --region asia-south1 --project gen-lang-client-0024674990
```

The workflow deliberately passes no `--set-env-vars`. That flag replaces the
service's entire environment, which would drop the SMTP settings the form
needs; leaving it off keeps the existing configuration across deploys.

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
