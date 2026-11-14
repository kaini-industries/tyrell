#!/bin/bash
set -euo pipefail
umask 022
input=${1:?Usage: build-stock-image.sh input-directory}
test "$(id -u)" = 0
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
test "${input##*/}" = input
output="${input%/input}/output"
test ! -e "$output"
mkdir "$output"
bash "$input/builder/ensure-image-deps.sh" --check
python3 "$input/image/overlay-stock.py" "$input" "$output"
image="$output/tyrell-trixie-arm64.img"
package="$input/assets/tyrell-status_0.1.0-1_arm64.deb"
python3 "$input/image/verify-image.py" "$image" --output-dir "$output" \
    --package "$package" --expected-package-sha "$(sha256sum "$package" | cut -d' ' -f1)" \
    --stock-lock "$input/image/stock-image.json" \
    --stock-evidence "$output/stock-base-verification.json"
# Verification records the raw image hash; write-manifest also streams this
# compressed payload and checks that it matches the verified image exactly.
xz -T2 -3 --keep "$image"
rm "$image"
cp "$input/image/stock-image.json" "$output/stock-image.json"
cp "$input/image-metadata.json" "$output/build-inputs.json"
cp "$input/package-manifest.json" "$output/package-manifest.json"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$output/builder-packages.tsv"
python3 "$input/image/write-manifest.py" "$output"
printf 'Stock-derived image built and verified as a file. No device or SD card was accessed.\n'
