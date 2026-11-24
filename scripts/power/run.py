#!/usr/bin/env python3
"""Prepare offline power-policy plans and memory-only transaction rehearsals."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import eligibility
import transaction

ROOT = Path(__file__).resolve().parents[2]
INPUT_BYTES = 256 * 1024
OUTPUT_BYTES = 2 * 1024 * 1024
SOURCES = ("scripts/power/run.py", "scripts/power/eligibility.py", "scripts/power/transaction.py",
           "packages/tyrell-zero-power-policy/power_policy.py",
           "image/contracts/tyrell-zero-power.json", "research/tyrell-zero-image-validation.json")


def unique_keys(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate JSON key: " + key)
        value[key] = item
    return value


def reject_constant(value):
    raise ValueError("nonfinite JSON value: " + value)


def read_file(path, maximum=INPUT_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("input must be a bounded regular file")
        raw = bytearray()
        while len(raw) <= maximum:
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        if len(raw) > maximum:
            raise ValueError("input exceeds size limit")
        return bytes(raw)
    finally:
        os.close(fd)


def load_bound(path):
    raw = read_file(path)
    value = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    return value, {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def source_binding():
    return {name: hashlib.sha256(read_file(ROOT / name)).hexdigest() for name in SOURCES}


def save_new(path, raw):
    """The single mutation is an explicitly selected new JSON output file."""
    if len(raw) > OUTPUT_BYTES:
        raise ValueError("report exceeds output limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="Unverified snapshot template, never an activation input")
    template.add_argument("--output", type=Path, required=True)
    for name in ("plan", "rehearse"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot", type=Path, required=True)
        command.add_argument("--preset", choices=("baseline", "ethernet", "cpu", "combined"), required=True)
        command.add_argument("--generation", required=True, help="32 lowercase hexadecimal characters")
        command.add_argument("--output", type=Path, required=True)
        if name == "rehearse":
            command.add_argument("--events", type=Path, required=True, help="At most 64 model events; no commands")
    args = parser.parse_args(argv)
    try:
        before = source_binding()
        inputs = {}
        if args.command == "template":
            result = eligibility.prepare_snapshot()
        else:
            snapshot, inputs["snapshot"] = load_bound(args.snapshot)
            if args.command == "plan":
                result = eligibility.plan(snapshot, args.preset, args.generation)
            else:
                events, inputs["events"] = load_bound(args.events)
                result = transaction.rehearse(snapshot, args.preset, args.generation, events)
        if source_binding() != before:
            raise ValueError("implementation changed while producing the report")
        # Templates retain the exact strict snapshot shape. Reports bind the
        # implementation separately from their declared, unverified inputs.
        if args.command != "template":
            result["implementation_sha256"] = before
            result["input_files"] = inputs
            result["request"] = {"preset": args.preset, "generation": args.generation}
        save_new(args.output, encode(result))
        if args.command == "template":
            print("Unverified template saved; no hardware observer or activation is provided.")
            return 0
        phase = result.get("phase", result.get("status", "unknown"))
        print("Offline report saved; state=" + phase + "; hardware writes=0; live authorization=false.")
        return 2 if (phase in ("blocked", "recovery-required", "interrupted") or
                     result.get("status") in ("blocked", "incomplete", "recovery-required", "interrupted")) else 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print("Power rehearsal rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
