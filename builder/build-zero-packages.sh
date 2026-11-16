#!/bin/bash
set -euo pipefail
umask 022
input=${1:?Usage: build-zero-packages.sh input-directory output-directory}
output=${2:?Usage: build-zero-packages.sh input-directory output-directory}
test ! -L /var/tmp/tyrell-locks
install -d -m 700 /var/tmp/tyrell-locks
test -O /var/tmp/tyrell-locks
test ! -L /var/tmp/tyrell-locks/builder.lock
exec 9>>/var/tmp/tyrell-locks/builder.lock
flock -n 9 || { printf 'The Tyrell builder is busy.\n' >&2; exit 1; }
test "$(dpkg --print-architecture)" = arm64
test "$(uname -m)" = aarch64
python3 "$input/builder/zero-guest.py" "$input" "$output"
