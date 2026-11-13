#!/bin/bash
set -euo pipefail
umask 022
input=${1:?Usage: build-image.sh input-directory}
mode=${2:-fresh}
work=/var/tmp/tyrell-image-work
test "$(id -u)" = 0
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
available=$(df --output=avail -B1 /var/tmp | tail -n1)
if test "$available" -lt 30000000000; then
    printf 'Need at least 30 GB free in the Linux builder; found %s bytes.\n' "$available" >&2
    exit 1
fi
if test -d "$work"; then
    mounts=$(findmnt -rn -o TARGET)
    if grep -Eq "^$work(/|$)" <<< "$mounts"; then
        printf 'Previous image build still has mounts; inspect and unmount before retrying.\n' >&2
        exit 1
    fi
    if test "$mode" != stage2 && test "$mode" != stage4; then
        printf 'Preserving previous image work at %s; remove or rename it deliberately to rebuild.\n' "$work" >&2
        exit 1
    fi
    prior_stage=$(( ${mode#stage} - 1 ))
    test -f "$work/pi-gen/work/tyrell-trixie-arm64/stage$prior_stage/rootfs/etc/os-release"
    test "$(git -C "$work/pi-gen" rev-parse HEAD)" = 5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4
    grep -Eq "\\] End .*/pi-gen/stage$prior_stage$" "$work/pi-gen/work/tyrell-trixie-arm64/build.log"
else
    test "$mode" = fresh
fi
export DEBIAN_FRONTEND=noninteractive
profile=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("apt_profile","discovery"))' "$input/image-metadata.json")
if test "$profile" = discovery; then
    bash "$input/builder/ensure-image-deps.sh" install
elif test "$profile" = frozen; then
    # Reuse the provisioned toolchain without silently updating the shared VM.
    # Its exact package inventory is exported independently of image APT.
    bash "$input/builder/ensure-image-deps.sh" --check
    python3 "$input/image/apt-lock.py" verify "$input/apt-repository"
    python3 - "$input" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
metadata=json.loads((root/'image-metadata.json').read_text())
actual=hashlib.sha256((root/'apt-repository/apt-lock.json').read_bytes()).hexdigest()
if metadata.get('apt_lock_sha256')!=actual: raise ValueError('Frozen APT lock differs from captured image metadata')
PY
else
    printf 'Unsupported APT profile: %s\n' "$profile" >&2
    exit 1
fi
mkdir -p "$work"
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
candidate="$work/prepared-$stamp"
cp -a "$input/vendor/pi-gen" "$candidate"
chown -R root:root "$candidate"
python3 "$input/image/prepare-vendor.py" "$candidate" "$input/assets" "$input/image-metadata.json"
vendor="$work/pi-gen"
if test "$profile" = frozen; then
    python3 "$input/image/apt-lock.py" configure "$candidate" "$input/apt-repository"
fi
cat > "$candidate/config" <<CONFIG
IMG_NAME=tyrell-trixie-arm64
IMG_FILENAME=tyrell-trixie-arm64
ARCHIVE_FILENAME=tyrell-trixie-arm64
RELEASE=trixie
DEPLOY_COMPRESSION=xz
COMPRESSION_LEVEL=3
TARGET_HOSTNAME=tyrell
FIRST_USER_NAME=pi
FIRST_USER_PASS=''
DISABLE_FIRST_BOOT_USER_RENAME=0
PASSWORDLESS_SUDO=0
ENABLE_SSH=0
LOCALE_DEFAULT=en_US.UTF-8
KEYBOARD_KEYMAP=us
KEYBOARD_LAYOUT="English (US)"
TIMEZONE_DEFAULT=Etc/UTC
STAGE_LIST="$vendor/stage0 $vendor/stage1 $vendor/stage2 $vendor/stage3 $vendor/stage4"
source "$vendor/tyrell-inputs.conf"
unset WPA_COUNTRY WPA_ESSID WPA_PASSWORD PUBKEY_SSH_FIRST_USER
CONFIG
if test "$mode" != fresh; then
    # Validate before moving the retained tree or deleting generated stages.
    python3 "$input/image/resume.py" "$vendor" "$candidate" "$mode"
    previous="$work/previous-vendor-$stamp"
    mv "$vendor" "$previous"
    test ! -d "$work/output" || mv "$work/output" "$work/previous-output-$stamp"
fi
mv "$candidate" "$vendor"
mkdir -p "$work/output"
if test "$profile" = frozen; then
    rm -f "$work/apt-server.port"
    python3 "$input/image/apt-lock.py" serve "$input/apt-repository" \
        --port-file "$work/apt-server.port" > "$work/apt-server.log" 2>&1 &
    apt_server_pid=$!
    trap 'kill "$apt_server_pid" 2>/dev/null || true; wait "$apt_server_pid" 2>/dev/null || true' EXIT
    for ((attempt=0;attempt<100;attempt++)); do
        test ! -s "$work/apt-server.port" || break
        kill -0 "$apt_server_pid"
        sleep .1
    done
    test -s "$work/apt-server.port"
    TYRELL_APT_BASE="http://127.0.0.1:$(cat "$work/apt-server.port")"
    export TYRELL_APT_BASE
fi
if test "$mode" != fresh; then
    mv "$previous/work" "$vendor/work"
    for stage in stage2 stage3 stage4 stage5 export-image; do
        if test "$stage" = export-image || test "${stage#stage}" -ge "${mode#stage}"; then
            rm -rf "$vendor/work/tyrell-trixie-arm64/$stage"
        fi
    done
    for ((stage=0;stage<${mode#stage};stage++)); do touch "$vendor/stage$stage/SKIP"; done
    printf 'Reusing completed stages before %s from the same pinned baseline/configuration.\n' "$mode" > "$work/output/resume-notes.txt"
fi
cp "$vendor/config" "$work/output/pi-gen-config.txt"
cp "$vendor/tyrell-prepared.json" "$work/output/vendor-preparation.json"
cp "$input/image/baseline-apps.json" "$work/output/baseline-apps.json"
cp "$input/image-metadata.json" "$work/output/build-inputs.json"
cp "$input/package-manifest.json" "$work/output/package-manifest.json"
(
    cd "$vendor"
    ./build.sh
) 2>&1 | tee "$work/build.log"
image="$vendor/work/tyrell-trixie-arm64/export-image/tyrell-trixie-arm64.img"
test -f "$image"
test -s "$vendor/deploy/tyrell-trixie-arm64.img.xz"
python3 "$input/image/verify-image.py" "$image" --output-dir "$work/output" \
    --package "$input/assets/tyrell-status_0.1.0-1_arm64.deb" \
    --expected-package-sha "$(sha256sum "$input/assets/tyrell-status_0.1.0-1_arm64.deb" | cut -d' ' -f1)"
cp "$vendor/deploy/"* "$work/output/"
cp "$work/build.log" "$work/output/pi-gen-build.log"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$work/output/builder-packages.tsv"
python3 "$input/image/write-manifest.py" "$work/output"
printf 'Image built and verified as a file. No device or SD card was accessed.\n'
