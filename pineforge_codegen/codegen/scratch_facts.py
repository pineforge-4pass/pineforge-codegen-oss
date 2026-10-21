"""Complete programming vocabulary for the pre-begin scratch certificate.

Lane V of CR-221 scope A. A generated strategy is classified before a run as
IN-SUBSET when every identifier string that can reach the scratch tables is a
finite compile-time set, or OUT-OF-SUBSET otherwise. UNKNOWN IS OUT: any
expression this module does not recognise makes its roster incomplete.

The five rosters are the F contract's S,F,E,H,J
(include/pineforge/detail/scratch_facts.hpp and docs/design/scratch-facts.md):
possible source IDs, from_entry strings, exit IDs, cohort-for arguments and
eligible entry/order-fill IDs. The certificate bytes are wire v1: one version
byte, then each set as one complete byte, a little-endian u64 count, and that
many entries of a little-endian u64 byte length followed by the raw bytes.
Entries are sorted by unsigned byte value, deduplicated, with no padding.

Facts only. This module does not refuse a run, does not emit an export and does
not change the trading computation, any hash leaf or any default.
"""

from dataclasses import dataclass
import struct

from ..ast_nodes import (
    Assignment,
    BinOp,
    BoolLiteral,
    ColorLiteral,
    ForInStmt,
    ForStmt,
    FuncCall,
    FuncDef,
    Identifier,
    MemberAccess,
    MethodDef,
    NaLiteral,
    NumberLiteral,
    Program,
    StringLiteral,
    Subscript,
    SwitchStmt,
    Ternary,
    TupleAssign,
    VarDecl,
)
from ..limits import syntax_children
from .. import signatures as sigs
from .capabilities import _expression


SCRATCH_FACTS_VERSION = 1
# Wire order and the accessor names of ScratchVocabularyFacts.
SCRATCH_FACT_SETS = ("S", "F", "E", "H", "J")

# Source IDs, exit IDs, exit from_entry arguments and eligible fill IDs, keyed
# by the lowered Pine call name.
_ENTRY_CALLS = ("strategy.entry", "strategy.order")
_EXIT_CALLS = ("strategy.exit", "strategy.close", "strategy.cancel")
_ALL_ORDER_CALLS = frozenset(_ENTRY_CALLS + _EXIT_CALLS
                             + ("strategy.close_all", "strategy.cancel_all"))

# Engine-created source labels. The preopen label and __margin_call__ are named
# by the accepted V contract (o1roots A0; the paired engine builds them at
# src/source/pine_adapter.cpp:5894 and :24558); the close-family labels are the
# same construction (:9431, :9483, :9783, :9791, :10645, :21007, :21047,
# :21067) and are included so S stays a superset of every placement source ID
# the adapter can create.
_INTERNAL_SOURCE_LABELS = frozenset((
    "__margin_call__",
    "__close__",
    "__pine_close_all",
    "__intraday_loss__",
    "__intraday_cap_close__",
))
_INTERNAL_SOURCE_PREFIXES = ("__margin_preopen__", "__close__")

# Values that are series at every bar and can never be a compile-time string.
_SERIES_NAMES = frozenset((
    "open", "high", "low", "close", "volume", "time", "time_close",
    "bar_index", "last_bar_index", "last_bar_time", "timenow",
    "hl2", "hlc3", "ohlc4", "hlcc4",
))

# input.* functions whose value is a string, plus the generic input() form.
_STRING_INPUTS = frozenset((
    "input.string", "input.text_area", "input.session", "input.symbol",
    "input.timeframe", "input.color", "input",
))

# Bounded call depth for user-function parameter unions.
_MAX_CALL_DEPTH = 6

# Marker for a definition whose value is not recoverable from the AST (a
# compound assignment or a destructuring target).
_UNRESOLVED = object()


class _Reason:
    """Short, stable OUT reason codes. Never reword one: tests pin them."""

    SERIES_VALUE = "series_value"
    LOOP_VARIABLE = "loop_variable"
    UNBOUND_IDENTIFIER = "unbound_identifier"
    SELF_REFERENCE = "self_reference"
    UNRESOLVED_DEFINITION = "unresolved_definition"
    STR_TOSTRING = "str_tostring"
    STRING_CONCAT = "string_concat"
    NON_STRING_EXPRESSION = "non_string_expression"
    NON_STRING_LITERAL = "non_string_literal"
    SUBSCRIPT = "subscript"
    MEMBER_ACCESS = "member_access"
    ARRAY_READ = "array_read"
    REQUEST_SECURITY = "request_security"
    METHOD_CALL = "method_call"
    USER_FUNCTION_NONCONSTANT = "user_function_nonconstant"
    RECURSIVE_CALL = "recursive_call"
    CALL_DEPTH = "call_depth"
    MISSING_ARGUMENT = "missing_argument"
    INPUT_DEFAULT = "input_default"
    ORDER_CALL_IN_METHOD = "order_call_in_method"
    ORDER_CALL_IN_UNCALLED_FUNCTION = "order_call_in_uncalled_function"
    UNRECOGNIZED_CALL = "unrecognized_call"
    UNRECOGNIZED_EXPRESSION = "unrecognized_expression"


@dataclass(frozen=True)
class _Result:
    """A finite value set, its completeness proof, and any OUT reasons."""

    complete: bool = True
    values: frozenset = frozenset()
    reasons: frozenset = frozenset()


def _incomplete(reason: str, values=frozenset(), reasons=frozenset()) -> _Result:
    return _Result(False, frozenset(values), frozenset((reason,)) | frozenset(reasons))


def _union(results) -> _Result:
    values = frozenset()
    reasons = frozenset()
    complete = True
    for result in results:
        values |= result.values
        reasons |= result.reasons
        complete = complete and result.complete
    return _Result(complete, values, reasons)


def _join(left: _Result, right: _Result) -> _Result:
    return _Result(left.complete and right.complete, left.values | right.values,
                   left.reasons | right.reasons)


def _callee_name(callee) -> str | None:
    """The lowered dotted name of a call target, or None when it is not a name."""

    if isinstance(callee, Identifier):
        return callee.name
    if isinstance(callee, MemberAccess):
        return _expression(callee)
    return None


def _parameter_names(namespace: str | None, name: str) -> list:
    return list(sigs.get_param_names(namespace, name) or [])


def _merge_arguments(node: FuncCall, namespace: str | None, name: str) -> dict:
    """Map positional and keyword arguments to parameter names, as the emitter does."""

    params = _parameter_names(namespace, name)
    merged = {}
    for index, argument in enumerate(node.args):
        if index < len(params):
            merged[params[index]] = argument
    merged.update(node.kwargs)
    return merged


def _parse_parameters(params) -> tuple:
    """Function parameter names, with a flag for a written default.

    The parser keeps a default in the parameter string, so the default
    expression itself is not available and is conservatively unresolved.
    """

    names = []
    for parameter in params:
        text = parameter if isinstance(parameter, str) else str(parameter)
        if "=" in text:
            names.append((text.split("=", 1)[0].strip(), True))
        else:
            names.append((text.strip(), False))
    return tuple(names)


class _Program:
    """Scope-aware index of one analyzed program."""

    def __init__(self, program: Program) -> None:
        self.program = program
        self.definitions: dict[str, list] = {}
        self.loop_variables: set[str] = set()
        self.functions: dict[str, FuncDef] = {}
        self.methods: list[MethodDef] = []
        self.calls_by_name: dict[str, list] = {}
        self.order_calls: list = []
        self._index()

    def _index(self) -> None:
        stack = [(self.program, None, None)]
        while stack:
            node, function, method = stack.pop()
            if isinstance(node, FuncDef):
                self.functions.setdefault(node.name, node)
                children_scope = (node, None)
            elif isinstance(node, MethodDef):
                self.methods.append(node)
                children_scope = (None, node)
            else:
                children_scope = (function, method)
            self._record(node, function, method)
            for child in syntax_children(node):
                stack.append((child,) + children_scope)

    def _record(self, node, function, method) -> None:
        scope = function if function is not None else method
        if isinstance(node, VarDecl) and node.name:
            self.definitions.setdefault(node.name, []).append((scope, node.value))
        elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
            value = node.value if node.op == ":=" else _UNRESOLVED
            self.definitions.setdefault(node.target.name, []).append((scope, value))
        elif isinstance(node, TupleAssign):
            for name in node.names:
                self.definitions.setdefault(name, []).append((scope, _UNRESOLVED))
        elif isinstance(node, (ForStmt, ForInStmt)):
            names = [getattr(node, "var", None)]
            names.extend(getattr(node, "vars", None) or ())
            for name in names:
                if name:
                    self.loop_variables.add(name)
        elif isinstance(node, FuncCall):
            name = _callee_name(node.callee)
            if isinstance(node.callee, Identifier) and name not in _ALL_ORDER_CALLS:
                # A user function may be defined after its call site, so the
                # index records every name-bound call and resolves later.
                self.calls_by_name.setdefault(name, []).append((node, function, method))
            if name in _ALL_ORDER_CALLS:
                self.order_calls.append((name, node, function, method))


class _Analysis:
    def __init__(self, program: Program) -> None:
        self.index = _Program(program)

    # -- expression classification ------------------------------------
    def classify(self, node, scope, env, visiting=frozenset()) -> _Result:
        if node is None:
            return _incomplete(_Reason.UNRECOGNIZED_EXPRESSION)
        if isinstance(node, StringLiteral):
            return _Result(True, frozenset((node.value,)), frozenset())
        if isinstance(node, (NumberLiteral, BoolLiteral)):
            return _incomplete(_Reason.NON_STRING_LITERAL)
        if isinstance(node, NaLiteral):
            return _incomplete(_Reason.NON_STRING_LITERAL)
        if isinstance(node, ColorLiteral):
            return _incomplete(_Reason.NON_STRING_LITERAL)
        if isinstance(node, Ternary):
            # Both branches are enumerated, so a bar-varying condition cannot
            # enlarge the set; only an incomplete branch can.
            return _join(self.classify(node.true_val, scope, env, visiting),
                         self.classify(node.false_val, scope, env, visiting))
        if isinstance(node, SwitchStmt):
            # Every arm is a finite alternative; the selector cannot enlarge
            # the set, but an incomplete arm does.
            results = [self._body_value(body, scope, env, visiting)
                       for _case, body in node.cases]
            results.append(self._body_value(node.default_body, scope, env, visiting))
            return _union(results)
        if isinstance(node, BinOp):
            left = self.classify(node.left, scope, env, visiting)
            right = self.classify(node.right, scope, env, visiting)
            if node.op != "+":
                return _incomplete(_Reason.NON_STRING_EXPRESSION,
                                   left.values | right.values)
            if not (left.complete and right.complete):
                return _Result(False, left.values | right.values,
                               left.reasons | right.reasons
                               | frozenset((_Reason.STRING_CONCAT,)))
            return _Result(True, frozenset(a + b for a in left.values
                                           for b in right.values), frozenset())
        if isinstance(node, Identifier):
            return self._identifier(node.name, scope, env, visiting)
        if isinstance(node, FuncCall):
            return self._call(node, scope, env, visiting)
        if isinstance(node, Subscript):
            return _incomplete(_Reason.SUBSCRIPT)
        if isinstance(node, MemberAccess):
            return _incomplete(_Reason.MEMBER_ACCESS)
        # Unknown node kind: UNKNOWN IS OUT.
        return _incomplete(_Reason.UNRECOGNIZED_EXPRESSION)

    def _body_value(self, body, scope, env, visiting) -> _Result:
        """The value of a block used as an expression: its last expression."""

        for statement in reversed(list(body or ())):
            expression = getattr(statement, "expr", None)
            if expression is not None:
                return self.classify(expression, scope, env, visiting)
        return _incomplete(_Reason.UNRECOGNIZED_EXPRESSION)

    def _identifier(self, name: str, scope, env, visiting) -> _Result:
        if env is not None and name in env:
            return env[name]
        if name in self.index.loop_variables:
            return _incomplete(_Reason.LOOP_VARIABLE)
        if name in visiting:
            return _incomplete(_Reason.SELF_REFERENCE)
        definitions = [value for owner, value in self.index.definitions.get(name, ())
                       if owner is scope]
        if not definitions:
            reason = _Reason.SERIES_VALUE if name in _SERIES_NAMES else _Reason.UNBOUND_IDENTIFIER
            return _incomplete(reason)
        results = []
        for value in definitions:
            if value is _UNRESOLVED:
                results.append(_incomplete(_Reason.UNRESOLVED_DEFINITION))
            else:
                results.append(self.classify(value, scope, env, visiting | {name}))
        return _union(results)

    def _argument_reasons(self, node: FuncCall, scope, env, visiting) -> frozenset:
        """Reasons carried by a call's arguments, without their values.

        The call itself is already OUT; surfacing why an argument is dynamic
        keeps the receipt's reason list readable and stable.
        """

        reasons = frozenset()
        arguments = list(node.args) + [value for value in node.kwargs.values()
                                       if value is not None]
        for argument in arguments:
            reasons |= self.classify(argument, scope, env, visiting).reasons
        return reasons

    def _call(self, node: FuncCall, scope, env, visiting) -> _Result:
        name = _callee_name(node.callee)
        if name is None:
            return _incomplete(_Reason.METHOD_CALL)
        if name == "str.tostring":
            return _incomplete(_Reason.STR_TOSTRING,
                               reasons=self._argument_reasons(node, scope, env, visiting))
        if name == "array.get":
            return _incomplete(_Reason.ARRAY_READ,
                               reasons=self._argument_reasons(node, scope, env, visiting))
        if name in ("request.security", "request.security_lower_tf"):
            return _incomplete(_Reason.REQUEST_SECURITY)
        if name in _STRING_INPUTS:
            namespace, function = name.rsplit(".", 1) if "." in name else (None, name)
            default = _merge_arguments(node, namespace, function).get("defval")
            if isinstance(default, StringLiteral):
                return _Result(True, frozenset((default.value,)), frozenset())
            return _incomplete(_Reason.INPUT_DEFAULT)
        if isinstance(node.callee, MemberAccess):
            # A method or a namespace call this module does not model.
            return _incomplete(_Reason.METHOD_CALL)
        function = self.index.functions.get(name)
        if function is not None:
            return self._function_return(function, node, scope, env, visiting)
        return _incomplete(_Reason.UNRECOGNIZED_CALL)

    def _function_return(self, function: FuncDef, call: FuncCall, scope, env, visiting) -> _Result:
        if function.name in visiting:
            return _incomplete(_Reason.RECURSIVE_CALL)
        # ``f(x) => expr`` is is_single_expr; a one-line body the parser kept
        # as a single ExprStmt returns the same value and is accepted too. Any
        # longer body is OUT: its result is not recoverable from the AST.
        if not function.is_single_expr and not (
                len(function.body) == 1
                and getattr(function.body[0], "expr", None) is not None):
            return _incomplete(_Reason.USER_FUNCTION_NONCONSTANT)
        return_expression = None
        for statement in function.body:
            expression = getattr(statement, "expr", None)
            if expression is not None:
                return_expression = expression
        if return_expression is None:
            return _incomplete(_Reason.USER_FUNCTION_NONCONSTANT)
        parameters = _parse_parameters(function.params)
        # Positional mapping for a user function uses its own parameter order.
        arguments = {}
        for index, argument in enumerate(call.args):
            if index < len(parameters):
                arguments[parameters[index][0]] = argument
        arguments.update(call.kwargs)
        bound = {}
        for parameter, has_default in parameters:
            argument = arguments.get(parameter)
            if argument is None:
                if has_default:
                    bound[parameter] = _incomplete(_Reason.INPUT_DEFAULT)
                else:
                    bound[parameter] = _incomplete(_Reason.MISSING_ARGUMENT)
            else:
                bound[parameter] = self.classify(argument, scope, env, visiting)
        return self.classify(return_expression, function, bound,
                             visiting | {function.name})

    # -- user-function parameter environments -------------------------
    def environment(self, function: FuncDef, depth=0, stack=frozenset()) -> dict | None:
        """The union of every argument passed to each parameter.

        Returns None for a function with no recognised call site: its body is
        unreachable and is not analysed. A call it cannot see (a method-style
        call or a library boundary) is not treated as absent: the caller marks
        the roster incomplete instead.
        """

        sites = self.index.calls_by_name.get(function.name, ())
        if not sites:
            return None
        if function.name in stack or depth > _MAX_CALL_DEPTH:
            return {name: _incomplete(_Reason.CALL_DEPTH)
                    for name, _default in _parse_parameters(function.params)}
        parameters = _parse_parameters(function.params)
        environment = {}
        for index, (parameter, has_default) in enumerate(parameters):
            results = []
            for call, caller, _caller_method in sites:
                argument = None
                if index < len(call.args):
                    argument = call.args[index]
                elif parameter in call.kwargs:
                    argument = call.kwargs[parameter]
                if argument is None:
                    results.append(_incomplete(_Reason.INPUT_DEFAULT if has_default
                                               else _Reason.MISSING_ARGUMENT))
                    continue
                caller_environment = None
                if caller is not None:
                    caller_environment = self.environment(caller, depth + 1,
                                                          stack | {function.name})
                results.append(self.classify(argument, caller, caller_environment,
                                             frozenset()))
            environment[parameter] = _union(results)
        return environment

    # -- the five rosters ---------------------------------------------
    def rosters(self) -> dict:
        entry_ids = []
        exit_ids = []
        from_entries = []
        exit_family_present = False
        for name, call, function, method in self.index.order_calls:
            if name in _ENTRY_CALLS:
                entry_ids.append(self._argument(call, "strategy", name.rsplit(".", 1)[1],
                                                "id", function, method))
            elif name in _EXIT_CALLS:
                exit_ids.append(self._argument(call, "strategy", name.rsplit(".", 1)[1],
                                               "id", function, method))
            if name in ("strategy.exit", "strategy.close", "strategy.close_all"):
                exit_family_present = True
            if name == "strategy.exit":
                from_entries.append(self._argument(call, "strategy", "exit",
                                                   "from_entry", function, method))
        entries = _union(entry_ids) if entry_ids else _Result()
        exits = _union(exit_ids) if exit_ids else _Result()
        # from_entry is not only the literal of strategy.exit. A close
        # placement copies its own ID on the non-default-FIFO path
        # (src/source/pine_adapter.cpp:10413-10415); a preopen margin slice
        # copies the opening source ID (":5894-5906"). With any placement
        # possible, F is therefore a superset of the empty string and every
        # known entry/exit ID, and its own completeness needs both rosters.
        from_result = _union(from_entries) if from_entries else _Result()
        from_values = set(from_result.values)
        if entries.values or exit_family_present:
            from_values |= set(entries.values) | set(exits.values)
            from_values.add("")
            from_result = _Result(from_result.complete and entries.complete
                                  and exits.complete, frozenset(from_values),
                                  from_result.reasons | entries.reasons | exits.reasons)
        else:
            from_result = _Result(from_result.complete, frozenset(from_values),
                                  from_result.reasons)

        # S: every PlacementSnapshot.source_id. Openings carry the entry/order
        # ID; exit and close placements carry their own ID
        # (src/source/pine_adapter.cpp:10413, :12002), and the adapter adds the
        # closed internal label families below.
        source_values = set(entries.values) | set(exits.values) | set(_INTERNAL_SOURCE_LABELS)
        for prefix in _INTERNAL_SOURCE_PREFIXES:
            for value in entries.values:
                source_values.add(prefix + value)
        for value in exits.values:
            source_values.add("__close__" + value)
        # The close-family prefixes depend on the exit IDs, so a dynamic exit
        # ID makes S incomplete even when every entry ID is a literal.
        source_ids = _Result(entries.complete and exits.complete, frozenset(source_values),
                             entries.reasons | exits.reasons)

        # H: every cohort_for argument; the adapter passes source IDs and
        # from_entry values only (adapter.cpp:5035, :5899, :12478, :6660).
        cohorts = _join(source_ids, from_result)

        # J: eligible entry/order-fill IDs are the opening source IDs.
        eligible = entries
        return {"S": source_ids, "F": from_result, "E": exits,
                "H": cohorts, "J": eligible}

    def _argument(self, call: FuncCall, namespace: str, name: str, parameter: str,
                  function, method) -> _Result:
        if method is not None:
            return _incomplete(_Reason.ORDER_CALL_IN_METHOD)
        environment = None
        if function is not None:
            environment = self.environment(function)
            if environment is None:
                return _incomplete(_Reason.ORDER_CALL_IN_UNCALLED_FUNCTION)
        arguments = _merge_arguments(call, namespace, name)
        node = arguments.get(parameter)
        # An omitted id/from_entry is the emitter's literal empty string.
        if node is None:
            return _Result(True, frozenset(("",)), frozenset())
        return self.classify(node, function, environment, frozenset())


def analyze_program(program: Program) -> dict:
    """Return the five rosters of ``program`` as ``_Result`` values."""

    return _Analysis(program).rosters()


def encode_certificate(rosters) -> bytes:
    """Wire v1 bytes for five ``(complete, strings)`` rosters in S,F,E,H,J order.

    Independent of dict and set iteration order: every roster is sorted by its
    unsigned UTF-8 bytes and deduplicated before it is written.
    """

    wire = bytearray((SCRATCH_FACTS_VERSION,))
    for complete, strings in rosters:
        encoded = sorted({value.encode("utf-8") for value in strings})
        wire.append(1 if complete else 0)
        wire += struct.pack("<Q", len(encoded))
        for value in encoded:
            wire += struct.pack("<Q", len(value))
            wire += value
    return bytes(wire)


def classify_program(program: Program) -> dict:
    """The additive ``scratch_facts`` receipt object for one program."""

    rosters = analyze_program(program)
    ordered = [(rosters[name].complete, rosters[name].values)
               for name in SCRATCH_FACT_SETS]
    complete = all(result.complete for result in rosters.values())
    reasons = set()
    for result in rosters.values():
        reasons |= result.reasons
    return {
        "version": SCRATCH_FACTS_VERSION,
        "classification": "in_subset" if complete else "out_of_subset",
        "certificate": encode_certificate(ordered).hex(),
        "reasons": sorted(reasons),
    }
