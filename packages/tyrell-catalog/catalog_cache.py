"""Unprivileged, bounded catalog cache. Discovery never grants installation rights."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import selectors
import signal
import ssl
import stat
import struct
import time
import resource
from urllib.parse import urlsplit
import uuid
import zlib

import catalog_metadata as metadata

MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_ARTWORK_BYTES = 64 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_FILES = 96
MAX_STATE_BYTES = 2 * metadata.MAX_ENVELOPE_BYTES
TRANSFER_SECONDS = 45
STATE_FILE = "catalog-state.json"


class CacheError(RuntimeError):
    pass


class Cancelled(CacheError):
    pass


class Busy(CacheError):
    pass


def require(condition, message):
    if not condition:
        raise CacheError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def system_clock():
    now = int(time.time())
    return now if now >= 1577836800 else None


def _file(info, uid):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
            and not info.st_mode & 0o022, "Cache file is redirected or writable by another user")


class CacheDirectory:
    """Anchor all access to no-follow directory descriptors, including ancestors."""
    def __init__(self, root, *, create=False, uid=None):
        self.uid = os.geteuid() if uid is None else uid
        path = Path(root).absolute()
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in path.parts[1:]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            require(info.st_uid == self.uid and not info.st_mode & 0o022,
                    "Cache directory must belong to the current user")
            self.fd = descriptor
            descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def subdirectory(self, name, *, create=False):
        require(name in ("packages", "artwork"), "Unknown cache object directory")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
            except FileExistsError:
                pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            require(info.st_uid == self.uid and not info.st_mode & 0o022, "Unsafe cache object directory")
            return fd
        except Exception:
            os.close(fd)
            raise

    def read(self, name, limit, *, directory=None):
        require("/" not in name and name not in ("", ".", ".."), "Unsafe cache file name")
        parent = self.fd if directory is None else directory
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        try:
            _file(os.fstat(fd), self.uid)
            require(os.fstat(fd).st_size <= limit, "Cached file exceeds its byte bound")
            result = bytearray()
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                require(len(result) <= limit, "Cached file grew beyond its byte bound")
            return bytes(result)
        finally:
            os.close(fd)

    @contextmanager
    def lock(self):
        fd = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     0o600, dir_fd=self.fd)
        try:
            _file(os.fstat(fd), self.uid)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Busy("Another catalog operation is running") from error
            yield
        finally:
            os.close(fd)

    def quota(self, additional, *, additional_files=1):
        total = count = 0
        def scan(fd, depth):
            nonlocal total, count
            with os.scandir(fd) as entries:
                for entry in entries:
                    count += 1
                    require(count + additional_files <= MAX_CACHE_FILES, "Catalog cache file quota exceeded")
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        require(depth == 0 and entry.name in ("packages", "artwork"), "Unknown cache directory")
                        child = self.subdirectory(entry.name)
                        try:
                            scan(child, depth + 1)
                        finally:
                            os.close(child)
                    else:
                        _file(info, self.uid)
                        total += info.st_size
                        require(total + additional <= MAX_CACHE_BYTES, "Catalog cache byte quota exceeded")
        scan(self.fd, 0)
        require(total + additional <= MAX_CACHE_BYTES, "Catalog cache byte quota exceeded")

    def cleanup_temporary(self):
        # A killed operation can leave only our bounded temporary regular file.
        # Never follow or remove unknown entries or another user's data.
        with os.scandir(self.fd) as entries:
            for index, entry in enumerate(entries):
                require(index < MAX_CACHE_FILES, "Catalog cache file quota exceeded")
                if entry.name.startswith(".partial-") and len(entry.name) == 41:
                    _file(entry.stat(follow_symlinks=False), self.uid)
                    os.unlink(entry.name, dir_fd=self.fd)

    def atomic(self, name, data, *, directory=None, cancelled=lambda: False, before_replace=None,
               commit_check=None):
        require("/" not in name and name not in ("", ".", ".."), "Unsafe cache destination")
        parent = self.fd if directory is None else directory
        try:
            existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _file(existing, self.uid)
        temporary = ".partial-" + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.fd)
        try:
            view = memoryview(data)
            while view:
                if cancelled():
                    raise Cancelled("Catalog operation cancelled")
                view = view[os.write(fd, view):]
            os.fsync(fd)
            if before_replace is not None:
                before_replace()
            if cancelled():
                raise Cancelled("Catalog operation cancelled")
            if commit_check is not None:
                commit_check()
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


def allowed_url(url, origins):
    try:
        origin = metadata.origin(url)
    except metadata.MetadataError as error:
        raise CacheError(str(error)) from error
    require(origin in origins, "Download origin is not pinned by the reviewed package")
    return urlsplit(url)


def _http_body(url, maximum, deadline):
    """The isolated network worker never follows redirects or executes content."""
    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(parsed.hostname, 443,
                                              timeout=min(10, max(0.1, deadline - time.monotonic())),
                                              context=ssl.create_default_context())
    try:
        connection.request("GET", parsed.path, headers={"User-Agent": "Tyrell-Catalog/0.1",
                                                       "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        require(response.status == 200, "HTTPS response was not 200; redirects are refused")
        encoding = response.getheader("Content-Encoding")
        require(encoding in (None, "identity"), "Encoded downloads are refused")
        lengths = response.headers.get_all("Content-Length", [])
        require(len(lengths) <= 1, "Ambiguous HTTP length")
        expected = None
        if lengths:
            require(lengths[0].isdigit(), "Invalid HTTP length")
            expected = int(lengths[0])
            require(expected <= maximum, "HTTP download exceeds its byte bound")
        result = bytearray()
        while True:
            require(time.monotonic() < deadline, "HTTPS download deadline expired")
            if connection.sock is not None:
                connection.sock.settimeout(min(10, max(0.1, deadline - time.monotonic())))
            chunk = response.read(min(65536, maximum + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
            require(len(result) <= maximum, "HTTP download exceeds its byte bound")
        require(expected is None or expected == len(result), "Truncated HTTP download")
        return bytes(result)
    finally:
        connection.close()


def _prepare_worker(result_fd, deadline):
    # A backend killed outside the GUI must not leave an inherited cache flock
    # held by a DNS worker. Keep only stdio and the one result pipe.
    try:
        descriptors = [int(name) for name in os.listdir("/proc/self/fd")]
    except OSError:
        upper = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if upper == resource.RLIM_INFINITY:
            upper = max(65536, result_fd + 1)
        os.closerange(3, result_fd)
        os.closerange(result_fd + 1, upper)
    else:
        for fd in descriptors:
            if fd > 2 and fd != result_fd:
                try:
                    os.close(fd)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    # Default termination runs in the kernel even while a resolver C call is
    # blocked, and still applies if its supervising backend has already died.
    signal.setitimer(signal.ITIMER_REAL,
                     min(TRANSFER_SECONDS, max(0.01, deadline - time.monotonic())) + 0.25)


def https_fetch(url, maximum, deadline, cancelled):
    """Bound DNS/TLS/read work by supervising a killable single-purpose child."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            _prepare_worker(write_fd, deadline)
            result = b"O" + _http_body(url, maximum, deadline)
        except Exception as error:
            result = b"E" + str(error).encode("ascii", "replace")[:300]
        try:
            view = memoryview(result)
            while view:
                view = view[os.write(write_fd, view):]
        finally:
            os.close(write_fd)
            os._exit(0)
    os.close(write_fd)
    selector = selectors.DefaultSelector()
    selector.register(read_fd, selectors.EVENT_READ)
    output = bytearray()
    try:
        while True:
            if cancelled():
                raise Cancelled("Catalog operation cancelled")
            remaining = deadline - time.monotonic()
            require(remaining > 0, "HTTPS download deadline expired")
            if not selector.select(min(0.1, remaining)):
                continue
            chunk = os.read(read_fd, min(65536, maximum + 302 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            require(len(output) <= maximum + 1, "Network worker exceeded its byte bound")
        require(bool(output) and output[0] == ord("O"), bytes(output[1:]).decode("ascii", "replace") or "HTTPS worker failed")
        return bytes(output[1:])
    finally:
        selector.close()
        os.close(read_fd)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        while True:
            try:
                os.waitpid(pid, 0)
                break
            except InterruptedError:
                continue


def validate_png(data, artwork):
    """Bound the actual image's dimensions and inflation, not only its metadata."""
    require(len(data) <= MAX_ARTWORK_BYTES and data.startswith(b"\x89PNG\r\n\x1a\n"), "Artwork is not a bounded PNG")
    position = 8
    header = None
    compressed = bytearray()
    ended = False
    while position < len(data):
        require(position + 12 <= len(data), "Truncated PNG")
        size = struct.unpack_from(">I", data, position)[0]
        require(size <= MAX_ARTWORK_BYTES and position + size + 12 <= len(data), "Oversized PNG chunk")
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + size]
        checksum = struct.unpack_from(">I", data, position + 8 + size)[0]
        require(zlib.crc32(kind + body) & 0xffffffff == checksum, "PNG checksum mismatch")
        if kind == b"IHDR":
            require(header is None and position == 8 and size == 13, "Invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            require((width, height) == (artwork["width"], artwork["height"])
                    and 0 < width <= 128 and 0 < height <= 128 and depth == 8 and color in (2, 6)
                    and (compression, filtering, interlace) == (0, 0, 0), "Unsupported or oversized PNG format")
            header = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            require(header is not None and not ended, "PNG image data precedes its header")
            compressed.extend(body)
        elif kind == b"IEND":
            require(size == 0 and header is not None and position + 12 == len(data), "Invalid PNG end")
            ended = True
        else:
            # The C1 artwork subset has no profiles or text. Ancillary chunks
            # such as iCCP/zTXt can carry independently compressed payloads that
            # are not bounded by the image's pixel dimensions.
            raise CacheError("Unsupported PNG chunk; only IHDR, IDAT and IEND are permitted")
        position += size + 12
    require(ended and bool(compressed) and header is not None, "Incomplete PNG")
    width, height, channels = header
    expected = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(bytes(compressed), expected + 1)
    require(len(pixels) == expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
            "PNG inflation differs from bounded image dimensions")
    stride = 1 + width * channels
    require(all(pixels[offset] <= 4 for offset in range(0, expected, stride)), "Invalid PNG filter")


class CatalogCache:
    def __init__(self, trust_policy, cache_root, *, seed=None, base_compatible=False,
                 metadata_url=None, transport=https_fetch, clock=system_clock,
                 cancelled=lambda: False, before_replace=None, packaged_artwork=None,
                 policy_current=lambda: True):
        self.trust = trust_policy
        self.root = Path(cache_root)
        self.seed = seed
        self.base_compatible = base_compatible
        self.metadata_url = metadata_url
        self.transport = transport
        self.clock = clock
        self.cancelled = cancelled
        self.before_replace = before_replace
        self.packaged_artwork = packaged_artwork
        self.policy_current = policy_current

    def compatible(self):
        return bool(self.base_compatible() if callable(self.base_compatible) else self.base_compatible)

    def verify(self, data):
        try:
            return metadata.verify_envelope(data, self.trust, self.trust["base_id"], self.clock())
        except metadata.MetadataError as error:
            raise CacheError(str(error)) from error

    def fresh_at_commit(self, verified):
        require(self.policy_current(), "Reviewed catalog policy changed; reopen the catalog")
        now = self.clock()
        payload = verified["payload"]
        require(type(now) is int and payload["issued_at"] <= now < payload["expires_at"],
                "Catalog expired or the clock became unknown before publication")

    def _state(self, directory):
        data = directory.read(STATE_FILE, MAX_STATE_BYTES)
        try:
            state = metadata.load_json(data, limit=MAX_STATE_BYTES)
            require(set(state) == {"schema", "envelope", "high_water"} and state["schema"] == 1,
                    "Invalid cached state schema")
            require(type(state["high_water"]) is int and 0 < state["high_water"] <= metadata.MAX_SEQUENCE,
                    "Invalid cached sequence hint")
            raw = base64.b64decode(state["envelope"], validate=True)
            require(len(raw) <= metadata.MAX_ENVELOPE_BYTES, "Cached catalog is oversized")
            verified = self.verify(raw)
            require(state["high_water"] >= verified["payload"]["sequence"], "Cached sequence hint regressed")
            return verified, raw, state["high_water"]
        except (ValueError, TypeError, KeyError, metadata.MetadataError) as error:
            raise CacheError("Invalid cached catalog state") from error

    def _catalog(self, directory=None):
        error = None
        if directory is not None:
            try:
                return (*self._state(directory), None)
            except FileNotFoundError:
                pass
            except (CacheError, OSError) as failure:
                error = "Cached metadata rejected; showing packaged catalog"
        if self.seed is not None:
            verified = self.verify(self.seed)
            return verified, self.seed, verified["payload"]["sequence"], error
        return None, None, self.trust["min_sequence"], error

    def _object_cached(self, directory, package):
        if directory is None:
            return False
        try:
            fd = directory.subdirectory("packages")
            try:
                data = directory.read(package["sha256"] + ".deb", min(MAX_PACKAGE_BYTES, package["bytes"]), directory=fd)
                return len(data) == package["bytes"] and digest(data) == package["sha256"]
            finally:
                os.close(fd)
        except (CacheError, OSError):
            return False

    def _artwork(self, directory, package):
        artwork = package["artwork"]
        candidates = []
        if self.packaged_artwork is not None:
            try:
                supplied = self.packaged_artwork(artwork)
                if supplied is not None:
                    candidates.append(supplied)
            except (CacheError, OSError):
                pass
        if directory is not None:
            try:
                fd = directory.subdirectory("artwork")
                try:
                    name = artwork["sha256"] + ".png"
                    data = directory.read(name, artwork["bytes"], directory=fd)
                    candidates.append((str(self.root.absolute() / "artwork" / name), data))
                finally:
                    os.close(fd)
            except (CacheError, OSError):
                pass
        for path, data in candidates:
            try:
                require(len(data) == artwork["bytes"] and digest(data) == artwork["sha256"], "Artwork cache identity changed")
                validate_png(data, artwork)
                return {"id": package["package"] + "@" + package["version"],
                        "path": path, **{key: artwork[key] for key in ("sha256", "bytes", "width", "height")}}
            except CacheError:
                continue
        return None

    def _status(self, directory):
        require(self.policy_current(), "Reviewed catalog policy changed; reopen the catalog")
        verified, _, _, warning = self._catalog(directory)
        if verified is None:
            return {"state": "empty", "sequence": 0, "message": warning or "No verified catalog; refresh explicitly",
                    "packages": [], "artworks": [], "installation_available": False}
        state = verified["freshness"] if self.compatible() else "incompatible"
        messages = {"fresh": "Verified discovery; downloads only", "expired": "Catalog expired; browse only",
                    "not-yet-valid": "Catalog not yet valid; check the clock", "clock-unknown": "Clock unknown; browse only",
                    "incompatible": "Base not verified; browse only"}
        packages = []
        artworks = []
        for item in verified["payload"]["packages"]:
            row = dict(item, id=item["package"] + "@" + item["version"])
            row["state"] = ("cached" if self._object_cached(directory, item) else "downloadable") if state == "fresh" else "browse-only"
            packages.append(row)
            artwork = self._artwork(directory, item)
            if artwork is not None:
                artworks.append(artwork)
        return {"state": state, "sequence": verified["payload"]["sequence"],
                "issued_at": verified["payload"]["issued_at"], "expires_at": verified["payload"]["expires_at"],
                "message": warning or messages[state], "packages": packages, "artworks": artworks,
                "catalog_sha256": verified["envelope_sha256"], "installation_available": False,
                "rollback_protection": "Packaged minimum only; user cache history is not tamper-resistant"}

    def status(self):
        try:
            with CacheDirectory(self.root) as directory:
                return self._status(directory)
        except FileNotFoundError:
            return self._status(None)
        except (CacheError, OSError) as error:
            status = self._status(None)
            status["message"] = "Cache unavailable; showing packaged catalog"
            return status

    def _download(self, url, maximum, deadline):
        allowed_url(url, self.trust["allowed_origins"])
        if self.cancelled():
            raise Cancelled("Catalog operation cancelled")
        require(time.monotonic() < deadline, "Catalog operation deadline expired")
        result = self.transport(url, maximum, deadline, self.cancelled)
        if self.cancelled():
            raise Cancelled("Catalog operation cancelled")
        require(isinstance(result, bytes) and len(result) <= maximum, "Download exceeds its byte bound")
        require(time.monotonic() < deadline, "Catalog operation deadline expired")
        return result

    def refresh(self):
        require(self.policy_current(), "Reviewed catalog policy changed; reopen the catalog")
        require(self.metadata_url is not None, "No catalog refresh source is configured")
        allowed_url(self.metadata_url, self.trust["allowed_origins"])
        with CacheDirectory(self.root, create=True) as directory, directory.lock():
            directory.cleanup_temporary()
            directory.quota(MAX_STATE_BYTES)
            current, _, high, _ = self._catalog(directory)
            deadline = time.monotonic() + TRANSFER_SECONDS
            data = self._download(self.metadata_url, metadata.MAX_ENVELOPE_BYTES, deadline)
            verified = self.verify(data)
            require(verified["freshness"] == "fresh", "Fresh catalog and a valid clock are required for refresh")
            sequence = verified["payload"]["sequence"]
            require(sequence >= high, "Catalog sequence replay rejected")
            if current is not None and sequence == current["payload"]["sequence"]:
                require(verified["envelope_sha256"] == current["envelope_sha256"], "Conflicting catalog bytes reuse a sequence")
            state = metadata.canonical({"schema": 1, "envelope": base64.b64encode(data).decode("ascii"),
                                        "high_water": max(sequence, high)})
            directory.atomic(STATE_FILE, state, cancelled=self.cancelled, before_replace=self.before_replace,
                             commit_check=lambda: self.fresh_at_commit(verified))
            return self._status(directory)

    def download(self, identity, *, artwork=False):
        require(self.compatible(), "Installed base is not verified for these downloads")
        with CacheDirectory(self.root, create=True) as directory, directory.lock():
            directory.cleanup_temporary()
            verified, _, _, _ = self._catalog(directory)
            require(verified is not None and verified["freshness"] == "fresh", "Fresh catalog and a valid clock are required for download")
            matches = [p for p in verified["payload"]["packages"] if p["package"] + "@" + p["version"] == identity]
            require(len(matches) == 1, "Selected package is not in the verified catalog")
            package = matches[0]
            item = package["artwork"] if artwork else package
            maximum = MAX_ARTWORK_BYTES if artwork else MAX_PACKAGE_BYTES
            require(0 < item["bytes"] <= maximum, "Selected download exceeds the application quota")
            group, extension = ("artwork", ".png") if artwork else ("packages", ".deb")
            subdir = directory.subdirectory(group, create=True)
            try:
                name = item["sha256"] + extension
                try:
                    data = directory.read(name, item["bytes"], directory=subdir)
                except (FileNotFoundError, CacheError):
                    data = None
                if data is not None and len(data) == item["bytes"] and digest(data) == item["sha256"]:
                    if artwork:
                        validate_png(data, item)
                    return {"status": "cached", "sha256": item["sha256"], "bytes": len(data),
                            "path": str(self.root.absolute() / group / name), "installation_available": False}
                directory.quota(item["bytes"])
                data = self._download(item["url"], item["bytes"], time.monotonic() + TRANSFER_SECONDS)
                require(len(data) == item["bytes"] and digest(data) == item["sha256"], "Downloaded bytes differ from signed size or SHA-256")
                # Expiry or base invalidation during a transfer must not publish it.
                latest = self.verify(verified_envelope(directory, self.seed))
                require(self.compatible() and latest["freshness"] == "fresh" and latest["envelope_sha256"] == verified["envelope_sha256"],
                        "Catalog validity changed during download")
                if artwork:
                    validate_png(data, item)
                def commit_check():
                    require(digest(verified_envelope(directory, self.seed)) == verified["envelope_sha256"],
                            "Catalog changed before download publication")
                    require(self.compatible(), "Installed base changed before download publication")
                    self.fresh_at_commit(verified)
                directory.atomic(name, data, directory=subdir, cancelled=self.cancelled,
                                 before_replace=self.before_replace, commit_check=commit_check)
                return {"status": "downloaded", "sha256": item["sha256"], "bytes": len(data),
                        "path": str(self.root.absolute() / group / name), "installation_available": False}
            finally:
                os.close(subdir)


def verified_envelope(directory, seed):
    try:
        state = metadata.load_json(directory.read(STATE_FILE, MAX_STATE_BYTES), limit=MAX_STATE_BYTES)
        return base64.b64decode(state["envelope"], validate=True)
    except FileNotFoundError:
        require(seed is not None, "Verified catalog disappeared")
        return seed
