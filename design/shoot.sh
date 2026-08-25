#!/bin/bash
# Dev helper: render a viewport screenshot of the built site with headless Chrome.
#   ./shoot.sh <out.png> <width> <height> ["sec=agents&frac=0.4" | "y=1200"]
# Renders index.html inside _shot.html's iframe (see the note in that file).
# Dev-only; not deployed.
set -e
cd "$(dirname "$0")"
CH="$HOME/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell"
OUT="$1"; W="${2:-1440}"; H="${3:-900}"; Q="$4"


D=$(mktemp -d)
mkdir -p "$(dirname "$OUT")"
"$CH" --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir="$D" \
  --window-size="$W,$H" --virtual-time-budget=9000 --screenshot="$OUT" \
  "http://localhost:8555/_shot.html?w=$W&h=$H&$Q" >/dev/null 2>&1
rm -rf "$D"
echo "$OUT"
