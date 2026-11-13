#!/bin/bash
# Explicit builder-tool provisioning; the OS image's APT lock is separate.
set -euo pipefail
mode=${1:-install}
case "$mode" in install|--check) ;; *) printf 'Usage: ensure-image-deps.sh [install|--check]\n' >&2; exit 2;; esac
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mapfile -t dependencies < "$directory/image-dependencies.txt"
test "${#dependencies[@]}" -gt 0
for package in "${dependencies[@]}"; do
    [[ "$package" =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || { printf 'Invalid image dependency: %s\n' "$package" >&2; exit 2; }
done
if test "$mode" = install; then
    test "$(id -u)" = 0
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends "${dependencies[@]}"
fi
for package in "${dependencies[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Status}\n' "$package" | grep -qx installed; then
        printf 'Missing builder image tool: %s; run python3 scripts/builder image-deps.\n' "$package" >&2
        exit 1
    fi
done
printf 'Image builder dependencies verified (%s packages).\n' "${#dependencies[@]}"
