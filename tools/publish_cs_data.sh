#!/usr/bin/env bash
# Publish a directory as the only commit on the cs-data branch. Each publish is
# a fresh root commit, so the branch never grows a history; files that didn't
# change are reused, so a push only uploads what's new.
set -euo pipefail
cd "${1:-out}"
if [ ! -s cs2.json ]; then
  echo "No snapshot to publish."
  exit 0
fi
[ -d .git ] || git init -q -b cs-data
git add -A
tree=$(git write-tree)
if [ "$(git rev-parse -q --verify 'HEAD^{tree}' || true)" = "$tree" ]; then
  echo "Snapshot unchanged."
  exit 0
fi
commit=$(git -c user.name='github-actions[bot]' \
             -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
             commit-tree "$tree" -m "CS2 data $(date -u +%Y-%m-%dT%H:%M:%SZ)")
git update-ref HEAD "$commit"
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$commit:refs/heads/cs-data"
