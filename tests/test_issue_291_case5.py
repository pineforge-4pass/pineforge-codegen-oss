"""Pin issue #291 case 5's exact source and its control at emission time.

The native variants in test_public_issue_291.py add intermediate reads. Here
the reporter's unmodified script must reset its input-backed member outside
any once guard; adding ``var`` must instead keep a persistent initializer.
"""

import pytest

from pineforge_codegen import transpile


REPORTER = """//@version=6
strategy('repro')
mult = input.float(2.0, 'Mult')
mult := mult * 2
// @pf-trace mult=mult
plot(mult)
"""

CONTROL = """//@version=6
strategy('repro')
multInput = input.float(2.0, 'Mult')
mult = multInput
mult := mult * 2
// @pf-trace mult=mult
plot(mult)
"""

GETTER = 'get_input_double("Mult", 2.0)'
CASES = (
    pytest.param(REPORTER, GETTER, id="reporter"),
    pytest.param(CONTROL, "multInput", id="copy-control"),
)


@pytest.mark.parametrize("source, initializer", CASES)
@pytest.mark.parametrize("persistent", (False, True), ids=("plain", "var"))
def test_exact_case5_declaration_lifetime(source, initializer, persistent):
    if persistent:
        source = source.replace("\nmult = ", "\nvar mult = ", 1)
    cpp = transpile(source)
    members, body = cpp.split("    void on_source_bar(", 1)
    body = body.split("\n    }", 1)[0]

    if initializer == "multInput":
        cached_input = (
            "        if (!_inputs_initialized_) {\n"
            f"            multInput = {GETTER};\n"
            "            _inputs_initialized_ = true;\n"
            "        }"
        )
        assert cached_input in body

    if persistent:
        reset = (
            "        if (!_pf_var_init_mult) {\n"
            f"            mult = {initializer};\n"
            "            _pf_var_init_mult = true;\n"
            "        }"
        )
        assert "    bool _pf_var_init_mult = false;" in members
        assert "_pf_var_init_mult = false;" not in body
        assert f"\n        mult = {initializer};" not in body
    else:
        # Matching the body indentation excludes an assignment hidden inside
        # the once-only input cache that caused 4, 8, 16, ... on the old code.
        reset = f"\n        mult = {initializer};"

    mutation = "\n        mult = (mult * 2);"
    assert reset in body
    assert mutation in body
    assert body.index(reset) < body.index(mutation)
    assert body.count(f"mult = {initializer};") == 1
    if initializer == "multInput":
        assert body.index(cached_input) < body.index(reset)
