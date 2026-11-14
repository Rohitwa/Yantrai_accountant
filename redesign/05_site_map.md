# Ohm site map and link strategy

14 Nov 2026. Covers every link on the home draft. Built by `samples/build_site.py` into `site/`.

## Strategy

1. **One shell.** Header, menus, phone sheet and footer are generated once and stamped on every page, so a link is fixed in one place.
2. **No new writing.** Every page is assembled from material Rohit has already approved: the hero slides, the page plan v1 tables (deck wording), the company profile, the live security page, the partner page. Headings are plain labels.
3. **Same blocks everywhere.** A page is a top (label, or the product's hero slide), bordered boxes with points, a three-step row where one exists, and one button.
4. **Sign-up and log-in leave the site.** They go to the product at workspace.yantrailabs.com, as on the live site.
5. **Pricing is its own page.** It is off the home page and reached from the menu and the footer.

## Where each link goes

| Link | Where it is | Goes to | Built from |
|---|---|---|---|
| OHM logo | top strip, footer | `index.html` | home draft |
| Company menu: About, Team, Careers, Contact | top strip | `company.html` and its sections | company profile (Ramsons facts, vision, mission, values, team), live careers page, contact block |
| Ohm Accountant | top strip, Products menu, footer, home product box | `accountant.html` | Accountant hero slide; page plan v1 section 4; proposal three-step |
| Ohm Auditor | top strip, Products menu, footer, home product box | `auditor.html` | Auditor hero slide; page plan v1 section 5; proposal gates and three-step |
| Get anything done | Products menu, home product box | `command.html` | Command hero slide; deck page 9; brochure three-step |
| Partners, Become a partner | top strip, nav button, footer, phone sheet | `partners.html` | partner page draft 1 |
| Pricing | nav, footer, phone sheet | `pricing.html` | the block removed from home |
| Customers, "Companies we work with" | nav, home logo strip | `customers.html` | the seven logos and the countries line |
| Security | nav, footer | `security.html` | home data path; six signals from the profile; four questions from the live security page |
| Log in | nav, footer, phone sheet | https://workspace.yantrailabs.com/ | live site |
| Start free, Get started, Fix this free | hero, products, problems, pricing | https://workspace.yantrailabs.com/signup | page plan v1 |
| Reach out to us, Talk to us | pricing | `company.html#contact` | contact block |
| Search icon | nav | opens a search over the eleven pages | new, small |
| Privacy, Terms | footer | `privacy.html`, `terms.html` | drafts from the security facts and the payment line; need legal review |

## Left out, and why

- **Language globe.** Only English is built. It comes back when a second language exists.
- **Certifications.** ISO 27001 and SOC 2 are not on the security page until there is a certificate on file.
- **Per-customer lines and quotes.** The customers page shows logos and names only until Rohit says which customers may be quoted.
- **Team photos.** No separate image files were found; the team boxes are text.

## To confirm

- The sign-up address (`/signup`) is from the page plan; check it resolves.
- Privacy and Terms are drafts and name YantrAI Labs; counsel adds the legal entity and the liability terms.
- The partner page payout answers are still assumptions.
- The home product box no longer says "English or Hindi"; that was not a confirmed capability.
