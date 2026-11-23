"""Typed, lexical constant proofs for arguments with a constant contract."""

from collections import Counter
from dataclasses import dataclass, replace

from ..ast_nodes import (
    ASTNode, BoolLiteral, ColorLiteral, FuncCall, Identifier, MemberAccess,
    NumberLiteral, StringLiteral, Ternary, VarDecl,
)
from ..limits import MAX_NESTING_DEPTH, TimeBudget, iter_ast_nodes
from ..library_v5 import _CONSTANT_NAMESPACES
from ..security_contexts import ScriptIndex
from ..signatures import INPUT_FUNCTIONS, MATH_CONSTANTS
from ..symbols import PineType
from ..builtin_constants import STRING_CONSTANT_VALUES, builtin_constant
from .color_constants import COLOR_PARAMETERS, color_arguments, literal_color_value
from .constant_fold import MISSING_INT64, fold_constant_expression
from .strategy_declaration import _NAMED_CONSTANTS
from .tables import (
    ALERT_FREQ_VALUES, DRAWING_STYLE_NS, NAME_ECHO_STRING_MEMBERS,
)


_STRING_INPUT_CONSTANT_MEMBERS = {
    **NAME_ECHO_STRING_MEMBERS,
    "alert": ALERT_FREQ_VALUES,
    **STRING_CONSTANT_VALUES,
    "session": frozenset({"regular", "extended"}),
}
_SCALAR_CONSTANTS = {
    **{f"math.{name}": value for name, value in MATH_CONSTANTS.items()},
    **{f"{namespace}.{member}": value
       for namespace, members in STRING_CONSTANT_VALUES.items()
       for member, value in members.items()},
}
_PLAIN_STRING_CONSTANTS = {
    **_SCALAR_CONSTANTS,
    **{f"{namespace}.{member}": member
       for namespace, members in NAME_ECHO_STRING_MEMBERS.items()
       for member in members},
}


@dataclass(frozen=True)
class ConstantValue:
    pine_type: PineType
    value: object


def input_default_contract(call):
    callee = call.callee
    if (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
            and callee.object.name == "input"):
        intrinsic = INPUT_FUNCTIONS.get(callee.member)
        return intrinsic.default_contract if intrinsic is not None else None
    return None


class ConstantResolver:
    """Resolve lexical constants and immutable global string input arguments.

    Binding identity, rather than a variable's spelling, owns the cache.
    General expression proofs require explicit const declarations. Typed
    string inputs also admit immutable global literal initializers. Bare
    inputs can additionally follow inferred constant expression bindings.
    """

    def __init__(self, program, budget=None):
        self.index = ScriptIndex(program)
        self.budget = budget or TimeBudget("<input>")
        self.order = {}
        declarations = Counter()
        pending = [program]
        while pending:
            node = pending.pop()
            if isinstance(node, ASTNode):
                self.order[id(node)] = len(self.order)
                if isinstance(node, VarDecl):
                    declarations[self.index.decl_binding.get((id(node), node.name))] += 1
                pending.extend(reversed([value for key, value in vars(node).items()
                                         if key not in ("annotations", "loc")]))
            elif isinstance(node, (list, tuple)):
                pending.extend(reversed(node))
            elif isinstance(node, dict):
                pending.extend(reversed(list(node.values())))
        self.cache = {}
        self.plain_input_cache = {}
        self.active = set()
        self.ambiguous = {binding for binding, count in declarations.items() if count > 1}
        self.string_declarations = Counter(self.index.decl_binding.values())
        self.string_cache = {}

    def string_input_argument(self, read):
        """Prove a preceding, uniquely declared global's string literal or builtin."""
        self.budget.check(read.loc)
        binding = self.index.refs.get(id(read))
        declaration = self.index.decls.get(binding)
        if (declaration is None or binding[0] != "global"
                or binding in self.index.reassigned
                or self.string_declarations[binding] != 1
                or declaration.is_varip
                or self.order[id(declaration)] >= self.order[id(read)]):
            return None
        if binding not in self.string_cache:
            initializer = declaration.value
            result = None
            if isinstance(initializer, MemberAccess):
                root = initializer
                members = []
                while isinstance(root, MemberAccess):
                    self.budget.check(root.loc)
                    members.append(root.member)
                    root = root.object
                if isinstance(root, Identifier) and id(root) not in self.index.refs:
                    # Strategy has runtime fields as well as named constants.
                    constant_namespaces = (_CONSTANT_NAMESPACES - {"strategy"}) | DRAWING_STYLE_NS
                    spelling = ".".join([root.name, *reversed(members)])
                    if (isinstance(initializer.object, Identifier)
                            and initializer.member in
                            _STRING_INPUT_CONSTANT_MEMBERS.get(root.name, ())):
                        result = initializer
                    elif (spelling in _NAMED_CONSTANTS
                            or (isinstance(initializer.object, Identifier)
                                and root.name in constant_namespaces)):
                        result = read
            else:
                literal_only = True
                for node, _ in iter_ast_nodes(initializer):
                    self.budget.check(node.loc)
                    if isinstance(node, (Identifier, MemberAccess, FuncCall)):
                        literal_only = False
                        break
                if literal_only:
                    value, proven = fold_constant_expression(initializer, {}, {})
                    if proven and isinstance(value, str):
                        result = StringLiteral(value=value, loc=read.loc)
            self.string_cache[binding] = result
        result = self.string_cache[binding]
        return read if isinstance(result, Identifier) else result

    def plain_string_default(self, expr) -> str | None:
        """Prove a bare input's string without using a runtime default."""
        proof = self._scalar(expr, 0, allow_inferred=True)
        # The ABI copies C strings, so a proved expression must survive that
        # boundary without truncation. Authored literal defaults keep their
        # historical handling.
        if proof is not None and proof.pine_type == PineType.STRING and "\0" not in proof.value:
            return proof.value
        return None

    def input_default(self, call):
        contract = input_default_contract(call)
        default = call.args[0] if call.args else call.kwargs.get("defval")
        if (contract is None or contract.qualifier != "const"
                or not isinstance(default, (Identifier, FuncCall))):
            return None
        # Literal-only builders retain their existing emission. A builder
        # reading a binding needs the same lexical proof as a bare carrier:
        # its member has not been assigned when static inputs initialize.
        if not any(isinstance(node, Identifier) and id(node) in self.index.refs
                   for node, _ in iter_ast_nodes(default)):
            return None
        proof = self.expression(default)
        return proof if proof is not None and proof.pine_type == contract.pine_type else None

    def _binding(self, read, depth, *, allow_inferred=False):
        binding = self.index.refs.get(id(read))
        declaration = self.index.decls.get(binding)
        if (declaration is None or binding in self.index.unstable or binding in self.ambiguous
                or self.order[id(declaration)] >= self.order[id(read)]
                or binding in self.active):
            return None
        qualifier = (declaration.annotations or {}).get("qualifier")
        inferred = (allow_inferred and not qualifier and binding[0] == "global"
                    and self.string_declarations[binding] == 1)
        if not inferred and (qualifier != "const" or declaration.type_hint is None):
            return None
        cache = self.plain_input_cache if allow_inferred else self.cache
        # The proof's remaining depth affects the result of a dependency
        # walk. Include it in the bare-input key so a cache hit cannot
        # borrow a proof made with more room or conceal a shorter proof.
        key = (binding, depth) if allow_inferred else binding
        if key not in cache:
            self.active.add(binding)
            try:
                value = (self._scalar(declaration.value, depth + 1, allow_inferred=True)
                         if allow_inferred else self.expression(declaration.value, depth + 1))
                if (allow_inferred and value is not None
                        and value.pine_type in (PineType.INT, PineType.FLOAT)
                        and (binding[0] != "global"
                             or not self._input_numeric_binding(declaration, value))):
                    value = None
                if value is not None and declaration.type_hint is not None:
                    if declaration.type_hint == "float" and value.pine_type == PineType.INT:
                        value = ConstantValue(PineType.FLOAT, float(value.value))
                    if value.pine_type.value != declaration.type_hint:
                        value = None
                cache[key] = value
            finally:
                self.active.remove(binding)
        return cache[key]

    @staticmethod
    def _input_numeric_binding(declaration, proof):
        # The emitter inlines literal and alias initializers as written, so
        # an annotation alone does not prove promotion at their use sites.
        if declaration.type_hint == "float" and proof.pine_type == PineType.INT:
            return False
        initializer = declaration.value
        if isinstance(initializer, (NumberLiteral, Identifier)):
            return True
        if isinstance(initializer, Ternary):
            # Some integer-valued arms (such as math.round) emit double
            # and are narrowed only at storage. Without literal arms we
            # cannot use the C++ conditional's carrier as the slot's type.
            if not all(isinstance(arm, NumberLiteral)
                       for arm in (initializer.true_val, initializer.false_val)):
                return False
            return (proof.pine_type == PineType.FLOAT
                    or (proof.value is not MISSING_INT64
                        and -(1 << 31) < proof.value < (1 << 31)))
        # A proved float-to-int cast uses the wide storage of that cast.
        # Numeric precomputation can otherwise replace a call or arithmetic
        # initializer with a literal of a different precision or type.
        return (isinstance(initializer, FuncCall)
                and isinstance(initializer.callee, Identifier)
                and initializer.callee.name == "int")

    def _scalar(self, expr, depth, *, allow_inferred=False):
        if expr is None or depth > MAX_NESTING_DEPTH:
            return None
        known = {}
        for node, _ in iter_ast_nodes(expr):
            if allow_inferred:
                self.budget.check(node.loc)
                if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                        and node.callee.name in self.index.funcs):
                    return None
            if isinstance(node, Identifier) and id(node) in self.index.refs:
                # A flat alias is one step, just as in expression(). Only
                # a read inside a larger expression adds an extra step.
                binding_depth = depth if allow_inferred and node is expr else depth + 1
                value = self._binding(node, binding_depth, allow_inferred=allow_inferred)
                if value is None or value.pine_type == PineType.COLOR:
                    return None
                known[node.name] = value.value
        constants = _PLAIN_STRING_CONSTANTS if allow_inferred else _SCALAR_CONSTANTS
        value, proven = fold_constant_expression(
            expr, known, constants, for_input_default=allow_inferred)
        if allow_inferred and proven and value is MISSING_INT64:
            return ConstantValue(PineType.INT, value)
        kind = {bool: PineType.BOOL, int: PineType.INT, float: PineType.FLOAT,
                str: PineType.STRING}.get(type(value))
        return ConstantValue(kind, value) if proven and kind is not None else None

    def expression(self, expr, depth=0):
        if depth > MAX_NESTING_DEPTH or expr is None:
            return None
        self.budget.check(expr.loc)
        if isinstance(expr, Identifier):
            return self._binding(expr, depth)
        if isinstance(expr, ColorLiteral):
            return ConstantValue(PineType.COLOR, literal_color_value(expr))
        if (isinstance(expr, MemberAccess) and isinstance(expr.object, Identifier)
                and id(expr.object) not in self.index.refs):
            constant = builtin_constant(expr.object.name, expr.member)
            if constant is not None and constant.type_spec.kind == "primitive":
                return ConstantValue(PineType.STRING, constant.value)
            value = literal_color_value(expr)
            if value is not None:
                return ConstantValue(PineType.COLOR, value)
        if isinstance(expr, Ternary):
            condition = self._scalar(expr.condition, depth + 1)
            left = self.expression(expr.true_val, depth + 1)
            right = self.expression(expr.false_val, depth + 1)
            if (condition is not None and condition.pine_type == PineType.BOOL
                    and left is not None and right is not None
                    and left.pine_type == right.pine_type == PineType.COLOR):
                value, proven = fold_constant_expression(replace(
                    expr, condition=BoolLiteral(value=condition.value),
                    true_val=NumberLiteral(value=left.value),
                    false_val=NumberLiteral(value=right.value)), {}, {})
                return ConstantValue(PineType.COLOR, value) if proven else None
        if (isinstance(expr, FuncCall) and isinstance(expr.callee, MemberAccess)
                and isinstance(expr.callee.object, Identifier)
                and expr.callee.object.name == "color"
                and id(expr.callee.object) not in self.index.refs
                and expr.callee.member in ("new", "rgb")):
            name = expr.callee.member
            parameters = COLOR_PARAMETERS[name]
            if (len(expr.args) > len(parameters)
                    or any(parameter not in parameters
                           or parameters.index(parameter) < len(expr.args)
                           for parameter in expr.kwargs)):
                return None
            arguments = color_arguments(name, expr)
            if not ((name == "new" and len(arguments) == 2)
                    or (name == "rgb" and len(arguments) in (3, 4))):
                return None
            folded = []
            for index, argument in enumerate(arguments):
                proof = self.expression(argument, depth + 1)
                if proof is None:
                    return None
                if name == "new" and index == 0:
                    if proof.pine_type != PineType.COLOR:
                        return None
                    packed = proof.value
                    folded.append(ColorLiteral(
                        value=f"#{packed & 0xFFFFFF:06x}{packed >> 24:02x}"))
                else:
                    if proof.pine_type not in (PineType.INT, PineType.FLOAT):
                        return None
                    folded.append(NumberLiteral(value=proof.value))
            value = literal_color_value(replace(expr, args=folded, kwargs={}))
            return ConstantValue(PineType.COLOR, value) if value is not None else None
        return self._scalar(expr, depth)
