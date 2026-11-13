#!/bin/bash
set -euo pipefail
umask 022
input=${1:?Usage: build-package.sh source-directory}
work=/var/tmp/tyrell-build
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
mkdir -p "$work/deps" "$work/artifacts"
# Recreate only this project's generated trees so deleted sources/dependencies
# and previous package staging cannot leak into the next build.
rm -rf "$work/cmake" "$work/deps" "$work/artifacts"
mkdir -p "$work/deps" "$work/artifacts"

readarray -t locked < <(python3 - "$input" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); lock=json.loads((p/'locks/build-inputs.json').read_text()); source=json.loads((p/'source-manifest.json').read_text())
print(lock['lvgl']['commit']); print(lock['lvgl']['sha256']); print(lock['source_date_epoch']); print(source['source_revision'])
PY
)
commit=${locked[0]}
expected=${locked[1]}
export SOURCE_DATE_EPOCH=${locked[2]}
revision=${locked[3]}
export TZ=UTC LC_ALL=C.UTF-8
archive="$input/downloads/lvgl-$commit.tar.gz"
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$work/deps" --no-same-owner
cmake -S "$input/packages/tyrell-status" -B "$work/cmake" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DLVGL_SOURCE_DIR="$work/deps/lvgl-$commit" \
    -DTYRELL_BUILD_REVISION="$revision" \
    -DTYRELL_ENABLE_SDL=OFF -DBUILD_TESTING=ON
cmake --build "$work/cmake" --parallel 4
ctest --test-dir "$work/cmake" --output-on-failure | tee "$work/artifacts/tests.txt"
cpack --config "$work/cmake/CPackConfig.cmake" -G DEB -B "$work/artifacts"
binary="$work/cmake/tyrell-status"
"$binary" --version | tee "$work/artifacts/version.txt"
"$binary" --json > "$work/artifacts/builder-status.json"
python3 -m json.tool "$work/artifacts/builder-status.json" >/dev/null
"$binary" --headless-screenshot "$work/artifacts/status-system.ppm" --frames 2 --page system
"$binary" --headless-screenshot "$work/artifacts/status-network.ppm" --frames 2 --page network
file "$binary" > "$work/artifacts/elf-file.txt"
readelf -h -d --version-info "$binary" > "$work/artifacts/elf-report.txt"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$work/artifacts/builder-packages.tsv"
cp "$input/source-manifest.json" "$work/artifacts/source-manifest.json"
cp "$input/locks/build-inputs.json" "$work/artifacts/build-inputs.json"
bash "$input/builder/verify-package.sh" "$work/artifacts"
python3 "$input/builder/write-manifest.py" "$work/artifacts"
