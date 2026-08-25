# AiFA — AI Teams for Finance (marketing site)

Static site built from the Claude Design canvas project
**Form submission request → `AiFA Hero White.dc.html`**.

## Why there is a build step

The `.dc.html` artboard is not a website: it is a component for the design-canvas
runtime (`support.js`), which pulls React, ReactDOM and Babel-standalone from
unpkg at page load and compiles the component in the browser. Shipping that
as-is means ~2 MB of third-party JS, a blank page until it finishes, no HTML for
crawlers, and a hard dependency on unpkg staying up.

So the canvas output is pre-rendered once, and its behaviour re-implemented in
~380 lines of dependency-free JS.

## Pipeline

```
design-source/AiFA Hero White.dc.html   canvas component (source of truth)
  └─ hooks added ─────────────────────► _tpl_hooked.html
       └─ rendered by support.js ─────► _captures/w1440.html  (DOM snapshot)
            └─ build.py ──────────────► index.html + site.css + app.js
```

`src/head.html`, `src/site.css`, `src/app.js` are hand-maintained.
`build.py` assembles them with the snapshot.

* **Changed the copy/CSS/JS?** `python3 build.py`
* **Changed the design in the canvas?** re-capture first (below), then build.

### Re-capturing after a canvas change

1. Pull the new `.dc.html` into `design-source/`.
2. Re-apply the `data-*` hooks (`data-stage`, `data-ring-agent`, `data-gate-*`, …)
   — these give `app.js` stable selectors; see the patch list in git history.
3. Serve this directory and open `_tpl_hooked.html`, then POST
   `#dc-root`'s first child's `innerHTML` to `/_capture?w1440.html`
   and the last `<style>` to `/_capture?helmet.css`, and the runtime's
   CSSOM-only stylesheet to `/_capture?hover.css`. `_devserver.py` handles those POSTs.
4. `python3 build.py`

Capture at **1440px** — `build.py` assumes the wide-tier layout and `site.css`
re-expresses the canvas's JS-computed responsive values as media queries.

## What app.js does

Everything the canvas component did at runtime:

* scroll-scrubbed agent ring (`#agents`) — stage, lit agents, arc, colour ramp
* scroll-scrubbed metric gate stack (`#proof`)
* trust-band reveal, hero `--q`, logo marquee pause on hover (CSS)
* the explainer video: lazy `src`, plays on hover when in view (pointer devices)
  or whenever in view (touch), plus the sound toggle

## Deviations from the artboard (deliberate)

* **Mobile.** The artboard is a 1440px desktop design; below 900px the agent dial
  is laid out linearly (chips wrap under the task label) and the metric gates
  stack. Nav collapses to a disclosure below 1020px.
* **Touch video.** No hover on touch, so the clip autoplays muted while in view
  and the "hover to play" hint is hidden.
* **`content-visibility`** removed from `#agents`/`#proof` — it made Chrome skip
  painting when the viewport jumps into the middle of a pinned track.

## Notes

* Every link resolves. The footer's section links are routed to on-page anchors
  by `ROUTES` in `app.js`; "About YantrAI Labs"/"Careers" go to `/yantrai`,
  "Log in" to `workspace.yantrailabs.com`. There are no standalone pages behind
  them yet — if you build e.g. a real security or careers page, point them there.
* `AIFA_FORM_ENDPOINT` is empty in a plain build, and the form then refuses to
  submit and tells the visitor to email instead, rather than dropping a lead.

## Local preview

`_devserver.py` on :8555 (static + the `/_capture` POST sink used by the
pre-render step). `./shoot.sh out.png 390 844 "sec=agents&frac=0.75"` renders a
viewport screenshot with headless Chrome.

## Deploy

This site ships as the **default page of yantrailabs.com**, which is a Flask app
on Google App Engine (GCP project `yantraivisionos`, region `asia-south1`),
source at `YantrAILabs/Yantrai_new`. Pushing to GitHub publishes nothing on its
own — App Engine deploys with `gcloud app deploy`.

Layout in that repo:

```
/            -> aifa/index.html      (this site)
/yantrai     -> index.html           (the company site, files untouched at root)
/api/savings-check                   (this site's form -> SMTP -> rohit@yantrailabs.com)
/api/book-demo                       (the company site's form, unchanged)
```

Because the files live under `aifa/` but are served at `/`, build with the
prefix set:

```bash
AIFA_BASE=aifa \
AIFA_FORM_ENDPOINT=/api/savings-check \
AIFA_SITE_URL=https://yantrailabs.com \
python3 build.py
cp index.html site.css app.js ~/Desktop/memory/Yantrai_new/aifa/
rsync -a assets/ ~/Desktop/memory/Yantrai_new/aifa/assets/
```

Then from `Yantrai_new`: `gcloud config set project yantraivisionos && gcloud app deploy`.

Running plain `python3 build.py` (no env vars) rebuilds the root-relative
version used for local preview in this directory.
