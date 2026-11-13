import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

validate = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'resume.py'))['validate']


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.old, self.new = [Path(self.temporary.name) / name for name in ('old', 'new')]
        for tree in (self.old, self.new):
            tree.mkdir()
            input_root = '/var/tmp/jobs/' + tree.name + '/input'
            for name in ('scripts/common', 'stage0/prerun.sh', 'stage1/prerun.sh',
                         'stage2/custom.sh', 'stage3/prerun.sh', 'stage4/final.sh'):
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('verified recipe ' + input_root + '/assets\n')
            (tree / 'config').write_text('STAGE_LIST=/var/tmp/tyrell-image-work/pi-gen/stage0\n')
            report = {'pi_gen_commit': 'baseline', 'drivers_commit': 'driver',
                      'input_root': input_root, 'input_assets_sha256': {'app.deb': 'a' * 64},
                      'apt_profile': 'frozen', 'apt_lock_sha256': 'b' * 64}
            self.write_report(tree, report)

    def write_report(self, tree, report):
        report['prepared_files_sha256'] = {
            str(path.relative_to(tree)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tree.rglob('*') if path.is_file() and path.name != 'tyrell-prepared.json'}
        (tree / 'tyrell-prepared.json').write_text(json.dumps(report))

    def change_report(self, tree, **values):
        report = json.loads((tree / 'tyrell-prepared.json').read_text())
        report.update(values)
        self.write_report(tree, report)

    def test_immutable_input_relocation_and_frozen_stage_changes_can_resume(self):
        (self.old / 'stage0/SKIP').touch()
        validate(self.old, self.new, 'stage4')
        validate(self.old, self.new, 'stage2')

    def test_changed_asset_or_apt_closure_is_rejected(self):
        for values in ({'input_assets_sha256': {'app.deb': 'c' * 64}}, {'apt_lock_sha256': 'c' * 64}):
            with self.subTest(values=values):
                self.change_report(self.new, **values)
                with self.assertRaisesRegex(ValueError, 'Resume inputs differ'):
                    validate(self.old, self.new, 'stage4')

    def test_retained_source_tampering_is_rejected(self):
        (self.old / 'stage1/prerun.sh').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed after preparation'):
            validate(self.old, self.new, 'stage2')

    def test_changed_earlier_stage_or_global_script_is_rejected(self):
        for name in ('stage2/custom.sh', 'scripts/common'):
            with self.subTest(name=name):
                (self.new / name).write_text('different source')
                self.change_report(self.new)
                with self.assertRaisesRegex(ValueError, 'Retained stage source differs'):
                    validate(self.old, self.new, 'stage4')

    def test_unrecorded_earlier_stage_file_is_rejected(self):
        (self.old / 'stage1/unrecorded').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'file set differs'):
            validate(self.old, self.new, 'stage4')

    def test_rebuilt_stage_can_change(self):
        (self.new / 'stage4/final.sh').write_text('changed final stage')
        self.change_report(self.new)
        validate(self.old, self.new, 'stage4')

    def test_legacy_missing_input_identity_fails_closed(self):
        self.change_report(self.old, input_root='')
        with self.assertRaisesRegex(ValueError, 'fresh build'):
            validate(self.old, self.new, 'stage4')


if __name__ == '__main__':
    unittest.main()
