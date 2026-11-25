"""Legacy/Zero vendor recipe selection with synthetic, exact-pinned inputs."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
API = runpy.run_path(str(ROOT / 'image/prepare-vendor.py'))


class VendorPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.image = self.root / 'image'; self.image.mkdir()
        self.vendor = self.root / 'vendor'; self.vendor.mkdir()
        self.assets = self.root / 'assets'; self.assets.mkdir()
        self.zero = True
        self.driver = '\n'.join([
            '# Always build the latest driver from the main branch.', 'rm -rf /tmp/dtoverlays',
            'git clone --depth=1 https://github.com/m5stack/m5stack-linux-dtoverlays.git ' + chr(92),
            '    /tmp/dtoverlays', 'git -C /tmp/dtoverlays rev-parse HEAD ' + chr(92),
            '    > /etc/cardputerzero-dtoverlays.commit', ''])
        chunks = {'build.sh': '#!/bin/bash\n',
                  'stage2/05-cardputerzero/01-run.sh': "#!/bin/bash\non_chroot << 'CHROOT'\n" + self.driver,
                  'stage2/05-cardputerzero/02-run.sh': '#!/bin/bash\ncfg80211.ieee80211_regdom=AE\n',
                  'stage2/05-cardputerzero/03-run.sh': '#!/bin/bash\n',
                  'stage2/05-cardputerzero/05-run.sh': '#!/bin/bash\n# Synthetic fixed-size stage\n'}
        self.chunks = {}
        for name, content in chunks.items():
            path = self.vendor / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
            self.chunks[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.lock = {'pi_gen_commit': API['ZERO_BASELINE'], 'apps': []}
        for role, data in [('launcher', b'launcher'), ('drivers', b'drivers')]:
            name = role + '.input'; (self.assets / name).write_bytes(data)
            self.lock[role] = {'name': name, 'sha256': hashlib.sha256(data).hexdigest()}
        self.lock['drivers']['commit'] = 'a' * 40
        self.lock['launcher']['package_version'] = '0.6.111'
        self.reviewed = {'bsp_commit': 'a' * 40, 'bsp_sha256': self.lock['drivers']['sha256'],
                         'launcher_sha256': self.lock['launcher']['sha256']}
        package = b'status'; (self.assets / 'status.deb').write_bytes(package)
        self.metadata = {'pi_gen_commit': API['ZERO_BASELINE'], 'drivers_commit': 'a' * 40,
                         'kernel_release': '6.18.39+rpt-rpi-v8', 'applaunch_version': '0.6.111',
                         'profile_id': 'tyrell-zero', 'package_sha256': hashlib.sha256(package).hexdigest(),
                         'package_filename': 'status.deb', 'package_version': '0.1.0-1'}
        camera = {'packages': []}
        for name in ['camera-packages.json', 'zero-camera-packages.json']:
            (self.image / name).write_text(json.dumps(camera))

    def invoke(self, *, zero=True):
        lock_name = 'zero-baseline-apps.json' if zero else 'baseline-apps.json'
        (self.image / lock_name).write_text(json.dumps(self.lock))
        metadata = self.root / 'metadata.json'; metadata.write_text(json.dumps(self.metadata))
        args = ['prepare-vendor.py', str(self.vendor), str(self.assets), str(metadata)]
        if zero: args.append('--zero-profile')
        namespace = API['main'].__globals__
        with patch.dict(namespace, __file__=str(self.image / 'prepare-vendor.py'),
                        EXPECTED=self.chunks, ZERO_EXPECTED=self.chunks, ZERO_VENDOR=self.reviewed), \
                patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            API['main']()
        return json.loads((self.vendor / 'tyrell-prepared.json').read_text())

    def test_explicit_zero_recipe_uses_separate_lock_and_disables_artifact_resolution(self):
        (self.image / 'baseline-apps.json').write_text('{"wrong": "legacy must not be read"}')
        report = self.invoke()
        self.assertEqual(report['pi_gen_commit'], API['ZERO_BASELINE'])
        fragment = (self.vendor / 'tyrell-inputs.conf').read_text()
        self.assertIn("APPLAUNCH_ARTIFACT_URL=''", fragment)
        self.assertIn(API['ZERO_BASELINE'], fragment)
        self.assertIn("= '0.6.111'", (self.vendor / 'stage4/99-tyrell/00-run.sh').read_text())
        self.assertIn("cfg80211.ieee80211_regdom=00", (self.vendor / 'stage2/05-cardputerzero/02-run.sh').read_text())

    def test_default_recipe_keeps_september_baseline_and_legacy_lock(self):
        self.lock['pi_gen_commit'] = API['BASELINE']; self.lock['launcher']['package_version'] = '0.6.105'
        self.metadata.update(pi_gen_commit=API['BASELINE'], applaunch_version='0.6.105')
        self.metadata.pop('profile_id')
        (self.image / 'zero-baseline-apps.json').write_text('{"wrong": "Zero must not be read"}')
        report = self.invoke(zero=False)
        self.assertEqual(report['pi_gen_commit'], API['BASELINE'])
        self.assertNotIn('APPLAUNCH_ARTIFACT_URL', (self.vendor / 'tyrell-inputs.conf').read_text())
        self.assertIn("= '0.6.105'", (self.vendor / 'stage4/99-tyrell/00-run.sh').read_text())

    def test_unreviewed_zero_identity_or_changed_pristine_chunk_fails_before_patching(self):
        self.lock['drivers']['commit'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'Unreviewed Zero'): self.invoke()
        self.assertFalse((self.vendor / 'tyrell-inputs.conf').exists())
        self.lock['drivers']['commit'] = 'a' * 40
        (self.vendor / 'build.sh').write_text('changed before prepare')
        with self.assertRaisesRegex(ValueError, 'pristine baseline'): self.invoke()
        self.assertFalse((self.vendor / 'tyrell-inputs.conf').exists())


if __name__ == '__main__': unittest.main()
