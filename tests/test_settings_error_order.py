"""The settings metadata visits every input's arguments (defval, options,
minval, maxval, step) in the constructor, which is emitted ahead of the
script body. An error it finds is held until the body is generated, so the
error raised is the script's first in source order, as before 1.1.0 added
that metadata (an unknown name on an earlier line used to lose to one in a
later input's minval)."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

HEAD = '//@version=6\nstrategy("t")\n'


def _first_error(source: str):
    with pytest.raises(CompileError) as err:
        transpile(HEAD + source)
    diagnostic = err.value.diagnostics[0]
    return diagnostic.location.line, diagnostic.message


@pytest.mark.parametrize("argument", [
    "minval = uu", "maxval = uu", "step = uu", "options = [1, uu]",
])
def test_an_earlier_body_error_comes_first(argument):
    line, message = _first_error(
        f'x = zz2 + 1\nlen = input.int(1, "Len", {argument})\nplot(close)\n')
    assert line == 3 and "'zz2'" in message


def test_an_earlier_input_error_comes_first():
    line, message = _first_error(
        'len = input.int(10, "Len", minval = uu)\nx = zz2 + 1\nplot(close)\n')
    assert line == 3 and "'uu'" in message


def test_an_input_error_alone_is_still_raised():
    line, message = _first_error('len = input.int(10, "Len", maxval = uu)\nplot(close)\n')
    assert line == 3 and message.startswith("Unknown variable 'uu'")
