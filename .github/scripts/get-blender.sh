#!/usr/bin/env bash
# Download and unpack Blender into ./blender, trying several mirrors
# (download.blender.org sometimes answers CI runners with an HTML page).
set -euo pipefail
file="blender-${BLENDER_VERSION}-linux-x64.tar.xz"
path="release/Blender${BLENDER_VERSION%.*}/${file}"
for base in \
    https://download.blender.org \
    https://mirrors.ocf.berkeley.edu/blender \
    https://ftp.nluug.nl/pub/graphics/blender \
    https://mirror.clarkson.edu/blender; do
  echo "Trying $base"
  if curl -fsSL --retry 3 -o "$file" "$base/$path" && xz -t "$file" 2>/dev/null; then
    mkdir -p blender
    tar -xJf "$file" -C blender --strip-components=1
    rm "$file"
    exit 0
  fi
done
echo "Could not download $file" >&2
exit 1
