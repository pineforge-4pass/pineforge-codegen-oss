"""Derived carriers of the measured fractional_input loop, not new TV tapes."""

from textwrap import dedent


def _source(body: str) -> str:
    return (
        '//@version=6\nstrategy("Fractional start carrier", '
        'currency=currency.NONE, initial_capital=1000000000000)\n'
        + 'n = input.int(5, "Numerator")\n'
        + dedent(body).lstrip()
        + 'if bar_index > 6 and bar_index % 7 == 3\n'
        + '    strategy.entry("L", strategy.long, qty=acc)\n'
        + 'if bar_index > 6 and bar_index % 7 == 5\n'
        + '    strategy.close("L")\n'
    )


# Each loop sums the start, start + 1 and start + 2 with the default step.
# Its oracle is fractional_input in tradingview_quantities.json: 10.5.
# The native tests name any carrier whose storage still loses that fraction.
CARRIER_SOURCES = {
    "reassignment": _source("""
        s = n
        s := s / 2
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
    "ternary_arm": _source("""
        cond = true
        s = cond ? n / 2 : n / 2
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
    "if_arm": _source("""
        cond = true
        s = if cond
            n / 2
        else
            n / 2
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
    "switch_arm": _source("""
        cond = true
        s = switch cond
            true => n / 2
            => n / 2
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
    "helper_return": _source("""
        start_value() => n / 2
        s = start_value()
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
    "helper_parameter": _source("""
        count_from(s) =>
            float subtotal = 0.0
            for i = s to s + 2
                subtotal += i
            subtotal
        float acc = count_from(n / 2)
    """),
    "tuple_element": _source("""
        pair() => [n / 2, 1]
        [a, b] = pair()
        float acc = 0.0
        for i = a to a + 2
            acc += i
    """),
    "array_element": _source("""
        arr = array.from(n / 2)
        s = array.get(arr, 0)
        float acc = 0.0
        for i = s to s + 2
            acc += i
    """),
}

CARRIER_FAMILIES = {
    name: "int" if name in {"reassignment", "helper_parameter"} else "double"
    for name in CARRIER_SOURCES
}
KNOWN_CARRIER_GAPS = {"reassignment", "helper_parameter"}
