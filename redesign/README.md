# Ohm site redesign

The redesigned English pages of yantrailabs.com. `redesign/src/build_site.py`
writes them into `public/`; `scripts/build.sh` runs it after staging the pages
that still come from `design/`. `redesign/` itself is in `.gcloudignore` and
never reaches the container: only what it writes into `public/` is served.

## What the redesign owns

| Address | Page |
|---|---|
| `/` | Home |
| `/accountant`, `/auditor`, `/command` | The three product pages |
| `/pricing` | Pricing |
| `/customers` | Customers |
| `/security` | Security |
| `/partners` | Partners, with the earning calculator |
| `/about` | Company: about, vision and mission, team, careers, contact |

Everything else in `public/` still comes from `design/`: `/agents`, `/for`,
`/workflows`, `/integrations`, `/research`, `/what-it-found`, `/careers`,
`/privacy`, `/terms` and the whole French tree under `/fr`. Those pages keep the
previous look, menu and footer.

## Building

```bash
scripts/build.sh          # design/ first, then the redesign, into public/
scripts/check-build.sh    # fails if public/ is not exactly what that builds
```

`build_site.py` with `OHM_LIVE=1` reads its images from `public/assets`, turns
every link into a folder address, writes a full `<head>` for each page, and
stops if any address a page points at is missing from `public/`. Without
`OHM_LIVE` it builds a flat draft into `redesign/site/` and needs a logo folder
that is not in this repository; that mode is for drafting elsewhere.

`05_site_map.md` is the link strategy the pages were built to.

## What changed for visitors, and what is still open

- **The savings-check form is no longer on the English home page.** The older
  pages link to `/#book` 191 times; the new home sends that address to
  `/about#contact`. `/api/savings-check` is untouched and the French home still
  has the form. The other old home addresses (`/#how`, `/#agents`,
  `/#integration`, `/#problem`, `/#proof`) are sent to the nearest new section.
- **The legal-entity line is not in the new footer.** The older pages still say
  "YantrAI is a brand of LuxeDeco Ventures Private Limited" with the registered
  office; `design/build.py` notes that Meta matches the website to the company
  it verifies. It was removed from the redesign on the owner's instruction.
  Decided on 15 Nov 2026: it stays out. Do not add it back to the new footer.
- **The careers form at `/careers` is not in the new menu.** The Company page
  has a Careers section with an address to write to; the form page still exists.
- **No share image.** `og-cover.jpg` is the earlier AiFA cover, so the new pages
  carry no `og:image` until a new one is made.
- **French.** A first-time visitor whose browser asks for French is still sent
  to `/fr/` from `/`, `/about` and `/security`, and sees the previous design.
- Partner earnings rest on assumed shares (20% of licence, 40% of
  implementation and maintenance), and "Become a partner" has no form behind it.
- The short Privacy and Terms drafts in `build_site.py` are not published; the
  live `/privacy` and `/terms` stay as they are.
