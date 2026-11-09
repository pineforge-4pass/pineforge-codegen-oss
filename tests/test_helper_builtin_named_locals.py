"""Requested-helper declarations shadow bare builtin variables."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.tables import BUILTIN_VARS
from pineforge_codegen.codegen.tables import BAR_BUILTINS, BAR_FIELDS, TIME_FIELD_EXPRS
from pineforge_codegen.codegen.visit_expr import _REALTIME_ONLY_VARS
from pineforge_codegen.lexer import KEYWORDS
from pineforge_codegen.signatures import BUILTIN_VARIABLES
from tests._e2e import reference_codegen, transpile_json
from tests.test_helper_series_words import (
    _REFERENCE, _STORE_CALL, _references, helper_source,
)


CPP_REFERENCE = "7d7681dcef6918f0cb304831320e5d8e5b545285"
_BARE_BUILTINS = (
    set(BUILTIN_VARS) | set(BAR_BUILTINS) | set(BAR_FIELDS)
    | set(TIME_FIELD_EXPRS) | set(_REALTIME_ONLY_VARS)
    | {name for name in BUILTIN_VARIABLES if "." not in name}
)
# `na` is a literal token, so it cannot declare an identifier. Dotted
# namespace members are expressions rather than bare declaration names.
_LOCAL_NAMES = sorted(_BARE_BUILTINS - KEYWORDS.keys())


def _source(name, *, string, persistent):
    source = helper_source(name, string=string)
    if persistent:
        source = source.replace(f"    {name} = ", f"    var {name} = ", 1)
    return source


@pytest.mark.parametrize("name", _LOCAL_NAMES)
@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
@pytest.mark.parametrize("capacity", [None, 100])
@pytest.mark.parametrize("persistent", [False, True], ids=["local", "var"])
def test_builtin_named_helper_history_matches_renamed_control(
    name, string, capacity, persistent,
):
    # A string price-field local read with history keeps the base's refusal.
    from tests.test_builtin_named_locals_price_refusal import (
        PRICE_FIELD_NAMES, assert_price_refusal,
    )
    if string and not persistent and name in PRICE_FIELD_NAMES:
        assert_price_refusal(
            _source(name, string=string, persistent=persistent), history_capacity=capacity,
        )
        return
    cpp = transpile(
        _source(name, string=string, persistent=persistent),
        history_capacity=capacity,
    )
    control = transpile(
        _source("aa", string=string, persistent=persistent),
        history_capacity=capacity,
    )
    assert _references(cpp) == _references(control)
    store = "_security_helper_series_str_" if string else "_security_helper_series_"
    references = _REFERENCE.findall(cpp)
    # Both the current-value read and the history read use the local's store.
    # This also rules out a numeric-map reference for a string local.
    assert set(_STORE_CALL.findall(cpp)) == {f"{store}.try_emplace("}
    assert all(reference.startswith(f"({store}.") for reference in references)
    assert re.search(_REFERENCE.pattern + r"\[0\]", cpp), cpp
    # An explicit capacity wraps even a literal history offset in a check.
    assert re.search(_REFERENCE.pattern + r"\[(?!0\])", cpp), cpp


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


@pytest.mark.parametrize("builtin", ["second", "hour", "minute"])
@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
def test_unshadowed_calendar_read_keeps_complete_cpp(
    builtin, string, reference, tmp_path,
):
    source = helper_source(string=string).replace(
        "h() =>\n", f"h() =>\n    clock = {builtin}\n", 1,
    )
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    expected = before["cpp"]
    if string:
        from tests.test_string_local_history_concat import with_string_join_fixed

        # The reference joined a string local with its previous value through
        # std::to_string (issue #354); that one site is the whole difference.
        expected = with_string_join_fixed(expected)
    assert transpile(source) == expected
