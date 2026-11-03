"""Missingness queries visit shared aliases once, without changing emission."""

import re
from types import SimpleNamespace

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import BinOp, FuncDef, Identifier, NumberLiteral, VarDecl
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.codegen.types import TypeInferer
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


class _AliasQuery(TypeInferer, NamingHelper):
    """Use the emitter's real AST walker without constructing a whole emitter."""


def _chain(depth):
    return (
        '//@version=6\nstrategy("Alias missingness", currency=currency.NONE)\n'
        'a0 = input.int(0)\n'
        + ''.join(f'a{i} = a{i - 1} + a{i - 1}\n'
                  for i in range(1, depth + 1))
        + f'float acc = 0.0\nfor walkIndex = a{depth} to 0\n'
        '    acc += walkIndex\nplot(acc)\n'
        + f'observed = na(a{depth})\nplot(observed ? 1 : 0)\n'
    )


def _translate_with_linear_query_guard(monkeypatch, depth):
    # Only translation can be long. Fail *during* that call, rather than
    # timing an exponential walk after it finishes. This is a test assertion,
    # not a production work/depth cap. The 40-level translation hangs on the
    # base without this guard; with it the old code fails after bounded work.
    active = calls = maximum = 0

    def counted(walk):
        def wrapper(self, *args, **kwargs):
            nonlocal active, calls, maximum
            if active == 0:
                calls = 0
            calls += 1
            assert calls <= 32 * (depth + 1), "alias query revisited shared subgraphs"
            active += 1
            try:
                return walk(self, *args, **kwargs)
            finally:
                active -= 1
                if active == 0:
                    maximum = max(maximum, calls)
        return wrapper

    with monkeypatch.context() as patch:
        for name in ("_int_operand_may_be_na", "_emitted_value_is_double"):
            patch.setattr(TypeInferer, name, counted(getattr(TypeInferer, name)))
        cpp = transpile(_chain(depth))
    assert maximum > 0
    return cpp, maximum


def _assignment(cpp, name):
    statements = [line.strip() for line in cpp.splitlines()
                  if re.match(rf'\s*{re.escape(name)} = ', line)]
    assert len(statements) == 1, statements
    return statements[0]


def test_forty_level_alias_chain_is_linear_and_keeps_statement_cpp(monkeypatch):
    shallow, small_work = _translate_with_linear_query_guard(monkeypatch, 3)
    deep, large_work = _translate_with_linear_query_guard(monkeypatch, 40)
    # 41/4 aliases, allowing the extra wide-arithmetic checks first reached
    # by the deep source. The per-query guard above also scales with depth.
    assert large_work <= 32 * small_work

    # Compare exactly a1's assignment and observed = na(aN). a1 has identical
    # operands in both scripts; observed tests the final alias for missingness
    # in both, with only the final alias spelling changed. All aliases are sums
    # of a nonmissing integer input. Neither comparison assumes equal numeric
    # values for a3 and a40 or equal widths for their arithmetic assignments.
    assert _assignment(deep, "a1") == _assignment(shallow, "a1")
    assert re.sub(r'\ba40\b', 'final_alias', _assignment(deep, "observed")) == re.sub(
        r'\ba3\b', 'final_alias', _assignment(shallow, "observed"))


def test_local_alias_cycle_keeps_seen_fallback_and_query_context(monkeypatch):
    # The parser can represent this forward local read even though normal
    # value-binding validation refuses it. Exercise the helper on that AST.
    source = 'f() =>\n    int left = right\n    int right = left\n    left\n'
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    function = next(node for node in program.body if isinstance(node, FuncDef))
    query = _AliasQuery()
    query._active_func_name = "f"
    query._func_info_map = {"f": SimpleNamespace(node=function)}
    query._current_func_local_types = {"left": "int", "right": "int"}
    query._current_func_param_types = {}
    query._current_loop_vars = set()
    query._global_mutable_infos = {}
    query._known_vars = {}
    monkeypatch.setattr(query, "_known_var_is_lexically_shadowed", lambda name: False,
                        raising=False)
    # Both initializers are identifiers, hence neither is a pure literal.
    monkeypatch.setattr(query, "_pure_int_literal_value", lambda value: None)
    root = Identifier(name="left")
    info = query._narrow_int_name_info(root.name)
    expected = info is None or info[1]
    assert expected is True
    assert query._int_operand_may_be_na(root, _seen=frozenset({"left"})) is expected
    assert query._int_operand_may_be_na(root) is expected

    # A later query in another active function must not reuse f's answer for
    # the same local spelling; returning to f must still recover its fallback.
    query._func_info_map["g"] = SimpleNamespace(node=FuncDef(
        name="g", body=[VarDecl(name="left", value=NumberLiteral(value=0))]))
    query._active_func_name = "g"
    assert query._int_operand_may_be_na(root) is False
    query._active_func_name = "f"
    assert query._int_operand_may_be_na(root) is expected


def test_builtin_and_tuple_read_of_same_name_keep_distinct_answers(monkeypatch):
    query = _AliasQuery()
    query.ctx = SimpleNamespace(global_expr_map={})
    query._current_func_local_types = {}
    query._current_func_param_types = {}
    query._current_loop_vars = set()
    query._global_mutable_infos = {}
    query._known_vars = {}
    monkeypatch.setattr(query, "_known_var_is_lexically_shadowed", lambda name: False,
                        raising=False)
    clock = Identifier(name="time")
    tuple_read = Identifier(name="time", annotations={"tuple_builtin_binding": True})
    assert query._int_operand_may_be_na(clock) is False
    assert query._int_operand_may_be_na(tuple_read) is True
    # Both reads share one query here. A cached clock proof must not suppress
    # the tuple binding's conservative answer for the same spelling.
    assert query._int_operand_may_be_na(BinOp(left=clock, op="+", right=tuple_read)) is True
