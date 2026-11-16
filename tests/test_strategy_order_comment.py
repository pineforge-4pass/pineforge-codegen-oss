"""The ``comment`` argument of ``strategy.order`` reaches the order it places.

Three targets, one test each:

* the emitted ``strategy_order`` call forwards the bound comment exactly once;
* the order-shape receipt keeps its version 1 fields: the descriptor of a
  ``strategy.order`` call has no ``comment`` field;
* natively, the comment of the order that closes a trade is that trade's
  ``strategy.closedtrades.exit_comment``, beside the three text fields that
  already arrive (``entry_id``, ``entry_comment``, ``exit_id``).

Every expected value is a string the script itself submits: the expectation is
identity with the input, never a value computed by the code under test.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu, skip_if_no_engine_lib


FIELDS = ("entry_id", "entry_comment", "exit_id", "exit_comment")

# Set to a directory to keep each case's Pine, emitted C++ and observations.
EVIDENCE_DIRECTORY = "PINEFORGE_ORDER_COMMENT_EVIDENCE"


def _inputs(case: str) -> dict[str, str]:
    values = {"entry_id": "ENTRY-ID", "entry_comment": "ENTRY-COMMENT",
              "exit_id": "EXIT-ID", "exit_comment": "EXIT-COMMENT"}
    if case == "length256":
        # Four distinct 256-character strings: no field can stand in for another.
        return {name: value + "-" + chr(65 + index) * (255 - len(value))
                for index, (name, value) in enumerate(values.items())}
    return values


def _pine_source(values: dict[str, str]) -> str:
    lines = ['//@version=6',
             'strategy("order comment", initial_capital=1000000, '
             'currency=currency.NONE, margin_long=0, margin_short=0, '
             'process_orders_on_close=false)']
    for name, value in values.items():
        lines += [f'var string supplied_{name} = {json.dumps(value)}',
                  f'var string observed_{name} = ""',
                  f'var int observed_{name}_length = -1',
                  f'var bool observed_{name}_equal = false']
    lines += ['var int observed_closed = -1', 'var int observed_open = -1',
              'var string observed_open_id = ""',
              'var string observed_open_comment = ""',
              'if bar_index == 0',
              '    strategy.entry(supplied_entry_id, strategy.long, qty=1, '
              'comment=supplied_entry_comment)',
              'if bar_index == 1',
              '    observed_open_id := strategy.opentrades.entry_id(0)',
              '    observed_open_comment := strategy.opentrades.entry_comment(0)',
              '    strategy.order(supplied_exit_id, strategy.short, qty=1, '
              'comment=supplied_exit_comment)',
              'if bar_index == 2',
              '    observed_closed := strategy.closedtrades',
              '    observed_open := strategy.opentrades']
    for name in FIELDS:
        lines += [f'    observed_{name} := strategy.closedtrades.{name}(0)',
                  f'    observed_{name}_length := str.length(observed_{name})',
                  f'    observed_{name}_equal := observed_{name} == supplied_{name}']
    return "\n".join(lines) + "\n"


def _driver() -> str:
    lines = ['#include <iostream>', 'int main() {', '    GeneratedStrategy strategy;',
             '    pineforge::Bar bars[] = {',
             '        {100.0, 101.0, 99.0, 100.0, 1.0, 1704067200000LL},',
             '        {101.0, 102.0, 100.0, 101.0, 1.0, 1704070800000LL},',
             '        {102.0, 103.0, 101.0, 102.0, 1.0, 1704074400000LL}};',
             '    strategy.run(bars, 3);',
             '    if (!strategy.last_error().empty()) {',
             '        std::cerr << strategy.last_error(); return 2; }']
    for name in ("closed", "open", "open_id", "open_comment"):
        lines += [f'    std::cout << "{name}\\t" << strategy.observed_{name} << "\\n";']
    for name in FIELDS:
        for suffix in ("", "_length", "_equal"):
            lines += [f'    std::cout << "{name}{suffix}\\t" '
                      f'<< strategy.observed_{name}{suffix} << "\\n";']
    lines += ['}']
    return "\n".join(lines) + "\n"


def _order_calls(cpp: str) -> list[str]:
    return [line.strip() for line in cpp.splitlines()
            if line.strip().startswith("strategy_order(")]


@pytest.mark.parametrize("comment, spelling", [
    ("supplied_exit_comment", "supplied_exit_comment"),
    ('"closing order"', '"closing order"'),
])
def test_emitted_order_call_forwards_the_comment_once(comment: str, spelling: str) -> None:
    source = _pine_source(_inputs("short")).replace(
        "comment=supplied_exit_comment", f"comment={comment}")
    calls = _order_calls(transpile(source))
    assert len(calls) == 1, calls
    assert calls[0].count(spelling) == 1, calls[0]


def test_order_shape_receipt_has_no_order_comment_key() -> None:
    cpp = transpile(_pine_source(_inputs("short")))
    order_shape = re.search(r'\{\\"call\\":\\"order\\".*?\}', cpp)
    assert order_shape is not None
    assert '\\"comment\\":' not in order_shape.group(0), order_shape.group(0)


@pytest.mark.parametrize("case", ["short", "length256"])
def test_order_comment_is_the_closed_trade_exit_comment(case: str) -> None:
    skip_if_no_engine_lib()
    values = _inputs(case)
    source = _pine_source(values)
    cpp = transpile(source)
    evidence = os.environ.get(EVIDENCE_DIRECTORY)
    if evidence:
        (Path(evidence) / f"{case}.pine").write_text(source)
        (Path(evidence) / f"{case}.cpp").write_text(cpp)
    raw = run_emitted_tu(cpp, _driver(), opt="-O2", label=f"order-comment-{case}")
    observed = dict(line.split("\t", 1) for line in raw.splitlines())
    report = {"case": case, "closed": int(observed["closed"]),
              "open": int(observed["open"]),
              "open_entry_id": observed["open_id"],
              "open_entry_comment": observed["open_comment"],
              "fields": {name: {"expected": values[name],
                                "actual": observed[name],
                                "length": int(observed[name + "_length"]),
                                "expected_length": len(values[name]),
                                "equal": observed[name + "_equal"] == "1"}
                         for name in FIELDS}}
    if evidence:
        (Path(evidence) / f"{case}-observations.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    # Real fills first: one closed trade, nothing left open.
    assert report["closed"] == 1 and report["open"] == 0, report
    assert observed["open_id"] == values["entry_id"], report
    assert observed["open_comment"] == values["entry_comment"], report
    wrong = [name for name, item in report["fields"].items()
             if not (item["actual"] == item["expected"]
                     and item["length"] == item["expected_length"] and item["equal"])]
    assert wrong == [], report
