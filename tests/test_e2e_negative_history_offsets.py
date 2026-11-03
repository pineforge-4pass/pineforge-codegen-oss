"""Runtime requirements for evaluated, computed negative history offsets.

The reporter is retained byte for byte. Native runs use a small synthetic
chart, not a reconstructed reporter/TradingView chart. These are deciding
regressions and controls, not a bundled TV receipt or sanitizer observation.
Translation and compilation must succeed before any runtime assertion.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tests import _compile as compile_env
from tests._e2e import build_strategy_library, engine_root, transpile_json


FIXTURES = Path(__file__).parent / "fixtures" / "negative_history_offsets"
REPORTER_SHA256 = "75dbbbe9d484c8f98808caefc2f976f9e8cb42d31e784946f86549dd5928b544"
# Unique prices distinguish a current read, a past read and an ignored input.
CLOSES = [100.0 + i * (i + 1) for i in range(16)]


@pytest.fixture(scope="module")
def native_cases(tmp_path_factory):
    compile_env.skip_if_no_engine_lib()
    engine = engine_root()
    assert engine is not None
    assert (engine / "scripts" / "run_strategy.py").is_file()
    base = tmp_path_factory.mktemp("negative_history_offsets")
    feed = base / "chart.csv"
    with feed.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        for i, close in enumerate(CLOSES):
            writer.writerow((1735689600000 + i * 900000,
                             close - 1, close + 1, close - 2, close, 100))
    libraries = {}
    receipts = {}

    def run(name: str, overrides: dict | None = None) -> dict:
        if name not in libraries:
            pine = FIXTURES / f"{name}.pine"
            if name == "reporter":
                assert hashlib.sha256(pine.read_bytes()).hexdigest() == REPORTER_SHA256
            work = base / name
            work.mkdir()
            result = transpile_json(pine)
            (work / "transpile.json").write_text(json.dumps(result, indent=2) + "\n")
            assert result.get("ok"), (
                f"runtime witness must compile: {result.get('diagnostics')}"
            )
            build_strategy_library(result["cpp"], work)
            libraries[name] = work
        key = (name, json.dumps(overrides, sort_keys=True))
        if key in receipts:
            return receipts[key]
        work = libraries[name]
        cell = work / hashlib.sha256(key[1].encode()).hexdigest()[:16]
        cell.mkdir()
        inputs = cell / "inputs.json"
        inputs.write_text(json.dumps(overrides) + "\n")
        output = cell / "native.json"
        command = [
            sys.executable, "-I", str(FIXTURES / "native_probe.py"),
            "--engine", str(engine), "--library", str(work / "strategy.so"),
            "--feed", str(feed), "--inputs", str(inputs),
            "--result", str(output),
        ]
        proc = subprocess.run(command, capture_output=True)
        (cell / "stdout.log").write_bytes(proc.stdout)
        (cell / "stderr.log").write_bytes(proc.stderr)
        (cell / "process.json").write_text(json.dumps({
            "command": command, "returncode": proc.returncode,
            "source_sha256": hashlib.sha256(
                (FIXTURES / f"{name}.pine").read_bytes()).hexdigest(),
            "cpp_sha256": hashlib.sha256((work / "generated.cpp").read_bytes()).hexdigest(),
        }, indent=2) + "\n")
        assert proc.returncode == 0, (
            f"native capture failed (not a semantic stop): {cell}\n"
            f"{proc.stderr.decode('utf-8', 'replace')}"
        )
        assert output.is_file(), f"native capture is incomplete: {cell}"
        receipt = json.loads(output.read_text())
        assert receipt["phase"] == "run_returned", receipt
        assert receipt["bars"] == len(CLOSES), receipt
        assert receipt["inputs"] == overrides, receipt
        assert receipt["feed_sha256"] == hashlib.sha256(feed.read_bytes()).hexdigest()
        receipts[key] = receipt
        return receipt

    return run


def _completed(receipt: dict) -> None:
    native = receipt["native"]
    assert native["status"] == 0 and not native["error"] and not native["code"], receipt
    assert receipt["report"]["script_bars_processed"] == len(CLOSES), receipt


def _located_stop(receipt: dict, source: str, index: str) -> None:
    # Use the written index's first token, before any lowering or remapping.
    lines = (FIXTURES / f"{source}.pine").read_text().splitlines()
    matches = [(i + 1, row.index(index) + 1)
               for i, row in enumerate(lines) if index in row]
    assert len(matches) == 1, matches
    line, col = matches[0]
    native = receipt["native"]
    assert native["error"] and native["code"], (
        "evaluated negative history offset continued instead of stopping", receipt
    )
    assert re.search(r"\bhistor(?:y|ical)\b", native["error"], re.I), receipt
    assert re.search(r"\b(?:offset|index)\b", native["error"], re.I), receipt
    assert re.search(
        rf"\bline\s+{line}\b[^\n]*\bcol(?:umn)?\s+{col}\b|:{line}:{col}\b",
        native["error"], re.I,
    ), (
        "runtime stop lost the authored history location", receipt
    )


def _trace(receipt: dict, name: str) -> list[float]:
    records = [row for row in receipt["report"]["trace"] if row["name"] == name]
    assert [row["bar_index"] for row in records] == list(range(len(CLOSES))), receipt
    return [row["value"] for row in records]


def _same_values(actual: list[float], expected: list[float]) -> None:
    assert len(actual) == len(expected)
    for bar, (value, want) in enumerate(zip(actual, expected)):
        assert (math.isnan(value) and math.isnan(want)) or value == want, (
            bar, value, want
        )


@pytest.mark.parametrize("overrides", [None, {"a": 29, "b": 31}])
def test_reporter_evaluated_negative_offset_stops(native_cases, overrides):
    _located_stop(native_cases("reporter", overrides), "reporter", "a - b]")


@pytest.mark.parametrize("a, quantity", [(31, 1), (32, 2), (33, 3), (46, 7)])
def test_reporter_same_library_obeys_input_overrides(native_cases, a, quantity):
    receipt = native_cases("reporter", {"a": a, "b": 31})
    _completed(receipt)
    trades = receipt["report"]["trades"]
    assert len(trades) == 1, receipt
    assert trades[0]["qty"] == quantity, receipt


@pytest.mark.parametrize("integer_mode", [False, True])
@pytest.mark.parametrize("units", [-1, -2, -2147483647])
def test_negative_offset_before_narrowing_stops(native_cases, integer_mode, units):
    _located_stop(native_cases("numeric", {
        "Units": units, "Denominator": 1, "Integer mode": integer_mode,
    }), "numeric", "whole]" if integer_mode else "fractional]")


@pytest.mark.parametrize("integer_mode", [False, True])
@pytest.mark.parametrize("units, denominator, missing, lag", [
    (0, 1, False, 0),
    (1, 1, False, 1),
    (15, 1, False, 15),  # valid offset with ordinary before-start warmup
    (0, 1, True, None),  # na must not be mistaken for a negative offset
])
def test_finite_nonnegative_and_na_controls(
    native_cases, integer_mode, units, denominator, missing, lag,
):
    receipt = native_cases("numeric", {
        "Units": units, "Denominator": denominator, "Missing": missing,
        "Integer mode": integer_mode,
    })
    _completed(receipt)
    expected = [
        CLOSES[bar - lag] if lag is not None and bar >= lag else math.nan
        for bar in range(len(CLOSES))
    ]
    _same_values(_trace(receipt, "sample"), expected)


@pytest.mark.parametrize("units, denominator, lag", [(45, 8, 5), (29, 10, 2)])
def test_fractional_conversion_keeps_truncation(native_cases, units, denominator, lag):
    receipt = native_cases("numeric", {
        "Units": units, "Denominator": denominator, "Integer mode": False,
    })
    _completed(receipt)
    expected = [CLOSES[bar - lag] if bar >= lag else math.nan
                for bar in range(len(CLOSES))]
    _same_values(_trace(receipt, "sample"), expected)


@pytest.mark.parametrize("units", [0, 1])
def test_offset_helper_is_evaluated_once(native_cases, units):
    receipt = native_cases("evaluation", {"Units": units, "Enabled": True})
    _completed(receipt)
    _same_values(_trace(receipt, "calls"), list(range(1, len(CLOSES) + 1)))
    expected = [CLOSES[bar - units] if bar >= units else math.nan
                for bar in range(len(CLOSES))]
    _same_values(_trace(receipt, "sample"), expected)


def test_unreached_negative_offset_does_not_stop_or_evaluate(native_cases):
    receipt = native_cases("evaluation", {"Units": -1, "Enabled": False})
    _completed(receipt)
    _same_values(_trace(receipt, "calls"), [0] * len(CLOSES))
    _same_values(_trace(receipt, "sample"), CLOSES)


def test_reached_helper_negative_offset_stops(native_cases):
    _located_stop(native_cases("evaluation", {"Units": -1, "Enabled": True}),
                  "evaluation", "next_offset(units)")


def test_requested_evaluated_negative_offset_stops(native_cases):
    _located_stop(native_cases("requested"), "requested", "a - b]")


@pytest.mark.parametrize("a, reference", [(31, "present"), (32, "previous")])
def test_requested_input_offsets_keep_the_requested_clock(native_cases, a, reference):
    receipt = native_cases("requested", {"a": a, "b": 31})
    _completed(receipt)
    expected = _trace(receipt, reference)
    assert any(math.isfinite(value) for value in expected), receipt
    _same_values(_trace(receipt, "sample"), expected)
