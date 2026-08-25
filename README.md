# AiFA — AI Teams for Finance

The site behind **yantrailabs.com**. Flask serves one static page and one form
endpoint; everything the browser can reach lives in `public/`.

## Layout

```
public/          the site — index.html, site.css, app.js, assets/, robots.txt, sitemap.xml
main.py          routing + POST /api/savings-check
design/          the pipeline that generates public/ (see design/README.md)
app.yaml         entrypoint for the Cloud Run Python buildpack
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

```bash
gcloud run deploy yantrai-website --source . \
  --region asia-south1 --project gen-lang-client-0024674990
```

Cloud Run service `yantrai-website`, project `gen-lang-client-0024674990`,
region `asia-south1`. `yantrailabs.com` is mapped to it. Nothing deploys on a
git push yet — that is still to be set up.

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
