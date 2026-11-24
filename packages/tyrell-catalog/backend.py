#!/usr/bin/env python3
"""Fixed unprivileged Tyrell Catalog backend; no package-manager operations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys

# Python -I removes the script directory. Import only adjacent installed modules,
# never user site-packages, PYTHONPATH, the cache or a metadata-selected path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_cache
import catalog_metadata

TRUST_PATH = Path("/usr/share/tyrell-catalog/trust.json")
SOURCE_PATH = Path("/usr/share/tyrell-catalog/source.json")
SEED_PATH = Path("/usr/share/tyrell-catalog/seed.json")
STORE_POLICY = Path("/usr/share/tyrell-zero/store-policy.json")
STORE_CATALOG = Path("/usr/share/tyrell-zero/store-catalog.json")
VENDOR = Path("/usr/share/APPLaunch/bin/M5CardputerZero-AppStore.vendor")
ARTWORK_ROOT = Path("/usr/share/tyrell-catalog/artwork")
MAX_VIEW_BYTES = 64 * 1024
stopping = False


def stop(_signal, _frame):
    global stopping
    stopping = True


def trusted_bytes(path, maximum):
    """Read fixed reviewed root files without following any symlink component."""
    path = Path(path)
    if not path.is_absolute():
        raise catalog_cache.CacheError("Trusted package path must be absolute")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            info = os.fstat(child)
            if info.st_uid != 0 or info.st_mode & 0o022:
                os.close(child)
                raise catalog_cache.CacheError("Trusted directory is not protected")
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_nlink != 1 or info.st_size > maximum):
                raise catalog_cache.CacheError("Trusted package file is not protected")
            result = bytearray()
            while True:
                chunk = os.read(fd, min(65536, maximum + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > maximum:
                    raise catalog_cache.CacheError("Trusted package file exceeds its bound")
            return bytes(result)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def installed_base_matches(binding):
    try:
        if not isinstance(binding, dict) or set(binding) != {"schema", "store_source", "vendor_sha256", "store_policy_sha256", "store_catalog_sha256"} or binding["schema"] != 1:
            return False
        policy_bytes = trusted_bytes(STORE_POLICY, 256 * 1024)
        catalog_bytes = trusted_bytes(STORE_CATALOG, 1024 * 1024)
        policy, catalog = catalog_metadata.load_json(policy_bytes), catalog_metadata.load_json(catalog_bytes, limit=1024 * 1024)
        vendor = trusted_bytes(VENDOR, 16 * 1024 * 1024)
        return (hashlib.sha256(policy_bytes).hexdigest() == binding["store_policy_sha256"]
                and hashlib.sha256(catalog_bytes).hexdigest() == binding["store_catalog_sha256"]
                and hashlib.sha256(vendor).hexdigest() == binding["vendor_sha256"]
                and policy.get("store_source") == catalog.get("store_source") == binding["store_source"]
                and policy.get("vendor_sha256") == binding["vendor_sha256"])
    except (catalog_cache.CacheError, catalog_metadata.MetadataError, OSError, ValueError):
        return False


def configured_cache():
    trust_bytes = trusted_bytes(TRUST_PATH, 64 * 1024)
    source_bytes = trusted_bytes(SOURCE_PATH, 64 * 1024)
    trust = catalog_metadata.load_json(trust_bytes)
    source = catalog_metadata.load_json(source_bytes)
    if (set(source) != {"schema", "metadata_url", "base_binding"} or source.get("schema") != 1
            or hashlib.sha256(catalog_metadata.canonical(source["base_binding"])).hexdigest() != trust.get("base_id")):
        raise catalog_cache.CacheError("Reviewed source and base identity differ")
    endpoint = source["metadata_url"]
    if endpoint is not None:
        catalog_cache.allowed_url(endpoint, trust["allowed_origins"])
    seed = trusted_bytes(SEED_PATH, catalog_metadata.MAX_ENVELOPE_BYTES)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg and Path(xdg).is_absolute():
        cache_root = Path(xdg) / "tyrell-catalog"
    else:
        import pwd
        cache_root = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache/tyrell-catalog"
    def policy_current():
        try:
            return (trusted_bytes(TRUST_PATH, 64 * 1024) == trust_bytes
                    and trusted_bytes(SOURCE_PATH, 64 * 1024) == source_bytes)
        except (catalog_cache.CacheError, OSError):
            return False
    return catalog_cache.CatalogCache(trust, cache_root, seed=seed,
                                     base_compatible=lambda: policy_current() and installed_base_matches(source["base_binding"]),
                                     metadata_url=endpoint, cancelled=lambda: stopping,
                                     packaged_artwork=packaged_artwork, policy_current=policy_current)


def packaged_artwork(artwork):
    path = ARTWORK_ROOT / (artwork["sha256"] + ".png")
    return str(path), trusted_bytes(path, catalog_cache.MAX_ARTWORK_BYTES)


def text(value, maximum):
    value = str(value)
    return "".join(c if 32 <= ord(c) <= 126 else " " for c in value)[:maximum]


def view(status):
    lines = ["\t".join(("CATALOG", "1", text(status["state"], 24), str(status["sequence"]), text(status["message"], 160)))]
    if type(status.get("issued_at")) is int and type(status.get("expires_at")) is int:
        lines.append("\t".join(("VALIDITY", str(status["issued_at"]), str(status["expires_at"]))))
    for item in status["packages"][:32]:
        lines.append("\t".join(("PACKAGE", text(item["id"], 164), text(item["title"], 48),
                                 text(item["version"], 96), str(item["bytes"]), text(item["state"], 16), text(item["summary"], 120))))
    for item in status.get("artworks", [])[:32]:
        # Paths are local fixed-cache or packaged paths, never metadata URLs.
        # The GUI reopens bounded bytes and rechecks the supplied hash itself.
        if all(32 <= ord(c) <= 126 and c != "\t" for c in item["path"]) and len(item["path"]) <= 1024:
            lines.append("\t".join(("ARTWORK", text(item["id"], 164), item["sha256"], str(item["bytes"]),
                                     str(item["width"]), str(item["height"]), item["path"])))
    output = "\n".join(lines) + "\n"
    if len(output.encode("ascii")) > MAX_VIEW_BYTES:
        raise catalog_cache.CacheError("Catalog display exceeds its byte bound")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("view", "status", "refresh", "download", "artwork"))
    parser.add_argument("--id")
    args = parser.parse_args(argv)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, stop)
    try:
        if args.command in ("refresh", "download", "artwork") and (os.getuid() == 0 or os.geteuid() != os.getuid()):
            raise catalog_cache.CacheError("Catalog writes must run as the normal user")
        cache = configured_cache()
        if args.command in ("view", "status"):
            status = cache.status()
            print(view(status), end="") if args.command == "view" else print(json.dumps(status, sort_keys=True))
            return 0
        if args.command == "refresh":
            cache.refresh()
            message = "Catalog refreshed; discovery only"
        else:
            if args.id is None:
                raise catalog_cache.CacheError("Choose an exact catalog package")
            result = cache.download(args.id, artwork=args.command == "artwork")
            message = "Verified download saved; use the original Store for installation"
            if result["status"] == "cached":
                message = "Verified download already cached; installation is separate"
        print("RESULT\t1\tok\t" + text(message, 160))
        return 0
    except Exception as error:
        if args.command == "view":
            print("CATALOG\t1\terror\t0\t" + text(str(error), 160))
        elif args.command == "status":
            print(json.dumps({"state": "error", "sequence": 0, "message": str(error), "packages": [], "installation_available": False}))
        else:
            print("RESULT\t1\t" + ("cancelled" if stopping else "error") + "\t" + text(str(error), 160))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
