"""Frozen-source inventory for counted-loop C++ emission.

The hashes pin generated C++ for these sources.
"""

from textwrap import dedent


MAIN_COMMIT = "c1cc532b0c486db81ac9253d8a86445a0385b406"


def _script(body: str) -> str:
    return '//@version=6\nstrategy("Loop emission baseline", currency=currency.NONE)\n' + dedent(body).lstrip()


# Outer-value controls use a distinct inner counter: a same-named counter in
# end/step reads that counter. Native value probes cover those spellings.
LOOP_SOURCES = {
    "literal_ascending": _script("""
        total = 0.0
        for walkIndex = 0 to 4
            total += walkIndex + 1
        plot(total)
    """),
    "literal_descending": _script("""
        total = 0.0
        for walkIndex = 4 to 0
            total += walkIndex + 1
        plot(total)
    """),
    "explicit_positive_step": _script("""
        total = 0.0
        for walkIndex = 5 to 1 by 2
            total += walkIndex
        plot(total)
    """),
    "explicit_negative_step": _script("""
        total = 0.0
        for walkIndex = 5 to 1 by -2
            total += walkIndex
        plot(total)
    """),
    "explicit_zero_step": _script("""
        total = 0.0
        for walkIndex = 0 to 3 by 0
            total += walkIndex
        plot(total)
    """),
    "input_bounds_and_step": _script("""
        first = input.int(1)
        last = input.int(5)
        stride = input.int(2)
        total = 0.0
        for walkIndex = first to last by stride
            total += walkIndex
        plot(total)
    """),
    "body_modified_float_end": _script("""
        float limit = 3.0
        total = 0.0
        for walkIndex = 0 to limit
            limit := limit - 1.0
            total += 1
        plot(total)
    """),
    "body_modified_int_step": _script("""
        int stride = 1
        total = 0.0
        for walkIndex = 0 to 4 by stride
            stride := 2
            total += walkIndex + 1
        plot(total)
    """),
    "helper_bounds": _script("""
        bound(int basis) => basis + 1
        total = 0.0
        for walkIndex = bound(0) to bound(4) by bound(1)
            total += walkIndex
        plot(total)
    """),
    "break_and_continue": _script("""
        total = 0.0
        for walkIndex = 0 to 8 by 1
            if walkIndex == 2
                continue
            if walkIndex == 5
                break
            total += walkIndex
        plot(total)
    """),
    "nested_reused_counter": _script("""
        total = 0.0
        for walkIndex = 0 to 2
            for walkIndex = 1 to 3 by 2
                total += walkIndex
            total += walkIndex
        plot(total)
    """),
    "nested_outer_counter_end": _script("""
        total = 0.0
        for walkIndex = 1 to 3
            for innerIndex = 0 to walkIndex
                total += innerIndex
        plot(total)
    """),
    "sibling_counter_scopes": _script("""
        total = 0.0
        for walkIndex = 0 to 2
            total += walkIndex
        for walkIndex = 2 to 0
            total += walkIndex
        plot(total)
    """),
    "for_in_value_and_index": _script("""
        values = array.from(1.0, 2.0, 3.0)
        total = 0.0
        for itemValue in values
            total += itemValue
        for [itemIndex, itemValue] in values
            total += itemIndex * itemValue
        plot(total)
    """),
    "for_in_map": _script("""
        values = map.new<string, float>()
        values.put("A", 2.0)
        total = 0.0
        for [itemKey, itemValue] in values
            total += itemValue
        plot(total)
    """),
    "function_loop_value": _script("""
        measure(int last) =>
            for walkIndex = 0 to last
                walkIndex + 1
        plot(measure(3))
    """),
    "outer_parameter_end": _script("""
        measure(int walkIndex) =>
            total = 0.0
            for innerIndex = 0 to walkIndex
                total += innerIndex
            total
        plot(measure(3))
    """),
    "outer_parameter_step": _script("""
        measure(int walkIndex) =>
            total = 0.0
            for innerIndex = 0 to 4 by walkIndex
                total += innerIndex
            total
        plot(measure(2))
    """),
    "outer_series_end": _script("""
        lag(series float value) => value[1]
        walkIndex = close + 2
        prior = lag(walkIndex)
        total = 0.0
        for innerIndex = 0 to walkIndex
            total += innerIndex
        plot(prior + total)
    """),
    "outer_block_local_end": _script("""
        total = 0.0
        if bar_index >= 0
            int walkIndex = bar_index % 3
            for innerIndex = 0 to walkIndex
                total += innerIndex
        plot(total)
    """),
    "same_name_in_sibling_block": _script("""
        total = 0.0
        if bar_index > 0
            int walkIndex = 3
            total += walkIndex
        if bar_index >= 0
            for walkIndex = 0 to 3
                total += walkIndex
        plot(total)
    """),
    "literal_end_at_function_tail": _script("""
        measure() =>
            total = 0.0
            for walkIndex = 3 to 0 by 1
                total += walkIndex
            total
        plot(measure())
    """),
}

# Pine int starts whose C++ expressions can be doubles or unresolved. Keep
# these sources in the full-C++ inventory as well as the counter-type checks.
INT_START_CASES = {
    "array_size": ("a = array.from(2, 4, 6)\n", "", "array.size(a) - 1"),
    "array_method_size": ("a = array.from(2, 4, 6)\n", "", "a.size() - 1"),
    "open_trade_count": ("", "", "strategy.opentrades - 1"),
    "closed_trade_count": ("", "", "strategy.closedtrades - 1"),
    "math_max_int": ("", "", "math.max(2, 4)"),
    "math_min_int": ("", "", "math.min(2, 4)"),
    "math_abs_int": ("", "", "math.abs(-4)"),
    "math_round_int": ("", "", "math.round(4)"),
    "math_floor_int": ("", "", "math.floor(4)"),
    "math_ceil_int": ("", "", "math.ceil(4)"),
    "bars_since": ("", "", "ta.barssince(close > open)"),
    "ternary_na_last": ("", "", "(close > open ? 4 : na)"),
    "ternary_na_first": ("", "", "(close > open ? na : 4)"),
    "local_array_size": ("a = array.from(2, 4, 6)\n", "n = array.size(a)\n", "n"),
    "local_math_max_int": ("", "n = math.max(2, 4)\n", "n"),
    "local_math_min_int": ("", "n = math.min(2, 4)\n", "n"),
    "local_math_abs_int": ("", "n = math.abs(-4)\n", "n"),
}


def _int_start_script(prefix: str, locals_: str, start: str, context: str) -> str:
    # A TA call cannot sit in a requested helper's loop header. Compute it
    # once before the loop, as that route requires; the chart row calls it
    # directly in the header.
    if context == "security" and start.startswith("ta."):
        locals_ += f"first = {start}\n"
        start = "first"
    body = (
        locals_ + "float acc = 0.0\n"
        f"for walkIndex = {start} to 0\n"
        "    acc += walkIndex\n"
    )
    if context == "security" or locals_:
        call = "measure()"
        if context == "security":
            call = f"request.security(syminfo.tickerid, timeframe.period, {call})"
        body = (
            "measure() =>\n"
            + "".join(f"    {line}\n" for line in body.splitlines())
            + "    acc\n"
            + f"plot({call})\n"
        )
    else:
        body += "plot(acc)\n"
    return _script(prefix + body)


INT_START_SOURCES = {
    f"{context}_{name}": _int_start_script(prefix, locals_, start, context)
    for name, (prefix, locals_, start) in INT_START_CASES.items()
    for context in ("chart", "security")
}
LOOP_SOURCES.update(INT_START_SOURCES)

# TradingView s1/s2 stop instead of executing these loops. They now have
# native stop regressions rather than assertions preserving main's wrong value.
STOP_SOURCES = {
    name: LOOP_SOURCES[name]
    for name in ("explicit_negative_step", "explicit_zero_step")
}
BASELINE_SOURCES = {
    name: source for name, source in LOOP_SOURCES.items()
    if name not in STOP_SOURCES
}
