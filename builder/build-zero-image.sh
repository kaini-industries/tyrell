#!/bin/bash
# Root execution in the isolated ARM64 builder only. Never accepts a device.
set -euo pipefail
input=${1:?captured input root}
work=${2:?job work directory}
output=${3:?attempt output directory}
mode=${4:?fresh or stage2}
job=${5:?job identifier}
bundle_sha=${6:?captured archive checksum}
test "$(id -u)" = 0
test "$(uname -m)" = aarch64
test "$(dpkg --print-architecture)" = arm64
case "$mode" in fresh|stage2|prepare) ;; *) exit 64 ;; esac
test "$work" = "/var/tmp/tyrell-zero-image-jobs/$job/work"
test -d "$output"
bash "$input/builder/ensure-image-deps.sh" --check
python3 "$input/image/zero-lock.py" verify "$input/apt-repository"
if test "$mode" = fresh || test "$mode" = prepare; then
    test ! -e "$work"
    mkdir -m 700 "$work"
    cp -a "$input/vendor/pi-gen" "$work/pi-gen"
    python3 "$input/image/prepare-zero.py" "$work/pi-gen" "$input/assets" "$input/image-metadata.json"
    vendor="$work/pi-gen"
    cat > "$vendor/config" <<CONFIG
IMG_NAME=tyrell-zero-trixie-arm64
IMG_FILENAME=tyrell-zero-trixie-arm64
ARCHIVE_FILENAME=tyrell-zero-trixie-arm64
RELEASE=trixie
DEPLOY_COMPRESSION=xz
COMPRESSION_LEVEL=3
TARGET_HOSTNAME=tyrell-zero
FIRST_USER_NAME=pi
FIRST_USER_PASS=''
DISABLE_FIRST_BOOT_USER_RENAME=0
PASSWORDLESS_SUDO=0
ENABLE_SSH=0
LOCALE_DEFAULT=en_US.UTF-8
KEYBOARD_KEYMAP=us
KEYBOARD_LAYOUT="English (US)"
TIMEZONE_DEFAULT=Etc/UTC
STAGE_LIST="$vendor/stage0 $vendor/stage1 $vendor/stage2"
source "$vendor/tyrell-inputs.conf"
unset WPA_COUNTRY WPA_ESSID WPA_PASSWORD PUBKEY_SSH_FIRST_USER
CONFIG
    python3 - "$work" "$input/image-metadata.json" "$job" "$bundle_sha" <<'PY'
import hashlib,json,os,sys
from pathlib import Path
work=Path(sys.argv[1]);metadata=json.loads(Path(sys.argv[2]).read_text())
record={'job_id':sys.argv[3],'bundle_sha256':sys.argv[4],
        'profile_sha256':metadata['profile_sha256'],'capture_sha256':metadata['capture_sha256'],
        'config_sha256':hashlib.sha256((work/'pi-gen/config').read_bytes()).hexdigest()}
p=work/'zero-job-identity.json';p.write_text(json.dumps(record,sort_keys=True)+'\n');p.chmod(0o600)
PY
else
    vendor="$work/pi-gen"
    previous=$(( ${TYRELL_IMAGE_ATTEMPT_ID:?attempt} - 1 ))
    python3 "$input/image/zero_resume.py" --job-root "${work%/work}" --bundle-sha256 "$bundle_sha" --previous-attempt "$previous" --authorized-attempt "$TYRELL_IMAGE_ATTEMPT_ID"
    # Preserve failed output/stages rather than deleting them during a retry.
    quarantine="$work/quarantine-attempt-$previous"
    mkdir -m 700 "$quarantine"
    for path in "$vendor/work/tyrell-zero-trixie-arm64/stage2" "$vendor/work/tyrell-zero-trixie-arm64/export-image" "$vendor/deploy"; do
        test ! -e "$path" || mv "$path" "$quarantine/$(basename "$path")"
    done
    touch "$vendor/stage0/SKIP" "$vendor/stage1/SKIP"
fi
cp "$vendor/config" "$output/pi-gen-config.txt"
cp "$vendor/tyrell-prepared.json" "$output/vendor-preparation.json"
cp "$input/image-metadata.json" "$output/build-inputs.json"
test "$mode" != prepare || exit 0
port_file="$output/apt-server.port"
python3 "$input/image/zero-lock.py" serve "$input/apt-repository" --port-file "$port_file" > "$output/apt-server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT
for ((n=0;n<100;n++)); do
    test ! -s "$port_file" || break
    kill -0 "$server_pid"
    sleep .1
done
test -s "$port_file"
export TYRELL_APT_BASE="http://127.0.0.1:$(cat "$port_file")"
(
    cd "$vendor"
    ./build.sh
) 2>&1 | tee "$output/pi-gen-build.log"
image="$vendor/work/tyrell-zero-trixie-arm64/export-image/tyrell-zero-trixie-arm64-lite.img"
compressed="$vendor/deploy/tyrell-zero-trixie-arm64-lite.img.xz"
test -s "$image"
test -s "$compressed"
python3 "$input/image/verify-zero.py" "$image" --metadata "$input/image-metadata.json" --locks-dir "$input/locks" --output-dir "$output"
cp "$compressed" "$output/"
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$output/builder-packages.tsv"
python3 "$input/image/write-manifest.py" "$output"
