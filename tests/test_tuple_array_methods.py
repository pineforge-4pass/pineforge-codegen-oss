"""Tuple array bindings retain the namespace-form array lowering."""

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("names", [("a", "b"), ("prices", "extremes")])
@pytest.mark.parametrize("local", [False, True])
def test_tuple_array_methods_match_namespace_calls(names, local):
    left, right = names

    def source(expression):
        statements = f"[{left}, {right}] = pair()\nresult = {expression}\n"
        if local:
            statements = ("measure() =>\n"
                          + "".join("    " + line + "\n"
                                    for line in statements.splitlines())
                          + "    result\nvalue = measure()\nplot(value)\n")
        else:
            statements += "plot(result)\n"
        return (
            '//@version=6\nstrategy("Tuple arrays")\n'
            'pair() => [array.from(close, open), array.from(high, low)]\n'
            + statements
        )

    cpp = transpile(source(f"{left}.get(0) + {right}.max()"))
    control = transpile(source(f"array.get({left}, 0) + array.max({right})"))
    result = lambda text: next(line.strip() for line in text.splitlines()
                               if "result = " in line and " + " in line)
    assert result(cpp) == result(control)
    assert f"{left}.get(" not in cpp
    assert f"{right}.max(" not in cpp
