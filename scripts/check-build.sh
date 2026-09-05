#!/usr/bin/env bash
# Fail if public/ is not exactly what design/ builds.
#
# Catches the drift that silently ships stale HTML: someone edits design/,
# commits, and forgets to rebuild and stage. Run in CI and before deploying.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain -- public)" ]; then
  echo "error: public/ has uncommitted changes; commit or stash them first." >&2
  git status --short -- public >&2
  exit 1
fi

scripts/build.sh >/dev/null

if [ -n "$(git status --porcelain -- public)" ]; then
  echo >&2
  echo "error: public/ does not match a fresh build of design/." >&2
  echo "Someone changed design/ without rebuilding. Run scripts/build.sh and commit:" >&2
  echo >&2
  git status --short -- public >&2
  git --no-pager diff --stat -- public >&2
  exit 1
fi

echo "public/ matches a fresh build of design/."
