"""A valid discovery signature cannot expand the existing root Store policy."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
sys.path.insert(0, str(PACKAGE))
import catalog_metadata as metadata

spec = importlib.util.spec_from_file_location("catalog_store_boundary", ROOT / "packages/tyrell-zero-store/guard.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class AuthorityBoundaryTests(unittest.TestCase):
    def test_signed_new_app_or_new_version_is_still_rejected_by_root_policy(self):
        policy = json.loads((ROOT / "packages/tyrell-zero-store/config/store-policy.json").read_text())
        catalog = json.loads((ROOT / "packages/tyrell-zero-store/config/store-catalog.json").read_text())
        guard = store.Guard(root=object(), policy=policy, catalog=catalog)
        seed = metadata.load_json((PACKAGE / "config/seed.json").read_bytes())["payload"]
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "publisher.pem"
            subprocess.run([metadata.OPENSSL, "genpkey", "-algorithm", "Ed25519", "-out", str(key)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=metadata.OPENSSL_ENV)
            public = subprocess.check_output([metadata.OPENSSL, "pkey", "-in", str(key), "-pubout"],
                                             env=metadata.OPENSSL_ENV).decode("ascii")
            trust = {"schema": 1, "base_id": seed["base_id"], "min_sequence": 1,
                     "keys": [{"id": metadata.public_key_id(public), "public_key_pem": public}],
                     "revoked_key_ids": [], "allowed_origins": ["https://raw.githubusercontent.com"]}
            for name, version in (("tyrell-catalog", "0.1.0-1"), ("tyrell-status", "0.1.2-1")):
                with self.subTest(package=name, version=version):
                    payload = json.loads(json.dumps(seed))
                    payload["packages"] = [payload["packages"][0]]
                    payload["packages"][0].update(package=name, version=version)
                    signed = metadata.sign_payload(payload, key, allowed_origins=trust["allowed_origins"])
                    self.assertEqual(metadata.verify_envelope(signed, trust, seed["base_id"],
                                                             payload["issued_at"] + 1)["freshness"], "fresh")
                    with self.assertRaisesRegex(store.GuardError, "not qualified"):
                        guard.record(name, version)
        self.assertEqual(guard.record("tyrell-status", "0.1.0-1")["sha256"],
                         "15471a8f605e826d6f81daf3260602042b70b9c19330cb03b0bf839039533b2b")


if __name__ == "__main__":
    unittest.main()
