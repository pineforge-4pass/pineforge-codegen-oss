"""A callable's persistent string can share a spelling with a Pine namespace."""

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests.test_issue_424_callable_var_string import DRIVER


def test_namespace_named_callable_var_string_in_seconds():
    source = '''//@version=6
strategy("example")
f(string a) =>
    var string size = a
    timeframe.in_seconds(size)
x = f("60")
plot(x)
'''
    output = run_emitted_tu(transpile(source), DRIVER, opt="-O0", label="size in seconds")
    assert output.strip() == "3600"
