#!/usr/bin/env bash
# Deploy yantrailabs.com from this checkout, with the checks that stop us
# shipping the wrong commit.
#
# `gcloud run deploy --source .` uploads THIS FOLDER. It does not read GitHub
# and will not tell you the checkout is stale — that is how a build two commits
# behind main once went live. Every guard below exists because of that.
#
#   scripts/deploy.sh              deploy after all checks pass
#   scripts/deploy.sh --dry-run    run the checks, print the command, deploy nothing
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE=yantrai-website
REGION=asia-south1
PROJECT=gen-lang-client-0024674990
SITE=https://yantrailabs.com/
BRANCH=main

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

fail() { echo "error: $*" >&2; exit 1; }

command -v git >/dev/null || fail "git is not installed."
# Only a real deploy needs gcloud, so --dry-run stays runnable anywhere.
if ! $DRY_RUN; then
  command -v gcloud >/dev/null || fail "gcloud is not installed. See https://cloud.google.com/sdk/docs/install"
fi

# 1. the right branch
current=$(git rev-parse --abbrev-ref HEAD)
[ "$current" = "$BRANCH" ] || fail "on branch '$current', expected '$BRANCH'. The site is deployed from $BRANCH."

# 2. nothing uncommitted — otherwise what ships is not what any commit says
[ -z "$(git status --porcelain)" ] || {
  git status --short >&2
  fail "working tree is not clean. Commit or stash before deploying."
}

# 3. in step with the remote, in both directions
echo "==> fetching origin/$BRANCH"
git fetch --quiet origin "$BRANCH"
read -r ahead behind < <(git rev-list --left-right --count "HEAD...origin/$BRANCH" | tr '\t' ' ')
[ "$behind" = "0" ] || fail "$behind commit(s) behind origin/$BRANCH. Run: git pull"
[ "$ahead"  = "0" ] || fail "$ahead commit(s) ahead of origin/$BRANCH. Push first, so the deployed commit exists on the remote."

# 4. public/ is what design/ builds — the site serves public/, not design/
echo "==> verifying public/ matches design/"
scripts/check-build.sh

# 5. line endings, which silently change every byte of the deployed HTML
if [ "$(git config --get core.autocrlf || echo false)" = "true" ]; then
  echo "warning: core.autocrlf=true — the deployed HTML will carry CRLF line endings." >&2
  echo "         Harmless, but the served bytes stop matching the repo. Consider:" >&2
  echo "           git config core.autocrlf false && git checkout -- ." >&2
fi

SHA=$(git rev-parse HEAD)
echo
echo "  service : $SERVICE ($REGION, $PROJECT)"
echo "  commit  : $SHA"
echo "  subject : $(git log -1 --pretty=%s)"
echo

if $DRY_RUN; then
  echo "--dry-run: checks passed. Would run:"
  echo "  gcloud run deploy $SERVICE --source . --region $REGION --project $PROJECT --platform managed --allow-unauthenticated --quiet"
  exit 0
fi

# No --set-env-vars on purpose: that flag replaces the service's whole
# environment and would drop the SMTP settings the savings-check form needs.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT" \
  --platform managed \
  --allow-unauthenticated \
  --quiet

# 6. prove the live site is serving this commit, rather than assuming it
echo
echo "==> verifying $SITE serves this build"
sha_cmd() { if command -v sha256sum >/dev/null; then sha256sum; else shasum -a 256; fi; }
# CRLF is stripped from both sides: some checkouts store the file with CRLF,
# and that would otherwise differ from the repo byte-for-byte for no reason.
local_hash=$(tr -d '\r' < public/index.html | sha_cmd | cut -d' ' -f1)
live_hash=$(curl -fsSL "$SITE" | tr -d '\r' | sha_cmd | cut -d' ' -f1)

if [ "$local_hash" = "$live_hash" ]; then
  echo "OK — $SITE is serving commit $SHA"
else
  echo "warning: the homepage served does not match public/index.html in this checkout." >&2
  echo "  local $local_hash" >&2
  echo "  live  $live_hash" >&2
  echo "A CDN or Firebase Hosting cache can take a minute; re-check before worrying:" >&2
  echo "  curl -s $SITE | tr -d '\\r' | shasum -a 256" >&2
  exit 1
fi
