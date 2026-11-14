#!/usr/bin/env bash
# Build the site and stage it into public/.
#
# public/ is what Cloud Run serves; design/ is where it is generated from.
# Both are committed, so they can drift — edit design/ without running this and
# the site ships the old HTML. Run this after any change under design/.
set -euo pipefail
cd "$(dirname "$0")/.."

# Same endpoint the committed app.js carries; the build bakes it in.
export AIFA_FORM_ENDPOINT="${AIFA_FORM_ENDPOINT:-/api/savings-check}"

echo "==> building en"
python3 design/build.py
echo "==> building fr"
AIFA_LOCALE=fr python3 design/build.py

# The build writes its output into design/ alongside its sources; these are the
# generated names. Keep in step with the locales and PAGES in design/build.py.
#
# The English home, About, Pricing and Security are no longer staged from here:
# the redesign (redesign/src/build_site.py, below) writes those addresses. The
# build still produces them under design/, and the French tree still uses them.
FILES=(site.css app.js page.css)
DIRS=(agents careers for fr integrations privacy research terms what-it-found workflows)

# The addresses the redesign owns. public/index.html is its home page.
REDESIGN=(about accountant auditor command customers partners pricing security)

echo "==> staging into public/"
for f in "${FILES[@]}"; do
  cp -p "design/$f" "public/$f"
done
for d in "${DIRS[@]}"; do
  # Replace rather than merge, so a page dropped from the build is dropped from
  # the site too. cp -R rather than rsync: this has to run on a stock macOS.
  rm -rf "public/$d"
  cp -R "design/$d" "public/$d"
done

echo "==> building the redesigned pages into public/"
for d in "${REDESIGN[@]}"; do
  rm -rf "public/$d"
done
# After the staging above on purpose: it reads public/fr to name the French
# twin of a page, and checks every address it links to exists in public/.
OHM_LIVE=1 python3 redesign/src/build_site.py

echo "==> done. Review with: git status --short public"
