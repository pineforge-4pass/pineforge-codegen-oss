"""Exact native entry-quantity joins and fixed-arm state comparisons.

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
from dataclasses import dataclass, replace
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

# Known pre-existing engine difference (a missing second fill), measured on
# the v1.9.1 tag, main and every head. Cause, decoded on a literal na-quantity
# source (the placed set of 178 entries and every quantity equal): the quantity
# of that order is na (the daily request is na on the first day), so it is
# sized at the full equity. TradingView cuts that quantity to the symbol's
# quantity step and then checks that the order is affordable at the fill; the
# engine applies no quantity step in a plain run, so the uncut quantity costs
# slightly more than the equity at the fill and the order is declined (the same
# happens for a literal na quantity with no request at all). This pin MUST FLIP
# to plain equality, by emptying the table, when the engine applies the step.
# Every other cell must remain exactly equal.
KNOWN_TAPE_ONLY = {
    "explicit-parity": (1704132000000,),
    "if-parity": (1704132000000,),
}
STATE_SOURCES = ("history-parity", "sma-parity")

# Values are measured on the native run (engine a3e7822e, the BTCUSDT hourly
# feed of 1,441 bars); this pin is exact in both directions. Keys: the six
# reports below. Each value has equal (int), different (int), and
# different_sha256 (hex str: sha256 of the sorted different entry times as
# decimal milliseconds, one per line). In these logs the engine's raw quantity
# is above the tape's on every different entry (never below) and by at most
# one unit of the tape's last digit. This MUST become plain equality (empty
# this table and turn the reports into exact assertions, 477 equal and 0
# different) when the engine's quantity computation is fixed at its root (the
# order quantity cut to the symbol's quantity step). The pin also moves with
# ANY change of the engine's order-quantity rounding: a change of these
# numbers means verify the fix, then re-measure and update or empty the pin.
LAST_DIGIT_BASELINE: dict[str, dict] = {
    "history-parity": {
        "equal": 226, "different": 251,
        "different_sha256": "9b48153973aa82d14d8e1d2a89acaad38215086de4624f10101241383497f261",
    },
    "history-fixed-60": {
        "equal": 240, "different": 237,
        "different_sha256": "7b33fe3eba1402876117b22d2ae9886b2497d624f56c41557e78ce0555f28376",
    },
    "history-fixed-240": {
        "equal": 227, "different": 250,
        "different_sha256": "a32bfbdae576d58918a3074abcb2983e63b5368a1759cfaa1190b2b03eab23cd",
    },
    "sma-parity": {
        "equal": 254, "different": 223,
        "different_sha256": "d81b0b67ea825ae98b1c12a1e29bbb5a731846b314329cbb0da09f96b0241172",
    },
    "sma-fixed-60": {
        "equal": 255, "different": 222,
        "different_sha256": "fd80e1e9ee8462bcb421d8721d0686741d364285c638a219e9855583ad8caa97",
    },
    "sma-fixed-240": {
        "equal": 232, "different": 245,
        "different_sha256": "0ade63cc2a5a99971f62bf8879372f7e891fcefd9b188af34c19c5006591a253",
    },
}


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


def _tape_rows(case: Case) -> list[dict[str, str]]:
    with (FIXTURES / case.tape).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _tape_entries(case: Case) -> dict[int, Decimal]:
    all_rows = _tape_rows(case)
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


def _state_rows(source: str) -> dict[int, dict[str, str]]:
    rows = {}
    for row in _tape_rows(Case(source, "tv_state_entries.tsv", source, "selected")):
        if row["source"] != source:
            continue
        stamp = _utc_ms(row["entry_time_utc8"], TAPE_ZONE)
        assert stamp not in rows, f"{source}: duplicate state entry {stamp}"
        rows[stamp] = row
    assert len(rows) == 477, f"{source}: expected 477 state entries, got {len(rows)}"
    return rows


def _state_arms(rows: dict[int, dict[str, str]]) -> dict[int, str]:
    arms = {}
    for stamp, row in rows.items():
        assert (stamp - START_MS) % HOUR_MS == 0, f"non-hourly entry: {stamp}"
        signal_bar_index = (stamp - START_MS) // HOUR_MS - 1
        assert int(row["signal_bar_index"]) == signal_bar_index, (
            f"{stamp}: tape signal bar {row['signal_bar_index']} != {signal_bar_index}")
        assert signal_bar_index > 6 and signal_bar_index % 3 == 0, (
            f"{stamp}: entry outside the authored signal schedule")
        arm = "240" if signal_bar_index % 2 else "60"
        assert row["arm"] == arm, f"{stamp}: tape arm {row['arm']} != authored arm {arm}"
        arms[stamp] = arm
    return arms


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
    known_tape_only: tuple[int, ...] = ()

    @property
    def matches(self) -> bool:
        return (self.common > 0 and self.equal == self.common
                and not self.different and not self.engine_only
                and self.tape_only == self.known_tape_only)

    def summary(self) -> str:
        return (f"engine={self.engine_count} tape={self.tape_count} "
                f"common={self.common} exact={self.equal} "
                f"different={len(self.different)} engine-only={len(self.engine_only)} "
                f"tape-only={len(self.tape_only)} "
                f"known-tape-only={len(set(self.tape_only) & set(self.known_tape_only))}")


def _quantum(tape: dict[int, Decimal]) -> Decimal:
    # Column precision, rather than each cell's spelling: a tape's "1" must
    # still detect 1.00001 when that column records five fractional places.
    assert tape, "cannot compare an empty tape"
    return Decimal(1).scaleb(min(amount.as_tuple().exponent
                                for amount in tape.values()))


def _compare(engine: dict[int, Decimal], tape: dict[int, Decimal],
             source: str = "") -> Comparison:
    quantum = _quantum(tape)
    common = engine.keys() & tape.keys()
    different = tuple(sorted(stamp for stamp in common
                             if engine[stamp].quantize(quantum, rounding=ROUND_HALF_UP)
                             != tape[stamp]))
    return Comparison(
        len(engine), len(tape), len(common), len(common) - len(different),
        different, tuple(sorted(engine.keys() - tape.keys())),
        tuple(sorted(tape.keys() - engine.keys())),
        KNOWN_TAPE_ONLY.get(source, ()),
    )


def _assert_matches(comparison: Comparison, label: str) -> None:
    no_longer_missing = tuple(stamp for stamp in comparison.known_tape_only
                             if stamp not in comparison.tape_only)
    assert not no_longer_missing, (
        f"{label}: known tape-only entries are no longer missing: {no_longer_missing}; "
        "verify the fix and empty KNOWN_TAPE_ONLY")
    assert comparison.tape_only == comparison.known_tape_only, (
        f"{label}: tape-only={comparison.tape_only}; "
        f"expected-known-tape-only={comparison.known_tape_only}")
    assert comparison.engine_only == (), f"{label}: engine-only={comparison.engine_only}"
    assert comparison.matches, (
        f"{label}: {comparison.summary()}; different={comparison.different}")


def _join_log(engine: dict[int, Decimal], tape: dict[int, Decimal],
              comparison: Comparison) -> bytes:
    stream = io.StringIO(newline="")
    stream.write(comparison.summary() + "\n")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(("entry_time_utc", "engine_quantity", "tape_quantity", "result"))
    for stamp in sorted(engine.keys() | tape.keys()):
        verdict = ("known-tape-only" if stamp in comparison.tape_only
                   and stamp in comparison.known_tape_only else
                   "tape-only" if stamp not in engine else
                   "engine-only" if stamp not in tape else
                   "different" if stamp in comparison.different else "equal")
        label = dt.datetime.fromtimestamp(stamp / 1000, UTC).strftime("%Y-%m-%d %H:%M")
        writer.writerow((label, engine.get(stamp, ""), tape.get(stamp, ""), verdict))
    return stream.getvalue().encode("utf-8")


def _write_log(directory: Path, label: str, log: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "join.tsv"
    destination.write_bytes(log)
    checksum = hashlib.sha256(log).hexdigest()
    (directory / "join.sha256").write_text(f"{checksum}  join.tsv\n", encoding="ascii")
    print(f"{label}: {log.decode('utf-8').splitlines()[0]} "
          f"join-log-sha256={checksum} join-log={destination}")
    return destination


def _assert_state_structure(comparison: Comparison, label: str) -> None:
    assert comparison.engine_only == (), f"{label}: engine-only={comparison.engine_only}"
    assert comparison.tape_only == (), f"{label}: tape-only={comparison.tape_only}"
    assert comparison.common == 477, f"{label}: {comparison.summary()}"


def _different_fingerprint(comparison: Comparison) -> str:
    stamps = "".join(f"{stamp}\n" for stamp in sorted(comparison.different))
    return hashlib.sha256(stamps.encode("ascii")).hexdigest()


def _assert_last_digit_baseline(
        comparison: Comparison, source: str, baseline_table: dict[str, dict],
        label: str) -> None:
    different = len(comparison.different)
    fingerprint = _different_fingerprint(comparison)
    measured = (f"measured equal={comparison.equal} different={different} "
                f"different_sha256={fingerprint}")
    assert source in baseline_table, (
        f"{label}: {source}: native measurement has not been pinned for this report; "
        f"{measured}")
    pin = baseline_table[source]
    message = (f"{label}: {source}: {measured}; "
               f"pinned equal={pin['equal']} different={pin['different']} "
               f"different_sha256={pin['different_sha256']}")
    if different < pin["different"]:
        message += "; verify the fix and empty the pin"
    assert (comparison.equal == pin["equal"]
            and different == pin["different"]
            and fingerprint == pin["different_sha256"]), message


def _selected_fixed_comparison(
        selected: dict[int, Decimal], fixed: dict[str, dict[int, Decimal]],
        arms: dict[int, str]) -> Comparison:
    # Expected quantities come only from the independent fixed runs. The arm
    # comes only from the tape checked against the authored signal calendar.
    expected = {stamp: fixed[arm][stamp] for stamp, arm in arms.items()
                if stamp in fixed[arm]}
    common = selected.keys() & expected.keys()
    different = tuple(sorted(stamp for stamp in common
                             if selected[stamp] != expected[stamp]))
    return Comparison(
        len(selected), len(expected), len(common), len(common) - len(different),
        different, tuple(sorted(selected.keys() - expected.keys())),
        tuple(sorted(expected.keys() - selected.keys())),
    )


def _assert_selected_matches_fixed(
        selected: dict[int, Decimal], fixed: dict[str, dict[int, Decimal]],
        arms: dict[int, str], label: str) -> None:
    for arm, entries in fixed.items():
        assert selected.keys() == entries.keys() == arms.keys(), (
            f"{label}: fixed-{arm} entry-time sets differ; "
            f"selected-only={sorted(selected.keys() - entries.keys())}, "
            f"fixed-only={sorted(entries.keys() - selected.keys())}, "
            f"schedule-only={sorted(arms.keys() - entries.keys())}, "
            f"outside-schedule={sorted(entries.keys() - arms.keys())}")
    comparison = _selected_fixed_comparison(selected, fixed, arms)
    failures = tuple((stamp, arms[stamp], str(selected[stamp]),
                      str(fixed[arms[stamp]][stamp])) for stamp in comparison.different)
    assert comparison.matches, (
        f"{label}: {comparison.summary()}; "
        f"(entry_time_ms, arm, selected_raw, fixed_raw)={failures}")


def _selected_fixed_log(
        source: str, selected: dict[int, Decimal],
        fixed: dict[str, dict[int, Decimal]], arms: dict[int, str]) -> bytes:
    comparison = _selected_fixed_comparison(selected, fixed, arms)
    stream = io.StringIO(newline="")
    stream.write(f"{source}: entries={len(selected)} equal={comparison.equal} "
                 f"different={len(comparison.different)} {comparison.summary()}\n")
    for arm, entries in fixed.items():
        common = entries.keys() & selected.keys() & arms.keys()
        applicable = {stamp for stamp in common if arms[stamp] == arm}
        equal = sum(entries[stamp] == selected[stamp] for stamp in applicable)
        summary = (f"{source.removesuffix('-parity')}-fixed-{arm}: "
                   f"entries={len(entries)} selected-arm-entries={len(applicable)} "
                   f"equal={equal} different={len(applicable) - equal}")
        stream.write(summary + "\n")
        print(summary)
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(("entry_time_utc", "arm", "selected_raw", "fixed_60_raw",
                     "fixed_240_raw", "result"))
    stamps = selected.keys() | arms.keys() | fixed["60"].keys() | fixed["240"].keys()
    for stamp in sorted(stamps):
        arm = arms.get(stamp, "")
        complete = (stamp in selected and stamp in arms
                    and all(stamp in entries for entries in fixed.values()))
        verdict = ("missing-or-extra" if not complete else
                   "different" if stamp in comparison.different else "equal")
        label = dt.datetime.fromtimestamp(stamp / 1000, UTC).strftime("%Y-%m-%d %H:%M")
        writer.writerow((label, arm, selected.get(stamp, ""), fixed["60"].get(stamp, ""),
                         fixed["240"].get(stamp, ""), verdict))
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


@pytest.fixture(scope="module")
def native_entries(native_inputs, tmp_path_factory):
    engine, feed = native_inputs
    cache: dict[str, dict[int, Decimal]] = {}

    def entries(source: str) -> dict[int, Decimal]:
        if source not in cache:
            directory = tmp_path_factory.mktemp(source)
            result = transpile_json(FIXTURES / f"{source}.pine")
            assert result["ok"], result.get("diagnostics")
            build_strategy_library(result["cpp"], directory)
            blob, _, stderr = run_strategy(
                engine, directory, feed, {"input_tf": "60", "script_tf": "60"}, "values")
            (directory / "run.stderr").write_text(stderr, encoding="utf-8")
            cache[source] = _engine_entries(blob)
        return cache[source]

    return entries


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture)
def test_native_entry_quantities_match_committed_tape_rules(
        case: Case, native_entries, tmp_path: Path) -> None:
    tape = _tape_entries(case)
    entries = native_entries(case.fixture)
    comparison = _compare(entries, tape, case.fixture)
    log = _join_log(entries, tape, comparison)
    destination = _write_log(tmp_path, case.fixture, log)
    if case.fixture in STATE_SOURCES:
        # Disclosed pre-existing difference of the engine's quantity computation
        # against TradingView (last digit). This MUST FLIP to equality, by
        # turning the report into an exact assertion, when its root fix lands.
        # Do not invent or pin counts before the native measurement.
        reports = [(case.fixture, comparison)]
        for arm in ("60", "240"):
            source = f"{case.fixture.removesuffix('-parity')}-fixed-{arm}"
            fixed_entries = native_entries(source)
            fixed_tape = _tape_entries(Case(source, case.tape, case.tape_source,
                                           f"fixed_{arm}"))
            fixed_comparison = _compare(fixed_entries, fixed_tape)
            _write_log(tmp_path / source, source,
                       _join_log(fixed_entries, fixed_tape, fixed_comparison))
            reports.append((source, fixed_comparison))
        # Report every source before any assertion, retaining all cells.
        for source, report in reports:
            _assert_state_structure(report, source)
            _assert_last_digit_baseline(report, source, LAST_DIGIT_BASELINE, source)
    else:
        # No warm-up entries are excluded, including the first scheduled fill.
        _assert_matches(comparison, f"{case.fixture}; join-log={destination}")


@pytest.mark.parametrize("kind", ("history", "sma"))
def test_native_selected_matches_engine_fixed_arm(
        kind: str, native_entries, tmp_path: Path) -> None:
    source = f"{kind}-parity"
    arms = _state_arms(_state_rows(source))
    selected = native_entries(source)
    fixed = {arm: native_entries(f"{kind}-fixed-{arm}") for arm in ("60", "240")}
    destination = _write_log(tmp_path, source,
                             _selected_fixed_log(source, selected, fixed, arms))
    # Raw Qty strings parsed to Decimal: no quantization in this comparison.
    _assert_selected_matches_fixed(selected, fixed, arms,
                                   f"{source}; join-log={destination}")


@pytest.mark.parametrize("source", STATE_SOURCES)
def test_state_tape_selected_equals_authored_fixed_arm(source: str) -> None:
    rows = _state_rows(source)
    arms = _state_arms(rows)
    for stamp, row in rows.items():
        fixed_column = f"fixed_{arms[stamp]}"
        assert row["selected"] == row[fixed_column], (
            f"{source} {stamp}: selected={row['selected']} "
            f"{fixed_column}={row[fixed_column]}")


# Synthetic controls below are not native evidence. Tape controls start from
# committed exports; raw CSV controls use independently authored bytes.
TAPE_CHECK_CASES = CASES + tuple(
    Case(f"{source.removesuffix('-parity')}-fixed-{arm}",
         "tv_state_entries.tsv", source, f"fixed_{arm}")
    for source in STATE_SOURCES for arm in ("60", "240")
)
STATE_TAPE_CHECK_CASES = tuple(
    case for case in TAPE_CHECK_CASES if case.tape == "tv_state_entries.tsv"
)


@pytest.mark.parametrize("case", TAPE_CHECK_CASES, ids=lambda case: case.fixture)
@pytest.mark.parametrize("sign", (-1, 1))
def test_join_self_check_pins_literal_export_precision(case: Case, sign: int) -> None:
    tape = _tape_entries(case)
    # Independent export contract: never derive the perturbation from _quantum.
    quantum = {
        "tv_bar_quantities.tsv": Decimal("0.00001"),
        "tv_hour_entries.tsv": Decimal("0.01"),
        "tv_state_entries.tsv": Decimal("0.00001"),
    }[case.tape]
    assert _quantum(tape) == quantum
    engine = dict(tape)
    # Compare complete synthetic entries, including the parity export's
    # fractional cell: native missing-fill exceptions do not apply here.
    _assert_matches(_compare(engine, tape), case.fixture)
    stamp = min(tape)
    if case.column == "entry_quantities":
        assert tape[stamp] == Decimal("2")  # An integer-valued counter cell.
    engine[stamp] += sign * quantum
    comparison = _compare(engine, tape)
    assert comparison.different == (stamp,)
    assert comparison.equal == len(tape) - 1
    assert comparison.engine_only == comparison.tape_only == ()
    assert not comparison.matches
    with pytest.raises(AssertionError, match="different="):
        _assert_matches(comparison, case.fixture)


@pytest.mark.parametrize(
    "case", tuple(case for case in TAPE_CHECK_CASES
                  if case.tape == "tv_bar_quantities.tsv"),
    ids=lambda case: case.fixture,
)
@pytest.mark.parametrize("spelling", ("1", "1.23456"), ids=("integer", "fractional"))
@pytest.mark.parametrize("sign", (-1, 1))
def test_join_self_check_counter_cell_spelling(
        case: Case, spelling: str, sign: int) -> None:
    tape = _tape_entries(case)
    assert _quantum(tape) == Decimal("0.00001")
    stamp = min(tape)
    # Synthetic cell spellings, not new native expectations. The price-counter
    # exports have no fractional cells; retain their independent column scale
    # while exercising both a bare "1" and a five-place fractional spelling.
    tape[stamp] = Decimal(spelling)
    assert _quantum(tape) == Decimal("0.00001")
    engine = dict(tape)
    _assert_matches(_compare(engine, tape), case.fixture)
    engine[stamp] += sign * Decimal("0.00001")
    comparison = _compare(engine, tape)
    assert comparison.different == (stamp,)
    assert comparison.equal == len(tape) - 1
    assert comparison.engine_only == comparison.tape_only == ()
    assert not comparison.matches
    with pytest.raises(AssertionError, match="different="):
        _assert_matches(comparison, case.fixture)


# Minimal native CSV projection: these are the three columns _engine_entries
# reads. tests/_e2e.py::run_strategy returns the runner's bytes unchanged.
# The blobs are authored here, never serialized from the parser's output.
@pytest.mark.parametrize("bom", (b"", b"\xef\xbb\xbf"), ids=("plain", "bom"))
@pytest.mark.parametrize(
    "changed_quantity", ("1.234567890", "1.234567892"), ids=("minus-1e-9", "plus-1e-9"),
)
def test_engine_entries_self_check_preserves_raw_quantity(
        bom: bytes, changed_quantity: str) -> None:
    blob = (
        b"Type,Date and time,Qty\n"
        b"Entry long,2024-01-01 00:00,1.234567891\n"
        b"Entry short,2024-01-01 01:00,2.345678912\n"
    )
    entries = _engine_entries(bom + blob)
    assert entries == {
        1704067200000: Decimal("1.234567891"),
        1704070800000: Decimal("2.345678912"),
    }
    changed_blob = blob.replace(b"1.234567891", changed_quantity.encode("ascii"))
    changed = _engine_entries(bom + changed_blob)
    assert changed == {
        1704067200000: Decimal(changed_quantity),
        1704070800000: Decimal("2.345678912"),
    }
    assert changed != entries
    arms = {1704067200000: "60", 1704070800000: "240"}
    fixed = {"60": entries, "240": entries}
    comparison = _selected_fixed_comparison(changed, fixed, arms)
    assert comparison.different == (1704067200000,)
    assert comparison.equal == 1
    with pytest.raises(AssertionError, match="selected_raw, fixed_raw"):
        _assert_selected_matches_fixed(changed, fixed, arms, "synthetic CSV")


@pytest.mark.parametrize("bom", (b"", b"\xef\xbb\xbf"), ids=("plain", "bom"))
def test_engine_entries_self_check_entry_kinds_and_utc(bom: bytes) -> None:
    blob = (
        b"Type,Date and time,Qty\n"
        b"Entry long,2024-01-01 00:00,1.234567891\n"
        b"Exit long,2024-01-01 00:00,9.876543219\n"
        b"Entry short,2024-01-01 01:00,2.345678912\n"
        b"Exit short,2024-01-01 02:00,8.765432198\n"
    )
    # Calendar literals: Jan 1 2024 00:00 UTC and one hour later.
    assert _engine_entries(bom + blob) == {
        1704067200000: Decimal("1.234567891"),
        1704070800000: Decimal("2.345678912"),
    }


@pytest.mark.parametrize("bom", (b"", b"\xef\xbb\xbf"), ids=("plain", "bom"))
def test_engine_entries_self_check_rejects_duplicate_labels(bom: bytes) -> None:
    blob = (
        b"Type,Date and time,Qty\n"
        b"Entry long,2024-01-01 00:00,1.234567891\n"
        b"Entry short,2024-01-01 00:00,2.345678912\n"
    )
    with pytest.raises(AssertionError, match="duplicate entry time: 1704067200000"):
        _engine_entries(bom + blob)


@pytest.mark.parametrize("case", STATE_TAPE_CHECK_CASES, ids=lambda case: case.fixture)
def test_last_digit_baseline_self_check_accepts_exact_pin(case: Case) -> None:
    tape = _tape_entries(case)
    changed = dict(tape)
    stamps = sorted(tape)[:2]
    for stamp in stamps:
        changed[stamp] += _quantum(tape)
    comparison = _compare(changed, tape)
    assert comparison.equal == 475
    assert comparison.different == tuple(stamps)
    baseline = {case.fixture: {
        "equal": comparison.equal,
        "different": len(comparison.different),
        "different_sha256": hashlib.sha256(
            "".join(f"{stamp}\n" for stamp in stamps).encode("ascii")).hexdigest(),
    }}
    _assert_last_digit_baseline(comparison, case.fixture, baseline, "synthetic tape")


@pytest.mark.parametrize("case", STATE_TAPE_CHECK_CASES, ids=lambda case: case.fixture)
@pytest.mark.parametrize("change", ("added", "removed", "moved", "equal"))
def test_last_digit_baseline_self_check_rejects_changed_pin(case: Case, change: str) -> None:
    tape = _tape_entries(case)
    first, second, third = sorted(tape)[:3]
    changed = dict(tape)
    quantum = _quantum(tape)
    for stamp in (first, second):
        changed[stamp] += quantum
    original = _compare(changed, tape)
    assert original.equal == 475
    assert original.different == (first, second)
    baseline = {case.fixture: {
        "equal": original.equal,
        "different": len(original.different),
        "different_sha256": hashlib.sha256(
            f"{first}\n{second}\n".encode("ascii")).hexdigest(),
    }}
    if change == "added":
        changed[third] += quantum
        expected_different, expected_equal = (first, second, third), 474
    elif change == "removed":
        changed[first] = tape[first]
        expected_different, expected_equal = (second,), 476
    elif change == "moved":
        changed[first] = tape[first]
        changed[third] += quantum
        expected_different, expected_equal = (second, third), 475
    else:
        # Remove an unchanged cell: only equal changes among the pinned fields.
        del changed[third]
        expected_different, expected_equal = (first, second), 474
    comparison = _compare(changed, tape)
    assert comparison.different == expected_different
    assert comparison.equal == expected_equal
    with pytest.raises(AssertionError) as error:
        _assert_last_digit_baseline(comparison, case.fixture, baseline, "synthetic tape")
    message = str(error.value)
    fingerprint = hashlib.sha256(
        "".join(f"{stamp}\n" for stamp in expected_different).encode("ascii")).hexdigest()
    assert (f"measured equal={expected_equal} different={len(expected_different)} "
            f"different_sha256={fingerprint}") in message
    pin = baseline[case.fixture]
    assert (f"pinned equal={pin['equal']} different={pin['different']} "
            f"different_sha256={pin['different_sha256']}") in message
    assert "synthetic tape" in message and case.fixture in message
    assert ("verify the fix and empty the pin" in message) == (change == "removed")


@pytest.mark.parametrize("case", STATE_TAPE_CHECK_CASES, ids=lambda case: case.fixture)
def test_last_digit_baseline_self_check_requires_measurement(case: Case) -> None:
    tape = _tape_entries(case)
    changed = dict(tape)
    changed[min(tape)] += _quantum(tape)
    comparison = _compare(changed, tape)
    with pytest.raises(AssertionError, match="native measurement has not been pinned") as error:
        _assert_last_digit_baseline(comparison, case.fixture, {}, "synthetic tape")
    assert case.fixture in str(error.value)


@pytest.mark.parametrize("case", STATE_TAPE_CHECK_CASES, ids=lambda case: case.fixture)
def test_different_fingerprint_self_check_empty(case: Case) -> None:
    tape = _tape_entries(case)
    comparison = _compare(dict(tape), tape)
    assert comparison.different == ()
    assert _different_fingerprint(comparison) == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("case", STATE_TAPE_CHECK_CASES, ids=lambda case: case.fixture)
def test_different_fingerprint_self_check_sorts_times(case: Case) -> None:
    tape = _tape_entries(case)
    first, second, third = sorted(tape)[:3]
    changed = dict(tape)
    for stamp in (first, third):
        changed[stamp] += _quantum(tape)
    comparison = _compare(changed, tape)
    expected = hashlib.sha256(f"{first}\n{third}\n".encode("ascii")).hexdigest()
    assert comparison.different == (first, third)
    assert _different_fingerprint(comparison) == expected
    assert _different_fingerprint(replace(comparison, different=(third, first))) == expected
    assert _different_fingerprint(_compare(dict(reversed(changed.items())), tape)) == expected
    changed[first] = tape[first]
    changed[second] += _quantum(tape)
    moved = _compare(changed, tape)
    assert moved.different == (second, third)
    assert _different_fingerprint(moved) == hashlib.sha256(
        f"{second}\n{third}\n".encode("ascii")).hexdigest()
    assert _different_fingerprint(moved) != expected


def _tape_control(case: Case) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    tape = _tape_entries(case)
    engine = {stamp: quantity for stamp, quantity in tape.items()
              if stamp not in KNOWN_TAPE_ONLY.get(case.fixture, ())}
    _assert_matches(_compare(engine, tape, case.fixture), case.fixture)
    return engine, tape


@pytest.mark.parametrize("case", TAPE_CHECK_CASES, ids=lambda case: case.fixture)
@pytest.mark.parametrize("sign", (-1, 1))
def test_join_self_check_reports_changed_quantity(case: Case, sign: int) -> None:
    engine, tape = _tape_control(case)
    stamp = min(engine)
    engine[stamp] += sign * _quantum(tape)
    comparison = _compare(engine, tape, case.fixture)
    assert comparison.different == (stamp,)
    assert comparison.equal == comparison.common - 1
    assert not comparison.engine_only
    assert comparison.tape_only == KNOWN_TAPE_ONLY.get(case.fixture, ())
    assert not comparison.matches
    with pytest.raises(AssertionError, match="different="):
        _assert_matches(comparison, case.fixture)
    log = _join_log(engine, tape, comparison).decode("utf-8")
    assert f"\t{engine[stamp]}\t{tape[stamp]}\tdifferent\n" in log


@pytest.mark.parametrize("case", TAPE_CHECK_CASES, ids=lambda case: case.fixture)
@pytest.mark.parametrize("change", ("missing", "extra"))
def test_join_self_check_reports_changed_entry_set(case: Case, change: str) -> None:
    engine, tape = _tape_control(case)
    if change == "missing":
        stamp = min(engine)
        del engine[stamp]
    else:
        stamp = max(tape) + HOUR_MS
        engine[stamp] = Decimal("1")
    comparison = _compare(engine, tape, case.fixture)
    if change == "missing":
        assert comparison.tape_only == tuple(sorted(
            (*KNOWN_TAPE_ONLY.get(case.fixture, ()), stamp)))
        assert not comparison.engine_only
    else:
        assert comparison.engine_only == (stamp,)
        assert comparison.tape_only == KNOWN_TAPE_ONLY.get(case.fixture, ())
    with pytest.raises(AssertionError):
        _assert_matches(comparison, case.fixture)
    assert not comparison.matches
    assert f"\t{'tape-only' if change == 'missing' else 'engine-only'}\n" in (
        _join_log(engine, tape, comparison).decode("utf-8"))


@pytest.mark.parametrize("source", tuple(KNOWN_TAPE_ONLY))
def test_join_self_check_requires_exact_known_missing_cell(source: str) -> None:
    case = next(case for case in CASES if case.fixture == source)
    engine, tape = _tape_control(case)
    known = (1704132000000,)
    assert KNOWN_TAPE_ONLY[source] == known
    comparison = _compare(engine, tape, source)
    assert comparison.tape_only == known
    assert comparison.engine_only == ()
    assert comparison.common == comparison.equal == 204
    _assert_matches(comparison, source)
    assert "\tknown-tape-only\n" in _join_log(engine, tape, comparison).decode("utf-8")

    # Restoration must demand removal of the pin, even with an exact quantity.
    restored = dict(engine)
    restored[known[0]] = tape[known[0]]
    with pytest.raises(AssertionError, match="verify the fix and empty KNOWN_TAPE_ONLY"):
        _assert_matches(_compare(restored, tape, source), source)
    # Removing the committed known cell must not silently retire the pin either.
    missing_tape = dict(tape)
    del missing_tape[known[0]]
    with pytest.raises(AssertionError, match="verify the fix and empty KNOWN_TAPE_ONLY"):
        _assert_matches(_compare(engine, missing_tape, source), source)
    # A different omission cannot replace the known one.
    del restored[min(engine)]
    with pytest.raises(AssertionError):
        _assert_matches(_compare(restored, tape, source), source)


def _fixed_control(source: str):
    rows = _state_rows(source)
    arms = _state_arms(rows)
    selected = {stamp: Decimal(row["selected"]) for stamp, row in rows.items()}
    fixed = {arm: {stamp: Decimal(row[f"fixed_{arm}"]) for stamp, row in rows.items()}
             for arm in ("60", "240")}
    _assert_selected_matches_fixed(selected, fixed, arms, source)
    return selected, fixed, arms


@pytest.mark.parametrize("source", STATE_SOURCES)
@pytest.mark.parametrize("target", ("selected", "60", "240"))
def test_fixed_arm_self_check_rejects_changed_raw_quantity(source: str, target: str) -> None:
    selected, fixed, arms = _fixed_control(source)
    stamp = next(stamp for stamp in arms if target == "selected" or arms[stamp] == target)
    changed = selected if target == "selected" else fixed[target]
    # Smaller than the tape's last digit: raw comparison must still fail.
    changed[stamp] += Decimal("0.000000001")
    comparison = _selected_fixed_comparison(selected, fixed, arms)
    assert comparison.different == (stamp,)
    assert comparison.equal == 476
    with pytest.raises(AssertionError, match="selected_raw, fixed_raw") as error:
        _assert_selected_matches_fixed(selected, fixed, arms, source)
    assert str(selected[stamp]) in str(error.value)
    assert str(fixed[arms[stamp]][stamp]) in str(error.value)
    assert f"\t{selected[stamp]}\t{fixed['60'][stamp]}\t{fixed['240'][stamp]}\tdifferent\n" in (
        _selected_fixed_log(source, selected, fixed, arms).decode("utf-8"))


@pytest.mark.parametrize("source", STATE_SOURCES)
@pytest.mark.parametrize("swap_all", (False, True), ids=("one-entry", "all-entries"))
def test_fixed_arm_self_check_rejects_wrong_arm(source: str, swap_all: bool) -> None:
    selected, fixed, arms = _fixed_control(source)
    candidates = [stamp for stamp in arms if fixed["60"][stamp] != fixed["240"][stamp]]
    assert candidates, "control must contain distinguishable arms"
    changed = list(arms) if swap_all else candidates[:1]
    for stamp in changed:
        other = "60" if arms[stamp] == "240" else "240"
        selected[stamp] = fixed[other][stamp]
    comparison = _selected_fixed_comparison(selected, fixed, arms)
    expected = tuple(sorted(candidates if swap_all else candidates[:1]))
    assert comparison.different == expected
    with pytest.raises(AssertionError, match="selected_raw, fixed_raw"):
        _assert_selected_matches_fixed(selected, fixed, arms, source)


@pytest.mark.parametrize("source", STATE_SOURCES)
@pytest.mark.parametrize("target", ("selected", "60", "240"))
@pytest.mark.parametrize("change", ("missing", "extra"))
def test_fixed_arm_self_check_rejects_changed_entry_set(
        source: str, target: str, change: str) -> None:
    selected, fixed, arms = _fixed_control(source)
    changed = selected if target == "selected" else fixed[target]
    if change == "missing":
        del changed[min(changed)]
    else:
        changed[max(arms) + HOUR_MS] = Decimal("1")
    with pytest.raises(AssertionError, match="entry-time sets differ"):
        _assert_selected_matches_fixed(selected, fixed, arms, source)
    assert "\tmissing-or-extra\n" in (
        _selected_fixed_log(source, selected, fixed, arms).decode("utf-8"))


@pytest.mark.parametrize("source", STATE_SOURCES)
@pytest.mark.parametrize("column", ("arm", "signal_bar_index"))
def test_state_schedule_self_check_rejects_changed_tape_rule(source: str, column: str) -> None:
    rows = _state_rows(source)
    _state_arms(rows)
    row = rows[min(rows)]
    if column == "arm":
        row[column] = "60" if row[column] == "240" else "240"
    else:
        row[column] = str(int(row[column]) + 1)
    with pytest.raises(AssertionError):
        _state_arms(rows)


@pytest.mark.parametrize("kind", ("history", "sma"))
@pytest.mark.parametrize("arm", ("60", "240"))
def test_fixed_arm_fixture_preserves_source(kind: str, arm: str) -> None:
    original = (FIXTURES / f"{kind}-parity.pine").read_text(encoding="utf-8")
    assert original.count("syminfo.tickerid, pf_tf,") == 1
    assert original.count('", currency =') == 1
    expected = original.replace("syminfo.tickerid, pf_tf,",
                                f'syminfo.tickerid, "{arm}",')
    expected = expected.replace('", currency =', f' - fixed {arm}", currency =', 1)
    assert (FIXTURES / f"{kind}-fixed-{arm}.pine").read_text(encoding="utf-8") == expected
