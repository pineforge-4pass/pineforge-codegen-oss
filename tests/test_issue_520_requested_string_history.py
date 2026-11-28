"""Measure the script from #520, with a tuple name and with a plain name.

Two measurement rows. The request reads held[2] directly. held is a var
string whose only store sits after the request and reads a, a name that an
unconditional store overwrites on every bar. In the tuple form a is a name
of a tuple declaration (the first row); in the plain form it is a plain
declared string (the second row).

The feed is the helper's: five four-hour groups of four hourly children.
The first three children of a group close at start, start + 1, start + 2,
with start 100, 1000, 10000, 100000, 1000000; the fourth closes at 7, 42,
316, 2024, 5. The helper returns the request's value at the four-hour
closes of groups 2, 3 and 4. The three outcomes to tell apart:

- [5, 6, 7], the chart-history fallback: held[2] on the chart clock is the
  second child's close, "10001", "100001", "1000001".
- [1, 2, 3], the requested clock: held is the four-hour close, "7", "42",
  "316" in slots 0, 1, 2.
- [3, 4, 5], the wrong value: the request replays held's store but not a, so
  the store reads the a the previous chart bar left, the third child's
  "102", "1002", "10002".

Either of the first two is a pass: neither is a silent wrong value. Any
other reading fails its row. The rows measure values only: a compile
refusal fails its row too. Each row prints what it read, so a passing run
still shows which of the two values the head produced (pytest -rP).

A limit on the third outcome: [3, 4, 5] assumes the evaluator runs
before the chart bar's own script. Were it to run after, a store that
reads the chart's a would also read [1, 2, 3] on this feed.

A third row is a control on the later-store route, on the same feed. It is
described where it is defined, after the two measurement rows.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env

# The harness below is copied byte for byte from this repository's tests at
# 092bd0d8, so that this file drives the same feed and the same observer on
# every head it measures. Main 782a4510 has no _four_hour_values_after_warmup,
# and its _run_requested takes neither group_minutes nor sec_id. Sources, in
# the order they are copied:
#   tests/test_security_helper_local_var.py:152-163    _find_engine_library
#   tests/test_security_helper_local_var.py:166-204    _compile_and_run
#   tests/test_security_helper_arg_history.py:39-46    _HOURLY_OPENS, _CHART_CLOSES
#   tests/test_security_helper_arg_history.py:50-55    _Evaluation
#   tests/test_security_helper_arg_history.py:106-125  _OBSERVER
#   tests/test_security_helper_arg_history.py:128-201  _run_requested
#   tests/test_issue_495_history_location.py:482-497   _four_hour_values_after_warmup


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    if compile_env._ENGINE_INC is None:
        return None
    root = compile_env._ENGINE_INC.parent
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(root.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(cpp_source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip("built libpineforge not found; set PINEFORGE_ENGINE_LIB")

    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None

    with tempfile.TemporaryDirectory(prefix="pineforge-security-var-") as tmp:
        cpp_path = Path(tmp) / "matrix.cpp"
        exe_path = Path(tmp) / "matrix"
        cpp_path.write_text(cpp_source)
        cmd = [
            compiler,
            "-std=c++17",
            "-O0",
            *compile_env.STRATEGY_FP_FLAGS,
            "-I", str(engine_inc),
            "-I", str(eigen_inc),
        ]
        if compile_env._GENERATED_INC is not None:
            cmd += ["-I", str(compile_env._GENERATED_INC)]
        cmd += [str(cpp_path), str(engine_lib), "-pthread", "-o", str(exe_path)]
        built = subprocess.run(cmd, capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError(
                "security helper-var runtime probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:80])
            )
        ran = subprocess.run([str(exe_path)], capture_output=True, text=True)
        if ran.returncode != 0:
            raise AssertionError(
                f"security helper-var runtime probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


_HOURLY_OPENS = (1.0, 3.0, 5.0, 7.0, 9.0)
_CHART_CLOSES = (
    (1.0, 2.0, 3.0, 3.0),
    (4.0, 5.0, 6.0, 6.0),
    (7.0, 8.0, 9.0, 9.0),
    (10.0, 11.0, 12.0, 12.0),
    (13.0, 14.0, 15.0, 15.0),
)


class _Evaluation(NamedTuple):
    timestamp: int
    complete: bool
    open: float
    close: float
    value: float | str


_OBSERVER = r"""
#include <iomanip>
#include <iostream>

struct RequestedHistoryObserver : GeneratedStrategy {
    const char* tag = "";

    void evaluate_security(int sec_id, const Bar& bar, bool is_complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, is_complete);
        if (sec_id == OBSERVED_ID) {
            std::cout << tag << ' ' << bar.timestamp << ' ' << is_complete
                      << ' ' << bar.open << ' ' << bar.close
                      << ' ' << OBSERVED_VALUE << '\n';
        }
    }
};

int main() {
    std::cout << std::setprecision(17);
"""


def _run_requested(
    cpp: str,
    feeds: dict[str, tuple[int, tuple[tuple[float, ...], ...]]] | None = None,
    *,
    value_expression: str | None = None,
    sec_id: int = 0,
    group_minutes: int = 60,
    text_result: bool = False,
) -> dict[str, list[_Evaluation]]:
    """Observe the real requested evaluator during the normal native run.

    Each feed is (chart minutes, five groups of chart closes). The first child
    opens at its specified group open; later children open at the previous
    child close. These valid OHLC bars aggregate to the inputs of the oracle.
    No engine callbacks, slot flags or generated histories are mocked.
    Observe a selected request, or a tuple component via value_expression.
    text_result retains a single-line string result verbatim, including empty.
    """
    if feeds is None:
        feeds = {"chart15": (15, _CHART_CLOSES)}
    runs = []
    for tag, (minutes, groups) in feeds.items():
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tag)
        assert len(groups) == len(_HOURLY_OPENS), (
            f"NOT COMPARED: {tag} has {len(groups)} groups, expected {len(_HOURLY_OPENS)}"
        )
        bars = []
        for group, (open_, children) in enumerate(zip(_HOURLY_OPENS, groups)):
            assert len(children) * minutes == group_minutes, (
                f"NOT COMPARED: {tag} group {group} spans {len(children) * minutes} minutes, "
                f"expected {group_minutes}"
            )
            for child, close in enumerate(children):
                timestamp = (group * group_minutes + child * minutes) * 60_000
                fields = (open_, max(open_, close), min(open_, close), close, 1.0)
                bars.append(
                    "        Bar{"
                    + ", ".join(repr(value) for value in fields)
                    + f", {timestamp}}}"
                )
                open_ = close
        runs.append(
            "    {\n"
            "        RequestedHistoryObserver strategy;\n"
            f'        strategy.tag = "{tag}";\n'
            "        Bar bars[] = {\n"
            + ",\n".join(bars)
            + "\n        };\n"
            + f'        strategy.run(bars, {len(bars)}, "{minutes}", "{minutes}");\n'
            + "        if (!strategy.last_error().empty()) {\n"
            + "            std::cerr << strategy.last_error(); return 1;\n"
            + "        }\n"
            + "    }\n"
        )
    value = value_expression if value_expression is not None else f"_req_sec_{sec_id}"
    if text_result:
        value = f"static_cast<const std::string&>({value})"
    observer = _OBSERVER.replace("OBSERVED_ID", str(sec_id)).replace(
        "OBSERVED_VALUE", value)
    output = _compile_and_run(cpp + observer + "".join(runs) + "    return 0;\n}\n")
    evaluations: dict[str, list[_Evaluation]] = {tag: [] for tag in feeds}
    for line in output.splitlines():
        fields = line.split(" ", 5) if text_result else line.split()
        assert len(fields) == 6, f"NOT COMPARED: expected 6 fields, received {fields!r}"
        tag, timestamp, complete, open_, close, value = fields
        assert tag in evaluations, f"NOT COMPARED: expected a tag in {list(feeds)}, received {tag!r}"
        assert complete in {"0", "1"}, f"NOT COMPARED: expected 0 or 1, received {complete!r}"
        evaluations[tag].append(
            _Evaluation(
                int(timestamp), complete == "1", float(open_), float(close),
                value if text_result else float(value),
            )
        )
    return evaluations


def _four_hour_values_after_warmup(source, *, sec_id=0):
    # The first three hourly children rise and write strings of the same
    # length. At the four-hour close, chart txt[2] therefore has that length.
    groups = tuple(
        (start, start + 1, start + 2, final)
        for start, final in ((100, 7), (1000, 42), (10000, 316), (100000, 2024), (1000000, 5))
    )
    rows = _run_requested(transpile(source), {"chart60": (60, groups)},
                          group_minutes=240, sec_id=sec_id)
    completed = [row for row in rows["chart60"] if row.complete]
    actual = [(row.timestamp, row.close) for row in completed]
    expected = [(slot * 240 * 60_000, group[-1]) for slot, group in enumerate(groups)]
    assert actual == expected, f"NOT COMPARED: completed bars {actual}, expected {expected}"
    # In contrast, txt[2] on the requested clock would see closes 7, 42, 316
    # and return lengths 1, 2, 3 in these same three slots.
    return [row.value for row in completed[2:]]


# The script from #520, the tuple form: the nine lines between the quotes
# are kept verbatim.
_SCRIPT_TUPLE_FORM = """\
//@version=6
strategy("direct read of a var string whose later store reads an overwritten tuple name")
pair() => ["seed", 1]
[a, n] = pair()
a := str.tostring(close)
var string held = ""
v = request.security(syminfo.tickerid, "240", str.length(held[2]))
held := a
plot(v + n)
"""

# The plain form: the tuple form with `string a = "seed"` in place of
# `pair() => ["seed", 1]` and `[a, n] = pair()`, and `plot(v)` in place of
# `plot(v + n)`. Every other line is the tuple form's, the title included.
_SCRIPT_PLAIN_FORM = """\
//@version=6
strategy("direct read of a var string whose later store reads an overwritten tuple name")
string a = "seed"
a := str.tostring(close)
var string held = ""
v = request.security(syminfo.tickerid, "240", str.length(held[2]))
held := a
plot(v)
"""


def test_tuple_form_later_store_reads_an_overwritten_tuple_name():
    values = _four_hour_values_after_warmup(_SCRIPT_TUPLE_FORM)
    print(f"The tuple form read {values}")
    assert values in ([5, 6, 7], [1, 2, 3]), (
        f"read {values}; a pass is the chart-history fallback [5, 6, 7] "
        "or the requested clock [1, 2, 3]; [3, 4, 5] is the wrong value "
        "(held's store replayed with the a the previous chart bar left)"
    )


def test_plain_form_later_store_reads_a_reassigned_plain_name():
    values = _four_hour_values_after_warmup(_SCRIPT_PLAIN_FORM)
    print(f"The plain form read {values}")
    assert values in ([5, 6, 7], [1, 2, 3]), (
        f"read {values}; a pass is the chart-history fallback [5, 6, 7] "
        "or the requested clock [1, 2, 3]; [3, 4, 5] is the wrong value "
        "(held's store replayed with the a the previous chart bar left)"
    )


# CONTROL, the later-store route. The plain form with one line changed: the
# later store is `held := str.tostring(close)`, the expression a holds, in
# place of `held := a`. Every other line is the plain form's, the title included.
#
# The request holds held, a var, from its own call. The evaluator replays
# held's declaration and its later store on the requested bar, so held is
# "7", "42", "316" in slots 0, 1, 2 and str.length(held[2]) is 1, 2, 3 in
# slots 2, 3, 4. Kept on chart history the row would read [5, 6, 7]; a store
# that read the chart's previous bar would read [3, 4, 5].
#
# The store reads no name: close is the requested bar's. So the rule that
# adds the names a later store reads (_replay_later_store_reads in
# pineforge_codegen/analyzer/base.py) adds nothing here, a is not replayed
# although it is reassigned, since no statement the request replays reads
# it, and the request's replay set is ("held",) with and without that rule.
# This row reads the same values on both.
_SCRIPT_CONTROL_LATER_STORE = """\
//@version=6
strategy("direct read of a var string whose later store reads an overwritten tuple name")
string a = "seed"
a := str.tostring(close)
var string held = ""
v = request.security(syminfo.tickerid, "240", str.length(held[2]))
held := str.tostring(close)
plot(v)
"""


def test_control_later_store_route_reads_the_requested_clock():
    values = _four_hour_values_after_warmup(_SCRIPT_CONTROL_LATER_STORE)
    print(f"Control (later-store route) read {values}")
    assert values == [1, 2, 3], (
        f"read {values}; the control reads the requested clock [1, 2, 3] "
        "with and without the rule that adds the names a later store reads "
        "(chart history for held reads [5, 6, 7])"
    )
