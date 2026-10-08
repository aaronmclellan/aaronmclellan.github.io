#!/usr/bin/env bash
# Publish a directory as the only commit on the cs-data branch. Force-pushing a
# fresh root commit each time keeps the branch from growing a history.
set -euo pipefail
cd "${1:-out}"
if [ ! -s cs2.json ]; then
  echo "No new snapshot; leaving the cs-data branch as it is."
  exit 0
fi
rm -rf .git
git init -q -b cs-data
git add -A
git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit -qm "CS2 data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" cs-data
rm -rf .git
