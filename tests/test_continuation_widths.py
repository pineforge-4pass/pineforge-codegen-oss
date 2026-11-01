"""TradingView operand and call wraps at each measured indentation width."""

from pathlib import Path

import pytest

from tests._e2e import transpile_json


_HEADER = (
    '//@version=6\n'
    'strategy("p1", overlay = true, pyramiding = 0, '
    'default_qty_type = strategy.fixed, default_qty_value = 1, '
    'initial_capital = 1000000000, process_orders_on_close = false)\n'
)
_TAIL = (
    'plot(a)\n'
    'if bar_index > 6 and bar_index % 7 == 3\n'
    '    strategy.entry("L", strategy.long, qty = 1)\n'
    'if bar_index > 6 and bar_index % 7 == 5\n'
    '    strategy.close("L")\n'
    'plot(close, "p")\n'
)
_CONTEXTS = {
    "top": ("float a = close +\n", "open\n", 4),
    "block": (
        "float a = 0.0\nif bar_index % 2 == 0\n    a := close +\n",
        "open\n", 6,
    ),
    "call": ("float a = math.max(close,\n", "open)\n", 4),
    "decl_call_block": (
        "float a = 0.0\nif bar_index % 2 == 0\n    a := math.max(close,\n",
        "open)\n", 6,
    ),
}

# Accepted rows are explicit unchanged pins. Refused rows fail before the
# change: either the lexer accepted them or it located the preceding operator.
_CASES = [
    pytest.param("top", 1, True, id="cont_top_1"),  # 1 % 4 = 1: operand joins.
    pytest.param("top", 2, True, id="cont_top_2"),  # 2 % 4 = 2: operand joins.
    pytest.param("top", 3, True, id="cont_top_3"),  # 3 % 4 = 3: operand joins.
    pytest.param("top", 4, False, id="cont_top_4"),  # 4 % 4 = 0: structural.
    pytest.param("top", 5, True, id="cont_top_5"),  # 5 % 4 = 1: operand joins.
    pytest.param("top", 6, True, id="cont_top_6"),  # 6 % 4 = 2: operand joins.
    pytest.param("top", 7, True, id="cont_top_7"),  # 7 % 4 = 3: operand joins.
    pytest.param("top", 8, False, id="cont_top_8"),  # 8 % 4 = 0: structural.
    pytest.param("top", 9, True, id="cont_top_9"),  # 9 % 4 = 1: operand joins.
    pytest.param("top", 11, True, id="cont_top_11"),  # 11 % 4 = 3: operand joins.
    pytest.param("top", 12, False, id="cont_top_12"),  # 12 % 4 = 0: structural.
    pytest.param("top", 13, True, id="cont_top_13"),  # 13 % 4 = 1: operand joins.
    pytest.param("top", 15, True, id="cont_top_15"),  # 15 % 4 = 3: operand joins.
    pytest.param("top", 16, False, id="cont_top_16"),  # 16 % 4 = 0: structural.
    pytest.param("top", 17, True, id="cont_top_17"),  # 17 % 4 = 1: operand joins.
    pytest.param("top", 20, False, id="cont_top_20"),  # 20 % 4 = 0: structural.
    pytest.param("top", 24, False, id="cont_top_24"),  # 24 % 4 = 0: structural.
    pytest.param("block", 5, True, id="cont_block_5"),  # 5 % 4 = 1: operand joins.
    pytest.param("block", 6, True, id="cont_block_6"),  # 6 % 4 = 2: operand joins.
    pytest.param("block", 8, False, id="cont_block_8"),  # 8 % 4 = 0: structural.
    pytest.param("block", 9, True, id="cont_block_9"),  # 9 % 4 = 1: operand joins.
    pytest.param("block", 12, False, id="cont_block_12"),  # 12 % 4 = 0: structural.
    pytest.param("block", 13, True, id="cont_block_13"),  # 13 % 4 = 1: operand joins.
    pytest.param("block", 16, False, id="cont_block_16"),  # 16 % 4 = 0: structural.
    pytest.param("block", 17, True, id="cont_block_17"),  # 17 % 4 = 1: operand joins.
    pytest.param("block", 20, False, id="cont_block_20"),  # 20 % 4 = 0: structural.
    pytest.param("call", 0, True, id="cont_call_0"),  # Open call ignores width 0.
    pytest.param("call", 1, True, id="cont_call_1"),  # Open call ignores width 1.
    pytest.param("call", 4, True, id="cont_call_4"),  # Open call ignores width 4.
    pytest.param("call", 8, True, id="cont_call_8"),  # Open call ignores width 8.
    pytest.param("call", 12, True, id="cont_call_12"),  # Open call ignores width 12.
    pytest.param("call", 16, True, id="cont_call_16"),  # Open call ignores width 16.
    pytest.param("decl_call_block", 0, True, id="cont_decl_call_block_0"),  # Open call ignores width 0.
    pytest.param("decl_call_block", 2, True, id="cont_decl_call_block_2"),  # Open call ignores width 2.
    pytest.param("decl_call_block", 4, True, id="cont_decl_call_block_4"),  # Open call ignores width 4.
    pytest.param("decl_call_block", 8, True, id="cont_decl_call_block_8"),  # Open call ignores width 8.
    pytest.param("decl_call_block", 12, True, id="cont_decl_call_block_12"),  # Open call ignores width 12.
    pytest.param("decl_call_block", 16, True, id="cont_decl_call_block_16"),  # Open call ignores width 16.
]


@pytest.mark.parametrize("context, width, accepted", _CASES)
def test_measured_continuation_width(
    tmp_path: Path, context: str, width: int, accepted: bool,
) -> None:
    prefix, operand, line = _CONTEXTS[context]
    pine = tmp_path / f"cont_{context}_{width}.pine"
    pine.write_text(_HEADER + prefix + " " * width + operand + _TAIL, encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"] is accepted, result
    if accepted:
        assert result["cpp"]
    else:
        (diagnostic,) = result["diagnostics"]
        assert diagnostic["severity"] == "error"
        assert diagnostic["code"] == "PF-E0038"
        # Two header lines plus the context put the operand on line 4 or 6.
        assert (diagnostic["line"], diagnostic["col"]) == (line, width + 1)
