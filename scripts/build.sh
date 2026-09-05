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
FILES=(index.html site.css app.js page.css)
DIRS=(about agents careers for fr integrations research security what-it-found workflows)

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

echo "==> done. Review with: git status --short public"
