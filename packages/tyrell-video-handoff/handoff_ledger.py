"""Durable, exclusive D1 state/event ledger. No device or service operations.

SQLite FULL transactions and directory sync define the tested commit boundary.
This is not power-cut qualification or authentication against the owning user.
Missing/corrupt existing state is never replaced by a fresh empty authority.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
from urllib.parse import quote

MAX_STATE_BYTES = 256 * 1024
MAX_EVENT_BYTES = 16 * 1024
MAX_COMMITS = 2048
MAX_DB_BYTES = 16 * 1024 * 1024
MAX_JOURNAL_BYTES = MAX_DB_BYTES + 512 * 1024
APPLICATION_ID = 0x54564832
VERSION = 1
ZERO = "0" * 64
TABLES = {
    "head": "CREATE TABLE head (id INTEGER PRIMARY KEY CHECK(id = 1), revision INTEGER NOT NULL, state BLOB NOT NULL, state_sha256 TEXT NOT NULL, chain_sha256 TEXT NOT NULL)",
    "events": "CREATE TABLE events (revision INTEGER PRIMARY KEY, event BLOB NOT NULL, state_sha256 TEXT NOT NULL, previous_sha256 TEXT NOT NULL, entry_sha256 TEXT NOT NULL)",
}


class LedgerError(ValueError):
    pass


class BusyError(LedgerError):
    pass


def _require(condition, message):
    if not condition:
        raise LedgerError(message)


def _canonical(value, maximum):
    _require(type(value) is dict, "ledger values must be JSON objects")
    def check(item, depth=0):
        _require(depth <= 64, "ledger JSON nesting exceeds limit")
        if type(item) is dict:
            _require(all(type(key) is str for key in item), "JSON keys must be strings")
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) is float:
            _require(math.isfinite(item), "nonfinite ledger value")
        else:
            _require(type(item) in (str, int, bool, type(None)), "unsupported ledger value")
    try:
        check(value)
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise LedgerError("invalid ledger JSON") from error
    _require(len(raw) <= maximum, "ledger JSON exceeds byte limit")
    return raw


def _pairs(items):
    value = {}
    for key, item in items:
        _require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def _decode(raw, maximum):
    _require(type(raw) is bytes and len(raw) <= maximum, "invalid stored JSON bytes")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(LedgerError("nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LedgerError("invalid stored JSON") from error
    _require(_canonical(value, maximum) == raw, "noncanonical stored JSON")
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _entry(revision, event, state_sha, previous):
    return _sha(_canonical({"revision": revision, "event_sha256": _sha(event),
                            "state_sha256": state_sha, "previous_sha256": previous}, MAX_EVENT_BYTES))


def _directory(value):
    candidate = Path(value).absolute()
    _require(".." not in candidate.parts, "ledger directory traversal")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            _require(current == candidate, "ledger parent directory is missing")
            break
        if stat.S_ISLNK(info.st_mode):
            allowed = sys.platform == "darwin" and current in (Path("/tmp"), Path("/var"))
            _require(allowed, "symlink ledger path")
        elif current != candidate:
            _require(stat.S_ISDIR(info.st_mode), "ledger ancestor is not a directory")
            _require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX,
                     "writable unprotected ledger ancestor")
    return candidate.resolve(strict=False)


class Ledger:
    """One process owns one ledger for its lifetime; returned values are copies."""

    @classmethod
    def create(cls, directory, initial):
        raw = _canonical(initial, MAX_STATE_BYTES)
        return cls(directory, initial=raw)

    @classmethod
    def open(cls, directory):
        return cls(directory)

    def __init__(self, directory, *, initial=None):
        self.directory = _directory(directory)
        self._pid = os.getpid()
        self._mutex = threading.RLock()
        self._db = None
        self._lock_fd = self._dir_fd = None
        self._db_identity = self._lock_identity = self._directory_identity = None
        self._poisoned = self._closed = False
        try:
            if initial is not None:
                self.directory.mkdir(mode=0o700)  # Existing roots are never initialized again.
            info = self.directory.lstat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
                     stat.S_IMODE(info.st_mode) == 0o700, "ledger directory must be private and owned")
            self._dir_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(self._dir_fd)
            self._directory_identity = (info.st_dev, info.st_ino)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
            if initial is not None:
                flags |= os.O_CREAT | os.O_EXCL
            self._lock_fd = os.open("lock", flags, 0o600, dir_fd=self._dir_fd)
            self._file("lock", 4096)
            info = os.fstat(self._lock_fd)
            self._lock_identity = (info.st_dev, info.st_ino)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BusyError("handoff ledger is already owned") from error
            if initial is not None:
                fd = os.open("ledger.sqlite3", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self._dir_fd)
                os.fsync(fd)
                os.close(fd)
                os.fsync(self._dir_fd)
                parent = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            self._files()
            uri = "file:" + quote(str(self.directory / "ledger.sqlite3"), safe="/") + "?mode=rw"
            self._db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None, check_same_thread=False)
            self._db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_STATE_BYTES + MAX_EVENT_BYTES)
            self._db.enable_load_extension(False)
            if initial is None:
                self._verify()
            else:
                self._db.execute("PRAGMA page_size=4096")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA fullfsync=ON")
            self._db.execute("PRAGMA trusted_schema=OFF")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA max_page_count=" + str(MAX_DB_BYTES // 4096))
            if initial is not None:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute("PRAGMA application_id=" + str(APPLICATION_ID))
                self._db.execute("PRAGMA user_version=" + str(VERSION))
                for sql in TABLES.values():
                    self._db.execute(sql)
                event = _canonical({"kind": "initialize"}, MAX_EVENT_BYTES)
                state_sha = _sha(initial)
                entry = _entry(0, event, state_sha, ZERO)
                self._db.execute("INSERT INTO head VALUES(1,?,?,?,?)", (0, initial, state_sha, entry))
                self._db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (0, event, state_sha, ZERO, entry))
                self._db.execute("COMMIT")
                os.fsync(self._dir_fd)
                self._verify()
        except BaseException as error:
            self.close()
            if isinstance(error, (KeyboardInterrupt, SystemExit, LedgerError)):
                raise
            raise LedgerError("cannot open handoff ledger") from error

    def _file(self, name, maximum):
        info = os.stat(name, dir_fd=self._dir_fd, follow_symlinks=False)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() and
                 stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= maximum,
                 "unsafe or oversized ledger file: " + name)
        return info

    def _files(self):
        directory = self.directory.lstat()
        _require(stat.S_ISDIR(directory.st_mode) and directory.st_uid == os.geteuid() and
                 stat.S_IMODE(directory.st_mode) == 0o700 and
                 (directory.st_dev, directory.st_ino) == self._directory_identity,
                 "ledger directory identity changed")
        names = set(os.listdir(self._dir_fd))
        _require({"lock", "ledger.sqlite3"} <= names <= {"lock", "ledger.sqlite3", "ledger.sqlite3-journal"},
                 "missing or unexpected ledger files")
        lock = self._file("lock", 4096)
        _require((lock.st_dev, lock.st_ino) == self._lock_identity, "ledger lock identity changed")
        database = self._file("ledger.sqlite3", MAX_DB_BYTES)
        identity = (database.st_dev, database.st_ino)
        if self._db_identity is None:
            self._db_identity = identity
        _require(identity == self._db_identity, "ledger database identity changed")
        if "ledger.sqlite3-journal" in names:
            self._file("ledger.sqlite3-journal", MAX_JOURNAL_BYTES)

    def _usable(self):
        _require(os.getpid() == self._pid, "ledger cannot be used after fork")
        _require(not self._closed and not self._poisoned, "ledger is closed or poisoned; reopen required")

    def _verify(self):
        self._files()
        db = self._db
        _require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)], "ledger integrity failure")
        _require(db.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,) and
                 db.execute("PRAGMA user_version").fetchone() == (VERSION,), "unsupported ledger version")
        _require(db.execute("PRAGMA page_size").fetchone() == (4096,), "unsupported ledger page size")
        schema = db.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
        _require(dict(schema) == TABLES and len(schema) == len(TABLES), "unexpected ledger schema")
        heads = db.execute("SELECT id,revision,state,state_sha256,chain_sha256 FROM head LIMIT 2").fetchall()
        _require(len(heads) == 1 and heads[0][0] == 1, "missing ledger head")
        _, revision, raw, state_sha, chain = heads[0]
        _require(type(revision) is int and 0 <= revision <= MAX_COMMITS, "invalid ledger revision")
        state = _decode(raw, MAX_STATE_BYTES)
        _require(state_sha == _sha(raw), "ledger state digest mismatch")
        rows = db.execute("SELECT revision,event,state_sha256,previous_sha256,entry_sha256 FROM events ORDER BY revision LIMIT ?",
                          (MAX_COMMITS + 2,)).fetchall()
        _require(len(rows) == revision + 1, "ledger history is incomplete")
        previous, history = ZERO, []
        for expected, row in enumerate(rows):
            number, event_raw, event_state_sha, before, entry = row
            _require(number == expected and before == previous and
                     isinstance(event_state_sha, str) and len(event_state_sha) == 64 and
                     all(c in "0123456789abcdef" for c in event_state_sha), "invalid ledger event chain")
            event = _decode(event_raw, MAX_EVENT_BYTES)
            _require(entry == _entry(number, event_raw, event_state_sha, previous), "ledger event digest mismatch")
            previous = entry
            history.append({"revision": number, "event": event, "state_sha256": event_state_sha,
                            "previous_sha256": before, "entry_sha256": entry})
        _require(previous == chain and rows[-1][2] == state_sha, "ledger head/history mismatch")
        return state, revision, history

    def _read_verified(self):
        self._usable()
        try:
            return self._verify()
        except BaseException as error:
            self._poisoned = True
            if isinstance(error, (KeyboardInterrupt, SystemExit, LedgerError)):
                raise
            raise LedgerError("cannot verify handoff ledger") from error

    def read(self):
        with self._mutex:
            return copy.deepcopy(self._read_verified()[0])

    @property
    def revision(self):
        with self._mutex:
            return self._read_verified()[1]

    def history(self):
        with self._mutex:
            return copy.deepcopy(self._read_verified()[2])

    def _fault(self, point):
        """Private test seam for subprocess termination at commit boundaries."""

    def commit(self, state, event, expected_revision=None):
        state_raw = _canonical(state, MAX_STATE_BYTES)
        event_raw = _canonical(event, MAX_EVENT_BYTES)
        with self._mutex:
            _, old, history = self._read_verified()
            if expected_revision is not None:
                _require(type(expected_revision) is int and expected_revision == old, "stale expected ledger revision")
            _require(old < MAX_COMMITS, "ledger history limit reached; no records discarded")
            revision, state_sha = old + 1, _sha(state_raw)
            previous = history[-1]["entry_sha256"]
            entry = _entry(revision, event_raw, state_sha, previous)
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (revision, event_raw, state_sha, previous, entry))
                self._db.execute("UPDATE head SET revision=?,state=?,state_sha256=?,chain_sha256=? WHERE id=1",
                                 (revision, state_raw, state_sha, entry))
                self._fault("before-commit")
                self._db.execute("COMMIT")
                self._fault("after-commit")
                os.fsync(self._dir_fd)
                self._verify()
                return revision
            except BaseException as error:
                self._poisoned = True
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise LedgerError("ledger commit failed; outcome requires reopen and reconciliation") from error

    def close(self):
        with self._mutex:
            if self._closed:
                return
            _require(os.getpid() == self._pid, "inherited ledger must not close/unlock its parent")
            self._closed = True
            try:
                if self._db is not None:
                    self._db.close()
            finally:
                if self._lock_fd is not None:
                    os.close(self._lock_fd)
                    self._lock_fd = None
                if self._dir_fd is not None:
                    os.close(self._dir_fd)
                    self._dir_fd = None

    def __enter__(self):
        self._usable()
        return self

    def __exit__(self, *_args):
        self.close()
