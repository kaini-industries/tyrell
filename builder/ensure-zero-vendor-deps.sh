#!/bin/bash
set -euo pipefail
mode=${1:?Usage: ensure-zero-vendor-deps.sh --check|install}
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
mapfile -t packages < "$(dirname "$0")/zero-vendor-dependencies.txt"
if test "$mode" = --check; then
    for package in "${packages[@]}"; do
        test "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null)" = installed || {
            printf 'Missing native vendor build dependency: %s\n' "$package" >&2
            exit 1
        }
    done
elif test "$mode" = install; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends "${packages[@]}"
else
    printf 'Unknown dependency action\n' >&2
    exit 64
fi
