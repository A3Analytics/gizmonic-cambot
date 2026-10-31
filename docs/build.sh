#!/usr/bin/env bash
# Renders each Markdown source to a self-contained HTML file and a Letter PDF.
# Needs pandoc 3.x and Google Chrome (or set CHROME to a Chromium binary).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
src="$here/src"
out="$here"
chrome="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
profile="$(mktemp -d)"
trap 'rm -rf "$profile"' EXIT

docs=(
  "$src/whitepaper.md:witness-whitepaper"
  "$src/astra-mechanical-spec.md:witness-astra-mechanical-spec"
  "$src/firmware-repo-plan.md:witness-firmware-repo-plan"
  "$here/../fixr/SETUP.md:witness-setup-guide"
)

for entry in "${docs[@]}"; do
  input="${entry%%:*}"
  name="${entry##*:}"
  toc=()
  if [[ "$name" != "witness-whitepaper" ]]; then
    toc=(--toc --toc-depth=2)
  fi
  pandoc "$input" \
    --from=markdown+yaml_metadata_block \
    --to=html5 \
    --standalone \
    --embed-resources \
    --resource-path="$src:$here" \
    --css="$src/style.css" \
    --metadata=lang:en \
    ${toc[@]+"${toc[@]}"} \
    --output="$out/$name.html"

  "$chrome" --headless=new --disable-gpu --no-first-run \
    --user-data-dir="$profile" \
    --no-pdf-header-footer \
    --print-to-pdf="$out/$name.pdf" \
    "file://$out/$name.html" 2>/dev/null

  echo "built $name.html $name.pdf"
done
