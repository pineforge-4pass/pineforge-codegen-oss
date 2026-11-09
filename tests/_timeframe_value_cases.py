"""Authored scalar cases shared by inventory checks and native log comparisons."""

from dataclasses import dataclass

from pineforge_codegen import _generate
from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.codegen.timeframe_values import TimeframeValues


@dataclass(frozen=True)
class FrameCase:
    name: str
    fast: str
    slow: str
    prelude: str = ""
    expected: tuple[str, ...] = ("240", "1440")
    request: bool = True

    def source(self):
        pine = (
            '//@version=6\nstrategy("Timeframe values", currency = currency.NONE, '
            'initial_capital = 1000000)\n'
            + self.prelude
            + f'chosen_tf = bar_index % 2 == 0 ? {self.fast} : {self.slow}\n'
            'if bar_index < 4\n'
            '    log.info("selected-frame=" + chosen_tf)\n'
        )
        if self.request:
            pine += (
                'got_v = request.security(syminfo.tickerid, chosen_tf, bar_index)\n'
                'qty_v = 1 + got_v % 5\n'
                'if bar_index > 6 and bar_index % 7 == 3\n'
                '    strategy.entry("L", strategy.long, qty = qty_v)\n'
                'if bar_index > 6 and bar_index % 7 == 5\n'
                '    strategy.close("L")\n'
                '// @pf-trace requested=got_v\n'
            )
        return pine


CASES = (
    FrameCase("literal", '"240"', '"1440"'),
    FrameCase("plus", "str.tostring(200 + 40)", "str.tostring(1400 + 40)"),
    FrameCase("string_plus", '"2" + "40"', '"14" + "40"'),
    FrameCase("minus", "str.tostring(280 - 40)", "str.tostring(1480 - 40)"),
    FrameCase("times", "str.tostring(24 * 10)", "str.tostring(144 * 10)"),
    FrameCase("div", "str.tostring(480 / 2)", "str.tostring(2880 / 2)"),
    FrameCase("ratio", "str.tostring((1 / 2) * 480)", "str.tostring((1 / 2) * 2880)"),
    FrameCase("mod", "str.tostring(1240 % 1000)", "str.tostring(3000 % 1560)"),
    FrameCase("negmod", "str.tostring(1000 + (-760 % 1000))",
              "str.tostring(2000 + (-560 % 1000))"),
    FrameCase("unary_plus", "str.tostring(+240)", "str.tostring(+1440)"),
    FrameCase("unary_minus", "str.tostring(-(-240))", "str.tostring(-(-1440))"),
    FrameCase("not", "str.tostring(not false ? 240 : 1)",
              "str.tostring(not true ? 1 : 1440)"),
    FrameCase("equal", "str.tostring(240 == 240 ? 240 : 1)",
              "str.tostring(240 == 1440 ? 1 : 1440)"),
    FrameCase("unequal", "str.tostring(240 != 1440 ? 240 : 1)",
              "str.tostring(1440 != 1440 ? 1 : 1440)"),
    FrameCase("less", "str.tostring(240 < 1440 ? 240 : 1)",
              "str.tostring(240 > 1440 ? 1 : 1440)"),
    FrameCase("less_equal", "str.tostring(240 <= 240 ? 240 : 1)",
              "str.tostring(1440 <= 240 ? 1 : 1440)"),
    FrameCase("greater", "str.tostring(1440 > 240 ? 240 : 1)",
              "str.tostring(240 > 1440 ? 1 : 1440)"),
    FrameCase("greater_equal", "str.tostring(240 >= 240 ? 240 : 1)",
              "str.tostring(240 >= 1440 ? 1 : 1440)"),
    FrameCase("and", "str.tostring((true and false) ? 1 : 240)",
              "str.tostring((true and true) ? 1440 : 1)"),
    FrameCase("or", "str.tostring((false or true) ? 240 : 1)",
              "str.tostring((false or false) ? 1 : 1440)"),
    FrameCase("andor", "str.tostring((true and false) ? 1 : 240)",
              "str.tostring((false or true) ? 1440 : 1)"),
    FrameCase("intcast", "str.tostring(int(481 / 2))", "str.tostring(int(2881 / 2))"),
    FrameCase("int_trunc", "str.tostring(int(240.9))", "str.tostring(int(1440.9))"),
    FrameCase("floatcast", "str.tostring(float(240))", "str.tostring(float(1440))"),
    FrameCase("boolcast", "str.tostring(bool(2) ? 240 : 0)",
              "str.tostring(bool(2) ? 1440 : 0)"),
    FrameCase("integral_float", "str.tostring(240.0)", "str.tostring(1440.0)"),
    FrameCase("tostring", "str.tostring(240)", "str.tostring(1440)"),
    FrameCase("format240", 'str.format("{0}", 240)', '"1440"'),
    # Grouped text is not a valid timeframe. Observe its scalar C++ rendering
    # without registering it; the separate invalid-arm row pins the refusal.
    FrameCase("format_grouping", 'str.format("{0}", 1440)', 'str.format("{0}", 2880)',
              expected=("1,440", "2,880"), request=False),
    FrameCase("helper_default", "frame_text()", "frame_text(1440)",
              "frame_text(int amount_v = 240) =>\n    str.tostring(amount_v)\n"),
    FrameCase("let_chain", "str.tostring(base_v)", "str.tostring(wide_v)",
              "base_v = 240\nwide_v = base_v * 6\n"),
    FrameCase("helper_let_chain", "frame_text(240)", "frame_text(1440)",
              "frame_text(int amount_v) =>\n    base_v = amount_v / 2\n"
              "    wide_v = base_v * 2\n    str.tostring(wide_v)\n"),
)
BY_NAME = {case.name: case for case in CASES}


def inventory(pine):
    """Use the real bound AST; return the interpreter and authored expression."""
    emitter, ctx, _cpp, _diagnostics, _sites = _generate(
        pine, check_support=True, filename="<timeframe-values>")
    declaration = next(stmt for stmt in ctx.ast.body
                       if isinstance(stmt, VarDecl) and stmt.name == "chosen_tf")
    return TimeframeValues(emitter), declaration.value


def values(evaluator, expression):
    return tuple(node.value for node in evaluator.strings(expression))


def quota_source(count):
    # Days keep every arm coarser than the native harness's 15-minute feed.
    arms = "".join(f'    {index} => str.tostring({index + 1}) + "D"\n'
                   for index in range(count - 1))
    return (
        '//@version=6\nstrategy("Reached timeframe inventory", currency = currency.NONE)\n'
        f'chosen_tf = switch bar_index % {count}\n' + arms
        + f'    => str.tostring({count}) + "D"\n'
        'log.info("quota-frame=" + chosen_tf)\n'
        'got_v = request.security(syminfo.tickerid, chosen_tf, bar_index)\n'
        'if bar_index % 7 == 3\n'
        '    strategy.entry("L", strategy.long, qty = 1 + int(nz(got_v)) % 5)\n'
        'if bar_index % 7 == 5\n'
        '    strategy.close("L")\n'
        '// @pf-trace requested=got_v\n'
    )
