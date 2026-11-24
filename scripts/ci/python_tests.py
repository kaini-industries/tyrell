#!/usr/bin/env python3
"""Run one existing unittest directory, retaining skips as incomplete evidence."""
import argparse
import json
from pathlib import Path
import sys
import unittest


def run(directory, pattern, report):
    suite = unittest.TestLoader().discover(str(directory), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    data = {"tests": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses)}
    data["status"] = "passed" if (result.wasSuccessful() and result.testsRun
                                           and not result.skipped and not result.expectedFailures) else "failed"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2) + "\n")
    return data["status"] == "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(0 if run(args.directory, args.pattern, args.report) else 1)
