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
        for name in [PROFILE, 'image/contracts/tyrell-zero-hardware-nov19.json',
                     'image/contracts/tyrell-zero-power.json',
                     'image/zero-baseline-apps.json', 'image/zero-camera-packages.json',
                     'locks/tyrell-zero-seed-apt.json']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)

    def read(self, name=PROFILE):
        return json.loads((self.root / name).read_text())

    def write(self, value, name=PROFILE):
        (self.root / name).write_text(json.dumps(value, indent=2) + '\n')

    def contract(self, kind, mutate, *, rebind=True):
        name = self.read()['contracts'][kind]['path']
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

    def test_zero_refresh_cannot_select_the_legacy_seed_or_vendor_locks(self):
        original = self.read()
        for role, legacy in [('apps', 'image/baseline-apps.json'),
                             ('camera', 'image/camera-packages.json')]:
            value = copy.deepcopy(original);value['vendor_inputs'][role]['path'] = legacy
            self.write(value);self.invalid('input path')
        value = copy.deepcopy(original);value['lock_paths']['seed_apt'] = 'locks/image-apt.json'
        self.write(value);self.invalid('lock_paths')

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
        report=profiles.inspect_profile(PROFILE,root=self.root)
        self.assertFalse(report['build_ready'])
        self.assertIn('zero-runner',{item['code'] for item in report['blockers']})

    def test_profile_cannot_reuse_full_source_apt_selection(self):
        value = self.read()
        value['lock_paths']['apt'] = 'locks/tyrell-zero-seed-apt.json'
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

    def pipeline_fixture(self):
        """Bind a small proof graph; mock only the separately tested APT validator."""
        profile = self.read();profile['readiness'].update(phase='pipeline', image_build_enabled=True)
        self.write(profile)
        implementations = [
            'image/zero_pipeline.py', 'image/zero_resume.py', 'image/run-zero-job.py',
            'image/prepare-zero.py', 'image/prepare-vendor.py', 'image/apt-lock.py', 'builder/build-zero-image.sh', 'image/zero_activation.py',
            'image/verify-zero.py', 'image/zero-lock.py', 'image/build-image.py', 'image/profiles.py',
            'builder/zero-bsp-check.py', 'builder/zero-pipeline-smoke.py']
        for name in implementations:
            target = self.root / name;target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        inputs = [PROFILE, profiles.LOCK_PATHS['seed_apt'],
                  *(item['path'] for item in profile['vendor_inputs'].values()),
                  *(item['path'] for item in profile['contracts'].values())]
        files = {name: profiles.sha256(self.root / name) for name in [*implementations, *inputs]}
        versions = {'tyrell-status': '0.1.0-1', 'tyrell-zero-session': '0.3.1-1',
                    'tyrell-zero-power-policy': '0.1.1-1', 'tyrell-zero-store': '0.1.1-1'}
        artifact = self.root / 'artifacts/fixture';artifact.mkdir(parents=True, exist_ok=True)
        def put(name, value):
            target = self.root / name;target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
        def reference(name):
            return {'path': name, 'sha256': profiles.sha256(self.root / name)}
        source_path = 'artifacts/fixture/source-qualification.json'
        put(source_path, {'schema': 1, 'status': 'passed', 'scope': 'pre-image-integration',
                          'physical_hardware_qualified': False})
        put(profiles.LOCK_PATHS['apt'], {'schema': 1, 'profile': 'tyrell-zero', 'complete': True})
        put(profiles.LOCK_PATHS['native_compat'],
            {'schema': 1, 'profile': 'tyrell-zero', 'complete': True, 'hardware_qualified': False})
        put(profiles.LOCK_PATHS['apps'], {'schema': 1, 'profile': 'tyrell-zero', 'complete': True,
            'packages': [{'Package': name, 'Version': version} for name, version in versions.items()],
            'qualification': reference(source_path)})
        put(profiles.LOCK_PATHS['source'], {'schema_version': 1, 'complete': True,
            'runtime_closure_sha256': profiles.sha256(self.root / profiles.LOCK_PATHS['native_compat']),
            'native_qualification_sha256': profiles.sha256(self.root / source_path)})
        reports = {
            'runtime_verification': {'schema_version': 1, 'status': 'passed', 'hardware_qualified': False,
                                     'checks': [{'name': 'detached-root-closure', 'passed': True}]},
            'runner_prepare': {'schema': 1, 'status': 'passed', 'scope': 'captured-vendor-preparation',
                'stage2_export': True, 'image_build_performed': False, 'offline_apt': True,
                'custom_install_order': ['store-diversion', 'custom-debs', 'runtime-detach', 'activation'],
                'files_sha256': files.copy()},
            'host_tests': {'schema': 1, 'status': 'passed', 'scope': 'pre-image-host-suites',
                'suites': [{'name': 'image', 'exit_code': 0, 'tests': 42, 'failures': 0, 'errors': 0},
                           {'name': 'builder', 'exit_code': 0, 'tests': 12, 'failures': 0, 'errors': 0}],
                'files_sha256': files.copy()},
            'runner_failure_fixture': {'status': 'passed', 'scope': 'actual-zero-wrapper-failure-fixtures-only',
                'full_image_build': False, 'physical_hardware_qualified': False,
                'checks': [{'name': 'durable-failure-and-replay', 'passed': True}],
                'runner_sha256': files['image/run-zero-job.py'],
                'pipeline_sha256': files['image/zero_pipeline.py'],
                'smoke_helper_sha256': files['builder/zero-pipeline-smoke.py']},
            'bsp_replay': {'status': 'passed', 'scope': 'two-fixed-path-bsp-builds-in-private-disposable-overlay-roots',
                'full_image_build': False, 'physical_hardware_qualified': False,
                'retained_bsp_payload_unchanged': True, 'retained_package_database_unchanged': True,
                'retained_sampled_headers_unchanged': True, 'live_builder_dpkg_unchanged': True,
                'native_compat_sha256': profiles.sha256(self.root / profiles.LOCK_PATHS['native_compat']),
                'source_archive_sha256': profiles.PINS['bsp_archive_sha256'],
                'helper_sha256': files['builder/zero-bsp-check.py']}}
        proof = {'schema': 1, 'profile': 'tyrell-zero', 'status': 'passed', 'scope': 'pre-image-pipeline',
            'hardware_qualified': False,
            'tests': {'host': 'passed', 'runner_prepare': 'passed', 'runtime_root': 'passed',
                      'runner_failure': 'passed', 'bsp_replay': 'passed'},
            'locks_sha256': {Path(profiles.LOCK_PATHS[role]).name: profiles.sha256(self.root / profiles.LOCK_PATHS[role])
                              for role in ('source', 'apt', 'apps', 'native_compat')},
            'files_sha256': files}
        for key, record in reports.items():
            name = 'artifacts/fixture/' + key + '.json';put(name, record);proof[key] = reference(name)
        fixture = {'proof': proof, 'proof_path': 'artifacts/fixture/pipeline.json',
                   'build': {'schema_version': 1, 'packages': versions}}
        self.publish_pipeline_fixture(fixture)
        return fixture

    def publish_pipeline_fixture(self, fixture):
        self.write(fixture['proof'], fixture['proof_path'])
        fixture['build']['pipeline_qualification'] = {'path': fixture['proof_path'],
            'sha256': profiles.sha256(self.root / fixture['proof_path'])}
        self.write(fixture['build'], profiles.LOCK_PATHS['build'])

    def change_pipeline_report(self, fixture, key, mutate, *, rebind=True):
        ref = fixture['proof'][key];value = self.read(ref['path']);mutate(value);self.write(value, ref['path'])
        if rebind:
            ref['sha256'] = profiles.sha256(self.root / ref['path'])
            self.publish_pipeline_fixture(fixture)

    def inspect_pipeline_fixture(self):
        def validate(root): self.assertEqual(root, self.root)
        with patch.object(profiles.runpy, 'run_path', return_value={'validate_inputs': validate}):
            return profiles.inspect_profile(PROFILE, root=self.root)

    def assert_pipeline_blocked(self, text):
        report = self.inspect_pipeline_fixture()
        self.assertFalse(report['build_ready'])
        self.assertIn('unqualified-build', {item['code'] for item in report['blockers']})
        self.assertTrue(any(text in item.get('reason', '') for item in report['blockers']), report['blockers'])

    def test_fully_bound_pipeline_proof_enables_only_pre_image_build_readiness(self):
        fixture = self.pipeline_fixture()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.inspect_pipeline_fixture()
        self.assertTrue(report['build_ready'], report['blockers'])
        self.assertEqual(report['blockers'], [])
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        for key in ('runner_prepare', 'host_tests', 'runtime_verification', 'runner_failure_fixture', 'bsp_replay'):
            self.assertIn(fixture['proof'][key]['path'], report['input_sha256'])

    def test_changed_qualified_helper_bytes_disable_pipeline(self):
        for name in ('builder/zero-bsp-check.py', 'builder/zero-pipeline-smoke.py'):
            with self.subTest(name=name):
                self.pipeline_fixture()
                path = self.root / name;path.write_bytes(path.read_bytes() + b'\n# unqualified change\n')
                self.assert_pipeline_blocked('Qualified implementation changed')

    def test_helpers_cannot_be_omitted_from_all_bound_file_maps(self):
        fixture = self.pipeline_fixture()
        for name in ('builder/zero-bsp-check.py', 'builder/zero-pipeline-smoke.py'):
            fixture['proof']['files_sha256'].pop(name)
            for key in ('runner_prepare', 'host_tests'):
                self.change_pipeline_report(fixture, key, lambda record, name=name: record['files_sha256'].pop(name))
        self.publish_pipeline_fixture(fixture)
        self.assert_pipeline_blocked('implementation proof is incomplete')

    def test_baseline_helper_and_selected_inputs_cannot_be_omitted_from_rebound_proof(self):
        profile = self.read()
        critical = ['image/prepare-vendor.py', 'image/apt-lock.py', PROFILE, profiles.LOCK_PATHS['seed_apt'],
                    *(item['path'] for item in profile['vendor_inputs'].values()),
                    *(item['path'] for item in profile['contracts'].values())]
        for name in critical:
            with self.subTest(missing=name):
                fixture = self.pipeline_fixture()
                fixture['proof']['files_sha256'].pop(name)
                for key in ('runner_prepare', 'host_tests'):
                    self.change_pipeline_report(fixture, key,
                        lambda record, name=name: record['files_sha256'].pop(name))
                self.publish_pipeline_fixture(fixture)
                self.assert_pipeline_blocked('implementation proof is incomplete')

    def test_rebound_native_reports_must_name_the_actual_helpers(self):
        for key, field, reason in [('runner_failure_fixture', 'smoke_helper_sha256', 'changed runner code'),
                                   ('bsp_replay', 'helper_sha256', 'different native/source inputs')]:
            with self.subTest(key=key):
                fixture = self.pipeline_fixture()
                self.change_pipeline_report(fixture, key, lambda value, field=field: value.update({field: '0' * 64}))
                self.assert_pipeline_blocked(reason)

    def test_report_byte_tampering_is_rejected_before_promotion(self):
        fixture = self.pipeline_fixture()
        self.change_pipeline_report(fixture, 'host_tests', lambda value: value.update(status='failed'), rebind=False)
        self.assert_pipeline_blocked('checksum mismatch')

    def test_rebound_runner_preparation_must_show_offline_ordered_stage2_work(self):
        mutations = [lambda value: value.update(status='failed'), lambda value: value.update(scope='full-image'),
            lambda value: value.update(stage2_export=False), lambda value: value.update(image_build_performed=True),
            lambda value: value.update(offline_apt=False),
            lambda value: value.update(custom_install_order=['custom-debs', 'store-diversion', 'runtime-detach', 'activation'])]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fixture = self.pipeline_fixture();self.change_pipeline_report(fixture, 'runner_prepare', mutate)
                self.assert_pipeline_blocked('preparation')

    def test_runner_preparation_requires_current_implementation_bindings(self):
        fixture = self.pipeline_fixture()
        self.change_pipeline_report(fixture, 'runner_prepare',
            lambda value: value['files_sha256'].__setitem__('image/prepare-zero.py', '0' * 64))
        self.assert_pipeline_blocked('implementation changed')

    def test_host_reports_require_nonempty_successful_actual_test_counts(self):
        mutations = [lambda value: value.update(suites=[]), lambda value: value.update(scope='manual-review'),
            lambda value: value['suites'][0].update(exit_code=1), lambda value: value['suites'][0].update(tests=0),
            lambda value: value['suites'][0].update(tests=True), lambda value: value['suites'][0].update(failures=1),
            lambda value: value['suites'][0].update(errors=1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fixture = self.pipeline_fixture();self.change_pipeline_report(fixture, 'host_tests', mutate)
                self.assert_pipeline_blocked('Host pipeline test suites')

    def test_host_reports_must_bind_the_entire_qualified_implementation(self):
        for mutate in [lambda value: value['files_sha256'].pop('image/verify-zero.py'),
                       lambda value: value['files_sha256'].__setitem__('image/zero_pipeline.py', '0' * 64)]:
            with self.subTest(mutate=mutate):
                fixture = self.pipeline_fixture();self.change_pipeline_report(fixture, 'host_tests', mutate)
                self.assert_pipeline_blocked('implementation')

    def test_changed_custom_package_versions_cannot_reuse_pipeline_proof(self):
        fixture = self.pipeline_fixture()
        fixture['build']['packages']['tyrell-zero-session'] = '0.2.0-1'
        self.publish_pipeline_fixture(fixture)
        self.assert_pipeline_blocked('Build package versions differ')

    def test_rebound_runtime_failure_cannot_be_hidden_by_passed_summary_flags(self):
        fixture = self.pipeline_fixture()
        self.change_pipeline_report(fixture, 'runtime_verification', lambda value: value['checks'][0].update(passed=False))
        self.assert_pipeline_blocked('runtime qualification did not pass')

    def test_cli_distinguishes_valid_foundation_from_ready(self):
        report = profiles.inspect_profile(PROFILE, root=self.root)
        with patch.object(profiles, 'inspect_profile', return_value=report), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(profiles.main(['check']), 0)
            self.assertEqual(profiles.main(['check', '--require-ready']), 2)


if __name__ == '__main__':
    unittest.main()
