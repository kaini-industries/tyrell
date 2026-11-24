#!/usr/bin/env python3
"""Inspect an inactive H3 experiment or analyse recorded SPI counters offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/observe"))
import records
import experiment
import counters

INPUT_BYTES = 256 * 1024
OUTPUT_BYTES = 2 * 1024 * 1024
SOURCES = ("scripts/panel/run.py", "scripts/panel/experiment.py", "scripts/panel/counters.py",
           "scripts/observe/records.py", "scripts/observe/collect.py",
           "research/panel-source-audit.json", "research/tyrell-zero-image-validation.json",
           "image/contracts/tyrell-zero-hardware.json")


def source_binding():
    return {name: hashlib.sha256(records.read_bytes(ROOT / name)).hexdigest() for name in SOURCES}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("template", help="Create a nondeployable experiment definition")
    create.add_argument("--output", type=Path, required=True)
    inspect = actions.add_parser("check", help="Check metadata coherence; never enables a build")
    inspect.add_argument("--definition", type=Path, required=True)
    inspect.add_argument("--output", type=Path, required=True)
    analyse = actions.add_parser("counters", help="Analyse an existing F2 bundle without device access")
    analyse.add_argument("--bundle", type=Path, required=True)
    analyse.add_argument("--spi", required=True, help="Recorded SPI alias, e.g. spi0.0; no automatic LCD selection")
    analyse.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        before = source_binding()
        if args.action == "template":
            result = experiment.template()
        elif args.action == "check":
            raw = records.read_bytes(args.definition, INPUT_BYTES)
            result = experiment.inspect(records.decode(raw))
            result["definition_sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            result = counters.analyse(args.bundle, args.spi)
        if source_binding() != before:
            raise ValueError("implementation changed while producing the report")
        if args.action != "template":
            result["implementation_sha256"] = before
        raw = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        if len(raw) > OUTPUT_BYTES:
            raise ValueError("report exceeds output limit")
        records.save_new(args.output, result)
        if args.action == "template":
            print("Inactive H3 definition saved; baseline acceptance and a qualified BSP remain pending.")
            return 0
        status = result.get("status", "unknown")
        print("Offline report saved; status=" + status + "; hardware qualification=false.")
        return 0 if args.action == "counters" and status == "complete" else 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print("Panel experiment rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
