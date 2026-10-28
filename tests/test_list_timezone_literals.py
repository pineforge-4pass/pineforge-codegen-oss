"""The timezone inventory uses parsed call arguments and lexical constants."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.list_timezone_literals import main


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "list_timezone_literals.py"


def test_timezone_inventory_cli_lists_literals_and_dynamic_sites(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second" / "nested"
    first.mkdir()
    second.mkdir(parents=True)
    (first / "a.pine").write_text(
        '//@version=6\n'
        'strategy("Literal zones")\n'
        'const string PREFIX = "UTC"\n'
        'const string ZONE = PREFIX + "+3"\n'
        'a = timestamp(ZONE, 2024, 1, 15, 8, 0)\n'
        'b = hour(time, timezone="America/New_York")\n'
        'c = minute(timezone="Europe/" + "London")\n'
        'd = timestamp("01 Jan 2024 00:00 UTC")\n'
        '// timestamp("Fake/Comment", 2024, 1, 15)\n'
        'text = \'hour(time, "Fake/String")\'\n'
        'e = dayofweek(time, "GMT+8")\n'
        'f = year(time, "UTC-5")\n'
        'g = month(time, "UTC")\n'
        'h = dayofmonth(time, "")\n'
        'i = second(time, "EST5EDT")\n'
        'j = weekofyear(time, "Etc/UTC")\n'
        'nested = hour(timestamp("UTC+3", 2024, 1, 15), "Europe/London")\n'
        'k = timestamp(day=15, timezone="GMT+0530", month=1, year=2024)\n'
        'methodValue = object.hour(time, "Fake/Method")\n'
        'year = year(time, "UTC+3")\n',
        encoding="utf-8",
    )
    (first / "empty.pine").write_text(
        '//@version=6\nstrategy("No calendar calls")\nplot(close)\n',
        encoding="utf-8",
    )
    (second / "b.pine").write_text(
        '//@version=6\n'
        'strategy("Dynamic zones")\n'
        'chosen = input.string("Asia/Taipei")\n'
        'changed = "Europe/London"\n'
        'changed := "UTC"\n'
        'var string persistent = "UTC+3"\n'
        'field = hour(time, chosen)\n'
        'stamp = timestamp(chosen, 2024, 1, 15, 8, 0)\n'
        'other = dayofmonth(time, changed)\n'
        'other2 = weekofyear(time, persistent)\n'
        'implicit = timestamp(2024, 1, 15, 8, 0)\n'
        'barHour = hour(time)\n'
        'exchange = minute(time, syminfo.timezone)\n'
        'zone = "GMT+8"\n'
        'read(string zone) =>\n'
        '    hour(time, zone)\n'
        'blockValue = if close > open\n'
        '    zone = "Europe/London"\n'
        '    year(time, zone)\n'
        'globalValue = hour(time, zone)\n'
        'utc = true ? "UTC+3" : chosen\n'
        'folded = timestamp(utc, 2024, 1, 15, 8, 0)\n'
        'multiline = hour(\n'
        '    time,\n'
        '    chosen)\n'
        'defaultRead(int start=timestamp(zone, 2024, 1, 15)) => start\n'
        'rule = switch "winter"\n'
        '    "winter" => "UTC-5"\n'
        '    => "UTC+3"\n'
        'switchValue = second(time, rule)\n'
        'condition = (not false and "x" == "x") ? "UTC+3" : "UTC-5"\n'
        'boolValue = minute(time, condition)\n',
        encoding="utf-8",
    )
    # This file is outside the requested .pine suffix and must not be parsed.
    (first / "notes.txt").write_text(
        'hour(time, "Fake/Notes")\n', encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "first", "second", "second/nested/b.pine"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    # All three scripts parse; the overlapping file argument is counted once.
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "scripts": [
            {
                "path": "first/a.pine",
                "zones": [
                    # Line 5 reads ZONE = PREFIX + "+3" = "UTC+3".
                    {"function": "timestamp", "line": 5, "kind": "literal", "value": "UTC+3"},
                    # Line 6 binds the timezone keyword after positional time.
                    {"function": "hour", "line": 6, "kind": "literal", "value": "America/New_York"},
                    # Line 7 concatenates the two strings with no time argument.
                    {"function": "minute", "line": 7, "kind": "literal", "value": "Europe/London"},
                    # Line 11's second argument is the literal GMT offset.
                    {"function": "dayofweek", "line": 11, "kind": "literal", "value": "GMT+8"},
                    # Line 12's second argument is the literal westward offset.
                    {"function": "year", "line": 12, "kind": "literal", "value": "UTC-5"},
                    # Line 13 explicitly supplies UTC rather than an exchange default.
                    {"function": "month", "line": 13, "kind": "literal", "value": "UTC"},
                    # Line 14 supplies an empty string, which remains in the inventory.
                    {"function": "dayofmonth", "line": 14, "kind": "literal", "value": ""},
                    # Line 15 supplies the bare POSIX DST spelling.
                    {"function": "second", "line": 15, "kind": "literal", "value": "EST5EDT"},
                    # Line 16 supplies the UTC alias without normalization.
                    {"function": "weekofyear", "line": 16, "kind": "literal", "value": "Etc/UTC"},
                    # The outer hour starts before the nested timestamp on line 17.
                    {"function": "hour", "line": 17, "kind": "literal", "value": "Europe/London"},
                    # The inner timestamp's first argument supplies its own zone.
                    {"function": "timestamp", "line": 17, "kind": "literal", "value": "UTC+3"},
                    # Line 18 binds timezone by name despite reordered calendar keywords.
                    {"function": "timestamp", "line": 18, "kind": "literal", "value": "GMT+0530"},
                    # Line 20 calls the builtin despite its same-named variable.
                    {"function": "year", "line": 20, "kind": "literal", "value": "UTC+3"},
                ],
            },
            # A parsed script without calendar calls still has an explicit empty row.
            {"path": "first/empty.pine", "zones": []},
            {
                "path": "second/nested/b.pine",
                "zones": [
                    # Line 7's input can be overridden, so its default is not a constant.
                    {"function": "hour", "line": 7, "kind": "dynamic"},
                    # The same input in timestamp's first slot remains dynamic.
                    {"function": "timestamp", "line": 8, "kind": "dynamic"},
                    # Line 9 reads a reassigned name, not its initial literal.
                    {"function": "dayofmonth", "line": 9, "kind": "dynamic"},
                    # Line 10 reads persistent storage rather than a constant declaration.
                    {"function": "weekofyear", "line": 10, "kind": "dynamic"},
                    # The numeric overload on line 11 depends on the exchange zone.
                    {"function": "timestamp", "line": 11, "kind": "dynamic"},
                    # The one-argument field read on line 12 also uses the exchange.
                    {"function": "hour", "line": 12, "kind": "dynamic"},
                    # Line 13 explicitly reads runtime symbol metadata.
                    {"function": "minute", "line": 13, "kind": "dynamic"},
                    # The helper parameter on line 16 shadows the global literal.
                    {"function": "hour", "line": 16, "kind": "dynamic"},
                    # The block's own stable declaration binds this read on line 19.
                    {"function": "year", "line": 19, "kind": "literal", "value": "Europe/London"},
                    # Leaving the block restores the global binding on line 20.
                    {"function": "hour", "line": 20, "kind": "literal", "value": "GMT+8"},
                    # A true constant condition selects UTC+3 without using the input.
                    {"function": "timestamp", "line": 22, "kind": "literal", "value": "UTC+3"},
                    # The dynamic expression is on line 25, after its call starts.
                    {"function": "hour", "line": 25, "kind": "dynamic"},
                    # Parameter defaults read the global constant on line 26.
                    {"function": "timestamp", "line": 26, "kind": "literal", "value": "GMT+8"},
                    # The switch subject matches its winter arm, selecting UTC-5.
                    {"function": "second", "line": 30, "kind": "literal", "value": "UTC-5"},
                    # Both Boolean operands are true, selecting the UTC+3 arm.
                    {"function": "minute", "line": 32, "kind": "literal", "value": "UTC+3"},
                ],
            },
        ],
        # The unique zone arguments sort lexically; text and input defaults add none.
        "literal_zones": [
            "", "America/New_York", "EST5EDT", "Etc/UTC", "Europe/London",
            "GMT+0530", "GMT+8", "UTC", "UTC+3", "UTC-5",
        ],
    }


def test_timezone_inventory_reports_parse_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("bad.pine").write_text(
        '//@version=6\nstrategy("Bad syntax")\n@\n', encoding="utf-8",
    )
    # The lexer diagnoses the literal @ at line 3, column 1, so the scan fails.
    assert main(["bad.pine"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "scripts": [{
            "path": "bad.pine", "zones": [],
            # CompileError includes the lexer's filename, position and exact text.
            "error": "bad.pine:3:1: Unexpected character: '@'",
        }],
        # A failed parse cannot contribute a proven literal.
        "literal_zones": [],
    }
