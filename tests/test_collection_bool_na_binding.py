"""Collection argument binding contracts and scalar controls.

Collection diagnostics and typed twins keep their authored contracts.
Unqualified scalar cases compare complete outcomes with the pinned parent.
No parent red, candidate green or TV outcome is claimed from this source,
with one exception that is stated where it is asserted: the keyword push on
a receiver, which the parent refused and which binds its value now.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp
from tests._e2e import REPO_ROOT, reference_codegen, transpile_json


HEADER = '//@version=6\nstrategy("Collection argument binding")\n'

# Each spelling supplies the same element slot through a different binding
# route. Expectations come from the bool type contract, not generated output.
COLLECTION_CALLS = (
    pytest.param(
        "array", "array.insert(values, 1, {value})", id="insert-positional",
    ),
    pytest.param(
        "array", "values.insert(1, value = {value})", id="insert-method",
    ),
    pytest.param(
        "array", "array.insert(value = {value}, index = 1, id = values)",
        id="insert-keyword-order",
    ),
    pytest.param(
        "array", "array.unshift(values, {value})", id="unshift-positional",
    ),
    pytest.param(
        "array", "values.unshift(value = {value})", id="unshift-method",
    ),
    pytest.param(
        "array", "array.unshift(value = {value}, id = values)",
        id="unshift-keyword-order",
    ),
    pytest.param(
        "array", "array.fill(values, {value})", id="array-fill-positional",
    ),
    pytest.param(
        "array", "values.fill(value = {value})", id="array-fill-method",
    ),
    pytest.param(
        "array", "array.fill(value = {value}, id = values)",
        id="array-fill-keyword-order",
    ),
    pytest.param(
        "matrix", "matrix.fill(values, {value})", id="matrix-fill-positional",
    ),
    pytest.param(
        "matrix", "values.fill(value = {value})", id="matrix-fill-method",
    ),
    pytest.param(
        "matrix", "matrix.fill(value = {value}, id = values)",
        id="matrix-fill-keyword-order",
    ),
)


def _collection_source(
    namespace: str, element: str, call: str, *, header: str = HEADER,
) -> str:
    initial = {"bool": "false", "float": "1.0"}[element]
    constructor, read = {
        "array": (
            f"array.new<{element}>(2, {initial})",
            "array.get(values, 0)",
        ),
        "matrix": (
            f"matrix.new<{element}>(2, 2, {initial})",
            "matrix.get(values, 0, 0)",
        ),
    }[namespace]
    plotted = f"{read} ? 1 : 0" if element == "bool" else f"nz({read})"
    return (
        header
        + f"{namespace}<{element}> values = {constructor}\n"
        + f"{call}\nplot({plotted})\n"
    )


def _assert_bool_na_diagnostic(
    source: str, *, code: str, message: str, arguments: dict,
    line: int, col: int, check_support: bool = True,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="collection_binding.pine", check_support=check_support)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == arguments
    assert diagnostic.location.file == "collection_binding.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (line, col, col + 2)


@pytest.mark.parametrize("namespace,template", COLLECTION_CALLS)
def test_bool_element_na_is_refused_at_its_bound_argument(namespace, template):
    call = template.format(value="na")
    _assert_bool_na_diagnostic(
        _collection_source(namespace, "bool", call),
        code="PF-E2067",
        message=f"Cannot store a value of the 'na' type in {namespace}<bool>.",
        arguments={"kind": namespace},
        line=4,
        col=call.rindex("na") + 1,
    )


@pytest.mark.parametrize("namespace,template", COLLECTION_CALLS)
@pytest.mark.parametrize("element,value", (
    pytest.param("bool", "false", id="bool-value-twin"),
    pytest.param("float", "na", id="nullable-element-twin"),
))
def test_bound_element_slot_accepts_its_typed_twin(
    namespace, template, element, value,
):
    source = _collection_source(namespace, element, template.format(value=value))
    compile_cpp(transpile(source), label="collection element binding twin")


SCALAR_CALLS = (
    pytest.param("ta.tr({value})", id="declared-bool-positional"),
    pytest.param("ta.tr(handle_na = {value})", id="declared-bool-keyword"),
    pytest.param(
        "ta.stdev(biased = {value}, length = 3, source = close)",
        id="declared-bool-reordered-keyword",
    ),
)


# Codegen main from before the collection-binding change. Its output for
# these scripts differs from this tree only by that change; a tree that also
# carries the collision-safe record names would differ for record methods.
SCALAR_PARENT = "f848df52b05cd4118dfd90101080446d8a7582ad"


@pytest.fixture(scope="module")
def scalar_parent_codegen():
    reference = reference_codegen(SCALAR_PARENT)
    assert reference is not None, f"The pinned scalar parent {SCALAR_PARENT} is required"
    assert reference.resolve() != REPO_ROOT.resolve(), "Parent and current roots must differ"
    return reference


def _assert_parent_outcome(source, reference, tmp_path, *, extra_env=None):
    # The scalar PF-E2044 expectations were unqualified draft hypotheses.
    # Run the identical authored script through both real source trees.
    pine = tmp_path / "collection_binding.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference, extra_env=extra_env)
    after = transpile_json(pine, root=REPO_ROOT, extra_env=extra_env)
    for outcome in (before, after):
        assert isinstance(outcome, dict), outcome
        assert isinstance(outcome["ok"], bool), outcome
        assert isinstance(outcome["diagnostics"], list), outcome
        if outcome["ok"]:
            assert isinstance(outcome["cpp"], str) and outcome["cpp"], outcome
        else:
            assert isinstance(outcome["error"], str) and outcome["error"], outcome
            assert outcome["diagnostics"], outcome
    assert after["ok"] is before["ok"], (before, after)
    # Compare all fields, including complete C++, diagnostics and error text.
    # No generated text or protocol field is normalized or discarded.
    assert after == before
    return after


@pytest.mark.parametrize("template", SCALAR_CALLS)
def test_declared_builtin_bool_parameter_keeps_parent_outcome(
    template, scalar_parent_codegen, tmp_path,
):
    statement = "value = " + template.format(value="na")
    _assert_parent_outcome(
        HEADER + statement + "\nplot(value)\n",
        scalar_parent_codegen, tmp_path,
    )


@pytest.mark.parametrize("template", SCALAR_CALLS)
def test_declared_builtin_bool_parameter_accepts_false_twin(template):
    source = HEADER + "value = " + template.format(value="false") + "\nplot(value)\n"
    compile_cpp(transpile(source), label="collection bool parameter twin")


@pytest.mark.parametrize("expression", (
    pytest.param("ta.change(na)", id="numeric-overload-remains-available"),
    pytest.param(
        "ta.stdev(biased = false, length = 3, source = na)",
        id="na-in-numeric-parameter",
    ),
))
def test_na_in_a_numeric_signature_slot_remains_admitted(expression):
    source = HEADER + f"value = {expression}\nplot(value)\n"
    compile_cpp(transpile(source), label="collection numeric slot twin")


@pytest.mark.parametrize("template", SCALAR_CALLS)
def test_uncalled_ta_owner_preserves_its_existing_admission(template):
    # CodeGen's existing omission rule runs after every definition body has
    # been analyzed. A new eager refusal must not preempt that decision.
    source = (
        HEADER
        + "unused_bool_na() => " + template.format(value="na")
        + "\nplot(close)\n"
    )
    compile_cpp(transpile(source), label="collection uncalled TA owner")


RETAINED_OWNERS = (
    pytest.param(
        "", "read() => ta.tr(na)",
        "value = read()\nplot(value)\n",
        id="called-function",
    ),
    pytest.param(
        "", "read() => ta.tr(na)",
        "unused() => read()\nvalue = read()\nplot(value)\n",
        id="live-owner-with-sites-borrowed-by-unused-caller",
    ),
    pytest.param(
        "type Holder\n    float factor\nmethod read(Holder self) =>\n",
        "    ta.tr(na) * self.factor",
        "h = Holder.new(1.0)\nplot(h.read())\n",
        id="called-udt-method",
    ),
    pytest.param(
        "type Holder\n    float factor\nmethod read(Holder self) =>\n",
        "    ta.tr(na) * self.factor",
        "plot(close)\n",
        id="uncalled-udt-method-is-not-an-omitted-ta-owner",
    ),
)


@pytest.mark.parametrize("prefix,statement,suffix", RETAINED_OWNERS)
def test_retained_ta_owner_keeps_parent_outcome(
    prefix, statement, suffix, scalar_parent_codegen, tmp_path,
):
    source = HEADER + prefix + statement + "\n" + suffix
    _assert_parent_outcome(source, scalar_parent_codegen, tmp_path)


@pytest.mark.parametrize("prefix,statement,suffix", RETAINED_OWNERS)
def test_retained_ta_owner_accepts_its_false_twin(prefix, statement, suffix):
    statement = statement.replace("ta.tr(na)", "ta.tr(false)")
    source = HEADER + prefix + statement + "\n" + suffix
    compile_cpp(transpile(source), label="collection retained owner twin")


def test_uncalled_function_without_ta_is_not_a_blanket_exemption():
    statement = "    values.fill(na)"
    source = (
        HEADER
        + "unused() =>\n"
        + "    values = array.new<bool>(2, false)\n"
        + statement + "\n"
        + "    array.size(values)\nplot(close)\n"
    )
    _assert_bool_na_diagnostic(
        source,
        code="PF-E2067",
        message="Cannot store a value of the 'na' type in array<bool>.",
        arguments={"kind": "array"},
        line=5,
        col=statement.rindex("na") + 1,
    )


def test_uncalled_function_without_ta_accepts_its_false_twin():
    source = (
        HEADER
        + "unused() =>\n"
        + "    values = array.new<bool>(2, false)\n"
        + "    values.fill(false)\n"
        + "    array.size(values)\nplot(close)\n"
    )
    compile_cpp(transpile(source), label="collection uncalled pure owner twin")


def test_collection_intent_obeys_the_same_omitted_ta_owner_decision():
    source = (
        HEADER
        + "unused() =>\n"
        + "    values = array.new<bool>(2, false)\n"
        + "    values.fill(na)\n"
        + "    ta.sma(close, 3)\nplot(close)\n"
    )
    compile_cpp(transpile(source), label="collection omitted collection intent")


def test_authored_collection_method_does_not_bind_the_builtin_of_its_name():
    source = (
        HEADER
        + "method fill(array<bool> self, float value) => array.size(self) + nz(value)\n"
        + "values = array.new<bool>(2, false)\n"
        + "plot(values.fill(na))\n"
    )
    compile_cpp(transpile(source), label="collection authored method binding")


# Parent expectations come from the actual bdeab1a7 eager checks and retained
# bool-na assertions. They are preservation rows, not newly witnessed TV rules.
PARENT_EAGER_CASES = (
    pytest.param(
        "flags = array.new<bool>(2, false)", "array.push(flags, {value})",
        "PF-E2067", {"kind": "array"}, id="parent-array-push",
    ),
    pytest.param(
        "flags = array.new<bool>(2, false)", "flags.set(0, value = {value})",
        "PF-E2067", {"kind": "array"}, id="parent-array-set",
    ),
    pytest.param(
        "flags = matrix.new<bool>(1, 1, false)", "matrix.set(flags, 0, 0, {value})",
        "PF-E2067", {"kind": "matrix"}, id="parent-matrix-set",
    ),
    pytest.param(
        "flags = map.new<string, bool>()", 'flags.put("key", {value})',
        "PF-E2044", {}, id="parent-map-value",
    ),
    pytest.param(
        "", "flags = array.new<bool>(2, {value})",
        "PF-E2044", {}, id="parent-generic-array-constructor",
    ),
    pytest.param(
        "", 'flag = input.bool({value}, "Flag")',
        "PF-E2044", {}, id="parent-input-bool-default",
    ),
)


def _parent_owner_source(setup: str, statement: str, owner: str) -> tuple[str, int, int]:
    setup_lines = setup.splitlines()
    if owner == "top":
        lines = [*setup_lines, statement, "plot(close)"]
        return HEADER + "\n".join(lines) + "\n", 3 + len(setup_lines), 0
    lines = [
        "read_parent() =>",
        *("    " + line for line in setup_lines),
        "    " + statement,
        "    ta.sma(close, 3)",
    ]
    if owner == "called":
        lines.append("plot(read_parent())")
    else:
        lines.append("plot(close)")
    return HEADER + "\n".join(lines) + "\n", 4 + len(setup_lines), 4


@pytest.mark.parametrize("setup,template,code,arguments", PARENT_EAGER_CASES)
@pytest.mark.parametrize("owner", ("top", "called", "uncalled"))
def test_parent_eager_refusal_is_not_dropped_for_a_ta_owner(
    setup, template, code, arguments, owner,
):
    statement = template.format(value="na")
    source, line, indent = _parent_owner_source(setup, statement, owner)
    message = (
        f"Cannot store a value of the 'na' type in {arguments['kind']}<bool>."
        if code == "PF-E2067"
        else "Invalid bool value: a bool cannot be na in Pine v6."
    )
    _assert_bool_na_diagnostic(
        source, code=code, message=message, arguments=arguments,
        line=line, col=indent + statement.rindex("na") + 1,
    )


@pytest.mark.parametrize("setup,template,code,arguments", PARENT_EAGER_CASES)
@pytest.mark.parametrize("owner", ("top", "called", "uncalled"))
def test_parent_eager_refusal_keeps_its_false_twin(
    setup, template, code, arguments, owner,
):
    source, _line, _indent = _parent_owner_source(
        setup, template.format(value="false"), owner,
    )
    compile_cpp(transpile(source), label="collection parent eager false twin")


@pytest.mark.parametrize("member,parameters,body,call", (
    pytest.param(
        "push", "float value", "array.size(self) + nz(value)", "{value}",
        id="authored-push-parent-refusal",
    ),
    pytest.param(
        "set", "int index, float value", "array.size(self) + index + nz(value)",
        "0, {value}", id="authored-set-parent-refusal",
    ),
))
def test_authored_method_keeps_the_parent_collection_refusal(
    member, parameters, body, call,
):
    declaration = f"method {member}(array<bool> self, {parameters}) => {body}\n"
    statement = f"result = flags.{member}({call.format(value='na')})"
    source = (
        HEADER + declaration + "flags = array.new<bool>(2, false)\n"
        + statement + "\nplot(result)\n"
    )
    _assert_bool_na_diagnostic(
        source,
        code="PF-E2067",
        message="Cannot store a value of the 'na' type in array<bool>.",
        arguments={"kind": "array"}, line=5, col=statement.rindex("na") + 1,
    )
    # The numeric parameter's non-na twin is independent of the store's bool
    # type: the parent's refusal at na precedes authored method dispatch.
    twin = (
        HEADER + declaration + "flags = array.new<bool>(2, false)\n"
        + f"result = flags.{member}({call.format(value='1.0')})\nplot(result)\n"
    )
    compile_cpp(transpile(twin), label="collection parent authored method twin")


def test_parent_array_initializer_refusal_precedes_duplicate_slot_codegen_error():
    statement = "flags = array.new<bool>(2, na, initial_value = na)"
    _assert_bool_na_diagnostic(
        HEADER + statement + "\nplot(close)\n",
        code="PF-E2044",
        message="Invalid bool value: a bool cannot be na in Pine v6.",
        arguments={}, line=3, col=statement.index("na") + 1,
        check_support=False,
    )


def test_parent_eager_store_error_precedes_a_later_analyzer_error():
    statement = "array.push(flags, na)"
    source = (
        HEADER + "flags = array.new<bool>(2, false)\n"
        + statement + "\nbool later = na\nplot(close)\n"
    )
    _assert_bool_na_diagnostic(
        source,
        code="PF-E2067",
        message="Cannot store a value of the 'na' type in array<bool>.",
        arguments={"kind": "array"}, line=4, col=statement.rindex("na") + 1,
    )


@pytest.mark.parametrize("body,builtin", (
    pytest.param("value = ta.tr(na)\n", "ta.tr", id="unqualified-tr"),
    pytest.param("value = ta.stdev(close, 3, na)\n", "ta.stdev", id="unqualified-stdev"),
    pytest.param("value = ta.alma(close, 3, 0.5, 6.0, na)\n", "ta.alma", id="unqualified-alma"),
    pytest.param("[middle, upper, lower] = ta.kc(close, 3, 2.0, na)\n", "ta.kc", id="unqualified-kc"),
    pytest.param("value = ta.kcw(close, 3, 2.0, na)\n", "ta.kcw", id="unqualified-kcw"),
    pytest.param("value = ta.variance(close, 3, na)\n", "ta.variance", id="unqualified-variance"),
    pytest.param("value = ta.vwap(close, na)\n", "ta.vwap", id="unqualified-vwap"),
    pytest.param("value = ta.barssince(na)\n", "ta.barssince", id="unqualified-barssince"),
    pytest.param("value = ta.valuewhen(na, close, 0)\n", "ta.valuewhen", id="unqualified-valuewhen"),
    pytest.param(
        'value = ta.pivot_point_levels("Traditional", na, false)\n',
        "ta.pivot_point_levels", id="unqualified-pivot-anchor",
    ),
    pytest.param(
        'value = ta.pivot_point_levels("Traditional", true, na)\n',
        "ta.pivot_point_levels", id="unqualified-pivot-developing",
    ),
    pytest.param(
        'strategy.entry("L", na, 1.0, 1.0, 1.0, "", 0, "", "", false, 0)\n',
        "strategy.entry", id="storage-only-entry-direction",
    ),
    pytest.param(
        'strategy.order("L", na, 1.0, 1.0, 1.0, "", 0, "", "", false, 0)\n',
        "strategy.order", id="storage-only-order-direction",
    ),
    pytest.param(
        'strategy.close("L", "", 1.0, 100.0, "", na, false)\n',
        "strategy.close", id="unqualified-close-immediately",
    ),
    pytest.param(
        'strategy.close_all("", "", false, na)\n',
        "strategy.close_all", id="unqualified-close-all-disable-alert",
    ),
    pytest.param(
        'strategy.exit("X", "L", 1.0, 100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, '
        '1.0, "", "", "", "", "", "", "", "", "", na)\n',
        "strategy.exit", id="unqualified-exit-disable-alert",
    ),
    pytest.param(
        "value = input.int(1, confirm = na)\n",
        "input.int", id="unqualified-input-confirm",
    ),
    pytest.param(
        "flags = map.new<bool, float>()\nflags.put(na, 1.0)\n",
        "map.put", id="unqualified-map-key",
    ),
))
def test_unqualified_storage_slots_create_needs_not_refusal_intents(body, builtin):
    # This is an analyzer-obligation unit, not compilation/TV acceptance.
    # Existing APIs keep module collection independent of the new fields.
    from pineforge_codegen.analyzer import Analyzer
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    source = HEADER + body + "plot(close)\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(ast).analyze()
    assert context.builtin_bool_na_intents == []
    assert any(
        name == builtin
        and reason == "no qualified new bool/na obligation; retain parent admission"
        for _owner, _location, name, reason in context.builtin_bool_na_needs
    ), f"{builtin} must be visited and retained as an unqualified obligation"


@pytest.mark.parametrize("flag", [
    pytest.param("na", id="unqualified-request-flag"),
    pytest.param("ignore_invalid_currency = na", id="keyword-request-flag"),
])
def test_currency_rate_na_flag_is_refused_during_analysis(flag):
    # Pine v6 has no missing bool value. A declared bool request parameter
    # rejects the argument rather than recording a deferred obligation.
    from pineforge_codegen.analyzer import Analyzer
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    statement = f'value = request.currency_rate("USD", "EUR", {flag})'
    source = HEADER + statement + "\nplot(close)\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    with pytest.raises(CompileError) as caught:
        Analyzer(ast).analyze()
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args == {
        "function_name": "request.currency_rate", "argument_type": "na",
        "parameter_type": "bool", "name": "ignore_invalid_currency",
    }
    col = statement.rindex("na") + 1
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (3, col, col + 2)


def test_currency_rate_na_flag_is_refused_at_its_argument():
    # The currency-rate flag is a declared bool parameter, and a Pine v6 bool
    # cannot be na, so the call is refused at the na argument instead of being
    # retained as an unqualified obligation.
    body = 'value = request.currency_rate("USD", "EUR", na)\n'
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body + "plot(close)\n")
    located = [
        (d.location.line, d.location.col)
        for d in caught.value.diagnostics
        if "request.currency_rate" in d.message
        and "ignore_invalid_currency" in d.message
    ]
    # Two header lines; na starts at the 45th column of the third line.
    assert located == [(3, body.index("na") + 1)]


# These are the parent's three version-limited contracts, in namespace and
# receiver form. The parent dropped the keyword of a receiver push and stopped
# with its arity error; the keyword binds the value slot now, as the other
# receiver rows bind theirs. Where that row is accepted it is held to the
# parent's outcome for the positional receiver spelling beside it.
PUSH_KEYWORD = "values.push(value = {value})"
PUSH_POSITIONAL = "values.push({value})"
VERSIONED_PARENT_CALLS = (
    pytest.param("array", "array.push(values, {value})", id="push-namespace"),
    pytest.param("array", PUSH_KEYWORD, id="push-receiver"),
    pytest.param(
        "array", PUSH_POSITIONAL, id="push-receiver-positional",
    ),
    pytest.param("array", "array.set(values, 0, {value})", id="array-set-namespace"),
    pytest.param("array", "values.set(0, value = {value})", id="array-set-receiver"),
    pytest.param(
        "matrix", "matrix.set(values, 0, 0, {value})", id="matrix-set-namespace",
    ),
    pytest.param(
        "matrix", "values.set(0, 0, value = {value})", id="matrix-set-receiver",
    ),
)

LEGACY_MAIN_DIRECTIVES = (
    pytest.param("//@version=5\n", id="v5-main"),
    pytest.param("", id="no-directive"),
)

LEGACY_PUBLIC_REFUSALS = (
    pytest.param("//@version=5\n", "PF-E2002", {"version": 5}, id="v5-main"),
    pytest.param("", "PF-E2001", {}, id="no-directive"),
)


def _assert_parent_push_keyword_arity(outcome, *, filename, line, col):
    # The parent's retained diagnostic snapshot pins PF-E5052 and its method
    # argument; focused job0954 retains the exact message and source locations.
    message = "array.push: wrong number of arguments"
    assert outcome["ok"] is False, outcome
    assert outcome["error"] == f"{filename}:{line}:{col}: {message}", outcome
    assert len(outcome["diagnostics"]) == 1, outcome
    diagnostic = outcome["diagnostics"][0]
    assert diagnostic["code"] == "PF-E5052", outcome
    assert diagnostic["args"] == {"method": "push"}, outcome
    assert diagnostic["message"] == message, outcome
    assert diagnostic["severity"] == "error", outcome
    assert (diagnostic["line"], diagnostic["col"]) == (line, col), outcome


def _assert_keyword_push_binds_its_value(
    keyword, positional, reference, tmp_path, *, filename, line, col,
):
    """The receiver push with ``value =`` against its positional spelling.

    ``keyword`` and ``positional`` are ``(source, extra_env)`` of one script
    that differs only in how the push is spelled. The pinned parent refused
    the keyword spelling (the old value, still asserted here on the parent),
    and this tree gives it the complete C++ the parent gives the positional
    spelling: the keyword names the same one value slot. The positional row
    of the same test holds this tree to the parent's whole positional outcome.
    """
    def run(case, root, name):
        source, extra_env = case
        pine = tmp_path / name
        pine.write_text(source, encoding="utf-8")
        return transpile_json(pine, root=root, extra_env=extra_env)

    _assert_parent_push_keyword_arity(
        run(keyword, reference, "push_keyword.pine"),
        filename=filename, line=line, col=col,
    )
    parent_positional = run(positional, reference, "push_positional.pine")
    assert parent_positional["ok"] is True, parent_positional
    assert isinstance(parent_positional["cpp"], str) and parent_positional["cpp"]
    current_keyword = run(keyword, REPO_ROOT, "push_keyword.pine")
    assert current_keyword["ok"] is True, current_keyword
    assert not [
        entry for entry in current_keyword["diagnostics"]
        if entry["severity"] == "error"
    ], current_keyword["diagnostics"]
    # Complete C++, nothing normalized or discarded.
    assert current_keyword["cpp"] == parent_positional["cpp"]


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
@pytest.mark.parametrize("directive,code,arguments", LEGACY_PUBLIC_REFUSALS)
@pytest.mark.parametrize("value", ("na", "false"))
def test_parent_legacy_main_keeps_its_earlier_public_version_refusal(
    namespace, template, directive, code, arguments, value,
    scalar_parent_codegen, tmp_path,
):
    source = _collection_source(
        namespace, "bool", template.format(value=value),
        header=directive + 'strategy("Collection argument binding")\n',
    )
    outcome = _assert_parent_outcome(source, scalar_parent_codegen, tmp_path)
    # _ensure_pine_v6 precedes call visits in both trees. This preservation
    # control cannot establish a behavioral RED for collection admission.
    assert outcome["ok"] is False, outcome
    assert len(outcome["diagnostics"]) == 1, outcome
    assert outcome["diagnostics"][0]["code"] == code, outcome
    assert outcome["diagnostics"][0]["args"] == arguments, outcome
    assert outcome["diagnostics"][0]["line"] == 1, outcome
    assert outcome["diagnostics"][0]["col"] == 1, outcome


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
def test_parent_collection_v6_keeps_its_whole_refusal_outcome(
    namespace, template, scalar_parent_codegen, tmp_path,
):
    source = _collection_source(namespace, "bool", template.format(value="na"))
    outcome = _assert_parent_outcome(source, scalar_parent_codegen, tmp_path)
    assert outcome["ok"] is False, outcome
    assert len(outcome["diagnostics"]) == 1, outcome
    assert outcome["diagnostics"][0]["code"] == "PF-E2067", outcome
    assert outcome["diagnostics"][0]["args"] == {"kind": namespace}, outcome


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
def test_parent_collection_v6_keeps_its_false_twin_outcome(
    namespace, template, scalar_parent_codegen, tmp_path,
):
    source = _collection_source(namespace, "bool", template.format(value="false"))
    if template == PUSH_KEYWORD:
        positional = _collection_source(
            namespace, "bool", PUSH_POSITIONAL.format(value="false"),
        )
        _assert_keyword_push_binds_its_value(
            (source, None), (positional, None), scalar_parent_codegen, tmp_path,
            filename="<input>", line=4, col=12,
        )
        return
    outcome = _assert_parent_outcome(source, scalar_parent_codegen, tmp_path)
    assert outcome["ok"] is True, outcome


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
@pytest.mark.parametrize("value", ("na", "false"))
def test_parent_collection_imported_v5_keeps_its_whole_outcome(
    namespace, template, value, scalar_parent_codegen, tmp_path,
):
    from tests._pine_libraries import Layout

    setup, read = {
        "array": (
            "array<bool> values = array.new<bool>(2, false)",
            "array.get(values, 0)",
        ),
        "matrix": (
            "matrix<bool> values = matrix.new<bool>(2, 2, false)",
            "matrix.get(values, 0, 0)",
        ),
    }[namespace]
    source = (
        HEADER + "import local/Legacy/1 as legacy\n"
        + "value = legacy.check()\nplot(value ? 1 : 0)\n"
    )

    def imported(call, directory):
        """The probe and the environment of the library spelling ``call``."""
        library = (
            '//@version=5\nlibrary("Legacy")\nexport check() =>\n'
            + f"    {setup}\n    {call.format(value=value)}\n    {read}\n"
        )
        layout = Layout(tmp_path / directory)
        digest = layout.add_library("local/Legacy/1", data=library.encode("utf-8"))
        layout.add_probe("legacy", source.encode("utf-8"), {"local/Legacy/1": digest})
        return source, layout.env

    if template == PUSH_KEYWORD:
        _assert_keyword_push_binds_its_value(
            imported(PUSH_KEYWORD, "library-env"),
            imported(PUSH_POSITIONAL, "library-env-positional"),
            scalar_parent_codegen, tmp_path,
            filename="local/Legacy/1", line=5, col=16,
        )
        return
    _source, extra_env = imported(template, "library-env")
    outcome = _assert_parent_outcome(
        source, scalar_parent_codegen, tmp_path, extra_env=extra_env,
    )
    assert outcome["ok"] is True, outcome


@pytest.mark.parametrize("template", (
    pytest.param(
        'map.put(map.new<string, bool>(), "key", {value})',
        id="map-namespace",
    ),
    pytest.param(
        'map.new<string, bool>().put("key", {value})',
        id="map-receiver",
    ),
    pytest.param("array.new<bool>(2, {value})", id="array-constructor"),
    pytest.param('input.bool({value}, "Flag")', id="input-default"),
))
@pytest.mark.parametrize(
    "directive", (*LEGACY_MAIN_DIRECTIVES, pytest.param("//@version=6\n", id="v6-main")),
)
def test_unversioned_parent_analyzer_contract_still_refuses_na(
    template, directive,
):
    # A direct policy unit, below the public main-version gate. This does not
    # claim non-v6 main reachability through analyze() or transpile().
    from pineforge_codegen.analyzer import Analyzer

    program, call = _parse_collection_call(directive, template)
    analyzer = Analyzer(program)
    with pytest.raises(CompileError) as caught:
        analyzer._check_registered_parent_bool_na(call)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2044"
    assert diagnostic.message == "Invalid bool value: a bool cannot be na in Pine v6."
    assert diagnostic.args == {}
    statement = template.format(value="na")
    assert diagnostic.location.line == len(directive.splitlines()) + 1
    assert diagnostic.location.col == statement.index("na") + 1
    assert diagnostic.location.end_col == statement.index("na") + 3


def _parse_collection_call(directive, template):
    from pineforge_codegen.ast_nodes import FuncCall, MemberAccess
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    source = directive + template.format(value="na") + "\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    call = program.body[0].expr
    assert isinstance(call, FuncCall)
    assert isinstance(call.callee, MemberAccess)
    return program, call


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
@pytest.mark.parametrize("directive,version", (
    pytest.param("//@version=5\n", 5, id="v5"),
    pytest.param("", None, id="no-directive"),
))
def test_inactive_parent_analyzer_check_does_not_query_the_receiver(
    namespace, template, directive, version, monkeypatch,
):
    # This isolates analyzer ordering; it does not claim public-call admission.
    from pineforge_codegen.analyzer import Analyzer

    program, call = _parse_collection_call(directive, template)
    assert program.version == version
    analyzer = Analyzer(program)

    def unexpected_type_query(_value):
        raise AssertionError(f"Inactive {namespace} policy queried a receiver type")

    monkeypatch.setattr(analyzer, "_type_spec_from_expr", unexpected_type_query)
    analyzer._check_registered_parent_bool_na(call)


@pytest.mark.parametrize("namespace,template", VERSIONED_PARENT_CALLS)
@pytest.mark.parametrize("directive", LEGACY_MAIN_DIRECTIVES)
def test_inactive_parent_binder_does_not_query_argument_types(
    namespace, template, directive,
):
    # Exercise the binder independently of the analyzer's earlier filter.
    from pineforge_codegen.intrinsic_binding import bind_parent_na_contract
    from pineforge_codegen.signatures import get_intrinsic

    program, call = _parse_collection_call(directive, template)
    intrinsic = get_intrinsic(namespace, call.callee.member)
    assert intrinsic is not None
    receiver = call.callee.object
    if receiver.name == namespace:
        receiver = None

    def unexpected_type_query(_value):
        raise AssertionError(f"Inactive {namespace} policy queried an argument type")

    assert bind_parent_na_contract(
        call, intrinsic, unexpected_type_query,
        pine_version=program.version, receiver=receiver,
    ) is None
