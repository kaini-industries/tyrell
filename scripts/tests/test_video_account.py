"""Run the real Video postinst with isolated account/service command shims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


POSTINST = Path(__file__).resolve().parents[2] / 'packages/tyrell-video/debian/postinst'
ACCOUNT = 'tyrell-video-hdmi'
VALID = {
    'passwd': ACCOUNT + ':x:112:115::/nonexistent:/usr/sbin/nologin',
    'group': ACCOUNT + ':x:115:',
    'shadow': ACCOUNT + ':!:20000:0:99999:7:::',
}
SHIM = r'''
import json, os, sys
from pathlib import Path
path = Path(os.environ['VIDEO_ACCOUNT_STATE'])
state = json.loads(path.read_text())
command = Path(sys.argv[0]).name
state['calls'].append([command, *sys.argv[1:]])
code = 0
output = ''
if command == 'getent':
    table = sys.argv[1]
    assert sys.argv[2:] == ['tyrell-video-hdmi']
    lookup = state.setdefault('lookups', {}).get(table, 0) + 1
    state['lookups'][table] = lookup
    fault = state.get('faults', {}).get(table, {})
    if fault.get('call', 1) == lookup and 'code' in fault:
        code = fault['code']
        output = fault.get('output', '')
    else:
        output = state['records'].get(table, '')
        code = 0 if output else 2
elif command == 'adduser':
    code = state.get('adduser_code', 0)
    if code == 0:
        state['records'] = state['created_records']
elif command not in ('deb-systemd-helper', 'deb-systemd-invoke', 'systemctl'):
    raise AssertionError('Unexpected external command: ' + command)
path.write_text(json.dumps(state))
if output:
    print(output)
raise SystemExit(code)
'''


class VideoAccountTests(unittest.TestCase):
    def run_postinst(self, records=None, *, faults=None, created=None, adduser_code=0,
                     action='configure'):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / 'state.json'
            state_path.write_text(json.dumps({
                'records': dict(VALID if records is None else records),
                'created_records': dict(VALID if created is None else created),
                'faults': faults or {}, 'adduser_code': adduser_code, 'calls': [],
            }))
            for command in ('getent', 'adduser', 'deb-systemd-helper',
                            'deb-systemd-invoke', 'systemctl'):
                executable = root / command
                executable.write_text('#!' + sys.executable + '\n' + SHIM)
                executable.chmod(0o755)
            # Only the shims are on PATH: these tests cannot call the host's
            # account or service utilities, including when run as root in CI.
            environment = dict(os.environ, PATH=str(root), VIDEO_ACCOUNT_STATE=str(state_path))
            result = subprocess.run(['/bin/sh', str(POSTINST), action],
                                    env=environment, capture_output=True, text=True, timeout=10)
            return result, json.loads(state_path.read_text())

    def assert_rejected(self, result, state):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(call[0] in ('deb-systemd-helper', 'deb-systemd-invoke', 'systemctl')
                             for call in state['calls']), state['calls'])

    def test_creates_dedicated_account_before_enabling_socket(self):
        result, state = self.run_postinst({})
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = state['calls']
        create = ['adduser', '--system', '--group', '--no-create-home', '--home',
                  '/nonexistent', '--shell', '/usr/sbin/nologin', ACCOUNT]
        self.assertIn(create, calls)
        self.assertLess(calls.index(create),
                        next(i for i, call in enumerate(calls) if call[0] == 'deb-systemd-helper'))

    def test_reuses_valid_identity_without_changing_it(self):
        result, state = self.run_postinst()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['records'], VALID)
        self.assertFalse(any(call[0] == 'adduser' for call in state['calls']))

    def test_unsafe_existing_identity_is_not_changed_or_enabled(self):
        invalid = [
            ('passwd', ACCOUNT + ':x:0:115::/nonexistent:/usr/sbin/nologin'),
            ('passwd', ACCOUNT + ':x:112:0::/nonexistent:/usr/sbin/nologin'),
            ('passwd', ACCOUNT + ':x:112:115::/home/user:/usr/sbin/nologin'),
            ('passwd', ACCOUNT + ':x:112:115::/nonexistent:/bin/sh'),
            ('passwd', ACCOUNT + ':x:not-a-uid:115::/nonexistent:/usr/sbin/nologin'),
            ('group', ACCOUNT + ':x:116:'),
            ('shadow', ACCOUNT + ':password-hash:20000:0:99999:7:::'),
            ('shadow', ACCOUNT + '::20000:0:99999:7:::'),
            ('shadow', 'another-user:!:20000:0:99999:7:::'),
        ]
        for table, value in invalid:
            with self.subTest(table=table, record=value):
                records = dict(VALID, **{table: value})
                result, state = self.run_postinst(records)
                self.assert_rejected(result, state)
                self.assertEqual(state['records'], records)
                self.assertFalse(any(call[0] == 'adduser' for call in state['calls']))

    def test_failed_lookup_with_plausible_output_is_rejected(self):
        # A function called under `if !` does not inherit shell errexit.
        # Each credential lookup must explicitly propagate command failure.
        for table in ('passwd', 'group', 'shadow'):
            with self.subTest(table=table):
                faults = {table: {'call': 2 if table == 'passwd' else 1,
                                  'code': 2, 'output': VALID[table]}}
                self.assert_rejected(*self.run_postinst(faults=faults))

    def test_missing_group_or_shadow_is_rejected(self):
        for table in ('group', 'shadow'):
            with self.subTest(table=table):
                records = dict(VALID)
                del records[table]
                self.assert_rejected(*self.run_postinst(records))

    def test_failed_creation_does_not_enable_service(self):
        self.assert_rejected(*self.run_postinst({}, adduser_code=1))

    def test_successful_adduser_is_rechecked_before_service_enable(self):
        self.assert_rejected(*self.run_postinst({}, created={}))

    def test_other_maintainer_actions_leave_accounts_and_services_alone(self):
        result, state = self.run_postinst(action='abort-upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['calls'], [])


if __name__ == '__main__':
    unittest.main()
