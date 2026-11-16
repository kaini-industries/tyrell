import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('tyrell_profiles', ROOT / 'image/profiles.py')
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)
PROFILE = 'image/profiles/tyrell-zero.json'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in [PROFILE, 'image/contracts/tyrell-zero-hardware.json',
                     'image/contracts/tyrell-zero-power.json',
                     'image/baseline-apps.json', 'image/camera-packages.json',
                     'locks/image-apt.json']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)

    def read(self, name=PROFILE):
        return json.loads((self.root / name).read_text())

    def write(self, value, name=PROFILE):
        (self.root / name).write_text(json.dumps(value, indent=2) + '\n')

    def contract(self, kind, mutate, *, rebind=True):
        name = 'image/contracts/tyrell-zero-' + kind + '.json'
        value = self.read(name)
        mutate(value)
        self.write(value, name)
        if rebind:
            profile = self.read()
            profile['contracts'][kind]['sha256'] = profiles.sha256(self.root / name)
            self.write(profile)

    def invalid(self, text=None):
        with self.assertRaises(profiles.ProfileError) as found:
            profiles.load_profile(PROFILE, root=self.root)
        if text:
            self.assertIn(text, str(found.exception))

    def test_valid_foundation_is_read_only_and_explicitly_not_build_ready(self):
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        report = profiles.inspect_profile(PROFILE, root=self.root)
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(report['structural_valid'])
        self.assertFalse(report['build_ready'])
        self.assertEqual(report['stages'], ['stage0', 'stage1', 'stage2'])
        self.assertEqual(report['hardware_capabilities'], 15)
        self.assertEqual(report['power_preset'], 'baseline')
        self.assertIn('zero-runner', {b['code'] for b in report['blockers']})

    def test_require_ready_cannot_fall_through_to_desktop_runner(self):
        with self.assertRaises(profiles.ProfileNotReady) as error:
            profiles.require_build_ready(PROFILE, root=self.root)
        self.assertFalse(error.exception.report['build_ready'])
        self.assertIn('AppStore', str(error.exception))

    def test_placeholder_locks_cannot_self_certify_build_readiness(self):
        for role, name in profiles.LOCK_PATHS.items():
            if role != 'seed_apt':
                self.write({'schema_version' if role == 'build' else 'schema': 1,
                            'complete': True, 'ready': True}, name)
        report = profiles.inspect_profile(PROFILE, root=self.root)
        self.assertFalse(report['build_ready'])
        self.assertIn('zero-verifier', {b['code'] for b in report['blockers']})
        self.assertIn('unqualified-apt', {b['code'] for b in report['blockers']})

    def test_rejects_stage4_and_missing_or_reordered_stage(self):
        original = self.read()
        for stages in [['stage0', 'stage1', 'stage2', 'stage4'], ['stage0', 'stage2'],
                       ['stage1', 'stage0', 'stage2']]:
            value = copy.deepcopy(original)
            value['stages'] = stages
            self.write(value)
            self.invalid('stages')

    def test_rejects_legacy_export_naming(self):
        value = self.read()
        value['output']['raw'] = 'tyrell-trixie-arm64.img'
        self.write(value)
        self.invalid('output')

    def test_rejects_an_optimized_default_before_qualification(self):
        value = self.read()
        value['selected_power_preset'] = 'combined'
        self.write(value)
        self.invalid('selected_power_preset')

    def test_rejects_original_launcher_or_vendor_pin_changes(self):
        original = self.read()
        for key in ['applaunch_main_sha256', 'applaunch_source', 'bsp_commit']:
            value = copy.deepcopy(original)
            value['pins'][key] = '0' * len(value['pins'][key])
            self.write(value)
            self.invalid('pins')

    def test_build_enabled_flag_is_not_an_implementation(self):
        value = self.read()
        value['readiness']['image_build_enabled'] = True
        self.write(value)
        self.invalid('build enablement')

    def test_profile_cannot_reuse_full_source_apt_selection(self):
        value = self.read()
        value['lock_paths']['apt'] = 'locks/image-apt.json'
        self.write(value)
        self.invalid('lock_paths')

    def test_profile_root_escape_and_credential_inputs_are_rejected(self):
        for path in ['../outside.json', '/tmp/outside.json', '.env', 'image/.env.json']:
            with self.subTest(path=path), self.assertRaises(profiles.ProfileError):
                profiles.load_profile(path, root=self.root)

    def test_contract_reference_traversal_is_rejected(self):
        value = self.read()
        value['contracts']['power']['path'] = 'image/contracts/../../outside.json'
        self.write(value)
        self.invalid('input path')

    def test_symlink_profile_and_symlink_parent_are_rejected(self):
        path = self.root / PROFILE
        content = path.read_bytes()
        path.unlink()
        original = self.root / 'original.json'
        original.write_bytes(content)
        path.symlink_to(original)
        self.invalid('Symlinks')
        path.unlink()
        path.write_bytes(content)
        contracts = self.root / 'image/contracts'
        contracts.rename(self.root / 'real-contracts')
        contracts.symlink_to(self.root / 'real-contracts', target_is_directory=True)
        self.invalid('Symlinks')

    def test_symlink_future_lock_is_not_followed(self):
        target = self.root / 'outside.json'
        target.write_text('{"secret":"never-read"}')
        (self.root / profiles.LOCK_PATHS['apt']).symlink_to(target)
        self.invalid('Symlinks')

    def test_duplicate_json_keys_are_rejected(self):
        (self.root / PROFILE).write_text('{"schema":1,"schema":1}')
        self.invalid('Duplicate JSON key')

    def test_json_boolean_is_not_integer_schema_one(self):
        value = self.read()
        value['schema'] = True
        self.write(value)
        self.invalid('schema')

    def test_unknown_fields_do_not_silently_take_effect(self):
        value = self.read()
        value['image_command'] = 'legacy-stage4'
        self.write(value)
        self.invalid('unsupported fields')

    def test_changed_contract_bytes_must_match_profile_hash(self):
        self.contract('hardware', lambda value: value.update(spare_card_required=False), rebind=False)
        self.invalid('checksum mismatch')

    def test_rebound_contract_still_cannot_drop_hardware(self):
        self.contract('hardware', lambda value: value['capabilities'].pop())
        self.invalid('fifteen')

    def test_rebound_contract_cannot_fake_hardware_acceptance(self):
        self.contract('hardware', lambda value: value['capabilities'][0].update(qualification='passed'))
        self.invalid('qualification')

    def test_rebound_contract_cannot_change_initial_delay_or_factor(self):
        for field, setting in [('ethernet_autosuspend_delay_ms', -1),
                               ('ondemand_sampling_down_factor', 2)]:
            value = self.read('image/contracts/tyrell-zero-power.json')
            original = copy.deepcopy(value)
            self.contract('power', lambda item: item['presets']['cpu'].update({field: setting}))
            self.invalid('presets')
            self.write(original, 'image/contracts/tyrell-zero-power.json')
            p = self.read()
            p['contracts']['power']['sha256'] = profiles.sha256(self.root / 'image/contracts/tyrell-zero-power.json')
            self.write(p)

    def test_rebound_contract_cannot_promote_usb_only_savings(self):
        self.contract('power', lambda value: value['release_gates'].update(usb_only_promotion_allowed=True))
        self.invalid('battery release gates')

    def test_rebound_contract_cannot_enable_host_tuning_during_install(self):
        self.contract('power', lambda value: value['safety'].update(install_sysfs_writes=True))
        self.invalid('installation/activation safety')

    def test_runtime_roots_cannot_omit_native_support(self):
        value = self.read()
        value['runtime_roots']['network'].remove('bluez')
        self.write(value)
        self.invalid('Protected runtime roots')

    def test_retained_seed_or_vendor_bytes_cannot_change_unnoticed(self):
        path = self.root / profiles.LOCK_PATHS['seed_apt']
        path.write_text('{}')
        self.invalid('seed APT')

    def test_existing_malformed_future_lock_stops_inspection(self):
        self.write({'ignored': True}, profiles.LOCK_PATHS['source'])
        (self.root / profiles.LOCK_PATHS['source']).write_text('{broken')
        with self.assertRaises(profiles.ProfileError):
            profiles.inspect_profile(PROFILE, root=self.root)

    def test_partial_source_audit_is_reported_as_incomplete(self):
        self.write({'schema': 1, 'complete': False,
                    'commit': profiles.PINS['applaunch_source']}, profiles.LOCK_PATHS['source'])
        report = profiles.inspect_profile(PROFILE, root=self.root)
        self.assertIn('incomplete-source', {b['code'] for b in report['blockers']})
        self.assertIn(profiles.LOCK_PATHS['source'], report['input_sha256'])

    def test_build_input_lock_is_hashed_but_cannot_enable_image_pipeline(self):
        self.write({'schema_version': 1, 'version': '0.1.0-1'}, profiles.LOCK_PATHS['build'])
        report = profiles.inspect_profile(PROFILE, root=self.root)
        self.assertEqual(report['input_sha256'][profiles.LOCK_PATHS['build']],
                         profiles.sha256(self.root / profiles.LOCK_PATHS['build']))
        self.assertFalse(report['build_ready'])
        self.write({'schema_version': True}, profiles.LOCK_PATHS['build'])
        with self.assertRaisesRegex(profiles.ProfileError, 'build-input lock schema'):
            profiles.inspect_profile(PROFILE, root=self.root)

    def test_malformed_nested_values_raise_profile_error(self):
        value = self.read()
        value['runtime_roots']['session'] = [{}]
        self.write(value)
        self.invalid('structure')

    def test_cli_distinguishes_valid_foundation_from_ready(self):
        report = profiles.inspect_profile(PROFILE, root=self.root)
        with patch.object(profiles, 'inspect_profile', return_value=report), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(profiles.main(['check']), 0)
            self.assertEqual(profiles.main(['check', '--require-ready']), 2)


if __name__ == '__main__':
    unittest.main()
