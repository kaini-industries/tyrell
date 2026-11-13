#!/bin/bash
# Installation/removal checks occur only inside the dedicated Linux builder.
set -euo pipefail
artifacts=${1:?Usage: verify-package.sh artifact-directory}
package="$artifacts/tyrell-status_0.1.0-1_arm64.deb"
test "$(dpkg-deb -f "$package" Package)" = tyrell-status
test "$(dpkg-deb -f "$package" Architecture)" = arm64
dpkg-deb --info "$package" > "$artifacts/package-info.txt"
dpkg-deb --contents "$package" > "$artifacts/package-contents.txt"
scratch=$(mktemp -d /tmp/tyrell-package-check.XXXXXX)
installed=0
cleanup() {
    if test "$installed" = 1; then sudo dpkg --remove tyrell-status >/dev/null || true; fi
    rm -rf "$scratch"
}
trap cleanup EXIT
dpkg-deb --control "$package" "$scratch/control"
for hook in preinst postinst prerm postrm triggers; do
    if test -e "$scratch/control/$hook"; then
        printf 'Unexpected package maintainer hook: %s\n' "$hook" >&2
        exit 1
    fi
done
dpkg-deb --extract "$package" "$scratch/root"
test -x "$scratch/root/usr/bin/tyrell-status"
test -f "$scratch/root/usr/share/APPLaunch/applications/tyrell-status.desktop"
test -f "$scratch/root/usr/share/APPLaunch/share/images/tyrell-status.png"
test ! -e "$scratch/root/etc"
test ! -e "$scratch/root/boot"
test ! -e "$scratch/root/lib/modules"
if dpkg-query -W -f='${Status}' tyrell-status 2>/dev/null | grep -qx 'install ok installed'; then
    printf 'Refusing to replace an installed tyrell-status package in the builder.\n' >&2
    exit 1
fi
installed=1
sudo dpkg --install "$package" > "$artifacts/install-test.txt"
/usr/bin/tyrell-status --version >> "$artifacts/install-test.txt"
/usr/bin/tyrell-status --json > "$artifacts/installed-status.json"
python3 -m json.tool "$artifacts/installed-status.json" >/dev/null
/usr/bin/tyrell-status --headless-screenshot "$artifacts/status-installed.ppm" --frames 2
sudo dpkg --remove tyrell-status >> "$artifacts/install-test.txt"
installed=0
test ! -e /usr/bin/tyrell-status
test ! -e /usr/share/APPLaunch/applications/tyrell-status.desktop
test ! -e /usr/share/APPLaunch/share/images/tyrell-status.png
printf 'PASS: builder-only package install, JSON/render smoke tests, and removal.\n' | tee -a "$artifacts/install-test.txt"
