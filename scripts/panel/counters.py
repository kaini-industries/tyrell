"""Analyse recorded SPI counters only; no device is opened or identified as LCD."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path, PurePosixPath
import re

_SPEC = importlib.util.spec_from_file_location(
    "tyrell_panel_observation_records", Path(__file__).resolve().parents[1] / "observe/records.py")
records = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(records)

SPI = re.compile(r"spi(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\Z")
UNSIGNED = re.compile(r"[0-9]{1,20}\n?\Z")
COUNTERS = ("bytes", "transfers", "errors", "timedout")
MAX_COUNTER = 2**64 - 1
FRAME_BYTES = 320 * 170 * 2
TIME_TOLERANCE_SECONDS = 0.000001


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _source(directory):
    """Bind analysis to stable bytes, including the inspected result itself."""
    limits = {"plan.json": records.MAX_JSON, "result.json": records.MAX_JSON,
              "samples.jsonl": records.MAX_SAMPLES}
    result_raw = records.read_bytes(directory / "result.json")
    before = {name: records.sha(result_raw if name == "result.json" else
                               records.read_bytes(directory / name, maximum))
              for name, maximum in limits.items()}
    plan, result, samples = records.inspect_bundle(directory)
    after = {name: records.sha(records.read_bytes(directory / name, maximum))
             for name, maximum in limits.items()}
    if (before != after or result != records.decode(result_raw) or
            result["plan_sha256"] != before["plan.json"] or
            result["samples_sha256"] != before["samples.jsonl"]):
        raise ValueError("observation bundle changed during analysis")
    return plan, result, samples, {"plan_sha256": before["plan.json"],
                                  "result_sha256": before["result.json"],
                                  "samples_sha256": before["samples.jsonl"]}


def _point(sample, spi, capture_trusted, observed_seconds):
    problems, capture_problems, values, device = [], [], {}, None
    start, end = sample.get("capture_started_seconds"), sample.get("capture_finished_seconds")
    if not capture_trusted:
        capture_problems.append("collector source does not establish parent capture semantics")
    if (not _finite(start) or not _finite(end) or end < start or
            start != sample["elapsed_seconds"]):
        capture_problems.append("missing or invalid parent capture envelope")
    elif (end > observed_seconds + TIME_TOLERANCE_SECONDS or
          not _finite(sample.get("read_seconds")) or
          sample["read_seconds"] > end - start + TIME_TOLERANCE_SECONDS):
        capture_problems.append("parent capture envelope contradicts collection timing")
    prefix = "/sys/bus/spi/devices/" + spi + "/statistics/"
    for name in COUNTERS:
        reading = sample["readings"].get(prefix + name, {})
        raw, resolved = reading.get("raw"), reading.get("resolved_path")
        if reading.get("status") != "ok":
            problems.append(name + ": " + str(reading.get("status", "missing")))
            continue
        if not isinstance(raw, str) or not UNSIGNED.fullmatch(raw) or int(raw) > MAX_COUNTER:
            problems.append(name + ": malformed unsigned counter")
            continue
        if (not isinstance(resolved, str) or len(resolved) > 1024 or
                not resolved.startswith("/sys/devices/") or "\\" in resolved or
                any(part in ("", ".", "..") for part in resolved.split("/")[1:]) or
                any(ord(char) < 32 or ord(char) == 127 for char in resolved) or
                PurePosixPath(resolved).as_posix() != resolved or
                not resolved.endswith("/" + spi + "/statistics/" + name)):
            problems.append(name + ": unverified canonical counter path")
            continue
        selected = resolved[:-len("/statistics/" + name)]
        if device is not None and device != selected:
            problems.append(name + ": counters resolve to different devices")
        device = selected if device is None else device
        values[name] = int(raw)
    return {"sample": sample["index"], "capture_start_seconds": start,
            "capture_end_seconds": end, "device": device, "counters": values,
            "problems": problems, "capture_problems": capture_problems}


def _rates(delta, nominal, minimum, maximum):
    rates = {}
    for name, count in {**delta, "bus_byte_frame_equivalents": delta["bytes"] / FRAME_BYTES}.items():
        values = {"nominal": count / nominal, "lower": count / maximum, "upper": count / minimum}
        if not all(_finite(value) for value in values.values()):
            raise ValueError("counter rate exceeds finite numeric range")
        rates[name + "_per_second"] = values
    return rates


def _interval(previous, current):
    problems = list(previous["problems"]) + list(current["problems"])
    value = {"start_sample": previous["sample"], "end_sample": current["sample"],
             "status": "incomplete", "problems": problems, "delta": None,
             "time_seconds": None, "rate_bounds": None}
    if previous["device"] != current["device"]:
        problems.append("canonical device identity changed")
    if problems:
        return value
    delta = {name: current["counters"][name] - previous["counters"][name] for name in COUNTERS}
    if any(count < 0 for count in delta.values()):
        value["status"] = "counter-reset"
        problems.append("counter decreased; reset, wrap or re-enumeration is not inferred")
        return value
    value["delta"] = delta
    problems.extend(previous["capture_problems"] + current["capture_problems"])
    if problems:
        return value
    nominal = current["capture_start_seconds"] - previous["capture_start_seconds"]
    minimum = current["capture_start_seconds"] - previous["capture_end_seconds"]
    maximum = current["capture_end_seconds"] - previous["capture_start_seconds"]
    if not all(_finite(t) and t > 0 for t in (nominal, minimum, maximum)):
        problems.append("counter capture windows overlap or have no positive separation")
        return value
    try:
        rates = _rates(delta, nominal, minimum, maximum)
    except (ValueError, OverflowError, ZeroDivisionError):
        problems.append("counter interval has nonfinite rate bounds")
        return value
    value.update(status="errors-observed" if delta["errors"] or delta["timedout"] else "complete",
                 delta=delta, time_seconds={"nominal": nominal, "minimum": minimum, "maximum": maximum},
                 rate_bounds=rates)
    return value


def analyse(bundle: Path, spi: str) -> dict:
    """Return descriptive deltas and software capture-envelope rate bounds."""
    if not isinstance(spi, str) or not SPI.fullmatch(spi):
        raise ValueError("select a bounded SPI alias such as spi0.0, not a path")
    tool_paths = {name: Path(records.__file__).with_name(name) for name in ("collect.py", "records.py")}
    expected = {name: records.sha(records.read_bytes(path)) for name, path in tool_paths.items()}
    _, result, samples, source = _source(Path(bundle))
    if expected != {name: records.sha(records.read_bytes(path)) for name, path in tool_paths.items()}:
        raise ValueError("capture implementation changed during analysis")
    capture_trusted = result["tool_sha256"] == expected
    points = [_point(sample, spi, capture_trusted, result["timing"]["observed_seconds"]) for sample in samples]
    intervals = [_interval(a, b) for a, b in zip(points, points[1:])]
    reset = any(row["status"] == "counter-reset" for row in intervals)
    incomplete = (not capture_trusted or result["status"] != "completed" or not intervals or
                  any(row["status"] == "incomplete" for row in intervals))
    errors = any(row["delta"] is not None and (row["delta"]["errors"] or row["delta"]["timedout"])
                 for row in intervals)
    status = "incomplete" if incomplete else "counter-reset" if reset else "errors-observed" if errors else "complete"
    totals, summary = None, None
    if status in ("complete", "errors-observed"):
        totals = {name: sum(row["delta"][name] for row in intervals) for name in COUNTERS}
        first, last = points[0], points[-1]
        summary = _rates(totals, last["capture_start_seconds"] - first["capture_start_seconds"],
                         last["capture_start_seconds"] - first["capture_end_seconds"],
                         last["capture_end_seconds"] - first["capture_start_seconds"])
    return {"schema": 1, "kind": "tyrell-panel-counter-analysis", "status": status,
            "selected_spi": spi, "source": source, "source_collection_status": result["status"],
            "collector_tool_sha256": result["tool_sha256"], "sample_count": len(points),
            "usable_interval_count": sum(row["rate_bounds"] is not None for row in intervals),
            "counter_delta_interval_count": sum(row["delta"] is not None for row in intervals),
            "resolved_device_path": points[0]["device"] if points else None,
            "totals": totals, "rate_bounds": summary, "intervals": intervals,
            "bus_byte_frame_equivalents": None if totals is None else totals["bytes"] / FRAME_BYTES,
            "full_frame_byte_divisor": FRAME_BYTES, "counter_reset_observed": reset,
            "new_errors_or_timeouts_observed": errors,
            "physical_refresh_hz": None, "device_identity_proven": False,
            "capture_window_bounds_verified": capture_trusted and bool(points) and
                                              all(not point["capture_problems"] for point in points),
            "timing_arithmetic_tolerance_seconds": TIME_TOLERANCE_SECONDS,
            "claims_release_approval": False,
            "hardware_qualified": False,
            "limitations": [
                "A selected SPI alias and canonical sysfs path do not identify or attest the LCD.",
                "Bus bytes include commands/protocol overhead and are not validated pixel payload.",
                "Frame-byte equivalents are not optical frames, panel refresh, vsync or TE timing.",
                "Bounds use the recorded collector-parent capture envelope, not hardware transfer timestamps.",
                "Legacy child read_seconds cannot establish bounds; missing envelopes or different tool hashes are incomplete.",
                "Current source hashes establish interpretation and integrity, not authenticated observations or real-time deadlines.",
                "Envelope start equals the sample timestamp; timing containment allows one microsecond of arithmetic tolerance.",
                "Interrupted or unusable intervals do not contribute an aggregate rate; resets are not unwrapped.",
                "Zero new error deltas do not erase historical error counts or prove fault-free hardware.",
                "Source reference/integrity validation is not candidate BSP, benefit, power or device acceptance.",
            ]}
