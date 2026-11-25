"""Regression checks for stale identities and false compatibility acceptance."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('compatibility_ledger', Path(__file__).resolve().parents[1] / 'ledger.py')
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for roots in ledger.APP_ROOTS.values():
            for name in roots:
                path = self.root / name
                if name.startswith('packages/'):
                    path.mkdir(parents=True, exist_ok=True)
                    (path / 'source.cpp').write_text('int source = 1;\n')
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{}\n')
        self.package = self.root / 'dist/app.deb'
        self.package.parent.mkdir()
        self.package.write_bytes(b'retained exact package bytes')
        digest = ledger.sha256(self.package)
        manifest = self.root / 'dist/manifest.json'
        manifest.write_text(json.dumps({'package_sha256': digest}))
        report = self.root / 'report.md'
        report.write_text('Historical artifact ' + digest + '\n')
        evidence = [{'id': 'report', 'path': 'report.md', 'sha256': ledger.sha256(report)},
                    {'id': 'manifest', 'path': 'dist/manifest.json', 'sha256': ledger.sha256(manifest)}]
        subjects = [{'id': app, 'component': app, 'kind': 'current-source', 'version': 'test',
                     'source_identity': 'fixture snapshot', 'availability': 'checkout',
                     'roots': list(roots), 'sha256': ledger.source_digest(self.root, roots)}
                    for app, roots in ledger.APP_ROOTS.items()]
        subjects.append({'id': 'retained', 'component': 'status', 'kind': 'retained-package',
                         'version': '1-1', 'source_identity': 'manifest source identity',
                         'availability': 'checkout', 'path': 'dist/app.deb',
                         'sha256': digest, 'manifest': 'manifest'})
        records = []
        for app in ledger.APP_ROOTS:
            for target in ('original-m5', 'tyrell-zero', 'gh0st'):
                records.append(self.record(app + '-' + target, app, target))
        records.append(self.record('retained-device', 'retained', 'original-m5',
                                   result='passed', scope='device-tested', historical=True))
        for target in ('stock-derived', 'full-source', 'tyrell-zero'):
            subjects.append({'id': target + '-image', 'component': 'image', 'kind': 'file-image',
                             'version': 'historical', 'source_identity': 'recorded recipe commit',
                             'availability': 'external-not-revalidated', 'sha256': digest})
            records.append(self.record(target + '-files', target + '-image', target,
                                       result='passed', scope='automated-tested', historical=True))
        self.data = {'schema': 1, 'kind': 'compatibility-evidence-ledger',
                     'reviewed_date': '2026-11-24', 'reviewed_revision': '1' * 40,
                     'scope': 'fixture evidence', 'grants_release_approval': False,
                     'evidence': evidence, 'subjects': subjects, 'records': records}

    @staticmethod
    def record(identity, subject, target, result='not-tested', scope='not-tested', historical=False):
        return {'id': identity, 'subject': subject, 'target': target, 'scenario': 'fixture scenario',
                'implementation': 'implemented', 'result': result, 'evidence_scope': scope,
                'binding': 'historical-exact-artifact' if historical else 'current-source',
                'toolchain': 'fixture toolchain', 'evidence': ['report'],
                'limitations': ['Finite fixture scope only.']}

    def errors(self):
        return ledger.validate_ledger(self.root, self.data)

    def test_valid_distinct_current_and_historical_evidence(self):
        self.assertEqual(self.errors(), [])

    def test_source_edit_and_new_file_invalidate_snapshot(self):
        path = self.root / 'packages/tyrell-status/new_header.hpp'
        path.write_text('int changed;\n')
        self.assertTrue(any('current source changed' in e for e in self.errors()))

    def test_source_boundary_cannot_drop_a_shared_dependency(self):
        row = next(s for s in self.data['subjects'] if s['id'] == 'chess')
        row['roots'].remove('packages/tyrell-game')
        self.assertTrue(any('incomplete app source boundary' in e for e in self.errors()))

    def test_shared_source_drift_invalidates_each_consumer(self):
        common = self.root / 'packages/tyrell-app-common/commands.hpp'
        common.write_text('// changed command contract\n')
        errors = self.errors()
        self.assertTrue(any('video' in error and 'current source changed' in error for error in errors))
        self.assertTrue(any('chess' in error and 'current source changed' in error for error in errors))
        self.assertTrue(any('status' in error and 'current source changed' in error for error in errors))
        self.assertTrue(any('catalog' in error and 'current source changed' in error for error in errors))
        self.assertTrue(any('sandline' in error and 'current source changed' in error for error in errors))

    def test_retained_package_tamper_fails_even_with_unchanged_reports(self):
        self.package.write_bytes(b'changed package')
        self.assertTrue(any('retained package bytes' in e for e in self.errors()))

    def test_package_cannot_be_rebound_without_its_manifest(self):
        self.package.write_bytes(b'replacement package')
        subject = next(s for s in self.data['subjects'] if s['id'] == 'retained')
        subject['sha256'] = ledger.sha256(self.package)
        self.assertTrue(any('differs from bound manifest' in e for e in self.errors()))

    def test_changed_report_needs_reviewed_binding(self):
        (self.root / 'report.md').write_text('unsupported broader claim')
        self.assertTrue(any('evidence bytes changed' in e for e in self.errors()))

    def test_unsafe_and_symlink_evidence_paths_are_rejected(self):
        for path in ('../report.md', '/etc/passwd', 'dist//manifest.json'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    ledger.safe_file(self.root, path)
        (self.root / 'alias').symlink_to(self.root / 'dist', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            ledger.safe_file(self.root, 'alias/manifest.json')

    def test_source_snapshot_is_not_physical_package_acceptance(self):
        self.data['records'][0].update(result='passed', evidence_scope='device-tested')
        self.assertTrue(any('physical acceptance requires exact' in e for e in self.errors()))

    def test_skipped_or_planned_scenario_cannot_claim_success(self):
        for scope, implementation in [('not-tested', 'implemented'), ('automated-tested', 'planned')]:
            with self.subTest(scope=scope):
                modified = copy.deepcopy(self.data)
                modified['records'][0].update(result='passed', evidence_scope=scope, implementation=implementation)
                self.assertTrue(any('cannot claim a test outcome' in e
                                    for e in ledger.validate_ledger(self.root, modified)))

    def test_new_current_identity_does_not_promote_historical_result(self):
        (self.root / 'packages/tyrell-status/source.cpp').write_text('int source = 2;\n')
        source = next(s for s in self.data['subjects'] if s['id'] == 'status')
        source['sha256'] = ledger.source_digest(self.root, source['roots'])
        self.assertEqual(self.errors(), [])
        current = [r for r in self.data['records'] if r['subject'] == 'status']
        self.assertEqual({r['result'] for r in current}, {'not-tested'})

    def test_missing_target_or_app_coverage_is_not_a_pass(self):
        self.data['records'] = [r for r in self.data['records'] if r['id'] != 'beacon-gh0st']
        self.assertTrue(any('coverage missing' in e for e in self.errors()))

    def test_external_image_is_not_treated_as_a_checked_local_file(self):
        image = next(s for s in self.data['subjects'] if s['kind'] == 'file-image')
        image['path'] = 'image-artifacts/current.img'
        self.assertTrue(any('must not imply local byte verification' in e for e in self.errors()))
        image.pop('path')
        image['sha256'] = '0' * 64
        self.assertTrue(any('digest is absent from bound evidence' in e for e in self.errors()))

    def test_duplicate_or_empty_records_and_release_approval_are_rejected(self):
        self.data['records'].append(copy.deepcopy(self.data['records'][0]))
        self.data['grants_release_approval'] = True
        errors = self.errors()
        self.assertTrue(any('duplicate id' in e for e in errors))
        self.assertTrue(any('deny release approval' in e for e in errors))
        self.data['records'] = []
        self.assertTrue(any('expected a nonempty list' in e for e in self.errors()))

    def test_malformed_enum_and_reference_types_return_errors(self):
        cases = [('subjects', 'kind'), ('subjects', 'component'),
                 ('records', 'subject'), ('records', 'target'),
                 ('records', 'evidence_scope'), ('records', 'result'),
                 ('records', 'implementation'), ('records', 'binding'),
                 ('records', 'evidence')]
        for section, key in cases:
            with self.subTest(section=section, key=key):
                modified = copy.deepcopy(self.data)
                modified[section][0][key] = {'unsupported': 'object'}
                self.assertTrue(ledger.validate_ledger(self.root, modified))

    def test_duplicate_json_fields_cannot_hide_an_approval_claim(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"grants_release_approval": true, "grants_release_approval": false}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            ledger.load_ledger(self.root, 'duplicate.json')


if __name__ == '__main__':
    unittest.main()
