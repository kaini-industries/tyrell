import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('tyrell_stock_image', Path(__file__).parents[1] / 'stock-image.py')
stock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stock)


class StockImageTests(unittest.TestCase):
    def fixture(self, tag='20260929-070146-5f4c44f', published='2026-09-29T07:02:28Z', prerelease=False):
        fields = {'pigen_commit': '5f4c44f3431bf1dfb968c10f7228ad6fa0c66cf4',
                  'launcher_version': '0.6.105', 'launcher_release': 'unknown',
                  'kernel_version': '1:6.18.39-1+rpt1', 'driver_commit': '7551854',
                  'config_cardputero_v0_5': True}
        data = (json.dumps(fields, indent=2) + '\n').encode()
        root = f'https://github.com/CardputerZero/pi-gen/releases/download/{tag}/'
        release = {'id': 398892510, 'tag_name': tag, 'published_at': published,
                   'prerelease': prerelease, 'draft': False,
                   'html_url': 'https://github.com/CardputerZero/pi-gen/releases/tag/' + tag,
                   'assets': [
                       {'id': 597525962, 'name': '2026-09-29-cardputerzero-trixie-arm64.img.xz',
                        'browser_download_url': root + '2026-09-29-cardputerzero-trixie-arm64.img.xz',
                        'size': 1786957924, 'digest': 'sha256:' + 'b' * 64},
                       {'id': 597527148, 'name': 'build-info.json', 'browser_download_url': root + 'build-info.json',
                        'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()},
                   ]}
        return release, fields, data

    def candidate(self):
        release, _, data = self.fixture()
        with patch.object(stock, 'select_release', return_value=release), \
                patch.object(stock, 'download', return_value=data), \
                patch.object(stock, 'api_json', return_value={'sha': '75518549a2640486f5d13ea72ca4935b198d7c20'}):
            return stock.discover()

    def test_default_selection_excludes_drafts_beta_and_asset_free_tags(self):
        stable, _, _ = self.fixture()
        beta, _, _ = self.fixture('beta', '2026-11-13T03:40:03Z', True)
        draft, _, _ = self.fixture('draft', '2026-11-14T03:40:03Z')
        draft['draft'] = True
        with patch.object(stock, 'api_json', return_value=[beta, draft, {'assets': []}, stable]):
            self.assertEqual(stock.select_release()['tag_name'], stable['tag_name'])
            self.assertEqual(stock.select_release(True)['tag_name'], 'beta')

    def test_pagination_and_publication_time_determine_latest(self):
        recent, _, _ = self.fixture()
        older, _, _ = self.fixture('older', '2026-09-28T07:02:28Z')
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith('&page=1'):
                return [older] + [{'assets': []}] * 99
            return [recent]

        with patch.object(stock, 'api_json', side_effect=fetch):
            self.assertEqual(stock.select_release()['tag_name'], recent['tag_name'])
        self.assertEqual(len(calls), 2)

    def test_explicit_prerelease_requires_opt_in_and_drafts_never_allowed(self):
        beta, _, _ = self.fixture('beta', prerelease=True)
        with patch.object(stock, 'api_json', return_value=beta):
            with self.assertRaisesRegex(ValueError, 'include-prereleases'):
                stock.select_release(release_tag='beta')
            self.assertEqual(stock.select_release(True, 'beta'), beta)
            beta['draft'] = True
            with self.assertRaisesRegex(ValueError, 'Draft'):
                stock.select_release(True, 'beta')

    def test_incomplete_newest_image_cannot_silently_select_older_release(self):
        current, _, _ = self.fixture()
        newer, _, _ = self.fixture('newer', '2026-11-13T03:40:03Z')
        newer['assets'].pop()
        with patch.object(stock, 'api_json', return_value=[current, newer]):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                stock.select_release()

    def test_newest_unsupported_os_architecture_or_compression_cannot_look_current(self):
        current, _, _ = self.fixture()
        for name in ('2027-01-01-cardputerzero-forky-arm64.img.xz',
                     '2027-01-01-cardputerzero-trixie-armhf.img.xz',
                     '2027-01-01-cardputerzero-trixie-arm64.img.zst',
                     'CardputerZero-2027.zip'):
            newer, _, _ = self.fixture('newer', '2027-01-01T00:00:00Z')
            newer['assets'][0]['name'] = name
            with self.subTest(name=name), patch.object(stock, 'api_json', return_value=[current, newer]):
                with self.assertRaisesRegex(ValueError, 'Unsupported stock image format'):
                    stock.select_release()

    def test_newest_build_info_without_image_cannot_silently_select_older_release(self):
        current, _, _ = self.fixture()
        newer, _, _ = self.fixture('newer', '2026-11-13T03:40:03Z')
        newer['assets'].pop(0)
        with patch.object(stock, 'api_json', return_value=[current, newer]):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                stock.select_release()

    def test_newest_unsupported_prerelease_still_requires_channel_opt_in(self):
        current, _, _ = self.fixture()
        newer, _, _ = self.fixture('newer', '2027-01-01T00:00:00Z', True)
        newer['assets'][0]['name'] = '2027-01-01-cardputerzero-forky-arm64.img.xz'
        with patch.object(stock, 'api_json', return_value=[newer, current]):
            self.assertEqual(stock.select_release()['tag_name'], current['tag_name'])
            with self.assertRaisesRegex(ValueError, 'Unsupported stock image format'):
                stock.select_release(include_prereleases=True)

    def test_discovery_verifies_small_metadata_and_expands_driver_without_image_download(self):
        release, _, data = self.fixture()
        with patch.object(stock, 'select_release', return_value=release), \
                patch.object(stock, 'download', return_value=data) as download, \
                patch.object(stock, 'api_json', return_value={'sha': '75518549a2640486f5d13ea72ca4935b198d7c20'}) as api:
            lock = stock.discover()
        download.assert_called_once_with(release['assets'][1]['browser_download_url'], maximum=65536)
        api.assert_called_once_with(stock.DRIVER_API + '/commits/7551854')
        self.assertEqual(lock['kernel_release'], '6.18.39+rpt-rpi-v8')
        self.assertEqual(lock['image']['sha256'], 'b' * 64)

    def test_build_info_digest_size_and_duplicate_keys_are_checked(self):
        for mutation in ['digest', 'size', 'duplicate']:
            with self.subTest(mutation=mutation):
                release, _, data = self.fixture()
                if mutation == 'digest':
                    data = data.replace(b'0.6.105', b'0.6.999')
                elif mutation == 'size':
                    data += b'\n'
                else:
                    data = b'{"driver_commit":"7551854","driver_commit":"abcdef0"}'
                    release['assets'][1]['size'] = len(data)
                    release['assets'][1]['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()
                with patch.object(stock, 'select_release', return_value=release), \
                        patch.object(stock, 'download', return_value=data), \
                        patch.object(stock, 'api_json') as api:
                    with self.assertRaisesRegex(ValueError, 'mismatch|Duplicate'):
                        stock.discover()
                    api.assert_not_called()

    def test_asset_metadata_rejects_unknown_digest_foreign_urls_and_path_injection(self):
        for mutation in ['digest', 'url', 'tag']:
            release, _, _ = self.fixture()
            if mutation == 'digest':
                release['assets'][0]['digest'] = None
            elif mutation == 'url':
                release['assets'][0]['browser_download_url'] = 'https://example.com/image.img.xz'
            else:
                release['tag_name'] = '../escape'
            with self.subTest(mutation=mutation), \
                    patch.object(stock, 'select_release', return_value=release), \
                    patch.object(stock, 'download') as download:
                with self.assertRaises(ValueError):
                    stock.discover()
                download.assert_not_called()

    def test_expanded_driver_must_match_verified_prefix(self):
        release, _, data = self.fixture()
        with patch.object(stock, 'select_release', return_value=release), \
                patch.object(stock, 'download', return_value=data), \
                patch.object(stock, 'api_json', return_value={'sha': 'a' * 40}):
            with self.assertRaisesRegex(ValueError, 'Driver commit'):
                stock.discover()

    def test_locked_identity_mismatches_are_rejected_offline(self):
        valid = self.candidate()
        mutations = [lambda x: x.update(board='v4'),
                     lambda x: x.update(kernel_release='6.99.0+rpt-rpi-v8'),
                     lambda x: x.update(applaunch_version='0.6.999'),
                     lambda x: x.update(pi_gen_commit='a' * 40),
                     lambda x: x['image'].update(name='../image.img.xz'),
                     lambda x: x['upstream'].update(prerelease='false'),
                     lambda x: x['image'].update(bytes=True),
                     lambda x: x['build_info'].update(bytes=65537)]
        for mutate in mutations:
            lock = copy.deepcopy(valid)
            mutate(lock)
            with self.subTest(lock=lock), patch.object(stock, 'download') as download:
                with self.assertRaises(ValueError):
                    stock.validate_lock(lock)
                download.assert_not_called()

    def test_new_lock_is_atomic_and_existing_lock_is_preserved(self):
        candidate = self.candidate()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'stock.json'
            stock.write_lock(output, candidate)
            before = output.read_bytes()
            self.assertEqual(stock.load_lock(output), candidate)
            self.assertEqual(stock.sha(output), hashlib.sha256(before).hexdigest())
            with self.assertRaisesRegex(ValueError, 'Preserving existing'):
                stock.write_lock(output, candidate)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_publish_race_preserves_competing_destination(self):
        candidate = self.candidate()
        original_link = stock.os.link
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'stock.json'

            def raced_link(source, destination):
                Path(destination).write_text('competing lock')
                original_link(source, destination)

            with patch.object(stock.os, 'link', side_effect=raced_link):
                with self.assertRaisesRegex(ValueError, 'Preserving existing'):
                    stock.write_lock(output, candidate)
            self.assertEqual(output.read_text(), 'competing lock')
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_check_writes_nothing_and_reports_asset_replacement(self):
        candidate = self.candidate()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'stock.json'
            stock.write_lock(output, candidate)
            before = output.read_bytes()
            for changed, expected in [(False, 'unchanged'), (True, 'update-available')]:
                proposed = copy.deepcopy(candidate)
                if changed:
                    proposed['image']['sha256'] = 'c' * 64
                stream = io.StringIO()
                with patch.object(stock, 'discover', return_value=proposed), contextlib.redirect_stdout(stream):
                    stock.main(['check', '--lock', str(output)])
                self.assertEqual(json.loads(stream.getvalue())['status'], expected)
                self.assertEqual(output.read_bytes(), before)
                self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_checked_in_stable_lock_loads_without_network(self):
        with patch.object(stock, 'download') as download:
            lock = stock.load_lock(stock.DEFAULT_LOCK)
        self.assertFalse(lock['upstream']['prerelease'])
        self.assertEqual(lock['upstream']['tag'], '20260929-070146-5f4c44f')
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
