# Ohm site redesign (draft 29)

A draft of the new Ohm site. **Nothing here is served.** `main.py` only serves
`public/`, and `redesign/` is listed in `.gcloudignore`, so this folder never
reaches the Cloud Run container. Merging this branch does not change
yantrailabs.com.

## What is here

```
site/            eleven built pages, each self-contained (CSS, JS and logos inline)
site/video/      links to the four films already in public/assets/ (ohm-invite*.mp4)
src/             build_site.py and the blocks it assembles
05_site_map.md   the link strategy: what every link opens, what was left out and why
```

Pages: `index`, `accountant`, `auditor`, `command`, `pricing`, `customers`,
`security`, `partners`, `company`, `privacy`, `terms`.

## Looking at it

```bash
python3 -m http.server 8331 --directory redesign/site
```

Then open http://localhost:8331. Sign-up and log-in buttons leave the site for
workspace.yantrailabs.com.

## Rebuilding

```bash
python3 redesign/src/build_site.py
```

The script writes into `redesign/site/`. It reads the customer and ERP logos
from `~/Desktop/memory/aifa_site/assets`, which is not in this repository, so a
rebuild only works on a machine that has that folder.

## Before it can go live

This draft is not wired into `public/`. Going live needs decisions first:

- Which live pages it replaces. The live site also has `/agents`, `/for`,
  `/workflows`, `/research`, `/integrations`, `/what-it-found`, `/careers` and
  the French tree under `/fr`, none of which exist in the draft.
- Addresses. The draft uses flat files (`pricing.html`); the live site uses
  folders (`/pricing/`). `sitemap.xml` and the locale routing in `main.py`
  follow the live layout.
- Privacy and Terms are short drafts marked "Draft, pending legal review".
- Partner earnings rest on assumed shares (20% of licence, 40% of
  implementation and maintenance). Confirm before publishing.
- "Become a partner" has no destination yet (form or WhatsApp).
