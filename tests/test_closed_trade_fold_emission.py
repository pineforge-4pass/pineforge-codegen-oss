"""Emission contracts using closed-trade plans supplied independently."""

from dataclasses import dataclass
import sys
from textwrap import dedent
from types import ModuleType

from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import ForStmt, IfStmt
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


@dataclass(frozen=True)
class FoldPlan:
    index: int
    for_node_id: int
    accumulators: tuple[tuple[str, str], ...]
    locals: tuple[tuple[str, str, str], ...]
    stem: str


SOURCE = (
    '//@version=6\n'
    'strategy("Closed trade totals", calc_on_order_fills = true)\n'
    'int count = 7\n'
    'float total = -0.25\n'
    'if strategy.closedtrades > 0\n'
    '    for i = 0 to strategy.closedtrades - 1\n'
    '        entryId = strategy.closedtrades.entry_id(i)\n'
    '        profit = strategy.closedtrades.profit(i)\n'
    '        if entryId == "Long"\n'
    '            count += 1\n'
    '            total += profit\n'
)


def _emitter(source=SOURCE):
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    emitter = CodeGen(Analyzer(program).analyze())
    # Direct statement emission needs the same callable and binding preparation.
    emitter._build_func_instances()
    emitter._prepare_runtime_scalar_var_initializers()
    return emitter


def _loop(emitter):
    return next(
        statement.body[0]
        for statement in emitter.ctx.ast.body
        if isinstance(statement, IfStmt)
    )


def _plan(node):
    return FoldPlan(
        index=0,
        for_node_id=id(node),
        accumulators=(("count", "count"), ("total", "total")),
        locals=(
            ("entryId", "entry_id", "std::string"),
            ("profit", "profit", "double"),
        ),
        stem="_pf_ctf0_",
    )


def _generate(monkeypatch, emitter, plans):
    calls = []
    module = ModuleType("pineforge_codegen.codegen.closed_trade_fold")

    def plan_closed_trade_folds(program, safe_name):
        calls.append(program)
        return plans

    module.plan_closed_trade_folds = plan_closed_trade_folds
    monkeypatch.setitem(sys.modules, module.__name__, module)
    cpp = emitter.generate()
    return cpp, calls


def test_fold_members_join_checkpoint_and_lifecycle_reset(monkeypatch):
    emitter = _emitter()
    node = _loop(emitter)
    cpp, calls = _generate(monkeypatch, emitter, (_plan(node),))

    # Two cursor integers and two exactly typed prefix values persist together.
    assert (
        "    int _pf_ctf0_settled = 0;\n"
        "    int _pf_ctf0_seen = 0;\n"
        "    decltype(GeneratedStrategy::count) _pf_ctf0_s0 = {};\n"
        "    decltype(GeneratedStrategy::total) _pf_ctf0_s1 = {};"
    ) in cpp
    # Generation performs exactly one planning pass before emitting members.
    assert len(calls) == 1
    assert calls[0] is emitter.ctx.ast
    # The two ordinary accumulator members precede these four checkpoint slots.
    assert (
        "        decltype(GeneratedStrategy::_pf_ctf0_settled) _pf_value_2;\n"
        "        decltype(GeneratedStrategy::_pf_ctf0_seen) _pf_value_3;\n"
        "        decltype(GeneratedStrategy::_pf_ctf0_s0) _pf_value_4;\n"
        "        decltype(GeneratedStrategy::_pf_ctf0_s1) _pf_value_5;"
    ) in cpp
    # Snapshot copies both cursors and both prefix values in declaration order.
    assert (
        "            _pf_ctf0_settled,\n"
        "            _pf_ctf0_seen,\n"
        "            _pf_ctf0_s0,\n"
        "            _pf_ctf0_s1,"
    ) in cpp
    # A fill recalculation restores all four values from that same checkpoint.
    assert (
        "        this->_pf_ctf0_settled = _pf_script_state_checkpoint_->_pf_value_2;\n"
        "        this->_pf_ctf0_seen = _pf_script_state_checkpoint_->_pf_value_3;\n"
        "        this->_pf_ctf0_s0 = _pf_script_state_checkpoint_->_pf_value_4;\n"
        "        this->_pf_ctf0_s1 = _pf_script_state_checkpoint_->_pf_value_5;"
    ) in cpp
    # A fresh run clears both cursors and value-initializes the saved scalars.
    assert (
        "        this->_pf_ctf0_settled = 0;\n"
        "        this->_pf_ctf0_seen = 0;\n"
        "        this->_pf_ctf0_s0 = {};\n"
        "        this->_pf_ctf0_s1 = {};"
    ) in cpp
    assert emitter._closed_trade_folds[id(node)][0] is node


def test_fold_reuses_one_body_and_excludes_the_newest_final_run():
    emitter = _emitter()
    node = _loop(emitter)
    emitter._closed_trade_folds = {id(node): (node, _plan(node))}
    lines = []
    emitter._visit_for(node, lines, 0)
    cpp = "\n".join(lines)

    # The plain statements occur once, with only the accumulator names remapped.
    assert dedent("""\
        auto _pf_ctf0_step = [&](int i, auto& _pf_ctf0_a0, auto& _pf_ctf0_a1) {
            std::string entryId = closed_trade_entry_id(i);
            double profit = closed_trade_profit(i);
            if ((entryId == std::string("Long"))) {
                _pf_ctf0_a0 += 1;
                _pf_ctf0_a1 += profit;
            }
        };
    """).rstrip() in cpp
    # Only the prefix before the final count's trailing exit-time run is settled.
    assert dedent("""\
        const int _pf_ctf0_final = _pf_ctf0_n < _pf_ctf0_seen ? _pf_ctf0_n : _pf_ctf0_seen;
        int _pf_ctf0_limit = 0;
        if (_pf_ctf0_final > 0) {
            const int64_t _pf_ctf0_run = closed_trade_exit_time(_pf_ctf0_final - 1);
            _pf_ctf0_limit = _pf_ctf0_final - 1;
            while (_pf_ctf0_limit > 0 && closed_trade_exit_time(_pf_ctf0_limit - 1) == _pf_ctf0_run)
                --_pf_ctf0_limit;
        }
        if (_pf_ctf0_n < _pf_ctf0_seen || _pf_ctf0_limit < _pf_ctf0_settled || _pf_ctf0_settled == 0) {
            _pf_ctf0_settled = 0;
            _pf_ctf0_s0 = count;
            _pf_ctf0_s1 = total;
        }
        for (int _pf_ctf0_i = _pf_ctf0_settled; _pf_ctf0_i < _pf_ctf0_limit; ++_pf_ctf0_i)
            _pf_ctf0_step(_pf_ctf0_i, _pf_ctf0_s0, _pf_ctf0_s1);
        if (_pf_ctf0_limit > _pf_ctf0_settled) _pf_ctf0_settled = _pf_ctf0_limit;
        _pf_ctf0_seen = _pf_ctf0_n;
        count = _pf_ctf0_s0;
        total = _pf_ctf0_s1;
        for (int _pf_ctf0_i = _pf_ctf0_settled; _pf_ctf0_i < _pf_ctf0_n; ++_pf_ctf0_i)
            _pf_ctf0_step(_pf_ctf0_i, count, total);
    """).rstrip() in cpp
    # Prefix, tail and verification execute the same single emitted accessor site.
    assert cpp.count("std::string entryId = closed_trade_entry_id(i);") == 1
    assert emitter._reads_trade_history
    assert emitter._active_var_remap == {}
    assert emitter._current_loop_vars == set()
    assert emitter._current_counted_loop_vars == set()


def test_verify_replays_from_declaration_values_and_compares_object_bytes():
    emitter = _emitter()
    node = _loop(emitter)
    emitter._closed_trade_folds = {id(node): (node, _plan(node))}
    lines = []
    emitter._visit_for(node, lines, 0)
    cpp = "\n".join(lines)

    # Verification starts with the current declaration values, including nonzero seeds.
    assert dedent("""\
        #ifdef PINEFORGE_CLOSED_TRADE_FOLD_VERIFY
        decltype(GeneratedStrategy::count) _pf_ctf0_v0 = count;
        decltype(GeneratedStrategy::total) _pf_ctf0_v1 = total;
        #endif
    """).rstrip() in cpp
    # The oracle replays indices zero through n-1 and compares both scalar representations.
    assert dedent("""\
        #ifdef PINEFORGE_CLOSED_TRADE_FOLD_VERIFY
        for (int _pf_ctf0_i = 0; _pf_ctf0_i < _pf_ctf0_n; ++_pf_ctf0_i)
            _pf_ctf0_step(_pf_ctf0_i, _pf_ctf0_v0, _pf_ctf0_v1);
        if (std::memcmp(&_pf_ctf0_v0, &count, sizeof(count)) != 0)
            throw std::runtime_error("closed-trade fold verification failed");
        if (std::memcmp(&_pf_ctf0_v1, &total, sizeof(total)) != 0)
            throw std::runtime_error("closed-trade fold verification failed");
        #endif
    """).rstrip() in cpp
    # Literal capture precedes prefix initialization, which precedes the tail and oracle.
    assert (
        cpp.index("_pf_ctf0_v1 = total;")
        < cpp.index("_pf_ctf0_s1 = total;")
        < cpp.index("total = _pf_ctf0_s1;")
        < cpp.index("std::memcmp(&_pf_ctf0_v1")
    )


def test_fold_preserves_bound_visits_counter_names_and_later_plain_loop(monkeypatch):
    source = SOURCE.replace("for i =", "for class =").replace(
        "entry_id(i)", "entry_id(class)"
    ).replace("profit(i)", "profit(class)") + (
        "int other = 0\n"
        "for j = 2 to 4\n"
        "    other += 1\n"
    )
    emitter = _emitter(source)
    node = _loop(emitter)
    emitter._closed_trade_folds = {id(node): (node, _plan(node))}
    visited = []
    original_visit = emitter._visit_expr

    def visit(expression):
        visited.append(expression)
        return original_visit(expression)

    monkeypatch.setattr(emitter, "_visit_expr", visit)
    lines = []
    emitter._visit_for(node, lines, 0)
    cpp = "\n".join(lines)

    # A reserved C++ counter name is escaped for both the lambda and accessor reads.
    assert "auto _pf_ctf0_step = [&](int _class_, auto& _pf_ctf0_a0, auto& _pf_ctf0_a1)" in cpp
    assert "std::string entryId = closed_trade_entry_id(_class_);" in cpp
    assert visited[0] is node.start
    assert visited[1] is node.end

    following = next(
        statement for statement in emitter.ctx.ast.body
        if isinstance(statement, ForStmt)
    )
    lines = []
    emitter._visit_for(following, lines, 0)
    # The fold consumes loop number zero; the following unplanned loop keeps number one.
    assert "\n".join(lines) == dedent("""\
        int _for_start_1 = (2);
        int _for_end_1 = (4);
        int _for_step_1 = (1);
        if (_for_step_1 < 0) _for_step_1 = -_for_step_1;
        if (_for_step_1 == 0) _for_step_1 = 1;
        const bool _for_down_1 = (_for_start_1 > _for_end_1);
        for (int j = _for_start_1; (_for_down_1 ? (j >= _for_end_1) : (j <= _for_end_1)); j += (_for_down_1 ? -_for_step_1 : _for_step_1), _for_end_1 = (4)) {
            other += 1;
        }
    """).rstrip()


def test_two_fold_plans_keep_separate_members_and_steps():
    source = SOURCE + (
        "float sum = 1.5\n"
        "if strategy.closedtrades > 0\n"
        "    for trade = 0 to strategy.closedtrades - 1\n"
        "        gain = strategy.closedtrades.profit(trade)\n"
        "        sum += gain\n"
    )
    emitter = _emitter(source)
    cpp = emitter.generate()

    # The two disjoint guarded loops each need a real plan and one emitted step.
    assert cpp.count("// closed-trade fold:") == 2
    # The second loop owns a distinct cursor pair and a prefix of its own accumulator type.
    assert (
        "    int _pf_ctf1_settled = 0;\n"
        "    int _pf_ctf1_seen = 0;\n"
        "    decltype(GeneratedStrategy::sum) _pf_ctf1_s0 = {};"
    ) in cpp
    # Each source body is emitted once into the step belonging to its plan.
    assert "auto _pf_ctf0_step = [&](int i, auto& _pf_ctf0_a0, auto& _pf_ctf0_a1)" in cpp
    assert "auto _pf_ctf1_step = [&](int trade, auto& _pf_ctf1_a0)" in cpp
    assert "_pf_ctf1_step(_pf_ctf1_i, _pf_ctf1_s0);" in cpp
    assert "_pf_ctf1_step(_pf_ctf1_i, sum);" in cpp
    assert "_pf_ctf1_step(_pf_ctf1_i, _pf_ctf1_v0);" in cpp


def test_literal_fold_retains_rows_for_the_exit_time_cursor():
    emitter = _emitter(
        '//@version=6\n'
        'strategy("Literal trade totals")\n'
        'int rows = 3\n'
        'float fraction = -0.25\n'
        'if strategy.closedtrades > 0\n'
        '    for i = 0 to strategy.closedtrades - 1\n'
        '        rows += 2\n'
        '        fraction += 0.125\n'
    )
    cpp = emitter.generate()

    # One literal-only fold still needs ledger rows for its exit-time scan.
    assert cpp.count("// closed-trade fold:") == 1
    assert (
        "    int strategy_reads_trade_history(void) {\n"
        "        return 1;\n"
        "    }"
    ) in cpp
    assert "this->set_trade_history_streamable(true);" not in cpp
