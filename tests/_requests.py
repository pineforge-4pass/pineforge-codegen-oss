"""Synthetic request data for the end-to-end tests of requests of other
symbols and recorded requests (lane XSYM-E).

A probe's pinned request data reaches the engine's runner as
``PINEFORGE_REQUESTS_ROOT/<slug>/requests.json`` (``pineforge-probe-requests/v1``)
plus ``files/<sha256>``: the workflow's environment contract, which the
runner reads for the strategy directory whose basename is ``<slug>``
(``scripts/run_strategy.py`` ``load_probe_requests``). These helpers write
such a root from bars and tapes the tests generate -- no TradingView data --
and run a built strategy under it, keeping its trace.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tests._e2e import build_strategy_library, transpile_json


SCHEMA = "pineforge-probe-requests/v1"
FEED_COLUMNS = ["timestamp", "time_close", "open", "high", "low", "close", "volume"]
FACTS = {"type": "index", "timezone": "Etc/UTC", "session": "regular", "currency": "USD",
         "mintick": 0.25}


@dataclass
class Feed:
    """Another symbol's bars at one timeframe: ``(open_ms, close_ms, close)``,
    with open/high/low derived from the close; ``columns`` holds named extra
    columns, one value per bar."""
    symbol: str
    timeframe: str
    bars: list[tuple[int, int, float]]
    columns: dict[str, list[float]] = field(default_factory=dict)

    def csv(self) -> bytes:
        names = list(self.columns)
        lines = [",".join(FEED_COLUMNS + names)]
        for i, (open_ms, close_ms, close) in enumerate(self.bars):
            extra = [repr(self.columns[n][i]) for n in names]
            lines.append(",".join([str(open_ms), str(close_ms), repr(close - 0.5),
                                   repr(close + 1.0), repr(close - 1.0), repr(close), "10",
                                   *extra]))
        return ("\n".join(lines) + "\n").encode()


def hourly(symbol: str, start_ms: int, count: int, minutes: int = 60,
           skip: frozenset[int] = frozenset(), timeframe: str | None = None) -> Feed:
    """``count`` bars of ``minutes`` from ``start_ms`` (those in ``skip`` left
    out), each closing when the next opens, with a close no two bars share."""
    step = minutes * 60_000
    bars = [(start_ms + i * step, start_ms + (i + 1) * step, 100.0 + ((i * 37) % 23) * 0.25 + i * 0.01)
            for i in range(count) if i not in skip]
    return Feed(symbol, timeframe or str(minutes), bars)


def write_root(base: Path, slug: str, chart_symbol: str, chart_tf: str,
               feeds: list[Feed] = (), recorded: dict[str, list[tuple[int, float]]] | None = None,
               symbols: dict[str, dict | None] | None = None) -> Path:
    """``base/requests/<slug>/`` for these feeds and recorded tapes; every
    feed's and tape's symbol gets ``FACTS`` unless ``symbols`` names it
    (``None``: the symbol is invalid)."""
    root = base / "requests"
    probe = root / slug
    (probe / "files").mkdir(parents=True, exist_ok=True)
    provenance = hashlib.sha256(b"synthetic").hexdigest()

    def put(data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        (probe / "files" / sha).write_bytes(data)
        return sha

    wanted = {f.symbol for f in feeds} | {k.split("|")[1] for k in (recorded or {})}
    entries = {}
    for sym in sorted(wanted | set(symbols or {})):
        facts = (symbols or {}).get(sym, FACTS)
        if facts is None:
            entries[sym] = {"canonical": None, "valid": False, "facts": None,
                            "factsSha256": provenance}
        else:
            entries[sym] = {"canonical": sym, "valid": True, "facts": facts,
                            "factsSha256": provenance}
    feed_docs = []
    for feed in feeds:
        data = feed.csv()
        feed_docs.append({"symbol": feed.symbol, "timeframe": feed.timeframe, "sha256": put(data),
                          "bytes": len(data), "columns": FEED_COLUMNS + list(feed.columns),
                          "provenanceSha256": provenance})
    rec_docs = []
    for key, rows in (recorded or {}).items():
        data = ("chart_open_ms,value\n" + "".join(f"{t},{v!r}\n" for t, v in rows)).encode()
        rec_docs.append({"key": key, "sha256": put(data), "columns": ["chart_open_ms", "value"],
                         "provenanceSha256": provenance})
    doc = {"schemaVersion": SCHEMA,
           "probe": {"probeId": f"local:tests/synthetic/{slug}", "slug": slug,
                     "symbol": chart_symbol, "timeframe": chart_tf,
                     "strategySha256": provenance},
           "window": {"fromMs": 0, "toMs": 4_102_444_800_000},
           "symbols": entries, "feeds": feed_docs, "recorded": rec_docs, "libraries": []}
    (probe / "requests.json").write_text(json.dumps(doc, indent=1))
    return root


@dataclass
class Run:
    ok: bool
    error: str
    trace: dict[int, dict[str, float]]
    trades: bytes


def build(source: str, workdir: Path) -> dict:
    """Transpile (the app's ``transpile_json``) and build ``source`` in
    ``workdir``; the transpile result."""
    workdir.mkdir(parents=True, exist_ok=True)
    pine = workdir / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    result = transpile_json(pine)
    assert result.get("ok"), json.dumps(result.get("diagnostics"), indent=1)
    build_strategy_library(result["cpp"], workdir)
    return result


def run(engine_root: Path, workdir: Path, feed: Path, root: Path | None,
        tickerid: str = "BINANCE:ETHUSDT", inputs: dict | None = None, tag: str = "run") -> Run:
    """Run the strategy built in ``workdir`` on ``feed``, the chart
    ``tickerid``, under the requests root ``root`` (None: unset) and the
    input overrides ``inputs`` (title -> text); its ``@pf-trace`` values by
    chart bar."""
    params = {"runtime_overrides": {"tickerid": tickerid, "ticker": tickerid.split(":")[-1],
                                    "type": "crypto", "timezone": "UTC", "session": "24x7"},
              # Input overrides by title (strategy_set_input).
              **(inputs or {})}
    (workdir / f"inputs_{tag}.json").write_text(json.dumps(params))
    env = dict(os.environ)
    env.pop("PINEFORGE_REQUESTS_ROOT", None)
    if root is not None:
        env["PINEFORGE_REQUESTS_ROOT"] = str(root)
    out, trace_path = workdir / f"trades_{tag}.csv", workdir / f"trace_{tag}.json"
    proc = subprocess.run(
        [sys.executable, str(engine_root / "scripts" / "run_strategy.py"), str(workdir),
         "--ohlcv", str(feed), "--inputs-json", str(workdir / f"inputs_{tag}.json"),
         "--no-trim-output", "-o", str(out), "--trace-json", str(trace_path)],
        capture_output=True, text=True, timeout=600, env=env)
    if proc.returncode != 0:
        return Run(False, proc.stdout + proc.stderr, {}, b"")
    per_bar: dict[int, dict[str, float]] = {}
    for entry in json.loads(trace_path.read_text())["trace"]:
        value = entry["value"]
        per_bar.setdefault(int(entry["timestamp"]), {})[entry["name"]] = (
            float(value) if value is not None else math.nan)
    return Run(True, "", per_bar, out.read_bytes())


def same(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= 1e-9 * max(1.0, abs(b))
