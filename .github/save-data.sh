#!/bin/bash
# Kaysjjel aso.db o docs/data f branch "data" (commit wa7d bla historique).
# Kaytsta3mel f lekher d kol run o f sauvegardes dyal nos run (ASO_CHECKPOINT).
set -e
cd "${GITHUB_WORKSPACE:-.}"
out=$(mktemp -d)
mkdir -p "$out/docs"
cp aso.db "$out/"
cp -r docs/data "$out/docs/"
cd "$out"
git init -q -b data
git add -A
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -qm update
git push -qf "https://x-access-token:${TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
rm -rf "$out"
