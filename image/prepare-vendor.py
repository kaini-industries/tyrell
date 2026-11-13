#!/usr/bin/env python3
"""Prepare the exact CardputerZero pi-gen baseline using verified local inputs.

Usage: prepare-vendor.py VENDOR_CHECKOUT ASSETS_DIR METADATA_JSON
Run against a pristine checkout/archive. Source tyrell-inputs.conf from pi-gen's
config and supply an explicit STAGE_LIST ending at stage4.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

BASELINE = '5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4'
EXPECTED = {
    'build.sh': '976a01a12d695fe91b1fc4be85578217a086d356ac19f6674a513f1edbba5308',
    'stage2/05-cardputerzero/01-run.sh': 'fd45585161d2208c838a6a37b476700b9e920478900dbcf9012764803f520edf',
    'stage2/05-cardputerzero/02-run.sh': 'b6bd753723d6dd9599185b369884ac14b7ad0bb056d9bb1012a1d566c0989acb',
    'stage2/05-cardputerzero/03-run.sh': 'c700ef1af64fc813406eafc7620bb3ae20582d98af217f986c5fb2e0053bbfbe',
}
BOOT_HASHES = [
    '/boot/firmware/config.txt',
    '/boot/firmware/start.elf',
    '/boot/firmware/fixup.dat',
    '/boot/firmware/CardputerZero_splash_custom.bin',
    '/boot/firmware/overlays/cardputerzero-v5-overlay.dtbo',
    '/boot/firmware/overlays/bq27220_v5.dtbo',
    '/boot/firmware/overlays/bmi270_bmm150_overlay.dtbo',
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def checked_asset(assets, spec):
    name = spec['name']
    if Path(name).name != name:
        raise ValueError('Asset filename must be a basename: ' + name)
    path = assets / name
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing regular input file: ' + str(path))
    if sha256(path) != spec['sha256']:
        raise ValueError('Asset checksum mismatch: ' + name)
    return path


def write_script(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vendor_checkout', type=Path)
    parser.add_argument('assets_dir', type=Path)
    parser.add_argument('metadata_json', type=Path)
    args = parser.parse_args()
    vendor = args.vendor_checkout.resolve()
    assets = args.assets_dir.resolve()
    metadata_path = args.metadata_json.resolve()
    lock = json.loads(Path(__file__).with_name('baseline-apps.json').read_text())
    camera_lock = json.loads(Path(__file__).with_name('camera-packages.json').read_text())
    metadata = json.loads(metadata_path.read_text())
    if lock['pi_gen_commit'] != BASELINE:
        raise ValueError('Unexpected pi-gen baseline in application lock')
    if (vendor / '.git').exists():
        actual = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != BASELINE:
            raise ValueError('Vendor Git checkout does not match the baseline')
    # This also supports an archive whose entire download was verified by the
    # caller: pin critical pristine chunks before applying any local mutation.
    for relative, expected in EXPECTED.items():
        if sha256(vendor / relative) != expected:
            raise ValueError('Expected pristine baseline file: ' + relative)
    if (vendor / 'stage4/99-tyrell').exists():
        raise ValueError('Project stage already exists; prepare a pristine baseline')

    for key, expected in {
        'pi_gen_commit': BASELINE,
        'drivers_commit': lock['drivers']['commit'],
        'kernel_release': '6.18.39+rpt-rpi-v8',
        'applaunch_version': '0.6.105',
    }.items():
        if metadata.get(key) != expected:
            raise ValueError('Unexpected image metadata field: ' + key)
    if not re.fullmatch('[0-9a-f]{64}', metadata.get('package_sha256', '')):
        raise ValueError('Image metadata must lock the custom package SHA-256')
    if not metadata.get('package_version'):
        raise ValueError('Image metadata must specify package_version')
    package_name = metadata.get('package_filename', 'tyrell-status_0.1.0-1_arm64.deb')
    package = checked_asset(assets, {'name': package_name, 'sha256': metadata['package_sha256']})
    launcher = checked_asset(assets, lock['launcher'])
    drivers = checked_asset(assets, lock['drivers'])
    app_assets = [(app, checked_asset(assets, app)) for app in lock['apps']]
    camera_assets = [(item, checked_asset(assets, item)) for item in camera_lock['packages']]
    camera_preinstall = [item for item, _ in camera_assets if item.get('preinstall', True)]
    board_stage = vendor / 'stage2/05-cardputerzero'

    # Keep the baseline libcamera ABI and the exact PipeWire family together.
    # This runs after stage2/prerun.sh copies the prior rootfs and before
    # 01-sys-tweaks installs rpicam-apps-lite from the moving APT repository.
    camera_repo = '/usr/local/share/tyrell/camera-repo'
    camera_script = ['#!/bin/bash -e', '',
                     '# Verified local packages remain available for coherent future dependencies.',
                     'install -d -m 0755 "$ROOTFS_DIR' + camera_repo + '"',
                     'install -d -m 0755 "$ROOTFS_DIR/etc/apt/sources.list.d" "$ROOTFS_DIR/etc/apt/preferences.d"']
    for item, source in camera_assets:
        camera_script.append('install -m 0644 ' + shlex.quote(str(source)) + ' "$ROOTFS_DIR' + camera_repo + '/' + item['name'] + '"')
    camera_script.extend([
        '(cd "$ROOTFS_DIR' + camera_repo + '" && dpkg-scanpackages --multiversion . /dev/null > Packages)',
        'cat > "$ROOTFS_DIR/etc/apt/sources.list.d/tyrell-camera.list" << \'TYRELL_CAMERA_SOURCE\'',
        'deb [trusted=yes] file:' + camera_repo + ' ./',
        'TYRELL_CAMERA_SOURCE',
        'cat > "$ROOTFS_DIR/etc/apt/preferences.d/tyrell-camera" << \'TYRELL_CAMERA_PINS\'',
        '# The retained local repository provides these exact baseline versions.',
    ])
    camera_groups = {}
    for item, _ in camera_assets:
        camera_groups.setdefault(item['version'], []).append(item['package'])
    for version, names in camera_groups.items():
        camera_script.extend(['Package: ' + ' '.join(names), 'Pin: version ' + version,
                              'Pin-Priority: 1001', ''])
    if camera_lock.get('excluded_packages'):
        camera_script.extend(['Package: ' + ' '.join(camera_lock['excluded_packages']),
                              'Pin: version *', 'Pin-Priority: -1', ''])
    camera_script.extend(['TYRELL_CAMERA_PINS', '', "on_chroot << 'TYRELL_CAMERA_CHROOT'", 'set -e',
                          'apt-get update',
                          'apt-get install -y --no-install-recommends --allow-downgrades ' + ' '.join(
                              shlex.quote(item['package'] + '=' + item['version']) for item in camera_preinstall),
                          'apt-mark hold ' + ' '.join(shlex.quote(item['package']) for item in camera_preinstall),
                          'TYRELL_CAMERA_CHROOT', ''])
    write_script(vendor / 'stage2/00-tyrell-camera/00-run.sh', '\n'.join(camera_script))

    original_driver = (board_stage / '01-run.sh').read_text()
    driver_old = '\n'.join([
        '# Always build the latest driver from the main branch.',
        'rm -rf /tmp/dtoverlays',
        'git clone --depth=1 https://github.com/m5stack/m5stack-linux-dtoverlays.git ' + chr(92),
        '    /tmp/dtoverlays',
        'git -C /tmp/dtoverlays rev-parse HEAD ' + chr(92),
        '    > /etc/cardputerzero-dtoverlays.commit',
        '',
    ])
    if original_driver.count(driver_old) != 1:
        raise ValueError('Pinned driver clone block did not match exactly')
    driver_new = (
        '# Use the verified, commit-pinned local source archive.\n'
        'rm -rf /tmp/dtoverlays\nmkdir -p /tmp/dtoverlays\n'
        'tar -xzf /var/tmp/tyrell-dtoverlays.tar.gz -C /tmp/dtoverlays --strip-components=1 --no-same-owner\n'
        "printf '%s\\n' " + shlex.quote(lock['drivers']['commit']) + ' > /etc/cardputerzero-dtoverlays.commit\n'
        'rm -f /var/tmp/tyrell-dtoverlays.tar.gz\n'
    )
    patched_driver = original_driver.replace(driver_old, driver_new, 1)
    archive_copy = (
        'install -d -m 0755 "$ROOTFS_DIR/var/tmp"\n'
        'install -m 0644 ' + shlex.quote(str(drivers)) + ' "$ROOTFS_DIR/var/tmp/tyrell-dtoverlays.tar.gz"\n\n'
    )
    patched_driver = patched_driver.replace("on_chroot << 'CHROOT'\n", archive_copy + "on_chroot << 'CHROOT'\n", 1)
    write_script(board_stage / '01-run.sh', patched_driver)

    launcher_script = (board_stage / '02-run.sh').read_text()
    if launcher_script.count('cfg80211.ieee80211_regdom=AE') != 1:
        raise ValueError('Vendor radio-region token did not match exactly')
    write_script(board_stage / '02-run.sh', launcher_script.replace(
        'cfg80211.ieee80211_regdom=AE', 'cfg80211.ieee80211_regdom=00', 1))

    # Preserve the vendor app order and the Camera package's known overwrite
    # exception, but eliminate release API calls and all latest resolution.
    app_script = ['#!/bin/bash -e', '', '# Install only checksum-verified locked application assets.',
                  'install -d -m 0755 "$ROOTFS_DIR/var/tmp/tyrell-vendor-apps"']
    for app, path in app_assets:
        destination = '/var/tmp/tyrell-vendor-apps/' + app['name']
        app_script.append('install -m 0644 ' + shlex.quote(str(path)) + ' "$ROOTFS_DIR' + destination + '"')
    app_script.extend(['', "on_chroot << 'TYRELL_VENDOR_APPS_CHROOT'", 'set -e'])
    for app, _ in app_assets:
        option = '-o Dpkg::Options::=--force-overwrite ' if app['id'] == 'camera' else ''
        destination = '/var/tmp/tyrell-vendor-apps/' + app['name']
        app_script.append('apt-get install -y --no-install-recommends ' + option + shlex.quote(destination))
    app_script.extend(['rm -rf /var/tmp/tyrell-vendor-apps', 'TYRELL_VENDOR_APPS_CHROOT', ''])
    write_script(board_stage / '03-run.sh', '\n'.join(app_script))

    package_version = shlex.quote(metadata['package_version'])
    version_field = shlex.quote('$' + '{Version}')
    camera_checks = '\n'.join(
        'test "$(dpkg-query -W -f=' + version_field + ' ' + shlex.quote(item['package']) + ')" = ' + shlex.quote(item['version'])
        for item in camera_preinstall)
    stage_script = f"""#!/bin/bash -e
install -d -m 0755 "$ROOTFS_DIR/etc" "$ROOTFS_DIR/var/tmp"
install -m 0644 {shlex.quote(str(package))} "$ROOTFS_DIR/var/tmp/tyrell-status.deb"
install -m 0644 {shlex.quote(str(metadata_path))} "$ROOTFS_DIR/etc/tyrell-image.json"
on_chroot << 'TYRELL_STATUS_CHROOT'
set -e
test "$(dpkg-deb -f /var/tmp/tyrell-status.deb Package)" = tyrell-status
test "$(dpkg-deb -f /var/tmp/tyrell-status.deb Architecture)" = arm64
dpkg --install /var/tmp/tyrell-status.deb
test "$(dpkg-query -W -f={version_field} tyrell-status)" = {package_version}
test "$(dpkg-query -W -f={version_field} linux-image-rpi-v8)" = '1:6.18.39-1+rpt1'
test "$(dpkg-query -W -f={version_field} applaunch)" = '0.6.105'
test "$(cat /etc/cardputerzero-dtoverlays.commit)" = '{lock['drivers']['commit']}'
{camera_checks}
/usr/bin/tyrell-status --version
rm -f /var/tmp/tyrell-status.deb
TYRELL_STATUS_CHROOT
python3 - "$ROOTFS_DIR" << 'TYRELL_IMAGE_METADATA'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
path=root/'etc/tyrell-image.json'
data=json.loads(path.read_text())
files={BOOT_HASHES!r}
data['boot_sha256']={{relative:hashlib.sha256((root/relative.lstrip('/')).read_bytes()).hexdigest() for relative in files}}
data['boot_hash_scope']='Selected board boot configuration, assets and overlays before export; excludes cmdline, issue and initramfs modified during export.'
data['camera_packages']={{{', '.join(repr(item['package']) + ': ' + repr(item['version']) for item in camera_preinstall)}}}
data['camera_repository']={{'path':{camera_repo!r},'pinned_packages':{len(camera_assets)},'preinstalled_packages':{len(camera_preinstall)},'policy':'Official baseline camera and PipeWire packages retained locally with exact version pins; newer libcamera ABI excluded.'}}
path.write_text(json.dumps(data,indent=2)+'\\n')
TYRELL_IMAGE_METADATA
"""
    write_script(vendor / 'stage4/99-tyrell/00-run.sh', stage_script)
    (vendor / 'stage2/SKIP_IMAGES').touch()
    fragment = (
        '# Generated verified local inputs; source this from pi-gen config.\n'
        'export APPLAUNCH_DEB_FILE=' + shlex.quote(str(launcher)) + '\n'
        'export GIT_HASH=' + shlex.quote(BASELINE) + '\n'
    )
    (vendor / 'tyrell-inputs.conf').write_text(fragment)
    changed = ['stage2/00-tyrell-camera/00-run.sh', 'stage2/05-cardputerzero/01-run.sh', 'stage2/05-cardputerzero/02-run.sh', 'stage2/05-cardputerzero/03-run.sh',
               'stage4/99-tyrell/00-run.sh', 'stage2/SKIP_IMAGES', 'tyrell-inputs.conf']
    report = {
        'pi_gen_commit': BASELINE, 'drivers_commit': lock['drivers']['commit'],
        'launcher_sha256': lock['launcher']['sha256'], 'package_sha256': metadata['package_sha256'],
        'apps': len(app_assets), 'config_fragment': str(vendor / 'tyrell-inputs.conf'),
        'camera_packages_locked': len(camera_assets), 'camera_packages_preinstalled': len(camera_preinstall),
        'input_assets_sha256': {path.name: sha256(path) for path in sorted(assets.iterdir()) if path.is_file()},
        'input_root': str(assets.parent),
        'apt_profile': metadata.get('apt_profile', 'discovery'),
        'apt_lock_sha256': metadata.get('apt_lock_sha256'),
        'prepared_files_sha256': {name: sha256(vendor / name) for name in changed},
    }
    (vendor / 'tyrell-prepared.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
