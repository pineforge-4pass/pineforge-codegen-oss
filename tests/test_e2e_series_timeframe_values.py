"""Native entry-quantity joins against the committed TradingView tapes.

Set PINEFORGE_BTCUSDT_1H_FEED to the BTCUSDT hourly OHLCV CSV, alongside the
usual PINEFORGE_ENGINE_INCLUDE / PINEFORGE_ENGINE_LIB / Eigen configuration.
Only the native fixture requires these inputs; the join self-check runs in CI.
Each native row leaves join.tsv and its SHA256 in pytest's temporary directory.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from tests import _compile as compile_env
from tests._e2e import (
    build_strategy_library, engine_root, run_strategy, transpile_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "series_timeframe"
FEED_ENV = "PINEFORGE_BTCUSDT_1H_FEED"
UTC = dt.timezone.utc
TAPE_ZONE = dt.timezone(dt.timedelta(hours=8))
START_MS = 1704067200000  # 2024-01-01 00:00 UTC
END_MS = 1709251200000  # 2024-03-01 00:00 UTC, inclusive
HOUR_MS = 3_600_000


@dataclass(frozen=True)
class Case:
    fixture: str
    tape: str
    tape_source: str
    column: str


# Literal fixture-to-export bindings: explicit requests use the matching
# selected-value export, never a value computed from a native control run.
CASES = (
    Case("explicit-parity", "tv_bar_quantities.tsv", "if-parity", "entry_quantities"),
    Case("explicit-price", "tv_bar_quantities.tsv", "if-price", "entry_quantities"),
    Case("history-parity", "tv_state_entries.tsv", "history-parity", "selected"),
    Case("hour-fixed-fast", "tv_hour_entries.tsv", "hour-fixed-fast", "quantity"),
    Case("hour-fixed-slow", "tv_hour_entries.tsv", "hour-fixed-slow", "quantity"),
    Case("hour-if-parity", "tv_hour_entries.tsv", "hour-if-parity", "quantity"),
    Case("hour-if-price", "tv_hour_entries.tsv", "hour-if-price", "quantity"),
    Case("hour-ternary-price", "tv_hour_entries.tsv", "hour-ternary-price", "quantity"),
    Case("if-parity", "tv_bar_quantities.tsv", "if-parity", "entry_quantities"),
    Case("if-price", "tv_bar_quantities.tsv", "if-price", "entry_quantities"),
    Case("sma-parity", "tv_state_entries.tsv", "sma-parity", "selected"),
    Case("ternary-price", "tv_bar_quantities.tsv", "ternary-price", "entry_quantities"),
)


def _utc_ms(label: str, zone: dt.tzinfo) -> int:
    return int(dt.datetime.strptime(label, "%Y-%m-%d %H:%M")
               .replace(tzinfo=zone).timestamp() * 1000)


def _insert(entries: dict[int, Decimal], stamp: int, quantity: str) -> None:
    assert stamp not in entries, f"duplicate entry time: {stamp}"
    amount = Decimal(quantity)
    assert amount.is_finite() and amount > 0, f"invalid quantity at {stamp}: {amount}"
    entries[stamp] = amount


def _tape_entries(case: Case) -> dict[int, Decimal]:
    with (FIXTURES / case.tape).open(encoding="utf-8", newline="") as stream:
        all_rows = list(csv.DictReader(stream, delimiter="\t"))
    rows = [row for row in all_rows if row["source"] == case.tape_source]
    assert rows, f"no committed tape rows for {case}"
    # Use the whole column, including other exported sources. In particular,
    # the all-integer price-counter rows share the parity row's five places.
    precision = min(Decimal(cell).as_tuple().exponent
                    for row in all_rows for cell in row[case.column].split(","))
    quantum = Decimal(1).scaleb(precision)
    entries: dict[int, Decimal] = {}
    if case.column == "entry_quantities":
        assert len(rows) == 1
        quantities = rows[0][case.column].split(",")
        assert len(quantities) == int(rows[0]["entries"]) == 205
        # The compact counter export omits time labels. Its independently
        # authored calendar is Jan 1 11:00 UTC, then every seven hours through
        # Feb 29 23:00: the bar-10 signal fills on bar 11 of the hourly window.
        # This attaches labels to the committed quantities, without consulting
        # generated code, feed prices, native entries or their ordinal numbers.
        first_label = dt.datetime(2024, 1, 1, 19, tzinfo=TAPE_ZONE)
        labels = (first_label + dt.timedelta(hours=7 * offset)
                  for offset in range(205))
        for label, quantity in zip(labels, quantities, strict=True):
            _insert(entries, int(label.timestamp() * 1000), quantity)
    else:
        for row in rows:
            _insert(entries, _utc_ms(row["entry_time_utc8"], TAPE_ZONE),
                    row[case.column])
    assert entries, f"empty committed tape for {case.fixture}"
    return {stamp: amount.quantize(quantum) for stamp, amount in entries.items()}


def _engine_entries(blob: bytes) -> dict[int, Decimal]:
    entries: dict[int, Decimal] = {}
    for row in csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))):
        if row["Type"].startswith("Entry"):
            # The native runner emits UTC labels; TradingView's labels above
            # are UTC+8. Neither trade numbers nor entry_index take part.
            _insert(entries, _utc_ms(row["Date and time"], UTC), row["Qty"])
    return entries


@dataclass(frozen=True)
class Comparison:
    engine_count: int
    tape_count: int
    common: int
    equal: int
    different: tuple[int, ...]
    engine_only: tuple[int, ...]
    tape_only: tuple[int, ...]

    @property
    def matches(self) -> bool:
        return (self.common > 0 and self.equal == self.common
                and not self.different and not self.engine_only and not self.tape_only)

    def summary(self) -> str:
        return (f"engine={self.engine_count} tape={self.tape_count} "
                f"common={self.common} equal={self.equal} "
                f"different={len(self.different)} engine-only={len(self.engine_only)} "
                f"tape-only={len(self.tape_only)}")


def _quantum(tape: dict[int, Decimal]) -> Decimal:
    # Column precision, rather than each cell's spelling: a tape's "1" must
    # still detect 1.00001 when that column records five fractional places.
    assert tape, "cannot compare an empty tape"
    return Decimal(1).scaleb(min(amount.as_tuple().exponent
                                for amount in tape.values()))


def _compare(engine: dict[int, Decimal], tape: dict[int, Decimal]) -> Comparison:
    quantum = _quantum(tape)
    common = engine.keys() & tape.keys()
    different = tuple(sorted(stamp for stamp in common
                             if engine[stamp].quantize(quantum, rounding=ROUND_HALF_UP)
                             != tape[stamp]))
    return Comparison(
        len(engine), len(tape), len(common), len(common) - len(different),
        different, tuple(sorted(engine.keys() - tape.keys())),
        tuple(sorted(tape.keys() - engine.keys())),
    )


def _join_log(engine: dict[int, Decimal], tape: dict[int, Decimal],
              comparison: Comparison) -> bytes:
    stream = io.StringIO(newline="")
    stream.write(comparison.summary() + "\n")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(("entry_time_utc", "engine_quantity", "tape_quantity", "result"))
    for stamp in sorted(engine.keys() | tape.keys()):
        verdict = ("tape-only" if stamp not in engine else
                   "engine-only" if stamp not in tape else
                   "different" if stamp in comparison.different else "equal")
        label = dt.datetime.fromtimestamp(stamp / 1000, UTC).strftime("%Y-%m-%d %H:%M")
        writer.writerow((label, engine.get(stamp, ""), tape.get(stamp, ""), verdict))
    return stream.getvalue().encode("utf-8")


@pytest.fixture(scope="module")
def native_inputs(tmp_path_factory) -> tuple[Path, Path]:
    # Reuse the native prerequisites, without requiring the unrelated ETH
    # corpus feed that skip_unless_e2e_env also checks.
    compile_env.skip_if_no_engine_lib()
    engine = engine_root()
    assert engine is not None
    if not (engine / "scripts" / "run_strategy.py").is_file():
        pytest.skip(f"engine checkout {engine} has no scripts/run_strategy.py")
    raw = os.environ.get(FEED_ENV)
    if not raw:
        pytest.skip(f"BTCUSDT 1h feed missing: set {FEED_ENV} to the hourly OHLCV CSV")
    original = Path(raw).expanduser()
    if not original.is_file():
        pytest.skip(f"BTCUSDT 1h feed missing: {FEED_ENV}={original}")
    with original.open(encoding="utf-8") as stream:
        if stream.readline().startswith("version https://git-lfs.github.com/spec/"):
            pytest.skip(f"BTCUSDT 1h feed is an unsmudged LFS pointer: {original}")

    feed = tmp_path_factory.mktemp("series_timeframe_feed") / "btc60.csv"
    stamps = []
    with original.open(encoding="utf-8", newline="") as inp, \
            feed.open("w", encoding="utf-8", newline="") as out:
        reader = csv.DictReader(inp)
        assert reader.fieldnames == ["timestamp", "open", "high", "low", "close", "volume"], (
            f"invalid BTCUSDT 1h feed header: {reader.fieldnames}")
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            stamp = int(row["timestamp"])
            if START_MS <= stamp <= END_MS:
                stamps.append(stamp)
                writer.writerow(row)
    assert stamps == list(range(START_MS, END_MS + HOUR_MS, HOUR_MS)), (
        "BTCUSDT 1h feed must contain every hourly bar from "
        "2024-01-01 00:00 UTC through 2024-03-01 00:00 UTC (inclusive)")
    return engine, feed


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture)
def test_native_entry_quantities_equal_committed_tape(
        case: Case, native_inputs, tmp_path: Path) -> None:
    engine, feed = native_inputs
    tape = _tape_entries(case)
    result = transpile_json(FIXTURES / f"{case.fixture}.pine")
    assert result["ok"], result.get("diagnostics")
    build_strategy_library(result["cpp"], tmp_path)
    blob, _, stderr = run_strategy(
        engine, tmp_path, feed, {"input_tf": "60", "script_tf": "60"},
        "values",
    )
    (tmp_path / "run.stderr").write_text(stderr, encoding="utf-8")
    entries = _engine_entries(blob)
    comparison = _compare(entries, tape)
    log = _join_log(entries, tape, comparison)
    destination = tmp_path / "join.tsv"
    destination.write_bytes(log)
    checksum = hashlib.sha256(log).hexdigest()
    (tmp_path / "join.sha256").write_text(f"{checksum}  join.tsv\n", encoding="ascii")
    print(f"{case.fixture}: {comparison.summary()} join-log-sha256={checksum} "
          f"join-log={destination}")
    # README retains the first warm-up entry; it grants no exclusions.
    # Require the full timestamp set and every quantity, including that entry.
    assert comparison.matches, (
        f"{case.fixture}: {comparison.summary()}; different={comparison.different}, "
        f"engine-only={comparison.engine_only}, tape-only={comparison.tape_only}; "
        f"join-log={destination}")


def test_join_self_check_rejects_an_altered_tape_quantity() -> None:
    """An authored change to a real tape row must produce a failed join."""
    tape = _tape_entries(Case(
        "hour-if-price", "tv_hour_entries.tsv", "hour-if-price", "quantity"))
    stamp = _utc_ms("2024-01-01 18:00", TAPE_ZONE)
    assert tape[stamp] == Decimal("1.09")
    # This is a comparator control, not runtime evidence or a native expectation.
    unchanged = dict(tape)
    assert _compare(unchanged, tape).matches
    altered = dict(tape)
    altered[stamp] = Decimal("9.99")
    comparison = _compare(unchanged, altered)
    assert not comparison.matches
    assert comparison.different == (stamp,)
    assert comparison.equal == len(tape) - 1
    assert not comparison.engine_only and not comparison.tape_only
    assert not _compare({}, tape).matches
    assert not _compare({stamp + 1: Decimal("1.09")}, tape).matches
    counter_tape = _tape_entries(Case(
        "if-price", "tv_bar_quantities.tsv", "if-price", "entry_quantities"))
    assert _quantum(counter_tape) == Decimal("0.00001")
    counter_control = dict(counter_tape)
    counter_stamp = _utc_ms("2024-01-01 19:00", TAPE_ZONE)
    counter_control[counter_stamp] = counter_tape[counter_stamp] + Decimal("0.00001")
    assert _compare(counter_control, counter_tape).different == (counter_stamp,)
