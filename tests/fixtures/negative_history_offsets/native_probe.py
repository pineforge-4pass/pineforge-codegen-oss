"""Read an actual generated strategy's failure fields in a separate process.

No expected result is passed to this probe. A zero process exit means the
capture finished; the captured native error fields decide whether the run
stopped. Python/setup exceptions and native signals remain process failures.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    # Borrow the paired engine's own ABI definitions, loader and report
    # decoder, rather than duplicating the versioned ReportC layout.
    sys.path.insert(0, str(args.engine / "scripts"))
    import run_strategy as runner

    strategy = runner.Strategy(args.library)
    lib = strategy.lib
    for name in (
        "strategy_set_input", "strategy_get_last_error",
        "strategy_get_last_error_code", "strategy_get_last_error_args",
        "strategy_last_run_status", "strategy_set_trace_enabled",
    ):
        if not hasattr(lib, name):
            raise RuntimeError(f"paired library lacks {name}")
    with args.feed.open() as stream:
        rows = list(csv.DictReader(stream))
    bars = (runner.BarC * len(rows))(*(
        runner.BarC(
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), float(row["volume"]), int(row["timestamp"]),
        )
        for row in rows
    ))
    overrides = json.loads(args.inputs.read_text())
    state = lib.strategy_create(None)
    if not state:
        raise RuntimeError("strategy_create returned a null handle")
    report = runner.ReportC()

    def failure_fields() -> dict:
        def read(name: str) -> str:
            raw = getattr(lib, name)(state)
            return raw.decode("utf-8", "replace") if raw else ""

        return {
            "error": read("strategy_get_last_error"),
            "code": read("strategy_get_last_error_code"),
            "args": read("strategy_get_last_error_args"),
            "status": lib.strategy_last_run_status(state),
        }

    try:
        for key, value in (overrides or {}).items():
            spelling = str(value).lower() if isinstance(value, bool) else str(value)
            lib.strategy_set_input(state, key.encode(), spelling.encode())
        setup = failure_fields()
        if setup["error"] or setup["code"] or setup["status"] != 0:
            raise RuntimeError(f"strategy setup failed: {setup!r}")
        lib.strategy_set_trace_enabled(state, 1)
        lib.run_backtest_full(
            state, bars, len(rows), b"15", b"15", 0, 4, 3,
            ctypes.byref(report),
        )
        receipt = {
            "phase": "run_returned",
            "inputs": overrides,
            "runner": str(Path(runner.__file__).resolve()),
            "library_sha256": hashlib.sha256(args.library.read_bytes()).hexdigest(),
            "feed_sha256": hashlib.sha256(args.feed.read_bytes()).hexdigest(),
            "feed_kind": "synthetic 15m semantic control",
            "bars": len(rows),
            "setup": setup,
            "native": failure_fields(),
            "report": runner._report_to_dict(report),
        }
        args.result.write_text(json.dumps(receipt, indent=2) + "\n")
    finally:
        lib.report_free(ctypes.byref(report))
        lib.strategy_free(state)


if __name__ == "__main__":
    main()
