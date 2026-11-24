"""Catalog cache, bounded transfer and normal-user protocol regressions."""
import base64
import copy
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import catalog_metadata as metadata
import catalog_cache as cache
import backend


def png(width=2, height=2, *, inflated=None):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    body = inflated if inflated is not None else (b"\0" + b"\x20\x40\x60" * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(body)) + chunk(b"IEND", b""))


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory(prefix="catalog-cache-keys-")
        cls.private = Path(cls.keys.name) / "signing.pem"
        subprocess.run([metadata.OPENSSL, "genpkey", "-algorithm", "Ed25519", "-out", str(cls.private)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=metadata.OPENSSL_ENV)
        cls.public = subprocess.check_output([metadata.OPENSSL, "pkey", "-in", str(cls.private), "-pubout"],
                                             env=metadata.OPENSSL_ENV).decode("ascii")
        cls.key_id = metadata.public_key_id(cls.public)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="catalog-cache-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "cache"
        self.now = 1795478410
        self.deb = b"signed-qualified-test-package\n"
        self.image = png()
        self.payload = {"schema": 1, "sequence": 1, "issued_at": 1795478400, "expires_at": 1795482000,
                        "base_id": "b" * 64, "packages": [{"package": "tyrell-status", "version": "0.1.0-1",
                        "architecture": "arm64", "sha256": cache.digest(self.deb), "bytes": len(self.deb),
                        "url": "https://example.test/packages/status.deb", "title": "Tyrell Status", "summary": "System status",
                        "artwork": {"sha256": cache.digest(self.image), "bytes": len(self.image), "url": "https://example.test/icons/status.png",
                                    "width": 2, "height": 2, "format": "png"}}]}
        self.trust = {"schema": 1, "base_id": "b" * 64, "min_sequence": 1,
                      "keys": [{"id": self.key_id, "public_key_pem": self.public}],
                      "revoked_key_ids": [], "allowed_origins": ["https://example.test"]}
        self.seed = self.sign()
        self.calls = []
        self.response = self.seed
        self.cancel = False
        self.client = cache.CatalogCache(self.trust, self.root, seed=self.seed, base_compatible=True,
                                         metadata_url="https://example.test/catalog.json", transport=self.transfer,
                                         clock=lambda: self.now, cancelled=lambda: self.cancel)

    def sign(self, sequence=None, *, payload=None):
        payload = copy.deepcopy(self.payload if payload is None else payload)
        if sequence is not None:
            payload["sequence"] = sequence
        return metadata.sign_payload(payload, self.private, allowed_origins=self.trust["allowed_origins"])

    def transfer(self, url, maximum, deadline, cancelled):
        self.calls.append((url, maximum))
        return self.response

    def state(self):
        return (self.root / cache.STATE_FILE).read_bytes()

    def publish(self, sequence):
        self.response = self.sign(sequence)
        self.client.refresh()

    def test_offline_open_is_network_silent_and_does_not_create_cache(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("startup must not use network")):
            result = self.client.status()
        self.assertEqual(result["state"], "fresh")
        self.assertEqual(result["packages"][0]["state"], "downloadable")
        self.assertFalse(result["installation_available"])
        self.assertFalse(self.calls)
        self.assertFalse(self.root.exists())

    def test_view_carries_signed_validity_for_local_idle_expiry_without_network(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("view must not use network")):
            result = self.client.status()
            output = backend.view(result)
        self.assertEqual(result["issued_at"], self.payload["issued_at"])
        self.assertEqual(result["expires_at"], self.payload["expires_at"])
        self.assertEqual(output.splitlines()[1], "VALIDITY\t1795478400\t1795482000")
        self.now = self.payload["expires_at"]
        expired = self.client.status()
        self.assertEqual(expired["state"], "expired")
        self.assertEqual(backend.view(expired).splitlines()[1], output.splitlines()[1])
        self.assertFalse(self.calls)
        self.assertFalse(self.root.exists())

    def test_expired_future_unknown_clock_and_incompatible_base_only_browse(self):
        for current, compatible, state in ((self.payload["expires_at"], True, "expired"),
                                           (self.payload["issued_at"] - 1, True, "not-yet-valid"),
                                           (None, True, "clock-unknown"), (self.now, False, "incompatible")):
            self.client.clock = lambda value=current: value
            self.client.base_compatible = compatible
            with self.subTest(state=state):
                result = self.client.status()
                self.assertEqual(result["state"], state)
                self.assertEqual(result["packages"][0]["state"], "browse-only")
                with self.assertRaises(cache.CacheError):
                    self.client.download("tyrell-status@0.1.0-1")
        self.assertFalse(self.calls)

    def test_valid_refresh_is_atomic_and_reopens_verified_metadata(self):
        self.publish(2)
        reopened = cache.CatalogCache(self.trust, self.root, seed=self.seed, base_compatible=True,
                                      clock=lambda: self.now, transport=lambda *_: self.fail("offline reopen fetched"))
        self.assertEqual(reopened.status()["sequence"], 2)
        state = metadata.load_json(self.state(), limit=cache.MAX_STATE_BYTES)
        self.assertEqual(state["high_water"], 2)
        self.assertEqual(base64.b64decode(state["envelope"]), self.response)

    def test_bad_signature_wrong_base_and_stale_refresh_preserve_last_verified_bytes(self):
        self.publish(2)
        before = self.state()
        envelope = metadata.load_json(self.sign(3)); envelope["signature"] = "A" * 88
        wrong_base = copy.deepcopy(self.payload); wrong_base["base_id"] = "a" * 64; wrong_base["sequence"] = 3
        for response in (metadata.canonical(envelope), self.sign(payload=wrong_base), b"not JSON"):
            self.response = response
            with self.subTest(response=response[:20]), self.assertRaises(cache.CacheError):
                self.client.refresh()
            self.assertEqual(self.state(), before)
        self.response = self.sign(3); self.client.clock = lambda: None
        with self.assertRaises(cache.CacheError): self.client.refresh()
        self.assertEqual(self.state(), before)

    def test_replay_and_reused_sequence_content_conflict_are_rejected(self):
        self.publish(3)
        before = self.state()
        self.response = self.sign(2)
        with self.assertRaisesRegex(cache.CacheError, "replay"): self.client.refresh()
        changed = copy.deepcopy(self.payload); changed.update(sequence=3, expires_at=self.payload["expires_at"] + 1)
        self.response = self.sign(payload=changed)
        with self.assertRaisesRegex(cache.CacheError, "reuse"): self.client.refresh()
        self.assertEqual(self.state(), before)
        self.response = base64.b64decode(metadata.load_json(before, limit=cache.MAX_STATE_BYTES)["envelope"])
        self.assertEqual(self.client.refresh()["sequence"], 3)

    def test_user_history_is_advisory_while_packaged_floor_is_enforced(self):
        self.publish(3)
        (self.root / cache.STATE_FILE).unlink()  # Same user can delete their cache.
        self.response = self.sign(2)
        self.assertEqual(self.client.refresh()["sequence"], 2)
        self.trust["min_sequence"] = 3  # A protected package policy, not cache state.
        with self.assertRaises(cache.CacheError): self.client.download("tyrell-status@0.1.0-1")
        self.response = self.sign(2)
        with self.assertRaises(cache.CacheError): self.client.refresh()

    def test_corrupt_cache_is_revalidated_on_every_offline_open(self):
        self.publish(2)
        state = metadata.load_json(self.state(), limit=cache.MAX_STATE_BYTES)
        envelope = metadata.load_json(base64.b64decode(state["envelope"]))
        envelope["payload"]["packages"][0]["title"] = "Forged title"
        state["envelope"] = base64.b64encode(metadata.canonical(envelope)).decode("ascii")
        (self.root / cache.STATE_FILE).write_bytes(metadata.canonical(state))
        result = self.client.status()
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["packages"][0]["title"], "Tyrell Status")
        self.assertIn("rejected", result["message"])

    def test_concurrent_mutation_is_rejected_before_network_and_reads_do_not_wait(self):
        self.publish(2)
        self.calls.clear()
        with cache.CacheDirectory(self.root) as directory, directory.lock():
            with self.assertRaises(cache.Busy): self.client.refresh()
            self.assertEqual(self.client.status()["sequence"], 2)
        self.assertFalse(self.calls)

    def test_cancelled_and_interrupted_refresh_keep_last_verified_catalog(self):
        self.publish(2); before = self.state(); self.response = self.sign(3)
        def interrupt():
            raise OSError("injected interruption before rename")
        self.client.before_replace = interrupt
        with self.assertRaises(OSError): self.client.refresh()
        self.assertEqual(self.state(), before)
        self.assertFalse(list(self.root.glob(".partial-*")))
        self.client.before_replace = None
        def cancel(*args):
            self.cancel = True
            return self.response
        self.client.transport = cancel
        with self.assertRaises(cache.Cancelled): self.client.refresh()
        self.assertEqual(self.state(), before)

    def test_cache_symlink_ancestor_lock_state_and_object_redirects_do_not_write_outside(self):
        outside = self.directory / "outside"; outside.mkdir(); victim = outside / "victim"; victim.write_bytes(b"preserve")
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, cache.CacheError)): self.client.refresh()
        self.assertEqual(victim.read_bytes(), b"preserve")
        self.root.unlink(); self.root.mkdir(mode=0o700)
        (self.root / ".lock").symlink_to(victim)
        with self.assertRaises((OSError, cache.CacheError)): self.client.refresh()
        (self.root / ".lock").unlink()
        (self.root / cache.STATE_FILE).symlink_to(victim)
        with self.assertRaises((OSError, cache.CacheError)): self.client.refresh()
        (self.root / cache.STATE_FILE).unlink()
        (self.root / "packages").symlink_to(outside, target_is_directory=True)
        self.response = self.deb
        with self.assertRaises((OSError, cache.CacheError)): self.client.download("tyrell-status@0.1.0-1")
        self.assertEqual(victim.read_bytes(), b"preserve")

    def test_hardlink_and_quota_abuse_fail_before_fetch(self):
        self.root.mkdir(mode=0o700)
        outside = self.directory / "outside"; outside.write_bytes(b"preserve")
        os.link(outside, self.root / cache.STATE_FILE)
        with self.assertRaises(cache.CacheError): self.client.refresh()
        self.assertEqual(outside.read_bytes(), b"preserve")
        (self.root / cache.STATE_FILE).unlink()
        for index in range(8): (self.root / str(index)).write_bytes(b"x")
        with patch.object(cache, "MAX_CACHE_FILES", 5), self.assertRaises(cache.CacheError): self.client.refresh()
        self.assertFalse(self.calls)

    def test_verified_download_is_hash_addressed_and_offline_reuse_rechecks_bytes(self):
        self.response = self.deb
        result = self.client.download("tyrell-status@0.1.0-1")
        path = Path(result["path"])
        self.assertEqual(path.name, cache.digest(self.deb) + ".deb")
        self.assertEqual(path.read_bytes(), self.deb)
        self.assertFalse(result["installation_available"])
        self.calls.clear()
        self.assertEqual(self.client.download("tyrell-status@0.1.0-1")["status"], "cached")
        self.assertFalse(self.calls)
        path.write_bytes(b"corrupt")
        self.assertEqual(self.client.status()["packages"][0]["state"], "downloadable")
        self.client.download("tyrell-status@0.1.0-1")
        self.assertEqual(path.read_bytes(), self.deb)

    def test_wrong_download_size_digest_and_selection_never_publish(self):
        for response in (b"x" * len(self.deb), self.deb + b"x", self.deb[:-1]):
            self.response = response
            with self.subTest(response=response), self.assertRaises(cache.CacheError):
                self.client.download("tyrell-status@0.1.0-1")
            self.assertFalse(list(self.root.rglob("*.deb")))
        with self.assertRaises(cache.CacheError): self.client.download("arbitrary@9")

    def test_expiry_or_base_change_during_download_blocks_publication(self):
        for invalidation in ("expiry", "base"):
            self.now = 1795478410; compatible = [True]
            self.client.base_compatible = lambda: compatible[0]
            def transfer(*args):
                if invalidation == "expiry": self.now = self.payload["expires_at"]
                else: compatible[0] = False
                return self.deb
            self.client.transport = transfer
            with self.subTest(invalidation=invalidation), self.assertRaises(cache.CacheError):
                self.client.download("tyrell-status@0.1.0-1")
            self.assertFalse(list(self.root.rglob("*.deb")))

    def test_expiry_and_base_change_at_final_commit_fence_preserve_prior_cache(self):
        self.publish(2)
        before = self.state()
        self.response = self.deb
        self.client.before_replace = lambda: setattr(self, "now", self.payload["expires_at"])
        with self.assertRaises(cache.CacheError): self.client.download("tyrell-status@0.1.0-1")
        self.assertFalse(list(self.root.rglob("*.deb")))
        self.assertEqual(self.state(), before)

        self.now = 1795478410
        self.client.before_replace = lambda: setattr(self.client, "base_compatible", False)
        with self.assertRaises(cache.CacheError): self.client.download("tyrell-status@0.1.0-1")
        self.assertFalse(list(self.root.rglob("*.deb")))
        self.client.base_compatible = True
        self.response = self.sign(3)
        self.client.before_replace = lambda: setattr(self, "now", self.payload["expires_at"])
        with self.assertRaises(cache.CacheError): self.client.refresh()
        self.assertEqual(self.state(), before)

    def test_configured_policy_revocation_floor_and_source_changes_block_inflight_commit(self):
        store_policy = metadata.canonical({"schema": 1, "store_source": "source", "vendor_sha256": cache.digest(b"vendor")})
        store_catalog = metadata.canonical({"schema": 1, "store_source": "source"})
        binding = {"schema": 1, "store_source": "source", "vendor_sha256": cache.digest(b"vendor"),
                   "store_policy_sha256": cache.digest(store_policy), "store_catalog_sha256": cache.digest(store_catalog)}
        base_id = cache.digest(metadata.canonical(binding))
        self.trust["base_id"] = self.payload["base_id"] = base_id
        source = {"schema": 1, "metadata_url": "https://example.test/catalog.json", "base_binding": binding}
        baseline = {backend.TRUST_PATH: metadata.canonical(self.trust), backend.SOURCE_PATH: metadata.canonical(source),
                    backend.SEED_PATH: self.sign(), backend.STORE_POLICY: store_policy,
                    backend.STORE_CATALOG: store_catalog, backend.VENDOR: b"vendor"}
        for mutation in ("revocation", "floor", "source"):
            values = dict(baseline)
            with self.subTest(mutation=mutation), patch.object(backend, "trusted_bytes", side_effect=lambda path, _limit: values[path]), \
                    patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.directory)}):
                client = backend.configured_cache()
                client.clock = lambda: self.now
                client.transport = lambda *_: self.deb
                self.assertTrue(client.compatible())
                def change():
                    if mutation == "source":
                        changed = copy.deepcopy(source); changed["metadata_url"] = None
                        values[backend.SOURCE_PATH] = metadata.canonical(changed)
                    else:
                        changed = copy.deepcopy(self.trust)
                        if mutation == "revocation": changed["revoked_key_ids"] = [self.key_id]
                        else: changed["min_sequence"] = 2
                        values[backend.TRUST_PATH] = metadata.canonical(changed)
                client.before_replace = change
                with self.assertRaises(cache.CacheError): client.download("tyrell-status@0.1.0-1")
                self.assertFalse(list((self.directory / "tyrell-catalog").rglob("*.deb")))
                self.assertFalse(client.policy_current())
                with self.assertRaises(cache.CacheError): client.refresh()
    def test_artwork_png_hash_dimensions_and_inflation_are_verified_before_publication(self):
        self.response = self.image
        result = self.client.download("tyrell-status@0.1.0-1", artwork=True)
        self.assertEqual(Path(result["path"]).read_bytes(), self.image)
        self.assertEqual(self.client.status()["artworks"][0]["sha256"], cache.digest(self.image))
        bad = png(inflated=b"x" * 2000)
        payload = copy.deepcopy(self.payload); payload["packages"][0]["artwork"].update(sha256=cache.digest(bad), bytes=len(bad))
        self.client.seed = self.sign(payload=payload); self.response = bad
        with self.assertRaises(cache.CacheError): self.client.download("tyrell-status@0.1.0-1", artwork=True)
        self.assertFalse((self.root / "artwork" / (cache.digest(bad) + ".png")).exists())

    def test_packaged_artwork_works_offline_but_corrupt_or_wrong_dimensions_are_hidden(self):
        self.client.packaged_artwork = lambda _: ("/usr/share/tyrell-catalog/artwork/test.png", self.image)
        self.assertEqual(len(self.client.status()["artworks"]), 1)
        self.client.packaged_artwork = lambda _: ("/usr/share/tyrell-catalog/artwork/test.png", png(3, 3))
        self.assertEqual(self.client.status()["artworks"], [])
        self.assertFalse(self.calls)

    def test_compressed_text_and_color_profiles_are_rejected_before_image_decoder(self):
        for kind, prefix in ((b"zTXt", b"Title\0\0"), (b"iCCP", b"Profile\0\0")):
            body = prefix + zlib.compress(b"x" * (1024 * 1024))
            chunk = (struct.pack(">I", len(body)) + kind + body
                     + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))
            image = self.image[:33] + chunk + self.image[33:]
            artwork = {**self.payload["packages"][0]["artwork"], "sha256": cache.digest(image), "bytes": len(image)}
            self.assertLess(len(image), cache.MAX_ARTWORK_BYTES)
            with self.subTest(kind=kind), patch.object(cache.zlib, "decompressobj") as decoder:
                with self.assertRaisesRegex(cache.CacheError, "Unsupported PNG chunk"):
                    cache.validate_png(image, artwork)
                decoder.assert_not_called()

    def test_unconfigured_refresh_does_not_create_cache_or_fetch(self):
        self.client.metadata_url = None
        with self.assertRaisesRegex(cache.CacheError, "configured"): self.client.refresh()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.calls)


class TransferTests(unittest.TestCase):
    def test_url_origins_protocol_and_encoded_traversal_are_restricted(self):
        for url in ("http://example.test/file", "https://other.test/file", "https://user@example.test/file",
                    "https://example.test:444/file", "https://example.test/a/%2e%2e/secret",
                    "https://example.test/%252e%252e/secret", "https://example.test/file?command=x"):
            with self.subTest(url=url), self.assertRaises(cache.CacheError):
                cache.allowed_url(url, ["https://example.test"])

    def response(self, body, *, status=200, headers=None):
        class Response:
            def __init__(self):
                self.status = status; self.headers = Message(); self.stream = io.BytesIO(body)
                for key, value in headers or []: self.headers.add_header(key, value)
            def getheader(self, name): return self.headers.get(name)
            def read(self, size): return self.stream.read(size)
        connection = unittest.mock.Mock(); connection.getresponse.return_value = Response(); connection.sock = None
        return connection

    def test_https_never_follows_redirects_or_accepts_oversized_encoded_truncated_responses(self):
        examples = [(b"x", 302, [("Location", "https://example.test/next")]),
                    (b"x", 200, [("Content-Encoding", "gzip")]),
                    (b"x", 200, [("Content-Length", "20")]),
                    (b"xy", 200, [("Content-Length", "3")]),
                    (b"xy", 200, [("Content-Length", "2"), ("Content-Length", "2")]),
                    (b"xxxx", 200, [])]
        for body, status, headers in examples:
            connection = self.response(body, status=status, headers=headers)
            with self.subTest(status=status, headers=headers), patch.object(cache.http.client, "HTTPSConnection", return_value=connection) as ctor:
                with self.assertRaises(cache.CacheError): cache._http_body("https://example.test/file", 3, time.monotonic() + 1)
                self.assertEqual(ctor.call_count, 1)
                connection.close.assert_called_once()
        connection = self.response(b"xyz", headers=[("Content-Length", "3")])
        with patch.object(cache.http.client, "HTTPSConnection", return_value=connection):
            self.assertEqual(cache._http_body("https://example.test/file", 3, time.monotonic() + 1), b"xyz")

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX fork required by native backend")
    def test_dns_or_blocked_transfer_is_killed_by_deadline_and_cancellation(self):
        def blocked(*args):
            time.sleep(5)
            return b"late"
        with patch.object(cache, "_http_body", side_effect=blocked):
            start = time.monotonic()
            with self.assertRaises(cache.CacheError):
                cache.https_fetch("https://example.test/file", 64, start + 0.06, lambda: False)
            self.assertLess(time.monotonic() - start, 0.75)
            start = time.monotonic()
            with self.assertRaises(cache.Cancelled):
                cache.https_fetch("https://example.test/file", 64, start + 2, lambda: time.monotonic() - start > 0.03)
            self.assertLess(time.monotonic() - start, 0.75)

    def test_worker_closes_inherited_descriptors_and_has_its_own_termination_alarm(self):
        with tempfile.TemporaryDirectory() as temporary:
            fd = os.open(str(Path(temporary) / "parent-only"), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                def check_closed(*args):
                    try:
                        os.fstat(fd)
                    except OSError:
                        return b"closed"
                    return b"inherited"
                with patch.object(cache, "_http_body", side_effect=check_closed):
                    self.assertEqual(cache.https_fetch("https://example.test/file", 64, time.monotonic() + 1, lambda: False), b"closed")
            finally:
                os.close(fd)
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            cache._prepare_worker(write_fd, time.monotonic() + 0.03)
            time.sleep(3)
            os._exit(9)
        os.close(write_fd); os.close(read_fd)
        start = time.monotonic()
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGALRM)
        self.assertLess(time.monotonic() - start, 0.9)

    def test_standalone_backend_death_cannot_leave_network_worker_holding_cache_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            code = ("import os,sys,time\n"
                    "from pathlib import Path\n"
                    "sys.path.insert(0,sys.argv[1])\n"
                    "import catalog_cache as c\n"
                    "root=Path(sys.argv[2])\n"
                    "def blocked(*args):\n"
                    " (root/'worker').write_text(str(os.getpid()))\n"
                    " time.sleep(4)\n"
                    " return b'late'\n"
                    "c._http_body=blocked\n"
                    "with c.CacheDirectory(root/'cache',create=True) as d,d.lock():\n"
                    " c.https_fetch('https://example.test/file',64,time.monotonic()+2,lambda:False)\n")
            parent = subprocess.Popen([sys.executable, "-c", code, str(PACKAGE), str(root)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            worker = None
            try:
                until = time.monotonic() + 2
                while not (root / "worker").exists() and time.monotonic() < until:
                    time.sleep(0.01)
                self.assertTrue((root / "worker").exists(), "network worker did not start")
                worker = int((root / "worker").read_text())
                parent.kill(); parent.wait(timeout=1)
                # The network worker is deliberately still sleeping. A fresh
                # process can immediately acquire the lock its parent held.
                with cache.CacheDirectory(root / "cache") as directory, directory.lock():
                    self.assertTrue(True)
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=1)
                if worker is not None:
                    try:
                        os.kill(worker, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                parent.stdout.close(); parent.stderr.close()


class BackendTests(unittest.TestCase):
    def setUp(self):
        backend.stopping = False

    def test_view_is_bounded_ascii_and_cannot_inject_rows(self):
        status = {"state": "fresh", "sequence": 1, "message": "trusted\nmessage", "packages": [
            {"id": "app@1", "title": "row\tforged\nname", "version": "1", "bytes": 30,
             "state": "downloadable", "summary": "a" * 1000}], "artworks": []}
        output = backend.view(status)
        self.assertEqual(len(output.splitlines()), 2)
        self.assertEqual(len(output.splitlines()[1].split("\t")), 7)
        self.assertLess(len(output), backend.MAX_VIEW_BYTES)
        output.encode("ascii")

    def test_cli_has_no_install_repair_remove_or_policy_override(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            for argv in (("install",), ("repair",), ("remove",), ("view", "--policy", "/tmp/policy")):
                with self.subTest(argv=argv), self.assertRaises(SystemExit): backend.main(argv)

    def test_root_or_setuid_mutation_refused_before_configuration(self):
        for real, effective in ((0, 0), (1000, 0)):
            for verb in ("refresh", "download", "artwork"):
                with self.subTest(uid=(real, effective), verb=verb), patch.object(backend.os, "getuid", return_value=real), \
                        patch.object(backend.os, "geteuid", return_value=effective), patch.object(backend, "configured_cache") as configured, \
                        patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(backend.main((verb,)), 1)
                    configured.assert_not_called()
                    self.assertIn("RESULT\t1\terror", output.getvalue())

    def test_base_check_uses_fixed_root_policy_catalog_and_vendor_exact_bytes(self):
        policy = metadata.canonical({"schema": 1, "store_source": "source", "vendor_sha256": cache.digest(b"vendor")})
        catalog = metadata.canonical({"schema": 1, "store_source": "source"})
        binding = {"schema": 1, "store_source": "source", "vendor_sha256": cache.digest(b"vendor"),
                   "store_policy_sha256": cache.digest(policy), "store_catalog_sha256": cache.digest(catalog)}
        values = {backend.STORE_POLICY: policy, backend.STORE_CATALOG: catalog, backend.VENDOR: b"vendor"}
        with patch.object(backend, "trusted_bytes", side_effect=lambda path, _limit: values[path]):
            self.assertTrue(backend.installed_base_matches(binding))
            values[backend.VENDOR] = b"changed"
            self.assertFalse(backend.installed_base_matches(binding))
        with patch.object(backend, "trusted_bytes", side_effect=PermissionError):
            self.assertFalse(backend.installed_base_matches(binding))


if __name__ == "__main__":
    unittest.main()
