"""Source evidence used exclusively to choose a counted loop's numeric family."""

from __future__ import annotations

from .ast_nodes import (
    Assignment, BinOp, ExprStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt,
    MemberAccess, NaLiteral, NumberLiteral, Subscript, SwitchStmt, Ternary,
    TupleAssign, TupleLiteral, UnaryOp, VarDecl,
)
from .limits import iter_ast_nodes
from .errors import CompileError, Diagnostic, Level, Phase
from .symbols import PineType, TypeSpec
from . import signatures as sigs


_INT = TypeSpec.primitive("int")
_FLOAT = TypeSpec.primitive("float")
_NA = TypeSpec.primitive("na")

# These calls declare a float result independently of their operands. Do not
# use get_return_type(... ) == FLOAT here: _sig defaults to FLOAT even for
# numeric overloads such as ta.median(int, int).
_FLOAT_CALLS = {
    None: frozenset({"float"}),
    "input": frozenset({"float", "source"}),
    "str": frozenset({"tonumber"}),
    "math": frozenset({
        "avg", "sum", "pow", "sqrt", "exp", "log", "log10", "random",
        "sign", "round_to_mintick", "sin", "cos", "tan", "asin", "acos",
        "atan", "atan2", "todegrees", "toradians",
    }),
    "ta": frozenset({
        "sma", "ema", "rma", "wma", "hma", "vwma", "alma", "swma",
        "linreg", "rsi", "stoch", "cci", "mfi", "mom", "roc", "cmo",
        "tsi", "wpr", "cog", "bbw", "kcw", "atr", "tr", "stdev",
        "variance", "sar", "percentrank", "percentile_linear_interpolation",
        "dev", "cum", "correlation", "rci", "highest", "lowest",
    }),
}
_ARRAY_ELEMENTS = frozenset({
    "get", "first", "last", "pop", "shift", "remove", "min", "max",
    "avg", "sum", "median", "mode", "stdev", "variance", "range",
    "percentile_nearest_rank", "percentile_linear_interpolation",
})


def _hint_spec(hint):
    hint = (hint or "").replace(" ", "")
    if hint in {"int", "float"}:
        return _FLOAT if hint == "float" else _INT
    if hint.endswith("[]"):
        element = _hint_spec(hint[:-2])
    elif hint.startswith("array<") and hint.endswith(">"):
        element = _hint_spec(hint[6:-1])
    else:
        return None
    return TypeSpec.array(element) if element is not None else None


def _terminal(body):
    node = body[-1] if body else None
    return node.expr if isinstance(node, ExprStmt) else node


def _join(specs):
    specs = [spec for spec in specs if spec != _NA]
    if any(spec == _FLOAT for spec in specs):
        return _FLOAT
    if specs and all(spec == _INT for spec in specs):
        return _INT
    if specs and specs[0] is not None and all(spec == specs[0] for spec in specs):
        return specs[0]
    return None


class LoopTypeSources:
    """Immutable syntax inventory; no inferred C++ or analyzer fallback types."""

    def __init__(self, ast):
        self.declarations = {}
        self.functions = {}
        self.parameter_hints = {}
        self.reassigned_names = set()
        for node, _depth in iter_ast_nodes(ast):
            # Conservatively deny constant proofs for a name written anywhere.
            if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                self.reassigned_names.add(node.target.name)
            if isinstance(node, (VarDecl, TupleAssign, ForStmt, FuncDef)):
                self.declarations[id(node)] = node
            if isinstance(node, FuncDef):
                self.functions.setdefault(node.name, []).append(node)
                hints = (node.annotations or {}).get("param_type_hints", ())
                for index, name in enumerate(node.params):
                    hint = hints[index] if index < len(hints) else None
                    self.parameter_hints.setdefault(
                        (f"func_{node.name}", name), [],
                    ).append(_hint_spec(hint))


class _CounterQuery:
    def __init__(self, sources, bindings, scopes):
        self.sources = sources
        self.bindings = bindings
        self.scopes = scopes
        # All declaration routes, including Identifier -> VarDecl, share this
        # query-local memo. A provisional unknown also breaks source cycles.
        self.declaration_specs = {}
        self._value_query = None

    def declaration(self, node):
        key = id(node)
        if key not in self.declaration_specs:
            self.declaration_specs[key] = None
            if isinstance(node, VarDecl):
                spec = (_hint_spec(node.type_hint) if node.type_hint
                        else self.infer(node.value))
            elif isinstance(node, TupleAssign):
                spec = (self.infer(node.value)
                        if isinstance(node.value, (FuncCall, TupleLiteral)) else None)
            elif isinstance(node, ForStmt):
                spec = self.counter_spec(node.start, node.step)
            elif isinstance(node, FuncDef):
                spec = self.infer(_terminal(node.body))
            else:
                spec = None
            self.declaration_specs[key] = spec
        return self.declaration_specs[key]

    def value_spec(self, node):
        if self._value_query is None:
            self._value_query = _CounterValueQuery(self)
        return self._value_query.infer(node)

    def counter_spec(self, start, step, *, counter_name=None, step_location=None):
        check_spec = self.infer(start)
        step_spec = self.infer(step)
        # TradingView checks int/int division as int even though its value
        # can be fractional. Keep that check separate from counter storage.
        if check_spec == _INT and step_spec == _FLOAT and counter_name is not None:
            step_type, counter_type = "float", "int"
            raise CompileError([Diagnostic(
                level=Level.ERROR,
                phase=Phase.ANALYZER,
                location=step_location or step.loc,
                message=(
                    f"The step of a 'for' loop has type {step_type}, "
                    f"but counter '{counter_name}' has type {counter_type}."
                ),
                hint="Use an int step or a float counter.",
            )])
        value_spec = self.value_spec(start)
        # Unknown starts with a positively float step keep the float route;
        # an int fallback is still not positive int evidence.
        if value_spec == _FLOAT or (check_spec in (None, _NA) and step_spec == _FLOAT):
            return _FLOAT
        return _INT

    def infer(self, node):
        if isinstance(node, NumberLiteral):
            return _FLOAT if isinstance(node.value, float) else _INT
        if isinstance(node, NaLiteral):
            return _NA
        if isinstance(node, VarDecl):
            return self.declaration(node)
        if isinstance(node, Identifier):
            binding = self.bindings.get(id(node))
            if binding is not None:
                declaration = self.sources.declarations.get(binding[0])
                spec = self.declaration(declaration)
                if isinstance(declaration, TupleAssign):
                    index = declaration.names.index(binding[1])
                    return spec[index] if isinstance(spec, tuple) and index < len(spec) else None
                return spec
            scope = self.scopes.get(id(node))
            hints = self.sources.parameter_hints.get((scope, node.name))
            if hints:
                # An untyped parameter stays unknown even if call-site
                # analysis happens to store a FLOAT for it.
                return hints[0] if all(hint == hints[0] for hint in hints) else None
            # Analyzer builtins occupy the global scope without a source
            # declaration. Authored globals and local shadows resolved above.
            if scope in (None, "global"):
                family = sigs.BUILTIN_VARIABLES.get(node.name)
                if family == PineType.FLOAT:
                    return _FLOAT
                if family == PineType.INT:
                    return _INT
            return None
        if isinstance(node, UnaryOp) and node.op in {"+", "-"}:
            return self.infer(node.operand)
        if isinstance(node, BinOp) and node.op in {"+", "-", "*", "/", "%"}:
            return _join([self.infer(node.left), self.infer(node.right)])
        if isinstance(node, Subscript):
            spec = self.infer(node.object)
            return spec if spec in (_INT, _FLOAT) else None
        if isinstance(node, Ternary):
            return _join([self.infer(node.true_val), self.infer(node.false_val)])
        if isinstance(node, IfStmt):
            return _join([
                self.infer(_terminal(node.body)),
                self.infer(_terminal(node.else_body)),
            ])
        if isinstance(node, SwitchStmt):
            return _join([
                *(self.infer(_terminal(body)) for _case, body in node.cases),
                self.infer(_terminal(node.default_body)),
            ])
        if isinstance(node, TupleLiteral):
            return tuple(self.infer(element) for element in node.elements)
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            name = f"{node.object.name}.{node.member}"
            for variables in (
                sigs.BUILTIN_VARIABLES, sigs.SYMINFO_VARIABLES,
                sigs.STRATEGY_VARIABLES, sigs.TIMEFRAME_VARIABLES,
            ):
                family = variables.get(name)
                if family == PineType.FLOAT:
                    return _FLOAT
                if family == PineType.INT:
                    return _INT
            return None
        if isinstance(node, FuncCall):
            return self.call(node)
        return None

    def call(self, node):
        namespace = None
        if isinstance(node.callee, Identifier):
            name = node.callee.name
            definitions = self.sources.functions.get(name)
            if definitions:
                specs = [self.declaration(definition) for definition in definitions]
                return specs[0] if all(spec == specs[0] for spec in specs) else None
        elif isinstance(node.callee, MemberAccess):
            name = node.callee.member
            receiver = node.callee.object
            if isinstance(receiver, Identifier):
                namespace = receiver.name
            array_spec = self.infer(receiver)
            if isinstance(array_spec, TypeSpec) and array_spec.kind == "array":
                return self.array_result(name, array_spec)
        else:
            return None

        def argument(index, keyword):
            return node.args[index] if index < len(node.args) else node.kwargs.get(keyword)

        if namespace == "math" and name in {"abs", "min", "max"}:
            return _join([self.infer(arg) for arg in (*node.args, *node.kwargs.values())])
        if namespace == "math" and name == "round":
            return _FLOAT if len(node.args) > 1 or "precision" in node.kwargs else _INT
        if namespace is None and name in {"nz", "fixnan", "input"}:
            return self.infer(argument(0, "defval" if name == "input" else "source"))
        if namespace == "ta" and name in {
            "change", "valuewhen", "median", "mode", "max", "min",
            "range", "percentile_nearest_rank",
        }:
            source = self.infer(argument(1 if name == "valuewhen" else 0, "source"))
            if source == _INT and name not in {"change", "valuewhen"}:
                # No TradingView int-result receipt yet for these six calls.
                # Leave the check unknown until their overloads are measured.
                return None
            return source
        if namespace == "request" and name == "security":
            return self.infer(argument(2, "expression"))
        if namespace == "array":
            if name == "size":
                return _INT
            if name in {"new_float", "new_int"}:
                return TypeSpec.array(_FLOAT if name == "new_float" else _INT)
            if name == "new":
                hints = (node.callee.annotations or {}).get("template_args", ())
                element = _hint_spec(hints[0]) if hints else None
                return TypeSpec.array(element) if element is not None else None
            if name == "from":
                element = _join([self.infer(arg) for arg in node.args])
                return TypeSpec.array(element) if element in (_INT, _FLOAT) else None
            return self.array_result(name, self.infer(argument(0, "id")))
        if name in _FLOAT_CALLS.get(namespace, ()):
            return _FLOAT
        # INT is explicitly declared in the registry; unlike FLOAT, it is
        # never _sig's default. This proves input.int, casts and calendar
        # calls without treating a default FLOAT as evidence for either slot.
        if sigs.get_return_type(
            namespace, name, len(node.args) + len(node.kwargs),
        ) == PineType.INT:
            return _INT
        return None

    @staticmethod
    def array_result(name, spec):
        if not isinstance(spec, TypeSpec) or spec.kind != "array":
            return None
        if name == "size":
            return _INT
        if name in {"copy", "slice"}:
            return spec
        if name in _ARRAY_ELEMENTS:
            return spec.element
        return None


class _CounterValueQuery(_CounterQuery):
    """Keep fractional division values unless literal arithmetic proves an int."""

    def __init__(self, check_query):
        super().__init__(check_query.sources, check_query.bindings, check_query.scopes)
        self.check_query = check_query
        self.integer_constants = {}

    def _integer_constant(self, node):
        """Prove an integer using only immutable literal arithmetic."""
        key = id(node)
        if key in self.integer_constants:
            return self.integer_constants[key]
        # Memoize failures too; a provisional failure breaks alias cycles.
        self.integer_constants[key] = None
        value = None
        if isinstance(node, NumberLiteral) and type(node.value) is int:
            value = node.value
        elif isinstance(node, Identifier):
            binding = self.bindings.get(key)
            declaration = self.sources.declarations.get(binding[0]) if binding else None
            if (isinstance(declaration, VarDecl)
                    and not declaration.is_var and not declaration.is_varip
                    and declaration.name not in self.sources.reassigned_names
                    and declaration.type_hint in (None, "int")
                    and (declaration.annotations or {}).get("qualifier") in (None, "const")):
                value = self._integer_constant(declaration.value)
        elif isinstance(node, UnaryOp) and node.op in {"+", "-"}:
            operand = self._integer_constant(node.operand)
            if operand is not None:
                value = -operand if node.op == "-" else operand
        elif isinstance(node, BinOp) and node.op in {"+", "-", "*", "/", "%"}:
            left = self._integer_constant(node.left)
            right = self._integer_constant(node.right)
            if left is not None and right is not None:
                if node.op == "+":
                    value = left + right
                elif node.op == "-":
                    value = left - right
                elif node.op == "*":
                    value = left * right
                elif right != 0:
                    if node.op == "/" and left % right == 0:
                        value = left // right
                    elif node.op == "%":
                        remainder = abs(left) % abs(right)
                        value = -remainder if left < 0 else remainder
        self.integer_constants[key] = value
        return value

    def infer(self, node):
        if isinstance(node, BinOp) and node.op == "/":
            return _INT if self._integer_constant(node) is not None else _FLOAT
        return super().infer(node)

    def counter_spec(self, start, step, **kwargs):
        # A referenced loop counter must use the same check/storage decision
        # as analysis, including the original check type of its step.
        return self.check_query.counter_spec(start, step, **kwargs)


def for_counter_type(
    start, sources, bindings, scopes, *, step=None, pine_v5=False, counter_types=None,
    counter_name=None, step_location=None,
):
    """Choose the counter family; analysis also validates a named counter's step."""
    if pine_v5:
        return PineType.INT
    if counter_types is not None and id(start) in counter_types:
        # Analysis recorded this same query while the source scope was live.
        # Reuse that decision rather than a later coarse symbol/storage type.
        return counter_types[id(start)]
    query = _CounterQuery(sources, bindings, scopes)
    spec = query.counter_spec(
        start, step, counter_name=counter_name, step_location=step_location,
    )
    return PineType.FLOAT if spec == _FLOAT else PineType.INT
