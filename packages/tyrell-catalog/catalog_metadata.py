"""Bounded signed discovery metadata. This module grants no install authority."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

MAX_ENVELOPE_BYTES = 256 * 1024
MAX_PACKAGES = 32
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_ARTWORK_BYTES = 64 * 1024
MAX_ARTWORK_DIMENSION = 128
MAX_VALIDITY_SECONDS = 90 * 24 * 60 * 60
MAX_SEQUENCE = 2**63 - 1
# Device/runtime selection is fixed. macOS's /usr/bin/openssl is LibreSSL and
# lacks this Ed25519 CLI; its development tests use the explicit OpenSSL 3 tool.
OPENSSL = "/opt/homebrew/opt/openssl@3/bin/openssl" if sys.platform == "darwin" else "/usr/bin/openssl"
DOMAIN = b"Tyrell Catalog v1\0"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ED25519_DER_PREFIX = bytes.fromhex("302a300506032b6570032100")
OPENSSL_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "OPENSSL_CONF": "/dev/null"}


class MetadataError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MetadataError(message)


def canonical(value):
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError) as error:
        raise MetadataError("Metadata is not canonical JSON data") from error


def load_json(data, *, limit=MAX_ENVELOPE_BYTES):
    require(isinstance(data, bytes) and 0 < len(data) <= limit, "Metadata size exceeds its bound")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate metadata field")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(MetadataError("Nonfinite JSON number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise MetadataError("Malformed metadata JSON") from error


def _fields(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), "Unexpected " + label + " fields")


def _integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, "Invalid " + label)


def _text(value, maximum, label):
    require(isinstance(value, str) and 0 < len(value) <= maximum
            and all(32 <= ord(c) <= 126 for c in value), "Invalid " + label)


def _sha(value, label):
    require(isinstance(value, str) and bool(HEX.fullmatch(value)), "Invalid " + label)


def origin(url):
    """Return a normalized safe HTTPS origin without performing DNS or I/O."""
    require(isinstance(url, str) and 0 < len(url) <= 2048
            and all(33 <= ord(c) <= 126 for c in url), "Invalid HTTPS URL")
    try:
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None
                and parsed.password is None and parsed.port in (None, 443)
                and not parsed.fragment and not parsed.query, "Unsafe HTTPS URL")
    except ValueError as error:
        raise MetadataError("Invalid HTTPS URL authority") from error
    require(bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", parsed.hostname))
            and ".." not in parsed.hostname and parsed.netloc == parsed.hostname,
            "HTTPS URL authority must be canonical")
    decoded = unquote(parsed.path)
    require(decoded.startswith("/") and all(33 <= ord(c) <= 126 for c in decoded) and "\\" not in decoded
            and not any(part in (".", "..") for part in decoded.split("/")), "Unsafe HTTPS URL path")
    # A second decoder must not reveal a hidden traversal or path separator.
    require("%" not in decoded, "Nested URL encoding is forbidden")
    return "https://" + parsed.hostname


def validate_payload(payload, allowed_origins):
    _fields(payload, ("schema", "sequence", "issued_at", "expires_at", "base_id", "packages"), "payload")
    require(type(payload["schema"]) is int and payload["schema"] == 1, "Unsupported catalog schema")
    _integer(payload["sequence"], 1, MAX_SEQUENCE, "catalog sequence")
    _integer(payload["issued_at"], 1577836800, MAX_SEQUENCE, "issue time")
    _integer(payload["expires_at"], 1577836801, MAX_SEQUENCE, "expiry time")
    require(0 < payload["expires_at"] - payload["issued_at"] <= MAX_VALIDITY_SECONDS,
            "Catalog validity interval exceeds its bound")
    _sha(payload["base_id"], "compatible base identity")
    require(isinstance(allowed_origins, list) and 0 < len(allowed_origins) <= 8,
            "Pinned HTTPS origins required")
    allowed = set()
    for value in allowed_origins:
        require(isinstance(value, str), "Pinned HTTPS origin must be text")
        require(origin(value + "/") == value, "Pinned HTTPS origin must be canonical")
        require(value not in allowed, "Duplicate pinned HTTPS origin")
        allowed.add(value)
    packages = payload["packages"]
    require(isinstance(packages, list) and 0 < len(packages) <= MAX_PACKAGES, "Catalog entry count exceeds its bound")
    identities = set()
    for item in packages:
        _fields(item, ("package", "version", "architecture", "sha256", "bytes", "url", "title", "summary", "artwork"), "package")
        require(isinstance(item["package"], str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,63}", item["package"])),
                "Invalid Debian package name")
        require(isinstance(item["version"], str) and bool(re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]{0,95}", item["version"])),
                "Invalid Debian package version")
        require(item["architecture"] in ("arm64", "all"), "Unsupported package architecture")
        identity = (item["package"], item["version"], item["architecture"])
        require(identity not in identities, "Duplicate catalog package identity")
        identities.add(identity)
        _sha(item["sha256"], "package checksum")
        _integer(item["bytes"], 1, MAX_PACKAGE_BYTES, "package size")
        require(origin(item["url"]) in allowed, "Package URL origin is not pinned")
        _text(item["title"], 40, "package title")
        _text(item["summary"], 120, "package summary")
        artwork = item["artwork"]
        _fields(artwork, ("sha256", "bytes", "url", "width", "height", "format"), "artwork")
        _sha(artwork["sha256"], "artwork checksum")
        _integer(artwork["bytes"], 1, MAX_ARTWORK_BYTES, "artwork size")
        require(origin(artwork["url"]) in allowed, "Artwork URL origin is not pinned")
        _integer(artwork["width"], 1, MAX_ARTWORK_DIMENSION, "artwork width")
        _integer(artwork["height"], 1, MAX_ARTWORK_DIMENSION, "artwork height")
        require(artwork["format"] == "png", "Unsupported artwork format")
    return payload


def public_key_der(pem):
    require(isinstance(pem, str) and len(pem) <= 256, "Invalid public key encoding")
    match = re.fullmatch(r"-----BEGIN PUBLIC KEY-----\n([A-Za-z0-9+/=\n]+)\n-----END PUBLIC KEY-----\n?", pem)
    require(match is not None, "Public key must use PEM public-key encoding")
    try:
        der = base64.b64decode(match.group(1).replace("\n", ""), validate=True)
    except ValueError as error:
        raise MetadataError("Malformed public key") from error
    require(len(der) == 44 and der.startswith(ED25519_DER_PREFIX), "Pinned key must be Ed25519")
    return der


def public_key_id(pem):
    return hashlib.sha256(public_key_der(pem)).hexdigest()


def _openssl(arguments, *, timeout=5):
    try:
        result = subprocess.run([OPENSSL, *map(str, arguments)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=OPENSSL_ENV, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MetadataError("Signature provider unavailable or timed out") from error
    require(result.returncode == 0, "Ed25519 signature operation failed")
    return result.stdout


def _unsigned(payload, key_id):
    return {"schema": 1, "key_id": key_id, "payload": payload}


def sign_payload(payload, private_key, *, allowed_origins):
    """Offline publisher operation. No private key is generated or retained here."""
    validate_payload(payload, allowed_origins)
    private_key = Path(private_key)
    require(private_key.is_file() and not private_key.is_symlink() and private_key.stat().st_size <= 16384,
            "A regular caller-provided private key is required")
    require(private_key.stat().st_mode & 0o077 == 0, "Private key permissions must exclude other users")
    der = _openssl(("pkey", "-in", private_key, "-pubout", "-outform", "DER"))
    require(len(der) == 44 and der.startswith(ED25519_DER_PREFIX), "Signing key must be Ed25519")
    key_id = hashlib.sha256(der).hexdigest()
    unsigned = _unsigned(payload, key_id)
    with tempfile.TemporaryDirectory(prefix="tyrell-catalog-sign-") as directory:
        message = Path(directory) / "message"
        message.write_bytes(DOMAIN + canonical(unsigned))
        signature = _openssl(("pkeyutl", "-sign", "-rawin", "-inkey", private_key.resolve(), "-in", message))
    require(len(signature) == 64, "Unexpected Ed25519 signature size")
    data = canonical({**unsigned, "signature": base64.b64encode(signature).decode("ascii")})
    require(len(data) <= MAX_ENVELOPE_BYTES, "Signed catalog exceeds its byte bound")
    return data


def verify_envelope(data, trust_policy, expected_base, now=None):
    """Verify signed bytes; freshness is separate so expired metadata can be browsed."""
    envelope = load_json(data)
    _fields(envelope, ("schema", "key_id", "payload", "signature"), "signed envelope")
    require(type(envelope["schema"]) is int and envelope["schema"] == 1, "Unsupported envelope schema")
    require(canonical(envelope) == data, "Signed envelope must use canonical JSON encoding")
    require(isinstance(trust_policy, dict) and type(trust_policy.get("schema")) is int
            and trust_policy["schema"] == 1, "Invalid pinned trust policy")
    _sha(expected_base, "local compatible base identity")
    _sha(trust_policy.get("base_id"), "pinned compatible base identity")
    require(expected_base == trust_policy["base_id"], "Local base differs from the pinned catalog compatibility boundary")
    validate_payload(envelope["payload"], trust_policy.get("allowed_origins"))
    require(envelope["payload"]["base_id"] == expected_base, "Catalog belongs to another compatible base")
    _integer(trust_policy.get("min_sequence"), 1, MAX_SEQUENCE, "protected minimum sequence")
    require(envelope["payload"]["sequence"] >= trust_policy["min_sequence"], "Catalog is below the protected sequence floor")
    keys = trust_policy.get("keys")
    require(isinstance(keys, list) and 0 < len(keys) <= 8, "Pinned public keys required")
    key_map = {}
    for key in keys:
        _fields(key, ("id", "public_key_pem"), "pinned key")
        _sha(key["id"], "pinned key identity")
        require(public_key_id(key["public_key_pem"]) == key["id"] and key["id"] not in key_map,
                "Pinned public key identity differs or is duplicated")
        key_map[key["id"]] = key["public_key_pem"]
    revoked = trust_policy.get("revoked_key_ids")
    require(isinstance(revoked, list) and len(revoked) <= 32
            and all(isinstance(value, str) for value in revoked) and len(set(revoked)) == len(revoked), "Invalid key revocation policy")
    for key_id in revoked:
        _sha(key_id, "revoked key identity")
    _sha(envelope["key_id"], "signing key identity")
    require(envelope["key_id"] in key_map and envelope["key_id"] not in revoked, "Catalog signing key is unknown or revoked")
    require(isinstance(envelope["signature"], str) and len(envelope["signature"]) == 88, "Invalid signature encoding")
    try:
        signature = base64.b64decode(envelope["signature"], validate=True)
    except ValueError as error:
        raise MetadataError("Invalid signature encoding") from error
    require(len(signature) == 64 and base64.b64encode(signature).decode("ascii") == envelope["signature"], "Invalid signature size")
    with tempfile.TemporaryDirectory(prefix="tyrell-catalog-verify-") as directory:
        directory = Path(directory)
        key, message, signed = directory / "public.pem", directory / "message", directory / "signature"
        key.write_text(key_map[envelope["key_id"]], encoding="ascii")
        message.write_bytes(DOMAIN + canonical(_unsigned(envelope["payload"], envelope["key_id"])))
        signed.write_bytes(signature)
        _openssl(("pkeyutl", "-verify", "-pubin", "-rawin", "-inkey", key, "-sigfile", signed, "-in", message))
    if now is None:
        freshness = "clock-unknown"
    else:
        _integer(now, 0, MAX_SEQUENCE, "current time")
        freshness = ("not-yet-valid" if now < envelope["payload"]["issued_at"] else
                     "expired" if now >= envelope["payload"]["expires_at"] else "fresh")
    return {"payload": envelope["payload"], "key_id": envelope["key_id"], "freshness": freshness,
            "envelope_sha256": hashlib.sha256(data).hexdigest()}


def base_identity(repository_root):
    """Compatibility identity of reviewed Store policy, catalog and vendor ABI."""
    root = Path(repository_root)
    directory = root / "packages/tyrell-zero-store/config"
    policy_bytes = (directory / "store-policy.json").read_bytes()
    catalog_bytes = (directory / "store-catalog.json").read_bytes()
    policy = load_json(policy_bytes)
    catalog = load_json(catalog_bytes)
    require(policy.get("schema") == 1 and catalog.get("schema") == 1
            and policy.get("store_source") == catalog.get("store_source"), "Reviewed Store identity differs")
    _sha(policy.get("vendor_sha256"), "reviewed vendor identity")
    identity = {"schema": 1, "store_source": policy["store_source"], "vendor_sha256": policy["vendor_sha256"],
                "store_policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
                "store_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest()}
    return hashlib.sha256(canonical(identity)).hexdigest()
