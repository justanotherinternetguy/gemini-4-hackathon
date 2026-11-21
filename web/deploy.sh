#!/usr/bin/env bash
# Build the site and publish it to the gh-pages branch, served at https://routr.internetguy.dev
#
#   web/deploy.sh
#
# Publishes only the built page, style, script and img/ (never logs). The branch holds nothing but
# the site, so each deploy replaces it. GitHub Pages serves the branch with the CNAME below.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-routr.internetguy.dev}"
cd "$ROOT"
.venv/bin/python -m web.build
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
cp -r runs/web/index.html runs/web/style.css runs/web/app.js runs/web/img "$OUT/"
echo "$DOMAIN" > "$OUT/CNAME"
touch "$OUT/.nojekyll"
REMOTE="$(git remote get-url origin)"
cd "$OUT"
git init -q -b gh-pages
git add -A
git -c user.name="$(git -C "$ROOT" config user.name)" -c user.email="$(git -C "$ROOT" config user.email)" \
  commit -q -m "Deploy routr site $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q -f "$REMOTE" gh-pages
echo "pushed gh-pages ($(du -sh . | cut -f1)) -> https://$DOMAIN"
