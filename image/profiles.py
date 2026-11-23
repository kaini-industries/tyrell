#!/usr/bin/env python3
"""Read-only structure and qualification gates for Tyrell Zero image inputs.

This module does not launch pi-gen, Lima, package installation, or a device
connection. Structural validity is deliberately separate from build readiness.
Stock and desktop-source builds retain their independent legacy runners.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = 'image/profiles/tyrell-zero.json'
STAGES = ['stage0', 'stage1', 'stage2']
HARDWARE_IDS = {
    'boot_sd', 'lcd_backlight', 'keyboard', 'power_gauge', 'audio', 'hdmi',
    'camera', 'imu_compass', 'rtc', 'infrared', 'wifi_ssh', 'bluetooth',
    'ethernet', 'usb_modes', 'expansion',
}
PINS = {
    'pi_gen_commit': '5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4',
    'bsp_commit': '75518549a2640486f5d13ea72ca4935b198d7c20',
    'bsp_archive_sha256': 'a541239495b9e1c15d2fe580c2cee4587ba9667e427d5f4d15d45038d8fd93a0',
    'kernel_release': '6.18.39+rpt-rpi-v8',
    'kernel_package': '1:6.18.39-1+rpt1',
    'applaunch_version': '0.6.105',
    'applaunch_source': 'e7f60859fcc8bd867f3b8c4b3acd45dfa82ca39f',
    'applaunch_deb_sha256': 'a38cffde9cb4d60f4ff2d6c95323c64f66cc705fdf315b0550e123ca7b5a9544',
    'applaunch_main_sha256': '9f26df9329160385c68794d26e0c7153271c6a69bb048938580e1495418c968c',
    'raspberrypi_sys_mods_version': '1:20260914',
    'raspberrypi_sys_mods_sha256': 'a6169bc9506b36ba92d72c88d3390b6eefc8eb616fd9a46100f9bbf3ac088211',
    'seed_apt_lock_sha256': '7df91bba2940d04aaa56179ab7320d08107f4dd18dcb420364dc946d9dc5ea8e',
    'greetd_version': '0.10.3-4',
}
LOCK_PATHS = {
    'seed_apt': 'locks/image-apt.json',
    'build': 'locks/tyrell-zero-build-inputs.json',
    'source': 'locks/tyrell-zero-session-source.json',
    'apt': 'locks/tyrell-zero-apt.json',
    'apps': 'locks/tyrell-zero-apps.json',
    'native_compat': 'locks/tyrell-zero-native-compat.json',
}
CUSTOM_PACKAGES = ['tyrell-status', 'tyrell-zero-session', 'tyrell-zero-store', 'tyrell-zero-power-policy']
TUNABLES = {
    'up_threshold': 50, 'ignore_nice_load': 0, 'powersave_bias': 0,
    'sampling_rate': 100000, 'io_is_busy': 0, 'sampling_down_factor': 50,
}
PRESETS = {
    name: {'ethernet_control': control, 'ethernet_autosuspend_delay_ms': 2000,
           'ondemand_sampling_down_factor': factor}
    for name, control, factor in [('baseline', 'on', 50), ('ethernet', 'auto', 50),
                                  ('cpu', 'on', 1), ('combined', 'auto', 1)]
}
OUTPUT = {'basename': 'tyrell-zero-trixie-arm64', 'suffix': '-lite',
          'raw': 'tyrell-zero-trixie-arm64-lite.img',
          'compressed': 'tyrell-zero-trixie-arm64-lite.img.xz'}


class ProfileError(ValueError):
    """Invalid or unsupported profile input."""


class ProfileNotReady(ProfileError):
    def __init__(self, report):
        self.report = report
        super().__init__('Tyrell Zero is not build-ready: ' +
                         '; '.join(item['detail'] for item in report['blockers']))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProfileError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _json(path, expected_sha256=None):
    try:
        # Validate the exact bytes that are parsed; a separate hash/read pair
        # could otherwise accept changed contents between the two operations.
        with path.open('rb') as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ProfileError('Profile/contract JSON exceeds supported size')
        if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ProfileError('Input checksum mismatch: ' + str(path))
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileError('Cannot read JSON input: ' + str(path)) from error
    if not isinstance(value, dict):
        raise ProfileError('JSON input must be an object: ' + str(path))
    return value


def repository_path(root, value, *, required=True):
    """Resolve a captured repository input without following child symlinks."""
    root = Path(root).resolve()
    if not isinstance(value, (str, Path)) or not str(value) or '\\' in str(value) or '\0' in str(value):
        raise ProfileError('Invalid repository input path')
    candidate = Path(value)
    if '..' in candidate.parts or candidate.name.startswith('.env'):
        raise ProfileError('Unsafe repository input path: ' + str(value))
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise ProfileError('Repository input escapes project root') from error
    else:
        relative = candidate
    if any(part.startswith('.env') for part in relative.parts):
        raise ProfileError('Credential files cannot be profile inputs')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ProfileError('Symlinks cannot be profile inputs: ' + str(relative))
    if required and not path.is_file():
        raise ProfileError('Missing regular repository input: ' + str(relative))
    if path.exists() and not path.is_file():
        raise ProfileError('Repository input is not a regular file: ' + str(relative))
    return path


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ProfileError(label + ' has missing or unsupported fields')


def _equals(value, expected, label):
    # JSON true compares equal to Python 1; exact typed JSON avoids that alias.
    if json.dumps(value, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ProfileError('Unsupported ' + label)


def _reference(root, spec, expected_path):
    _keys(spec, {'path', 'sha256'}, 'Input reference')
    _equals(spec['path'], expected_path, 'input path')
    if not re.fullmatch(r'[0-9a-f]{64}', str(spec['sha256'])):
        raise ProfileError('Invalid input SHA-256')
    path = repository_path(root, spec['path'])
    return path, _json(path, expected_sha256=spec['sha256'])


def _hardware(value):
    _keys(value, {'schema', 'id', 'plan_revision', 'kernel_release', 'bsp_commit',
                  'retain_complete_modules', 'retain_complete_firmware',
                  'retain_complete_overlays', 'protected_trees', 'capabilities',
                  'qualification', 'spare_card_required', 'current_device_access'},
          'Hardware contract')
    for key, expected in {'schema': 1, 'id': 'tyrell-zero-hardware', 'plan_revision': 3,
                          'kernel_release': PINS['kernel_release'],
                          'bsp_commit': PINS['bsp_commit'], 'retain_complete_modules': True,
                          'retain_complete_firmware': True, 'retain_complete_overlays': True,
                          'qualification': 'not-tested-for-Zero', 'spare_card_required': True,
                          'current_device_access': False}.items():
        _equals(value[key], expected, 'hardware ' + key)
    _equals(value['protected_trees'], ['/lib/modules', '/lib/firmware', '/boot/firmware/overlays'],
            'protected complete hardware trees')
    categories = value['capabilities']
    if not isinstance(categories, list) or len(categories) != len(HARDWARE_IDS):
        raise ProfileError('Hardware contract must retain all fifteen capabilities')
    names = []
    for row in categories:
        _keys(row, {'id', 'required_proof', 'qualification'}, 'Hardware capability')
        if not isinstance(row['required_proof'], str) or not row['required_proof'].strip():
            raise ProfileError('Hardware capability lacks required proof')
        _equals(row['qualification'], 'not-tested-for-Zero', 'hardware qualification')
        names.append(row['id'])
    if len(set(names)) != len(names) or set(names) != HARDWARE_IDS:
        raise ProfileError('Hardware contract omits or duplicates native capability')


def _power(value):
    _keys(value, {'schema', 'id', 'plan_revision', 'default_preset', 'package',
                  'selection_config', 'selection_schema', 'activation_implemented',
                  'qualification', 'presets', 'baseline_controls', 'ethernet_match',
                  'conditional_search', 'runtime_override', 'safety', 'release_gates'},
          'Power contract')
    for key, expected in {'schema': 1, 'id': 'tyrell-zero-power', 'plan_revision': 3,
                          'default_preset': 'baseline', 'package': 'tyrell-zero-power-policy',
                          'selection_config': '/etc/tyrell-zero/power-policy.json',
                          'selection_schema': {'schema': 1, 'preset': 'baseline'},
                          'activation_implemented': False,
                          'qualification': 'not-tested-for-Zero', 'presets': PRESETS,
                          'runtime_override': '/run/tmpfiles.d/raspberrypi-sys-mods-ondemand-governor.conf'}.items():
        _equals(value[key], expected, 'power ' + key)
    _equals(value['baseline_controls'], {
        'governor': 'ondemand', 'cpu_min_khz': 600000, 'cpu_max_khz': 1000000,
        'ondemand_tunables': TUNABLES, 'ethernet_parent_policy': 'auto',
        'wifi_power_save': 'preserve-effective-on-and-explicit-user-choice',
        'ethernet_eee': 'preserve-enabled', 'gpu_mem_mib': 128, 'cma_mib': 64,
        'swap': 'preserve'}, 'baseline controls')
    _equals(value['ethernet_match'], {
        'vendor_id': '0fe6', 'product_id': '9900', 'usb_device_driver': 'r8152-cfgselector',
        'interface_driver': 'r8152', 'integrated_board_topology_required': True,
        'hardcoded_bus_number_allowed': False, 'wakeup_attribute': 'record-availability-and-value'},
        'integrated Ethernet match')
    search = value['conditional_search']
    _keys(search, {'cpu_factors', 'ethernet_delays_ms', 'trigger'}, 'Conditional search')
    _equals(search['cpu_factors'], [2, 5], 'conditional CPU factors')
    _equals(search['ethernet_delays_ms'], [5000], 'conditional Ethernet delays')
    if not isinstance(search['trigger'], str) or not search['trigger'].strip():
        raise ProfileError('Conditional tuning needs an evidence trigger')
    _equals(value['safety'], {
        'install_sysfs_writes': False, 'install_udev_triggers': False, 'polling_daemon': False,
        'eligibility_before_optimized_write': True, 'transactional_invalidation': True,
        'restore_live_and_boot_policy': True}, 'power installation/activation safety')
    _equals(value['release_gates'], {
        'battery_promotion_required': True, 'usb_only_promotion_allowed': False,
        'hardware_and_performance_required': True, 'combined_independent_battery_proof_required': True,
        'raw_gauge_correction_allowed': False, 'claims_require_explicit_reference': True,
        'all_gates': 'future-unexecuted'}, 'battery release gates')


def _load_profile(path=DEFAULT_PROFILE, root=ROOT):
    """Load and strictly validate the implemented foundation schema; no writes."""
    root = Path(root).resolve()
    profile = _json(repository_path(root, path))
    _keys(profile, {'schema', 'id', 'profile_version', 'plan_revision', 'architecture',
                    'release', 'build_mode', 'stages', 'integration_stage', 'output',
                    'pins', 'contracts', 'lock_paths', 'custom_packages',
                    'selected_power_preset', 'runtime_roots', 'desktop_policy',
                    'safety', 'readiness', 'vendor_inputs'}, 'Zero profile')
    for key, expected in {'schema': 1, 'id': 'tyrell-zero', 'profile_version': 1,
                          'plan_revision': 3, 'architecture': 'arm64', 'release': 'trixie',
                          'build_mode': 'zero-native', 'stages': STAGES,
                          'integration_stage': 'stage2/99-tyrell', 'output': OUTPUT,
                          'pins': PINS, 'lock_paths': LOCK_PATHS,
                          'custom_packages': CUSTOM_PACKAGES,
                          'selected_power_preset': 'baseline'}.items():
        _equals(profile[key], expected, 'profile ' + key)
    _equals(profile['safety'], {'device_access': False, 'sd_card_access': False,
                               'live_builder_base_package_install': False,
                               'automatic_security_deferral': False}, 'profile safety')
    _equals(profile['desktop_policy'], {'omit_stages': ['stage3', 'stage4'],
        'exclude_display_manager': 'lightdm', 'exclude_compositor': 'labwc',
        'retain_required_shared_libraries': True}, 'desktop policy')
    _keys(profile['readiness'], {'phase', 'image_build_enabled', 'reason'}, 'Readiness')
    if profile['readiness']['phase'] not in {'foundation', 'pipeline'}:
        raise ProfileError('Unsupported readiness phase')
    if type(profile['readiness']['image_build_enabled']) is not bool:
        raise ProfileError('Build enablement must be an explicit boolean')
    if not isinstance(profile['readiness']['reason'], str) or not profile['readiness']['reason'].strip():
        raise ProfileError('Foundation needs an actionable readiness reason')
    _keys(profile['contracts'], {'hardware', 'power'}, 'Contracts')
    for kind, validator in [('hardware', _hardware), ('power', _power)]:
        _, value = _reference(root, profile['contracts'][kind],
                              'image/contracts/tyrell-zero-' + kind + '.json')
        validator(value)
    _keys(profile['vendor_inputs'], {'apps', 'camera'}, 'Vendor inputs')
    _, vendor = _reference(root, profile['vendor_inputs']['apps'], 'image/baseline-apps.json')
    _reference(root, profile['vendor_inputs']['camera'], 'image/camera-packages.json')
    if (vendor['pi_gen_commit'] != PINS['pi_gen_commit'] or
            vendor['drivers']['commit'] != PINS['bsp_commit'] or
            vendor['drivers']['sha256'] != PINS['bsp_archive_sha256'] or
            vendor['launcher']['sha256'] != PINS['applaunch_deb_sha256'] or
            vendor['launcher']['package_version'] != PINS['applaunch_version']):
        raise ProfileError('Vendor package lock conflicts with profile pins')
    for name in LOCK_PATHS.values():
        repository_path(root, name, required=False)
    if sha256(repository_path(root, LOCK_PATHS['seed_apt'])) != PINS['seed_apt_lock_sha256']:
        raise ProfileError('Retained seed APT lock does not match profile')
    groups = profile['runtime_roots']
    _keys(groups, {'session', 'network', 'audio', 'storage', 'maintenance', 'vendor',
                   'camera', 'graphics'}, 'Runtime roots')
    for name, packages in groups.items():
        if not isinstance(packages, list) or not packages or len(set(packages)) != len(packages):
            raise ProfileError('Missing/duplicate runtime roots: ' + name)
        if any(not isinstance(item, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', item)
               for item in packages):
            raise ProfileError('Invalid runtime package name: ' + name)
    for group, needed in {'session': {'greetd', 'libpam-systemd', 'dbus-user-session', 'polkitd'},
                           'network': {'network-manager', 'wpasupplicant', 'openssh-server', 'bluez', 'avahi-daemon'},
                           'audio': {'pipewire', 'pipewire-pulse', 'wireplumber', 'rtkit', 'alsa-utils'},
                           'storage': {'udisks2'}, 'maintenance': {'apt', 'ca-certificates'},
                           'vendor': {'applaunch'}, 'camera': {'libcamera0.7', 'libcamera-ipa', 'rpicam-apps'},
                           'graphics': {'libdrm2', 'libegl1', 'libgles2'}}.items():
        if not needed.issubset(groups[group]):
            raise ProfileError('Protected runtime roots omitted: ' + group)
    return profile


def load_profile(path=DEFAULT_PROFILE, root=ROOT):
    """Reject malformed structures consistently rather than leaking raw errors."""
    try:
        return _load_profile(path, root=root)
    except ProfileError:
        raise
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise ProfileError('Invalid profile/contract structure: ' + str(error)) from error


def inspect_profile(path=DEFAULT_PROFILE, root=ROOT):
    """Evaluate bound input/test receipts without starting a VM or image build."""
    root = Path(root).resolve()
    profile = load_profile(path, root=root)
    paths = [repository_path(root, path)]
    paths += [repository_path(root, item['path']) for item in profile['contracts'].values()]
    paths += [repository_path(root, item['path']) for item in profile['vendor_inputs'].values()]
    paths.append(repository_path(root, LOCK_PATHS['seed_apt']))
    blockers = []
    documents = {}
    for role in ['build', 'source', 'apt', 'apps', 'native_compat']:
        name = LOCK_PATHS[role]
        target = repository_path(root, name, required=False)
        present = target.is_file()
        data = None
        if present:
            data = _json(target)  # Invalid JSON is never a valid captured input.
            if role == 'build' and (type(data.get('schema_version')) is not int
                                    or data['schema_version'] != 1 or 'schema' in data):
                raise ProfileError('Unsupported Zero build-input lock schema')
            paths.append(target)
        documents[role] = data
    qualified = set()
    input_error = None
    if all(documents.get(role) for role in ('apt','apps','native_compat')):
        try:
            lock_api=runpy.run_path(str(repository_path(root,'image/zero-lock.py')))
            lock_api['validate_inputs'](root)
            qualified.update(('apt','apps','native_compat'))
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
            input_error=str(error)
    source=documents.get('source') or {}
    if ('native_compat' in qualified and source.get('complete') is True
            and source.get('runtime_closure_sha256')==sha256(root/LOCK_PATHS['native_compat'])
            and source.get('native_qualification_sha256')==(documents['apps'].get('qualification') or {}).get('sha256')):
        qualified.add('source')
    proof_ok=False
    proof_error=None
    try:
        reference=(documents.get('build') or {}).get('pipeline_qualification',{})
        if not isinstance(reference,dict) or not {'path','sha256'}.issubset(reference):
            raise ProfileError('Missing bound pre-image pipeline qualification record')
        proof_path=repository_path(root,reference['path'])
        proof=_json(proof_path,reference['sha256']);paths.append(proof_path)
        if (proof.get('schema')!=1 or proof.get('profile')!='tyrell-zero' or proof.get('status')!='passed'
                or proof.get('scope')!='pre-image-pipeline' or proof.get('hardware_qualified') is not False
                or proof.get('tests')!={'host':'passed','runner_prepare':'passed','runtime_root':'passed',
                                        'runner_failure':'passed','bsp_replay':'passed'}):
            raise ProfileError('Pipeline qualification scope/tests differ')
        bound={Path(LOCK_PATHS[role]).name:sha256(root/LOCK_PATHS[role])
               for role in ('source','apt','apps','native_compat')}
        if proof.get('locks_sha256')!=bound:raise ProfileError('Pipeline qualification locks changed')
        versions={item['Package']:item['Version'] for item in documents['apps']['packages']}
        if (documents.get('build') or {}).get('packages')!=versions:
            raise ProfileError('Build package versions differ from qualified custom payloads')
        required={'image/zero_pipeline.py','image/zero_resume.py','image/run-zero-job.py',
                  'image/prepare-zero.py','builder/build-zero-image.sh','image/zero_activation.py',
                  'image/verify-zero.py','image/zero-lock.py','image/build-image.py','image/profiles.py',
                  'builder/zero-bsp-check.py','builder/zero-pipeline-smoke.py'}
        files=proof.get('files_sha256',{})
        if not required.issubset(files):raise ProfileError('Pipeline implementation proof is incomplete')
        for name,identity in files.items():
            target=repository_path(root,name)
            if sha256(target)!=identity:raise ProfileError('Qualified implementation changed: '+name)
            paths.append(target)
        runtime=proof['runtime_verification']
        record_path=repository_path(root,runtime['path'])
        record=_json(record_path,runtime['sha256']);paths.append(record_path)
        if (record.get('status')!='passed' or not record.get('checks') or
                any(item.get('passed')is not True for item in record['checks'])
                or record.get('hardware_qualified') is not False):
            raise ProfileError('Assembled runtime qualification did not pass')
        for key in ('runner_prepare','host_tests'):
            ref=proof[key];target=repository_path(root,ref['path'])
            value=_json(target,ref['sha256']);paths.append(target)
            if value.get('schema')!=1 or value.get('status')!='passed':
                raise ProfileError('Pipeline preparation/host qualification failed: '+key)
            identities=value.get('files_sha256',{})
            needed=({'image/prepare-zero.py','builder/build-zero-image.sh','image/zero-lock.py'}
                    if key=='runner_prepare' else set(files))
            if not needed.issubset(identities):
                raise ProfileError('Pipeline preparation/host implementation binding is incomplete: '+key)
            for name,identity in identities.items():
                if files.get(name)!=identity:
                    raise ProfileError('Pipeline preparation/host implementation changed: '+name)
            if key=='runner_prepare' and (value.get('scope')!='captured-vendor-preparation'
                    or value.get('stage2_export')is not True
                    or value.get('image_build_performed')is not False
                    or value.get('offline_apt')is not True
                    or value.get('custom_install_order')!=['store-diversion','custom-debs','runtime-detach','activation']):
                raise ProfileError('Captured vendor preparation did not pass')
            if key=='host_tests' and (value.get('scope')!='pre-image-host-suites'
                    or not value.get('suites') or any(
                        item.get('exit_code')!=0 or type(item.get('tests'))is not int
                        or item['tests']<1 or item.get('failures')!=0 or item.get('errors')!=0
                        for item in value['suites'])):
                raise ProfileError('Host pipeline test suites did not pass')
        for key,scope in [('runner_failure_fixture','actual-zero-wrapper-failure-fixtures-only'),
                          ('bsp_replay','two-fixed-path-bsp-builds-in-private-disposable-overlay-roots')]:
            ref=proof[key];target=repository_path(root,ref['path'])
            value=_json(target,ref['sha256']);paths.append(target)
            if (value.get('status')!='passed' or value.get('scope')!=scope
                    or value.get('full_image_build')is not False
                    or value.get('physical_hardware_qualified')is not False):
                raise ProfileError('Native pipeline evidence failed: '+key)
            if key=='runner_failure_fixture' and (not value.get('checks') or any(item.get('passed')is not True for item in value['checks'])):
                raise ProfileError('Native failure/interruption checks failed')
            if key=='runner_failure_fixture' and (value.get('runner_sha256')!=sha256(root/'image/run-zero-job.py')
                    or value.get('pipeline_sha256')!=sha256(root/'image/zero_pipeline.py')
                    or value.get('smoke_helper_sha256')!=sha256(root/'builder/zero-pipeline-smoke.py')):
                raise ProfileError('Native failure proof belongs to changed runner code')
            if key=='bsp_replay' and any(value.get(name)is not True for name in (
                    'retained_bsp_payload_unchanged','retained_package_database_unchanged',
                    'retained_sampled_headers_unchanged','live_builder_dpkg_unchanged')):
                raise ProfileError('BSP proof changed retained or live state')
            if key=='bsp_replay' and (value.get('native_compat_sha256')!=sha256(root/LOCK_PATHS['native_compat'])
                    or value.get('source_archive_sha256')!=PINS['bsp_archive_sha256']
                    or value.get('helper_sha256')!=sha256(root/'builder/zero-bsp-check.py')):
                raise ProfileError('BSP proof belongs to different native/source inputs')
        proof_ok=True
        qualified.add('build')
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        proof_error=str(error)
    for role in ('build','source','apt','apps','native_compat'):
        if role in qualified:continue
        data=documents.get(role)
        incomplete=role=='source' and data is not None and data.get('complete') is False
        code=('incomplete-source' if incomplete else 'unqualified-'+role) if data is not None else 'missing-'+role
        blockers.append({'code':code,'path':LOCK_PATHS[role],
                         'detail':('Complete and qualify the partial source audit: ' if incomplete else 'Qualify the frozen ')+LOCK_PATHS[role],
                         **({'reason':input_error} if role in ('apt','apps','native_compat') and input_error else {})})
    for code,detail in [
        ('native-wizard-integration','Qualify native Wizard/runtime identities against the detached frozen Zero root.'),
        ('appstore-integration','Qualify the guarded AppStore and final baseline against the detached frozen Zero root.'),
        ('zero-runner','Qualify the profile runner, captured preparation and attempt/resume boundaries.'),
        ('zero-verifier','Run the independent Zero verifier against the detached frozen runtime root.')]:
        if not proof_ok or not {'source','apt','apps','native_compat'}.issubset(qualified):
            blockers.append({'code':code,'detail':detail,**({'reason':proof_error} if proof_error else {})})
    if profile['readiness']['image_build_enabled'] is not True:
        blockers.append({'code':'build-disabled','detail':'Profile explicitly disables image builds.'})
    return {'schema': 1, 'profile_id': profile['id'], 'plan_revision': profile['plan_revision'],
            'structural_valid': True, 'build_ready': not blockers, 'phase': profile['readiness']['phase'],
            'stages': profile['stages'], 'output': profile['output'],
            'power_preset': profile['selected_power_preset'],
            'hardware_capabilities': len(HARDWARE_IDS),
            'input_sha256': {str(item.relative_to(root)): sha256(item) for item in sorted(set(paths))},
            'blockers': blockers}


def require_build_ready(path=DEFAULT_PROFILE, root=ROOT):
    report = inspect_profile(path, root=root)
    if not report['build_ready']:
        raise ProfileNotReady(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check'])
    parser.add_argument('--profile', default=DEFAULT_PROFILE)
    parser.add_argument('--require-ready', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = inspect_profile(args.profile)
        print(json.dumps(report, indent=2))
        return 2 if args.require_ready and not report['build_ready'] else 0
    except (ProfileError, OSError) as error:
        print('Profile validation failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
