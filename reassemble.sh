#!/usr/bin/env sh
# Rebuild a trailsdb data directory from this branch: joins split files, unpacks
# the catalog and the pack exports. Usage: ./reassemble.sh /path/to/data
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=${1:?usage: reassemble.sh /path/to/data}
mkdir -p "$out/normalized" "$out/exports"

for f in "$here"/normalized/*.geojsonl.gz; do cp "$f" "$out/normalized/"; done
for first in "$here"/normalized/*.part-00; do
  [ -e "$first" ] || continue
  base=${first%.part-00}; cat "$base".part-* > "$out/normalized/$(basename "$base")"
done

cat "$here"/catalog/catalog.sqlite.gz.part-* | gunzip -c > "$out/catalog.sqlite"

for pack in "$here"/exports/*/; do
  name=$(basename "$pack"); mkdir -p "$out/exports/$name"
  cp "$pack"/*.json "$out/exports/$name/"
  for gz in "$pack"/*.geojsonl.gz; do [ -e "$gz" ] && gunzip -c "$gz" > "$out/exports/$name/$(basename "$gz" .gz)"; done
  for first in "$pack"/*.geojsonl.gz.part-00; do
    [ -e "$first" ] || continue
    base=${first%.part-00}; cat "$base".part-* | gunzip -c > "$out/exports/$name/$(basename "$base" .gz)"
  done
done
echo "data directory ready at $out"
