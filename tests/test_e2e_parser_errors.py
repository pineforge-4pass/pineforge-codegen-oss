"""The app's transpile_json interface rejects Pine syntax errors at their source location."""

from __future__ import annotations

import pytest

from tests._e2e import transpile_json


@pytest.mark.parametrize(
    ("bad_statement", "column", "message"),
    [
        ("x = 1 2", 7, "Unexpected token"),
        ('s = "abc" "def"', 11, "Unexpected token"),
        ("x = (1 + )", 10, "Unexpected token"),
    ],
)
def test_syntax_error_is_a_located_refusal(tmp_path, bad_statement, column, message):
    source = ("//@version=6\nstrategy(\"parser-error\", overlay=true)\n"
              f"{bad_statement}\n"
              "if bar_index == 2\n"
              '    strategy.entry("L", strategy.long)\n')
    pine = tmp_path / "syntax_error.pine"
    pine.write_text(source)
    result = transpile_json(pine)
    assert result["ok"] is False, result.get("diagnostics")
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert len(errors) == 1, result["diagnostics"]
    assert (errors[0]["line"], errors[0]["col"]) == (3, column)
    assert message in errors[0]["message"]
    print(f"parser refusal: 3:{column} {errors[0]['message']}")
