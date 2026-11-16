#!/bin/bash
set -euo pipefail
input=${1:?input-directory}
output=${2:?output-directory}
case ${3:-wizard} in
  wizard) driver=zero-native-guest.py ;;
  integration) driver=zero-integration-guest.py ;;
  *) exit 64 ;;
esac
test ! -L /var/tmp/tyrell-locks
install -d -m 700 /var/tmp/tyrell-locks
test -O /var/tmp/tyrell-locks
test ! -L /var/tmp/tyrell-locks/builder.lock
exec 9>>/var/tmp/tyrell-locks/builder.lock
flock -n 9 || exit 1
sudo -n unshare --net -- setpriv --reuid "$(id -u)" --regid "$(id -g)" --init-groups -- \
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/tmp LANG=C.UTF-8 \
  python3 "$input/builder/$driver" "$input" "$output"
