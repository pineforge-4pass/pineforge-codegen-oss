"""Typed, lexical constant proofs for arguments with a constant contract."""

from collections import Counter
from dataclasses import dataclass, replace

from ..ast_nodes import (
    ASTNode, BoolLiteral, ColorLiteral, FuncCall, Identifier, MemberAccess,
    NumberLiteral, Ternary, VarDecl,
)
from ..limits import MAX_NESTING_DEPTH, TimeBudget, iter_ast_nodes
from ..security_contexts import ScriptIndex
from ..signatures import INPUT_FUNCTIONS, MATH_CONSTANTS
from ..symbols import PineType
from .color_constants import COLOR_PARAMETERS, color_arguments, literal_color_value
from .constant_fold import fold_constant_expression


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
    """Resolve preceding explicit const declarations on this exact AST.

    Binding identity, rather than a variable's spelling, owns the cache.
    Reassignment is deliberately unproven: a declaration's initial value is
    insufficient evidence of its value at a later argument use.
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
        self.active = set()
        self.ambiguous = {binding for binding, count in declarations.items() if count > 1}

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

    def _binding(self, read, depth):
        binding = self.index.refs.get(id(read))
        declaration = self.index.decls.get(binding)
        if (declaration is None or binding in self.index.unstable or binding in self.ambiguous
                or (declaration.annotations or {}).get("qualifier") != "const"
                or declaration.type_hint is None
                or self.order[id(declaration)] >= self.order[id(read)]
                or binding in self.active):
            return None
        if binding not in self.cache:
            self.active.add(binding)
            try:
                value = self.expression(declaration.value, depth + 1)
                if (value is not None and declaration.type_hint == "float"
                        and value.pine_type == PineType.INT):
                    value = ConstantValue(PineType.FLOAT, float(value.value))
                if value is not None and value.pine_type.value != declaration.type_hint:
                    value = None
                self.cache[binding] = value
            finally:
                self.active.remove(binding)
        return self.cache[binding]

    def _scalar(self, expr, depth):
        known = {}
        for node, _ in iter_ast_nodes(expr):
            if isinstance(node, Identifier) and id(node) in self.index.refs:
                value = self._binding(node, depth + 1)
                if value is None or value.pine_type == PineType.COLOR:
                    return None
                known[node.name] = value.value
        value, proven = fold_constant_expression(
            expr, known, {f"math.{name}": value for name, value in MATH_CONSTANTS.items()})
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
