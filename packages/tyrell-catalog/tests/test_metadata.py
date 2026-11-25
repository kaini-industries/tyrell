"""Real Ed25519 verification and qualified-input regression coverage."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
sys.path.insert(0, str(PACKAGE))
import catalog_metadata as metadata

spec = importlib.util.spec_from_file_location("catalog_generator", PACKAGE / "tools/generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="catalog-metadata-test-")
        cls.directory = Path(cls.temporary.name)
        cls.private = cls.directory / "signing.pem"
        subprocess.run([metadata.OPENSSL, "genpkey", "-algorithm", "Ed25519", "-out", str(cls.private)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=metadata.OPENSSL_ENV)
        cls.public = subprocess.check_output([metadata.OPENSSL, "pkey", "-in", str(cls.private), "-pubout"],
                                             env=metadata.OPENSSL_ENV).decode("ascii")
        cls.key_id = metadata.public_key_id(cls.public)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.payload = {"schema": 1, "sequence": 1, "issued_at": 1795478400, "expires_at": 1795482000,
                        "base_id": "b" * 64, "packages": [{"package": "tyrell-status", "version": "0.1.0-1",
                        "architecture": "arm64", "sha256": "c" * 64, "bytes": 100,
                        "url": "https://example.test/packages/status.deb", "title": "Tyrell Status", "summary": "System status",
                        "artwork": {"sha256": "d" * 64, "bytes": 64, "url": "https://example.test/icons/status.png",
                                    "width": 80, "height": 80, "format": "png"}}]}
        self.trust = {"schema": 1, "base_id": "b" * 64, "min_sequence": 1,
                      "keys": [{"id": self.key_id, "public_key_pem": self.public}],
                      "revoked_key_ids": [], "allowed_origins": ["https://example.test"]}

    def signed(self, payload=None):
        return metadata.sign_payload(payload or self.payload, self.private, allowed_origins=self.trust["allowed_origins"])

    def verify(self, data=None, trust=None, base=None, now=1795478410):
        return metadata.verify_envelope(data or self.signed(), trust or self.trust, base or "b" * 64, now)

    def test_actual_ed25519_round_trip_is_deterministic_and_network_silent(self):
        first = self.signed()
        self.assertEqual(first, self.signed())
        with patch("socket.getaddrinfo", side_effect=AssertionError("verification must not use network")):
            result = self.verify(first)
        self.assertEqual(result["payload"], self.payload)
        self.assertEqual(result["freshness"], "fresh")
        self.assertEqual(result["envelope_sha256"], hashlib.sha256(first).hexdigest())

    def test_changed_signed_package_or_signature_cannot_verify(self):
        for mutation in ("payload", "signature", "key_id"):
            envelope = metadata.load_json(self.signed())
            if mutation == "payload": envelope["payload"]["packages"][0]["sha256"] = "e" * 64
            elif mutation == "key_id": envelope["key_id"] = "f" * 64
            else:
                value = bytearray(base64.b64decode(envelope["signature"])); value[0] ^= 1
                envelope["signature"] = base64.b64encode(value).decode("ascii")
            with self.subTest(mutation=mutation), self.assertRaises(metadata.MetadataError):
                self.verify(metadata.canonical(envelope))

    def test_signature_domain_cannot_be_reused_as_payload_signature(self):
        envelope = metadata.load_json(self.signed())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload"
            path.write_bytes(metadata.canonical(envelope["payload"]))
            signature = metadata._openssl(("pkeyutl", "-sign", "-rawin", "-inkey", self.private, "-in", path))
        envelope["signature"] = base64.b64encode(signature).decode("ascii")
        with self.assertRaises(metadata.MetadataError): self.verify(metadata.canonical(envelope))

    def test_wrong_base_and_root_floor_deny_even_valid_signature(self):
        for mutation in ("local-base", "policy-base", "payload-base", "floor"):
            trust = copy.deepcopy(self.trust); payload = copy.deepcopy(self.payload); expected = "b" * 64
            if mutation == "local-base": expected = "a" * 64
            elif mutation == "policy-base": trust["base_id"] = "a" * 64
            elif mutation == "payload-base": payload["base_id"] = "a" * 64
            else: trust["min_sequence"] = 2
            with self.subTest(mutation=mutation), self.assertRaises(metadata.MetadataError):
                self.verify(self.signed(payload), trust, expected)

    def test_revocation_and_wrong_pinned_key_identity_deny(self):
        for mutation in ("revoked", "wrong-id", "duplicate-key", "non-ed25519"):
            trust = copy.deepcopy(self.trust)
            if mutation == "revoked": trust["revoked_key_ids"] = [self.key_id]
            elif mutation == "wrong-id": trust["keys"][0]["id"] = "a" * 64
            elif mutation == "duplicate-key": trust["keys"].append(copy.deepcopy(trust["keys"][0]))
            else: trust["keys"][0]["public_key_pem"] = "-----BEGIN PUBLIC KEY-----\nYQ==\n-----END PUBLIC KEY-----\n"
            with self.subTest(mutation=mutation), self.assertRaises(metadata.MetadataError): self.verify(trust=trust)

    def test_expired_future_and_unknown_clock_stay_browsable_but_not_fresh(self):
        signed = self.signed()
        for current, expected in ((self.payload["issued_at"] - 1, "not-yet-valid"),
                                  (self.payload["issued_at"], "fresh"),
                                  (self.payload["expires_at"] - 1, "fresh"),
                                  (self.payload["expires_at"], "expired"), (None, "clock-unknown")):
            with self.subTest(now=current): self.assertEqual(self.verify(signed, now=current)["freshness"], expected)

    def test_duplicate_malformed_oversized_and_noncanonical_json_deny(self):
        envelope = metadata.load_json(self.signed())
        examples = [b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'\xff', b'[',
                    b" " * (metadata.MAX_ENVELOPE_BYTES + 1), json.dumps(envelope).encode(),
                    metadata.canonical({**envelope, "command": "install"})]
        for data in examples:
            with self.subTest(prefix=data[:32]), self.assertRaises(metadata.MetadataError): self.verify(data)

    def test_schema_counts_sizes_times_and_text_are_bounded(self):
        mutations = [lambda p: p.update(schema=True), lambda p: p.update(sequence=True), lambda p: p.update(sequence=0),
                     lambda p: p.update(expires_at=p["issued_at"] + metadata.MAX_VALIDITY_SECONDS + 1),
                     lambda p: p.update(packages=p["packages"] * 33),
                     lambda p: p["packages"][0].update(bytes=metadata.MAX_PACKAGE_BYTES + 1),
                     lambda p: p["packages"][0].update(title="\nexit"),
                     lambda p: p["packages"][0].update(package="../status"),
                     lambda p: p["packages"][0]["artwork"].update(width=129),
                     lambda p: p["packages"][0]["artwork"].update(bytes=metadata.MAX_ARTWORK_BYTES + 1),
                     lambda p: p["packages"][0].update(install_command="dpkg")]
        for mutation in mutations:
            payload = copy.deepcopy(self.payload); mutation(payload)
            with self.assertRaises(metadata.MetadataError): metadata.validate_payload(payload, self.trust["allowed_origins"])

    def test_unsafe_url_origins_credentials_controls_and_encodings_deny(self):
        for value in ("http://example.test/a", "https://other.test/a", "https://u:p@example.test/a",
                      "https://example.test/a#fragment", "https://example.test/a?token=x",
                      "https://example.test/%2e%2e/a", "https://example.test/%252e%252e/a",
                      "https://example.test/a%0ab", "https://example.test:444/a", "https://EXAMPLE.test/a"):
            payload = copy.deepcopy(self.payload); payload["packages"][0]["url"] = value
            with self.subTest(url=value), self.assertRaises(metadata.MetadataError): self.signed(payload)

    def test_duplicate_identity_cannot_hide_changed_package_bytes(self):
        payload = copy.deepcopy(self.payload); other = copy.deepcopy(payload["packages"][0]); other["sha256"] = "f" * 64
        payload["packages"].append(other)
        with self.assertRaises(metadata.MetadataError): self.signed(payload)

    def test_signature_subprocess_environment_and_deadline_are_fixed(self):
        data = self.signed()
        with patch.dict("os.environ", {"OPENSSL_CONF": "/invalid", "OPENSSL_MODULES": "/invalid"}):
            self.assertEqual(self.verify(data)["freshness"], "fresh")
        with patch.object(metadata.subprocess, "run", side_effect=subprocess.TimeoutExpired(metadata.OPENSSL, 5)):
            with self.assertRaises(metadata.MetadataError): self.verify(data)

    def test_publisher_requires_regular_private_key_and_restrictive_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key.pem"; key.write_bytes(self.private.read_bytes()); key.chmod(0o644)
            with self.assertRaises(metadata.MetadataError): metadata.sign_payload(self.payload, key, allowed_origins=self.trust["allowed_origins"])
            key.unlink(); key.symlink_to(self.private)
            with self.assertRaises(metadata.MetadataError): metadata.sign_payload(self.payload, key, allowed_origins=self.trust["allowed_origins"])


class QualifiedGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()

    def generate(self, root=ROOT, revision=None):
        return generator.generate_payload(root, revision or self.revision, 1, 1795478400, 1798070400)

    def test_committed_offline_seed_verifies_against_current_reviewed_store(self):
        config = PACKAGE / "config"
        trust = metadata.load_json((config / "trust.json").read_bytes())
        source = metadata.load_json((config / "source.json").read_bytes())
        expected = metadata.base_identity(ROOT)
        seed = metadata.verify_envelope((config / "seed.json").read_bytes(), trust, expected)
        self.assertEqual(hashlib.sha256(metadata.canonical(source["base_binding"])).hexdigest(), expected)
        self.assertIsNone(source["metadata_url"])
        self.assertEqual({(p["package"], p["version"], p["architecture"]): p["sha256"]
                          for p in seed["payload"]["packages"]}, generator.QUALIFIED)

    def test_real_qualified_dist_and_store_inputs_produce_only_approved_three(self):
        payload = self.generate()
        self.assertEqual({(p["package"], p["version"], p["architecture"]): p["sha256"] for p in payload["packages"]}, generator.QUALIFIED)
        self.assertEqual(payload["base_id"], metadata.base_identity(ROOT))
        self.assertTrue(all('/' + self.revision + '/' in p["url"] for p in payload["packages"]))
        self.assertTrue(all(p["artwork"]["width"] == 80 and p["artwork"]["height"] == 80 for p in payload["packages"]))

    def test_missing_source_revision_cannot_label_worktree_bytes_as_published(self):
        with self.assertRaises(metadata.MetadataError): self.generate(revision="0" * 40)

    def test_new_or_changed_root_approved_row_is_not_implicitly_signed(self):
        for mutation in ("new-version", "changed-sha", "duplicate"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); target = root / 'packages/tyrell-zero-store/config'
                target.mkdir(parents=True)
                catalog = json.loads((ROOT / 'packages/tyrell-zero-store/config/store-catalog.json').read_text())
                if mutation == "new-version": catalog["packages"][0]["version"] = "0.1.2-1"
                elif mutation == "changed-sha": catalog["packages"][0]["sha256"] = "a" * 64
                else: catalog["packages"].append(copy.deepcopy(catalog["packages"][0]))
                (target / 'store-catalog.json').write_text(json.dumps(catalog))
                with self.subTest(mutation=mutation), self.assertRaises(metadata.MetadataError): self.generate(root)

    def test_modified_package_bytes_fail_before_source_or_signing_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'packages/tyrell-zero-store/config/store-catalog.json'; path.parent.mkdir(parents=True)
            path.write_bytes((ROOT / path.relative_to(root)).read_bytes())
            deb = root / 'dist/tyrell-status/0.1.0/tyrell-status_0.1.0-1_arm64.deb'; deb.parent.mkdir(parents=True)
            deb.write_bytes(b'not the qualified package')
            with self.assertRaisesRegex(metadata.MetadataError, "checksum"): self.generate(root)

    def test_base_identity_changes_when_reviewed_policy_bytes_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'packages/tyrell-zero-store/config'; target.mkdir(parents=True)
            for name in ('store-policy.json', 'store-catalog.json'):
                shutil.copyfile(ROOT / 'packages/tyrell-zero-store/config' / name, target / name)
            before = metadata.base_identity(root)
            with (target / 'store-policy.json').open('ab') as stream: stream.write(b'\n')
            self.assertNotEqual(before, metadata.base_identity(root))


if __name__ == "__main__":
    unittest.main()
