#!/usr/bin/env bash
# Publish the share-clean files of the last commit to the public GitHub repo.
#   tools/publish_public.sh            update the public code
#   tools/publish_public.sh 1.0.0      ...and tag v1.0.0: GitHub then builds the apps and makes a release
# git archive honours .gitattributes export-ignore, so CLAUDE.md, research notes etc. never leave this
# machine. Lines in .publish_blocklist (gitignored: names, addresses...) must not appear in any
# published file, or nothing is pushed. Public commits use the GitHub no-reply e-mail address.
set -euo pipefail
cd "$(dirname "$0")/.."
public="${PUBLIC_REPO:-fernandocc/tcg-catalog}"
dir=build/public
if [ ! -d "$dir/.git" ]; then
  rm -rf "$dir"
  gh repo clone "$public" "$dir" 2>/dev/null || { mkdir -p "$dir"; git -C "$dir" init -q -b main; git -C "$dir" remote add origin "https://github.com/$public.git"; }
fi
git -C "$dir" fetch -q origin 2>/dev/null && git -C "$dir" reset -q --hard origin/main 2>/dev/null || true
find "$dir" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git archive HEAD | tar -x -C "$dir"

if [ -s .publish_blocklist ]; then
  if grep -rIniF -f <(grep -v '^\s*$' .publish_blocklist) --exclude-dir=.git "$dir"; then
    echo "Personal text found in the files above -- nothing was published." >&2; exit 1
  fi
fi

login=$(gh api user -q .login); id=$(gh api user -q .id)
git -C "$dir" config user.name "$login"
git -C "$dir" config user.email "$id+$login@users.noreply.github.com"
git -C "$dir" add -A
git -C "$dir" diff --cached --quiet || git -C "$dir" commit -q -m "Update to $(git rev-parse --short HEAD)"
git -C "$dir" push -q origin HEAD:main
if [ -n "${1:-}" ]; then
  git -C "$dir" tag -a "v$1" -m "TCG Catalog $1"
  git -C "$dir" push -q origin "v$1"
  echo "Tagged v$1 -- builds: https://github.com/$public/actions"
fi
echo "Published to https://github.com/$public"
