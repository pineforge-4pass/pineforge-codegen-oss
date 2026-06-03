"""Static validation of timeframe literal format for request.security and
request.security_lower_tf at codegen time.

Codegen does NOT know ``input_tf`` — that is an engine-side concern. Here we
only verify the string literal is a well-formed Pine TF token shape:
``<positive-int>[S|H|D|W|M]``. Variable / input.timeframe / function-call
timeframes are runtime-resolved and intentionally skipped.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.support_checker import SupportChecker


PRELUDE = '//@version=6\nstrategy("t")\n'


# -- _validate_pine_tf_literal unit tests ----------------------------------

@pytest.mark.parametrize(
    "tf",
    ["1", "3", "5", "15", "30", "45", "60", "120", "240", "480", "1440",
     "1S", "5S", "10S", "15S", "30S", "45S",
     "1H", "2H", "3H", "4H",
     "1D", "1W", "1M", "3M", "12M",
     # Bare suffix shorthand accepted by Pine: "D" == "1D", etc.
     "D", "W", "M", "S", "H"],
)
def test_validate_pine_tf_literal_accepts_valid(tf: str) -> None:
    assert SupportChecker._validate_pine_tf_literal(tf) is None


@pytest.mark.parametrize(
    "tf",
    ["", "abc", "1.5", "1.5H", "1X", "1Q", "-15", "0", "0H",
     "1HH", "h", "d", "1m"],
)
def test_validate_pine_tf_literal_rejects_invalid(tf: str) -> None:
    assert SupportChecker._validate_pine_tf_literal(tf) is not None


# -- request.security integration ------------------------------------------

def test_security_invalid_tf_literal_rejected():
    src = PRELUDE + 'x = request.security("ETHUSDT", "abc", close)\n'
    with pytest.raises(Exception, match="invalid timeframe literal"):
        transpile(src)


def test_security_lower_tf_invalid_literal_rejected():
    src = PRELUDE + 'x = request.security_lower_tf(syminfo.tickerid, "BAD", close)\n'
    with pytest.raises(Exception, match="invalid timeframe literal"):
        transpile(src)


@pytest.mark.parametrize(
    "tf",
    ['"1"', '"5"', '"15"', '"60"', '"1H"', '"1D"', '"1W"', '"1M"', '"15S"'],
)
def test_security_valid_tf_literals_accepted(tf: str):
    src = PRELUDE + f"x = request.security(syminfo.tickerid, {tf}, close)\n"
    transpile(src)  # no exception


def test_security_variable_tf_skipped():
    # Runtime tf — codegen can't validate; engine catches it.
    src = (
        PRELUDE
        + 'htf = input.timeframe("1H")\n'
        + "x = request.security(syminfo.tickerid, htf, close)\n"
    )
    transpile(src)  # no exception


def test_security_empty_tf_rejected():
    src = PRELUDE + 'x = request.security(syminfo.tickerid, "", close)\n'
    with pytest.raises(Exception, match="invalid timeframe literal|empty"):
        transpile(src)


def test_security_lower_tf_valid_literal_accepted():
    src = (
        PRELUDE
        + 'a = request.security_lower_tf(syminfo.tickerid, "1", close)\n'
    )
    transpile(src)  # no exception


def test_security_tf_kwarg_form_validated():
    src = (
        PRELUDE
        + 'x = request.security(symbol=syminfo.tickerid, timeframe="1Q", '
          'expression=close)\n'
    )
    with pytest.raises(Exception, match="invalid timeframe literal"):
        transpile(src)
