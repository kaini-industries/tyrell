#!/bin/bash
# Installation/removal checks occur only inside the dedicated Linux builder.
set -euo pipefail
artifacts=${1:?Usage: verify-package.sh artifact-directory}
readarray -t selected < <(python3 - "$artifacts/source-manifest.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); print(p.get('application','status')); print(p.get('package','tyrell-status')); print(p.get('debian_version',p['version']+'-1'))
PY
)
app=${selected[0]}
name=${selected[1]}
version=${selected[2]}
case "$app:$name" in status:tyrell-status|video:tyrell-video) ;; *) exit 1 ;; esac
package="$artifacts/${name}_${version}_arm64.deb"
test "$(dpkg-deb -f "$package" Package)" = "$name"
test "$(dpkg-deb -f "$package" Version)" = "$version"
test "$(dpkg-deb -f "$package" Architecture)" = arm64
dpkg-deb --info "$package" > "$artifacts/package-info.txt"
dpkg-deb --contents "$package" > "$artifacts/package-contents.txt"
scratch=$(mktemp -d /tmp/tyrell-package-check.XXXXXX)
installed=0
cleanup() {
    if test "$installed" = 1; then sudo dpkg --remove "$name" >/dev/null || true; fi
    rm -rf "$scratch"
}
trap cleanup EXIT
dpkg-deb --control "$package" "$scratch/control"
for hook in preinst postinst prerm postrm triggers; do
    if test -e "$scratch/control/$hook"; then
        if test "$app" = video && [[ "$hook" =~ ^(postinst|prerm|postrm)$ ]]; then
            sh -n "$scratch/control/$hook"
            continue
        fi
        printf 'Unexpected package maintainer hook: %s\n' "$hook" >&2
        exit 1
    fi
done
dpkg-deb --extract "$package" "$scratch/root"
test -x "$scratch/root/usr/bin/$name"
test -f "$scratch/root/usr/share/APPLaunch/applications/$name.desktop"
test -f "$scratch/root/usr/share/APPLaunch/share/images/$name.png"
# Permit this app's files and, for Video only, its fixed HDMI console broker.
# Status still rejects every service/helper/hook; boot assets remain forbidden.
python3 - "$scratch/root" "$name" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]); name=sys.argv[2]
allowed={f'usr/bin/{name}',f'usr/share/APPLaunch/applications/{name}.desktop',f'usr/share/applications/{name}.desktop',f'usr/share/APPLaunch/share/images/{name}.png',f'usr/share/icons/hicolor/80x80/apps/{name}.png'}
if name == 'tyrell-video':
    allowed.update({'usr/libexec/tyrell-video-hdmi-session', 'usr/libexec/tyrell-video-audio-session', 'usr/lib/systemd/system/tyrell-video-hdmi.socket', 'usr/lib/systemd/system/tyrell-video-hdmi@.service'})
for path in root.rglob('*'):
    rel=path.relative_to(root).as_posix()
    if path.is_symlink() or (path.is_file() and rel not in allowed and not rel.startswith(f'usr/share/doc/{name}/')):
        raise SystemExit('Unexpected packaged path: '+rel)
    if path.is_file() and path.stat().st_mode & 0o6022:
        raise SystemExit('Unsafe packaged permissions: '+rel)
    if path.is_dir() and not (rel=='usr' or rel.startswith('usr/')):
        raise SystemExit('Unexpected packaged directory: '+rel)
PY
if dpkg-query -W -f='${Status}' "$name" 2>/dev/null | grep -qx 'install ok installed'; then
    printf 'Refusing to replace an installed %s package in the builder.\n' "$name" >&2
    exit 1
fi
installed=1
sudo dpkg --install "$package" > "$artifacts/install-test.txt"
"/usr/bin/$name" --version >> "$artifacts/install-test.txt"
if test "$app" = status; then
    "/usr/bin/$name" --json > "$artifacts/installed-status.json"
    python3 -m json.tool "$artifacts/installed-status.json" >/dev/null
    "/usr/bin/$name" --headless-screenshot "$artifacts/status-installed.ppm" --frames 2
else
    "/usr/bin/$name" --diagnostics > "$artifacts/installed-diagnostics.json"
    python3 -m json.tool "$artifacts/installed-diagnostics.json" >/dev/null
    "/usr/bin/$name" --headless-screenshot "$artifacts/video-installed.ppm" --frames 2 --page browser
fi
if test "$app" = video; then
    test -x /usr/libexec/tyrell-video-hdmi-session
    test -x /usr/libexec/tyrell-video-audio-session
    test -f /usr/lib/systemd/system/tyrell-video-hdmi.socket
    if test -d /run/systemd/system; then
        systemctl is-active --quiet tyrell-video-hdmi.socket
        systemctl is-enabled tyrell-video-hdmi.socket >> "$artifacts/install-test.txt"
    fi
fi
sudo dpkg --remove "$name" >> "$artifacts/install-test.txt"
installed=0
if test "$app" = video; then
    test ! -e /usr/libexec/tyrell-video-hdmi-session
    test ! -e /usr/libexec/tyrell-video-audio-session
    test ! -e /usr/lib/systemd/system/tyrell-video-hdmi.socket
    test ! -S /run/tyrell-video-hdmi.sock
fi
test ! -e "/usr/bin/$name"
test ! -e "/usr/share/APPLaunch/applications/$name.desktop"
test ! -e "/usr/share/APPLaunch/share/images/$name.png"
printf 'PASS: %s builder-only package install, JSON/render smoke tests, and removal.\n' "$name" | tee -a "$artifacts/install-test.txt"
