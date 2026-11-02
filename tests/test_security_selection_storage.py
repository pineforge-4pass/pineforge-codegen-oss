"""Requested selections allocate state only for their lexical declarations."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests.test_security_coof_restore_members import _inventory


@pytest.mark.parametrize("expression", ("close", "label_v", 'str.tostring(close)'))
def test_plain_request_does_not_allocate_selection_string_history(expression):
    pine = (
        '//@version=6\nstrategy("Requested string storage")\n'
        'var string label_v = "ready"\n'
        "got_v = request.security(syminfo.tickerid, timeframe.period, close)\n"
        f"label_result_v = request.security(syminfo.tickerid, timeframe.period, {expression})\n"
    )
    cpp = transpile(pine)
    # A top-level var has its own request state; no selection-local string
    # history map, checkpoint field or reset may be added for it.
    assert "_security_helper_series_str_" not in cpp
    assert not re.search(r"unordered_map<std::string,\s*Series<std::string>>", cpp)
    inventory = _inventory(cpp)
    assert "_security_helper_series_str_" not in inventory
    # The checkpoint inventory must contain each declared state once.
    class_body = cpp.split("class GeneratedStrategy :", 1)[1].split(
        "    struct _PFScriptState {", 1)[0]
    declarations = set(re.findall(
        r"^    [^\n;]+? (\w+)(?:\{[^;\n]*\}| = [^;\n]*)?;",
        class_body, re.M))
    # This source has no TA caches or varip state; only the immutable
    # precalculation mode flag is outside the rollback inventory.
    declared_state = declarations - {"_use_precalc"}
    assert set(inventory) == declared_state
    assert len(inventory) == len(declared_state)


def test_requested_selection_string_var_keeps_its_series_map():
    pine = (
        '//@version=6\nstrategy("Local string state")\n'
        "chosen = if bar_index >= 0\n"
        '    var string label_v = "ready"\n    label_v\nelse\n    "waiting"\n'
        "got_v = request.security(syminfo.tickerid, timeframe.period, chosen)\n"
    )
    # A var declared inside the requested arm needs the string history map.
    assert "_security_helper_series_str_" in transpile(pine)


def test_requested_arm_average_has_one_lazy_site():
    pine = (
        '//@version=6\nstrategy("Conditional average")\n'
        "chosen = if bar_index % 2 == 0\n"
        "    averaged_v = ta.sma(close, 3)\n    averaged_v\nelse\n    0.0\n"
        "got_v = request.security(syminfo.tickerid, timeframe.period, chosen)\n"
    )
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    generator = CodeGen(Analyzer(ast).analyze())
    generator.generate()
    item = generator._security_calls[0]
    info = generator._security_eval_info[0]
    # Reading the arm's local reads its computed value, not a second TA call.
    assert len(generator._security_lazy_ta_keys(0, item["expr_node"], info)) == 1
