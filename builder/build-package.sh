#!/bin/bash
set -euo pipefail
umask 022
# Share the guest lock with detached image jobs, including direct script runs.
test ! -L /var/tmp/tyrell-locks
install -d -m 700 /var/tmp/tyrell-locks
test -O /var/tmp/tyrell-locks
test ! -L /var/tmp/tyrell-locks/builder.lock
exec 9>>/var/tmp/tyrell-locks/builder.lock
flock -n 9 || { printf 'The Tyrell builder is busy.\n' >&2; exit 1; }
input=${1:?Usage: build-package.sh source-directory}
work=/var/tmp/tyrell-build
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
# Recreate only this project's generated trees so deleted sources/dependencies
# and previous package staging cannot leak into the next build.
rm -rf "$work/cmake" "$work/deps" "$work/artifacts"
mkdir -p "$work/deps" "$work/artifacts"

readarray -t locked < <(python3 - "$input" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); lock=json.loads((p/'locks/build-inputs.json').read_text()); source=json.loads((p/'source-manifest.json').read_text())
print(lock['lvgl']['commit']); print(lock['lvgl']['sha256']); print(source['source_date_epoch']); print(source['source_revision']); print(source.get('application','status')); print(source.get('package','tyrell-status'))
PY
)
commit=${locked[0]}
expected=${locked[1]}
export SOURCE_DATE_EPOCH=${locked[2]}
revision=${locked[3]}
app=${locked[4]}
package=${locked[5]}
case "$app:$package" in
    status:tyrell-status) ;;
    video:tyrell-video) bash "$input/builder/ensure-video-deps.sh" ;;
    *) printf 'Unsupported application/package: %s/%s\n' "$app" "$package" >&2; exit 1 ;;
esac
export TZ=UTC LC_ALL=C.UTF-8
archive="$input/downloads/lvgl-$commit.tar.gz"
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$work/deps" --no-same-owner
shell_options=()
if test "$app" = status; then shell_options+=(-DTYRELL_ENABLE_SHELL_LINK=OFF); fi
cmake -S "$input/packages/$package" -B "$work/cmake" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DLVGL_SOURCE_DIR="$work/deps/lvgl-$commit" \
    -DTYRELL_BUILD_REVISION="$revision" \
    -DTYRELL_ENABLE_SDL=OFF -DBUILD_TESTING=ON "${shell_options[@]}"
cmake --build "$work/cmake" --parallel 4
ctest --test-dir "$work/cmake" --output-on-failure | tee "$work/artifacts/tests.txt"
cp "$work/cmake/Testing/Temporary/LastTest.log" "$work/artifacts/ctest-detail.txt"
ctest --test-dir "$work/cmake" --show-only=json-v1 > "$work/artifacts/test-inventory.json"
cpack --config "$work/cmake/CPackConfig.cmake" -G DEB -B "$work/artifacts"
binary="$work/cmake/$package"
"$binary" --version | tee "$work/artifacts/version.txt"
if test "$app" = status; then
    "$binary" --json > "$work/artifacts/builder-status.json"
    python3 -m json.tool "$work/artifacts/builder-status.json" >/dev/null
    "$binary" --headless-screenshot "$work/artifacts/status-system.ppm" --frames 2 --page system
    "$binary" --headless-screenshot "$work/artifacts/status-network.ppm" --frames 2 --page network
else
    "$binary" --diagnostics > "$work/artifacts/builder-diagnostics.json"
    python3 -m json.tool "$work/artifacts/builder-diagnostics.json" >/dev/null
    for page in browser player tracks error hdmi; do
        "$binary" --headless-screenshot "$work/artifacts/video-$page.ppm" --frames 2 --page "$page"
    done
    gst-inspect-1.0 --version > "$work/artifacts/gstreamer-version.txt"
    for element in playbin appsink kmssink pulsesink matroskademux qtdemux h264parse avdec_h264; do
        gst-inspect-1.0 "$element" > "$work/artifacts/gstreamer-$element.txt"
    done
fi
file "$binary" > "$work/artifacts/elf-file.txt"
readelf -h -d --version-info "$binary" > "$work/artifacts/elf-report.txt"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$work/artifacts/builder-packages.tsv"
cp "$input/source-manifest.json" "$work/artifacts/source-manifest.json"
python3 - "$input" "$work/artifacts/build-inputs.json" "$app" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); lock=json.loads((p/'locks/build-inputs.json').read_text())
if sys.argv[3]=='video': lock.update(json.loads((p/'locks/video-build-inputs.json').read_text()))
Path(sys.argv[2]).write_text(json.dumps(lock,indent=2)+'\n')
PY
bash "$input/builder/verify-package.sh" "$work/artifacts"
python3 "$input/builder/write-manifest.py" "$work/artifacts"
