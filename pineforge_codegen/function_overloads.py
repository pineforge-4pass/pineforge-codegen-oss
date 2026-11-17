"""Bind every call of an overloaded user function to the declaration Pine selects.

A group is the declarations of one function name when every parameter of each
declares a scalar or a record (``type``), no two declare the same types with
the same qualifiers, and every call follows the final declaration. Each
earlier declaration gets a stable name shared by every subsequent pass; the
final one keeps the source name.

A call is read with the type and the qualifier of each argument, as the script
spells them. A declaration takes the call when the arguments bind to its
parameters, each argument has its parameter's type or is an int for a float
parameter, and no argument has a stronger qualifier than its parameter
declares (const, input, simple, series; an unqualified parameter takes any).
Of the declarations that take the call, the one that converts the fewest
arguments is selected, then the one whose qualifiers are the weakest, then the
later one.

A parameter that declares no qualifier, or no type, holds its argument's. A
function body is therefore read once for each different set of arguments its
calls pass, and the calls inside it are bound with those. Calls of one
function whose bodies bind differently reach separate copies of it, so the
selected declaration, its parameters and its result follow a value through
nested calls into the emitted C++. A copy is marked as one, and the history
and persistent bindings its body declares are stored apart from those of the
declaration it was copied from (``analyzer.callable_history_names``).

Two rules keep what compiled before compiling. A declaration whose body would
pass a value on to a ``simple`` or ``const`` parameter that cannot take it is
passed over while another declaration takes the call. A call that no
declaration takes keeps the binding it had: the one declaration its proven
argument types name in a group of scalars, and the final declaration
otherwise.

An argument whose type the script does not prove fits any parameter. When
such an argument is all that tells the remaining declarations apart, the later
one is kept. A qualifier that is not proven is series.
"""

from __future__ import annotations

import copy
from bisect import bisect_left

from .analyzer.callable_history_names import OVERLOAD_COPY
from .analyzer.operators import fixed_builtin_result, source_builtin_result
from .analyzer.qualifiers import _BUILTIN_PARAMETERS, _RANKS, _declared_qualifiers
from .analyzer.tables import BUILTIN_VARS
from .ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, EnumDecl, ExprStmt,
    ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, NaLiteral, NumberLiteral, Program, StringLiteral, Subscript,
    SwitchStmt, Ternary, TupleAssign, TupleLiteral, TypeDecl, UnaryOp,
    VarDecl, WhileStmt,
)
from .builtin_constants import builtin_constant
from .errors import Phase
from .library_inline import (
    _CONST_MEMBER_NAMESPACES, _PURE_CALL_NAMESPACES, _PURE_TIMEFRAME_CALLS,
    _SIMPLE_MEMBER_NAMESPACES,
)
from .limits import TimeBudget, syntax_children
from .symbols import PineType, TypeSpec


_SCALAR_TYPES = {
    "float": PineType.FLOAT,
    "int": PineType.INT,
    "bool": PineType.BOOL,
    "string": PineType.STRING,
    "color": PineType.COLOR,
}
_SCALARS = frozenset(_SCALAR_TYPES.values())
_NUMBERS = (PineType.INT, PineType.FLOAT)

# The analyzer's qualifier order.
_CONST, _INPUT, _SIMPLE, _SERIES = (
    _RANKS["const"], _RANKS["input"], _RANKS["simple"], _RANKS["series"],
)
_BOOLEAN_OPERATORS = frozenset({"==", "!=", ">", "<", ">=", "<=", "and", "or"})
_ARITHMETIC = frozenset({"+", "-", "*", "/", "%"})


class _Missing:
    """The type of a bare ``na``, which a parameter of any type takes."""

    def __repr__(self) -> str:
        return "na"


# A value is its type and its qualifier. The type is a scalar, the name of a
# record, ``_NA``, or None when the script does not prove it. The qualifier
# is a rank in the analyzer's order; one that is not proven is series.
_NA = _Missing()
_UNKNOWN = (None, _SERIES)


def _is_record(kind) -> bool:
    return isinstance(kind, str) and not isinstance(kind, PineType)


def _scalar(kind):
    return kind if kind in _SCALARS else None


def _common(first, second):
    """The type the arms of one expression share, or None."""
    if first is _NA:
        return second
    if second is _NA:
        return first
    if first is None or second is None:
        return None
    if first == second:
        return first
    if first in _NUMBERS and second in _NUMBERS:
        return PineType.FLOAT
    return None


def _spelling(callee: ASTNode) -> str | None:
    """The dotted name a call is written with."""
    parts: list[str] = []
    while isinstance(callee, MemberAccess):
        parts.append(callee.member)
        callee = callee.object
    if not isinstance(callee, Identifier):
        return None
    return ".".join((callee.name, *reversed(parts)))


def _nodes(root: ASTNode):
    """Include parameter defaults, which are stored outside syntax children."""
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        stack.extend(syntax_children(node))
        if isinstance(node, (FuncDef, MethodDef)):
            stack.extend(
                value for value in
                (node.annotations or {}).get("param_defaults", ())
                if isinstance(value, ASTNode)
            )


def _user_calls(root: ASTNode) -> list[FuncCall]:
    """The calls written with a plain name, in one order for a declaration
    and for every copy of it."""
    return [
        node for node in _nodes(root)
        if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
    ]


def _written_signature(definition: FuncDef) -> tuple:
    """The type and the qualifier each parameter declares."""
    notes = definition.annotations or {}
    hints = list(notes.get("param_type_hints", ()))
    qualifiers = list(notes.get("param_qualifiers") or ())
    qualifiers += [None] * (len(hints) - len(qualifiers))
    return tuple(zip(hints, qualifiers))


class _Names:
    """The values an expression can read: the blocks around it, innermost
    last, then the script's own declarations above ``limit``."""

    def __init__(self, declarations: dict, limit: int | None = None) -> None:
        self.declarations = declarations
        self.limit = limit
        self.blocks: list[dict] = []
        self.statement = 0

    def lookup(self, name: str):
        for block in reversed(self.blocks):
            if name in block:
                return block[name]
        found = self.declarations.get(name)
        if found is not None and (self.limit is None or found[0] < self.limit):
            return found[1]
        return None

    def define(self, name: str, value) -> None:
        if self.blocks:
            self.blocks[-1][name] = value
        else:
            self.declarations[name] = (self.statement, value)


class _Walk:
    """What reading one body decides: the declaration and copy each call
    binds, by the call's id, and whether every value the body passes on fits
    the parameter it reaches."""

    __slots__ = ("decisions", "nodes", "feasible")

    def __init__(self) -> None:
        self.decisions: dict[int, tuple[int, int]] = {}
        self.nodes: dict[int, FuncCall] = {}
        self.feasible = True


class _Instance:
    """A declaration read with one set of arguments: the copy that holds its
    bindings, the value it returns, and whether its body takes them."""

    __slots__ = ("variant", "result", "feasible")

    def __init__(self, variant: int, result, feasible: bool) -> None:
        self.variant = variant
        self.result = result
        self.feasible = feasible


class _Resolver:
    def __init__(self, program: Program, groups: dict[str, list[FuncDef]],
                 records: frozenset[str], budget: TimeBudget | None) -> None:
        self.groups = groups
        self.records = records
        self.budget = budget
        self.visits = 0
        self.fields = {
            statement.name: {field.name: field.type_name for field in statement.fields}
            for statement in program.body if isinstance(statement, TypeDecl)
        }
        self.enums = {
            statement.name for statement in program.body
            if isinstance(statement, EnumDecl)
        }
        self.reassigned: set[str] = set()
        self.methods: dict[str, list[MethodDef]] = {}
        for node in _nodes(program):
            if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                self.reassigned.add(node.target.name)
            elif isinstance(node, MethodDef):
                self.methods.setdefault(node.name, []).append(node)
        # The statement each function is declared at, and the functions
        # declared once. A name declared more than once outside a group
        # keeps the lookup it had, so its calls are not read here.
        self.index: dict[int, int] = {}
        declared: dict[str, list[FuncDef]] = {}
        for index, statement in enumerate(program.body):
            if isinstance(statement, FuncDef):
                self.index[id(statement)] = index
                declared.setdefault(statement.name, []).append(statement)
        self.single = {
            name: found[0] for name, found in declared.items() if len(found) == 1
        }
        # The script's own declarations: name -> (statement, value).
        self.declarations: dict[str, tuple[int, tuple]] = {}
        self.signatures: dict[int, tuple[list, list]] = {}
        self.defaults: dict[int, tuple[list, _Walk]] = {}
        self.calls: dict[int, list[FuncCall]] = {}
        self.instances: dict[tuple, _Instance] = {}
        # The bindings of each copy of a declaration, the declaration's own
        # first: one target, or None, for each of its calls.
        self.variants: dict[int, list[tuple]] = {}
        self.open: set[tuple] = set()
        self.script = _Walk()

    # ------------------------------------------------------------------
    # Declarations
    # ------------------------------------------------------------------

    def _tick(self, node: ASTNode) -> None:
        self.visits += 1
        if self.budget is not None and self.visits % 256 == 0:
            self.budget.check(node.loc, phase=Phase.ANALYZER)

    def _declared(self, hint) -> PineType | str | None:
        """The scalar or record a type hint names."""
        if not isinstance(hint, str):
            return None
        if hint in _SCALAR_TYPES:
            return _SCALAR_TYPES[hint]
        return hint if hint in self.records else None

    def _signature(self, definition) -> tuple[list, list]:
        """The type and the qualifier rank each parameter declares, or None."""
        found = self.signatures.get(id(definition))
        if found is None:
            count = len(definition.params)
            hints = list((definition.annotations or {}).get("param_type_hints", ()))
            qualifiers = list(_declared_qualifiers(definition))
            hints += [None] * (count - len(hints))
            qualifiers += [None] * (count - len(qualifiers))
            found = self.signatures[id(definition)] = (
                [self._declared(hint) for hint in hints[:count]],
                [_RANKS.get(qualifier) for qualifier in qualifiers[:count]],
            )
        return found

    def _defaults(self, definition) -> tuple[list, _Walk]:
        """The value of each parameter's default, or None, and what reading
        the defaults decides."""
        found = self.defaults.get(id(definition))
        if found is None:
            count = len(definition.params)
            walk = _Walk()
            # A default that calls its own declaration finds none yet.
            self.defaults[id(definition)] = ([None] * count, walk)
            names = _Names(self.declarations, self.index.get(id(definition)))
            names.blocks.append({})
            written = list((definition.annotations or {}).get("param_defaults", ()))
            written += [None] * (count - len(written))
            found = self.defaults[id(definition)] = ([
                self._value(value, names, walk)
                if isinstance(value, ASTNode) else None
                for value in written[:count]
            ], walk)
        return found

    def _calls(self, definition) -> list[FuncCall]:
        found = self.calls.get(id(definition))
        if found is None:
            found = self.calls[id(definition)] = _user_calls(definition)
        return found

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    @staticmethod
    def _kind(spec):
        if spec is None or spec.kind != "primitive":
            return None
        return _SCALAR_TYPES.get(spec.name)

    @staticmethod
    def _spec(kind):
        if kind is _NA:
            return TypeSpec.primitive("na")
        if kind in _SCALARS:
            return TypeSpec.primitive(kind.value)
        return None

    def _block(self, statements, names: _Names, walk: _Walk, bound=None):
        """The value of a block's last statement."""
        names.blocks.append(dict(bound or ()))
        try:
            result = _UNKNOWN
            for statement in statements or ():
                result = self._value(statement, names, walk)
            return result
        finally:
            names.blocks.pop()

    def _value(self, node, names: _Names, walk: _Walk):
        """The type and the qualifier of an expression, or of the value a
        statement leaves, binding the calls inside it.

        The qualifier rules are the analyzer's (``ParameterQualifiers``),
        with series wherever that leaves a qualifier open, so a call bound
        here to a ``simple`` or ``const`` parameter is one it accepts.
        """
        if not isinstance(node, ASTNode):
            return _UNKNOWN
        self._tick(node)
        if isinstance(node, NumberLiteral):
            kind = PineType.FLOAT if isinstance(node.value, float) else PineType.INT
            return kind, _CONST
        if isinstance(node, StringLiteral):
            return PineType.STRING, _CONST
        if isinstance(node, BoolLiteral):
            return PineType.BOOL, _CONST
        if isinstance(node, ColorLiteral):
            return PineType.COLOR, _CONST
        if isinstance(node, NaLiteral):
            return _NA, _CONST
        if isinstance(node, Identifier):
            found = names.lookup(node.name)
            if found is not None:
                return found
            if node.name == "na":
                return _NA, _CONST
            # A builtin variable changes from bar to bar.
            return _scalar(BUILTIN_VARS.get(node.name)), _SERIES
        if isinstance(node, ExprStmt):
            return self._value(node.expr, names, walk)
        if isinstance(node, UnaryOp):
            kind, rank = self._value(node.operand, names, walk)
            if node.op == "not":
                return PineType.BOOL, rank
            return (kind if kind in _NUMBERS else None), rank
        if isinstance(node, BinOp):
            left = self._value(node.left, names, walk)
            right = self._value(node.right, names, walk)
            rank = max(left[1], right[1])
            if node.op in _BOOLEAN_OPERATORS:
                return PineType.BOOL, rank
            kinds = (left[0], right[0])
            if node.op == "+" and PineType.STRING in kinds:
                return PineType.STRING, rank
            if node.op in _ARITHMETIC and all(
                    kind is None or kind is _NA or kind in _NUMBERS
                    for kind in kinds):
                if PineType.FLOAT in kinds:
                    return PineType.FLOAT, rank
                # The quotient of two ints is left unproven.
                if kinds == (PineType.INT, PineType.INT) and node.op != "/":
                    return PineType.INT, rank
            return None, rank
        if isinstance(node, Ternary):
            condition = self._value(node.condition, names, walk)
            first = self._value(node.true_val, names, walk)
            second = self._value(node.false_val, names, walk)
            return (
                _common(first[0], second[0]),
                max(condition[1], first[1], second[1]),
            )
        if isinstance(node, Subscript):
            kind = self._value(node.object, names, walk)[0]
            self._value(node.index, names, walk)
            # A history reference keeps its value's type and is series.
            return (kind if kind in _SCALARS or _is_record(kind) else None), _SERIES
        if isinstance(node, TupleLiteral):
            ranks = [self._value(value, names, walk)[1] for value in node.elements]
            return None, max(ranks, default=_SERIES)
        if isinstance(node, MemberAccess):
            owner = node.object
            if isinstance(owner, Identifier) and names.lookup(owner.name) is None:
                return self._builtin_member(owner.name, node.member)
            kind = self._value(owner, names, walk)[0]
            if _is_record(kind):
                # A field of an object is series.
                field = self.fields.get(kind, {}).get(node.member)
                return self._declared(field), _SERIES
            return _UNKNOWN
        if isinstance(node, FuncCall):
            return self._call(node, names, walk)
        if isinstance(node, VarDecl):
            kind, rank = self._value(node.value, names, walk)
            if node.type_hint:
                kind = self._declared(node.type_hint)
            elif kind is _NA or node.name in self.reassigned:
                # A name the script writes again may hold a wider type than
                # its initializer; only a declared type is proven for it.
                kind = None
            rank = max(rank, _RANKS.get(
                (node.annotations or {}).get("qualifier"), _CONST,
            ))
            if node.is_var or node.is_varip or node.name in self.reassigned:
                rank = _SERIES
            names.define(node.name, (kind, rank))
            return kind, rank
        if isinstance(node, TupleAssign):
            rank = self._value(node.value, names, walk)[1]
            for name in node.names:
                if name != "_":
                    names.define(
                        name, (None, _SERIES if name in self.reassigned else rank),
                    )
            return None, rank
        if isinstance(node, Assignment):
            kind = self._value(node.target, names, walk)[0]
            self._value(node.value, names, walk)
            return kind, _SERIES
        if isinstance(node, IfStmt):
            rank = self._value(node.condition, names, walk)[1]
            first = self._block(node.body, names, walk)
            second = (
                self._block(node.else_body, names, walk)
                if node.else_body else (_NA, _CONST)
            )
            return _common(first[0], second[0]), max(rank, first[1], second[1])
        if isinstance(node, SwitchStmt):
            rank = _CONST
            if node.expr is not None:
                rank = self._value(node.expr, names, walk)[1]
            kind = _NA
            arms = [body for _case, body in node.cases]
            for case, _body in node.cases:
                if case is not None:
                    rank = max(rank, self._value(case, names, walk)[1])
            if node.default_body:
                arms.append(node.default_body)
            for body in arms:
                arm = self._block(body, names, walk)
                kind, rank = _common(kind, arm[0]), max(rank, arm[1])
            return kind, rank
        if isinstance(node, ForStmt):
            bounds = [
                self._value(value, names, walk)
                for value in (node.start, node.end, node.step) if value is not None
            ]
            whole = bool(bounds) and all(bound[0] == PineType.INT for bound in bounds)
            self._block(node.body, names, walk, {node.var: (
                PineType.INT if whole else None,
                max((bound[1] for bound in bounds), default=_SERIES),
            )})
            return _UNKNOWN
        if isinstance(node, ForInStmt):
            self._value(node.iterable, names, walk)
            self._block(node.body, names, walk, {
                name: _UNKNOWN
                for name in (node.vars or ([node.var] if node.var else []))
                if name
            })
            return _UNKNOWN
        if isinstance(node, WhileStmt):
            self._value(node.condition, names, walk)
            self._block(node.body, names, walk)
            return _UNKNOWN
        if isinstance(node, (FuncDef, MethodDef)):
            # The calls of a declaration inside another are not bound here,
            # so the outer one is not proven to take its arguments.
            walk.feasible = False
            return _UNKNOWN
        for child in syntax_children(node):
            self._value(child, names, walk)
        return _UNKNOWN

    def _builtin_member(self, namespace: str, member: str):
        """``namespace.member`` where no script declaration has that name."""
        if namespace in self.enums:
            return None, _CONST
        constant = builtin_constant(namespace, member)
        kind = self._kind(constant.type_spec) if constant is not None else None
        if namespace in _CONST_MEMBER_NAMESPACES or namespace in ("math", "dayofweek"):
            return kind, _CONST
        if namespace in _SIMPLE_MEMBER_NAMESPACES or namespace == "chart":
            return kind, _SIMPLE
        if namespace == "session" and member in ("regular", "extended"):
            return kind, _CONST
        return kind, _SERIES

    def _function(self, name: str, arguments: list, keywords: dict, joined: int):
        """The result of a builtin called by a plain name."""
        if name in _SCALAR_TYPES:
            # A cast is as fixed as its argument.
            return _SCALAR_TYPES[name], joined
        if name == "na":
            return PineType.BOOL, joined
        if name in ("nz", "fixnan"):
            kind = _NA
            for value in arguments:
                kind = _common(kind, value[0])
            return _scalar(kind), joined
        if name == "input":
            first = arguments[0] if arguments else keywords.get("defval")
            return (_scalar(first[0]) if first is not None else None), _INPUT
        if name == "timestamp":
            return PineType.INT, joined
        if name in BUILTIN_VARS:
            return _scalar(BUILTIN_VARS[name]), _SERIES
        return _UNKNOWN

    def _call(self, node: FuncCall, names: _Names, walk: _Walk):
        callee = node.callee
        namespace = receiver = None
        if isinstance(callee, MemberAccess):
            if (isinstance(callee.object, Identifier)
                    and names.lookup(callee.object.name) is None):
                namespace = callee.object.name
            else:
                receiver = self._value(callee.object, names, walk)
        elif not isinstance(callee, Identifier):
            self._value(callee, names, walk)
        arguments = [self._value(value, names, walk) for value in node.args]
        keywords = {
            name: self._value(value, names, walk)
            for name, value in node.kwargs.items()
        }
        joined = max(
            (value[1] for value in (*arguments, *keywords.values())),
            default=_SERIES,
        )
        self._reach_builtin(node, arguments, keywords, names, walk)
        if isinstance(callee, Identifier):
            if names.lookup(callee.name) is not None:
                return _UNKNOWN
            found = self._user_call(node, arguments, keywords, walk)
            if found is not None:
                return found
            # A method called as a function takes its receiver first.
            self._reach_methods(callee.name, arguments, keywords, walk)
            return self._function(callee.name, arguments, keywords, joined)
        if not isinstance(callee, MemberAccess):
            return _UNKNOWN
        member = callee.member
        if namespace is None:
            self._reach_methods(member, [receiver, *arguments], keywords, walk)
            if (_is_record(receiver[0]) and member == "copy"
                    and not node.args and not node.kwargs):
                return receiver[0], _SERIES
            return _UNKNOWN
        if namespace in self.records and member in ("new", "copy"):
            return namespace, _SERIES
        if namespace == "input":
            kind = self._kind(fixed_builtin_result(
                "input", member, len(arguments) + len(keywords),
            ))
            return kind, (_SERIES if member == "source" else _INPUT)
        if namespace in ("str", "math", "ta"):
            spec = fixed_builtin_result(
                namespace, member, len(arguments) + len(keywords),
            )
            if spec is None and not keywords:
                spec = source_builtin_result(namespace, member, tuple(
                    self._spec(value[0]) for value in arguments
                ))
            volatile = namespace == "ta" or (
                namespace == "math" and member in ("random", "sum")
            )
            return self._kind(spec), (_SERIES if volatile else joined)
        if namespace == "color":
            kind = (
                PineType.COLOR if member in ("new", "rgb", "from_gradient")
                else PineType.FLOAT if member in ("r", "g", "b", "t") else None
            )
            return kind, joined
        if namespace in _PURE_CALL_NAMESPACES or (
                namespace == "timeframe" and member in _PURE_TIMEFRAME_CALLS):
            return None, joined
        return _UNKNOWN

    # ------------------------------------------------------------------
    # What a body passes on
    # ------------------------------------------------------------------

    def _reach_builtin(self, node: FuncCall, arguments: list, keywords: dict,
                       names: _Names, walk: _Walk) -> None:
        """The builtin parameters whose qualifier the analyzer checks."""
        root = node.callee
        while isinstance(root, MemberAccess):
            root = root.object
        if not isinstance(root, Identifier) or names.lookup(root.name) is not None:
            return
        parameters = _BUILTIN_PARAMETERS.get(_spelling(node.callee), ())
        for index, (name, qualifier) in enumerate(parameters):
            value = arguments[index] if index < len(arguments) else keywords.get(name)
            if value is not None and value[1] > _RANKS.get(qualifier, _SERIES):
                walk.feasible = False

    def _reach_methods(self, name: str, values: list, keywords: dict,
                       walk: _Walk) -> None:
        """A member call can reach every method of its name, receiver first."""
        for method in self.methods.get(name, ()):
            limits = self._signature(method)[1]
            parameters = list(method.params)
            bound = list(zip(limits, values))
            bound += [
                (limits[parameters.index(key)], value)
                for key, value in keywords.items() if key in parameters
            ]
            if any(limit is not None and value[1] > limit for limit, value in bound):
                walk.feasible = False

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _bind(self, definition, arguments: list, keywords: dict):
        """The value each parameter takes, or None when the arguments do not
        bind (too many, an unknown keyword, one given twice, a parameter
        without a default left out)."""
        parameters = list(definition.params)
        if len(arguments) > len(parameters):
            return None
        bound = dict(enumerate(arguments))
        for key, value in keywords.items():
            if key not in parameters or parameters.index(key) in bound:
                return None
            bound[parameters.index(key)] = value
        defaults = self._defaults(definition)[0]
        for index in range(len(parameters)):
            if index not in bound:
                if defaults[index] is None:
                    return None
                bound[index] = defaults[index]
        return [bound[index] for index in range(len(parameters))]

    def _fit(self, definition, values: list):
        """How a declaration takes these values: the int arguments it
        converts to float and the sum of its parameters' qualifier ranks;
        None when a type or a qualifier rules it out."""
        types, limits = self._signature(definition)
        conversions = weight = 0
        for (kind, rank), declared, limit in zip(values, types, limits):
            if (declared is not None and kind is not None and kind is not _NA
                    and kind != declared):
                if kind != PineType.INT or declared != PineType.FLOAT:
                    return None
                conversions += 1
            if limit is not None and rank > limit:
                return None
            weight += _SERIES if limit is None else limit
        return conversions, weight

    def _profile(self, definition, values: list) -> tuple:
        """What each parameter holds in a body read for these values: its
        declared type and qualifier, or its argument's."""
        types, limits = self._signature(definition)
        return tuple(
            (
                declared if declared is not None
                else None if kind is _NA else kind,
                rank if limit is None else limit,
            )
            for (kind, rank), declared, limit in zip(values, types, limits)
        )

    def _proof_ranks(self, candidates: list, fits: list, arguments: list,
                     keywords: dict) -> bool:
        """Whether proof tells the declarations that take a call apart: every
        argument's type is proven, or the unproven ones bind parameters of
        one type in each declaration."""
        positions = [index for index, value in enumerate(arguments) if value[0] is None]
        written = [name for name, value in keywords.items() if value[0] is None]
        if not positions and not written:
            return True
        seen = set()
        for _cost, position in fits:
            definition = candidates[position]
            types = self._signature(definition)[0]
            parameters = list(definition.params)
            seen.add(tuple(
                [types[index] for index in positions]
                + [types[parameters.index(name)] for name in written]
            ))
        return len(seen) <= 1

    def _unmatched(self, candidates: list, bound: dict) -> int:
        """The declaration a call keeps when none takes it, so the analyzer
        answers it as before: the one its proven argument types name in a
        group of scalars, and the final one otherwise."""
        scalar = not any(
            _is_record(kind) for definition in candidates
            for kind in self._signature(definition)[0]
        )
        if scalar:
            exact = [
                position for position, values in bound.items()
                if [kind for kind, _rank in values]
                == self._signature(candidates[position])[0]
            ]
            if len(exact) == 1:
                return exact[0]
        return len(candidates) - 1

    def _decide(self, walk: _Walk, node: FuncCall, definition, variant: int) -> None:
        walk.decisions[id(node)] = (id(definition), variant)
        walk.nodes[id(node)] = node

    def _user_call(self, node: FuncCall, arguments: list, keywords: dict,
                   walk: _Walk):
        """Bind a call of a user function and return its value; None when
        the name is not one this pass binds."""
        name = node.callee.name
        candidates = self.groups.get(name)
        if candidates is None:
            definition = self.single.get(name)
            if definition is None:
                return None
            values = self._bind(definition, arguments, keywords)
            if values is None:
                return _UNKNOWN
            limits = self._signature(definition)[1]
            if any(limit is not None and value[1] > limit
                   for value, limit in zip(values, limits)):
                walk.feasible = False
            instance = self._instantiate(definition, self._profile(definition, values))
            if not instance.feasible:
                walk.feasible = False
            self._decide(walk, node, definition, instance.variant)
            return instance.result
        bound: dict[int, list] = {}
        fits: list[tuple[tuple[int, int], int]] = []
        for position, definition in enumerate(candidates):
            values = self._bind(definition, arguments, keywords)
            if values is None:
                continue
            bound[position] = values
            fit = self._fit(definition, values)
            if fit is not None:
                fits.append((fit, position))
        if self._proof_ranks(candidates, fits, arguments, keywords):
            # The fewest conversions, then the weakest qualifiers, then the
            # later declaration.
            fits.sort(key=lambda item: (item[0], -item[1]))
        else:
            fits.sort(key=lambda item: -item[1])
        for _cost, position in fits:
            definition = candidates[position]
            instance = self._instantiate(
                definition, self._profile(definition, bound[position]),
            )
            if instance.feasible:
                self._decide(walk, node, definition, instance.variant)
                return instance.result
        # No declaration takes the call.
        walk.feasible = False
        position = self._unmatched(candidates, bound)
        definition = candidates[position]
        variant, result = 0, _UNKNOWN
        if position in bound:
            instance = self._instantiate(
                definition, self._profile(definition, bound[position]),
            )
            variant, result = instance.variant, instance.result
        self._decide(walk, node, definition, variant)
        return result

    def _instantiate(self, definition, profile: tuple) -> _Instance:
        """Read a declaration's body with what its parameters hold."""
        key = (id(definition), profile)
        found = self.instances.get(key)
        if found is not None:
            return found
        if key in self.open:
            # A declaration that reaches itself has no value yet.
            return _Instance(0, _UNKNOWN, True)
        self.open.add(key)
        walk = _Walk()
        try:
            written = self._defaults(definition)[1]
            names = _Names(self.declarations, self.index.get(id(definition)))
            kind, rank = self._block(
                definition.body, names, walk, zip(definition.params, profile),
            )
        finally:
            self.open.discard(key)
        decisions = {**written.decisions, **walk.decisions}
        vector = tuple(decisions.get(id(call)) for call in self._calls(definition))
        variants = self.variants.setdefault(id(definition), [])
        for variant, known in enumerate(variants):
            if known == vector:
                break
        else:
            variant = len(variants)
            variants.append(vector)
        # A user function returns simple or series, on literals too.
        instance = self.instances[key] = _Instance(
            variant,
            (None if kind is _NA else kind, max(_SIMPLE, rank)),
            walk.feasible and written.feasible,
        )
        return instance

    def _declared_profile(self, definition) -> tuple:
        types, limits = self._signature(definition)
        return tuple(
            (kind, _SERIES if limit is None else limit)
            for kind, limit in zip(types, limits)
        )

    def run(self, program: Program) -> None:
        names = _Names(self.declarations)
        for index, statement in enumerate(program.body):
            names.statement = index
            if isinstance(statement, FuncDef):
                continue
            if isinstance(statement, MethodDef):
                # A method's calls are bound with what its parameters declare.
                walk = _Walk()
                inner = _Names(self.declarations, index)
                self._block(statement.body, inner, walk, zip(
                    statement.params, self._declared_profile(statement),
                ))
                for found in (self._defaults(statement)[1], walk):
                    self.script.decisions.update(found.decisions)
                    self.script.nodes.update(found.nodes)
                continue
            self._value(statement, names, self.script)
        for statement in program.body:
            # A function nothing calls is read with what its parameters declare.
            if isinstance(statement, FuncDef) and id(statement) not in self.variants:
                self._instantiate(statement, self._declared_profile(statement))


def bind_scalar_overloads(program: Program, *,
                          budget: TimeBudget | None = None) -> Program:
    """Bind the calls of complete, distinct scalar and record signatures
    before name-keyed passes.

    Forward and recursive groups retain the previous behavior: every call must
    occur in a statement after the group's final definition. Declarations
    that repeat one signature, qualifiers included, also stay on their
    original path, including the existing duplicate-signature diagnostic. So
    does a group with a parameter that declares no type or any other type (a
    collection, an enum, a builtin object).
    """
    groups: dict[str, list[FuncDef]] = {}
    last_definition: dict[str, int] = {}
    record_names: set[str] = set()
    for index, statement in enumerate(program.body):
        if isinstance(statement, FuncDef):
            groups.setdefault(statement.name, []).append(statement)
            last_definition[statement.name] = index
        elif isinstance(statement, TypeDecl):
            record_names.add(statement.name)
    records = frozenset(record_names)
    groups = {
        name: definitions for name, definitions in groups.items()
        if len(definitions) > 1
        and all(
            len((definition.annotations or {}).get("param_type_hints", ()))
            == len(definition.params)
            and all(hint in _SCALAR_TYPES or hint in records for hint in
                    (definition.annotations or {}).get("param_type_hints", ()))
            for definition in definitions
        )
        and len({
            _written_signature(definition) for definition in definitions
        }) == len(definitions)
    }
    if not groups:
        return program
    for index, statement in enumerate(program.body):
        for node in _nodes(statement):
            if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                    and node.callee.name in groups
                    and index <= last_definition[node.callee.name]):
                groups.pop(node.callee.name)
            elif (isinstance(node, FuncDef) and node is not statement
                  and node.name in groups):
                groups.pop(node.name)
    if not groups:
        return program

    # Reserve each authored name's whole suffix family, including call-site
    # variants, without materializing every prefix of a long identifier.
    occupied: set[str] = set()
    for node in _nodes(program):
        name = getattr(node, "name", None)
        if isinstance(name, str):
            occupied.add(name)
        occupied.update(getattr(node, "params", ()))
        if isinstance(node, TupleAssign):
            occupied.update(node.names)
        if isinstance(node, ForStmt):
            occupied.add(node.var)
        if isinstance(node, ForInStmt):
            occupied.update(node.vars or ([node.var] if node.var else []))
    ordered_names = sorted(occupied)
    allocated: set[str] = set()

    def collides(identity: str) -> bool:
        family = identity + "_"
        index = bisect_left(ordered_names, family)
        return (
            identity in occupied or identity in allocated
            or (index < len(ordered_names) and ordered_names[index].startswith(family))
        )

    def allocate(stem: str) -> str:
        identity = stem
        suffix = 2
        while collides(identity):
            identity = f"{stem}_{suffix}"
            suffix += 1
        allocated.add(identity)
        return identity

    identities: dict[int, str] = {}
    for name, definitions in groups.items():
        for ordinal, definition in enumerate(definitions[:-1], 1):
            identities[id(definition)] = allocate(f"pf_overload_{name}_{ordinal}")
        # A call that no declaration takes still uses the final name. Its
        # signature alone cannot prove a mismatch for the original group.
        fallback = definitions[-1]
        identities[id(fallback)] = name
        fallback.annotations = {
            **(fallback.annotations or {}), "scalar_overload_fallback": True,
        }

    resolver = _Resolver(program, groups, records, budget)
    resolver.run(program)

    # The copies a call reaches. Every declaration stays; a copy read only
    # for a call that another declaration took in the end is not emitted.
    counts = {
        id(statement): len(resolver.variants.get(id(statement), ())) or 1
        for statement in program.body if isinstance(statement, FuncDef)
    }
    reached: set[tuple[int, int]] = set()
    pending = [(key, 0) for key in counts]
    pending += list(resolver.script.decisions.values())
    while pending:
        target = pending.pop()
        if target in reached:
            continue
        reached.add(target)
        vectors = resolver.variants.get(target[0]) or [()]
        pending += [found for found in vectors[target[1]] if found is not None]

    # Every copy is made before a call is renamed, so each starts from the
    # declaration as it was written.
    emitted: dict[tuple[int, int], FuncDef] = {}
    names: dict[tuple[int, int], str] = {}
    for statement in program.body:
        if not isinstance(statement, FuncDef):
            continue
        key = id(statement)
        base = identities.get(key, statement.name)
        stem = base if base.startswith("pf_overload_") else f"pf_overload_{base}"
        for variant in range(counts[key]):
            if (key, variant) not in reached:
                continue
            if variant == 0:
                emitted[(key, variant)] = statement
            else:
                # A copy declares every name the declaration does. The mark
                # lets the analyzer give each binding of the copy's body
                # storage of its own (``analyzer.callable_history_names``).
                clone = copy.deepcopy(statement)
                clone.annotations = {
                    **(clone.annotations or {}), OVERLOAD_COPY: True,
                }
                emitted[(key, variant)] = clone
            names[(key, variant)] = (
                base if variant == 0 else allocate(f"{stem}_v{variant + 1}")
            )
    for (key, variant), node in emitted.items():
        vectors = resolver.variants.get(key) or [()]
        for call, target in zip(_user_calls(node), vectors[variant]):
            if target is not None:
                call.callee.name = names[target]
    for call_id, target in resolver.script.decisions.items():
        resolver.script.nodes[call_id].callee.name = names[target]
    body: list = []
    for statement in program.body:
        body.append(statement)
        if isinstance(statement, FuncDef):
            key = id(statement)
            for variant in range(1, counts[key]):
                clone = emitted.get((key, variant))
                if clone is not None:
                    clone.name = names[(key, variant)]
                    body.append(clone)
            statement.name = names[(key, 0)]
    program.body[:] = body
    return program
