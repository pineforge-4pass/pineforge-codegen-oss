"""Strict, source-order-preserving binding for typed Pine methods.

Typed extension methods are inventoried before semantic analysis so a call may
precede its declaration without falling through to a same-named builtin.  This
module deliberately contains no analyzer or codegen state: both phases consume
the same signature and binding result.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast_nodes import ASTNode, FuncCall, MethodDef, Program


@dataclass(frozen=True)
class MethodSignature:
    key: str
    param_names: tuple[str, ...]
    param_defaults: tuple[ASTNode | None, ...]
    param_type_hints: tuple[str | None, ...]
    declaration: MethodDef | None = None


@dataclass(frozen=True)
class BoundMethodArgs:
    """Non-receiver arguments in parameter and evaluation order."""

    args_by_param: tuple[ASTNode, ...]
    evaluation_order: tuple[ASTNode, ...]


class MethodBindError(ValueError):
    pass


def signature_from_method_def(node: MethodDef) -> MethodSignature:
    annotations = node.annotations or {}
    defaults = list(annotations.get("param_defaults", ()))
    hints = list(annotations.get("param_type_hints", ()))
    while len(defaults) < len(node.params):
        defaults.append(None)
    while len(hints) < len(node.params):
        hints.append(None)
    return MethodSignature(
        key=f"{node.type_name}.{node.name}",
        param_names=tuple(node.params[1:]),
        param_defaults=tuple(defaults[1:len(node.params)]),
        param_type_hints=tuple(hints[1:len(node.params)]),
        declaration=node,
    )


def signature_from_callable(
    key: str,
    param_names: list[str] | tuple[str, ...],
    param_defaults: list[ASTNode | None] | tuple[ASTNode | None, ...],
    param_type_hints: list[str | None] | tuple[str | None, ...] = (),
) -> MethodSignature:
    """Build a non-receiver signature from an analyzed method FuncInfo."""

    names = tuple(param_names)
    defaults = list(param_defaults)
    hints = list(param_type_hints)
    while len(defaults) < len(names):
        defaults.append(None)
    while len(hints) < len(names):
        hints.append(None)
    return MethodSignature(
        key=key,
        param_names=names,
        param_defaults=tuple(defaults[:len(names)]),
        param_type_hints=tuple(hints[:len(names)]),
    )


def inventory_method_signatures(program: Program) -> dict[str, MethodSignature]:
    """Inventory direct method declarations without analyzing their bodies."""

    result: dict[str, MethodSignature] = {}
    for stmt in program.body:
        if isinstance(stmt, MethodDef):
            signature = signature_from_method_def(stmt)
            # Preserve the analyzer's historical first-declaration lookup. This
            # prepass does not introduce method overloading or duplicate policy.
            result.setdefault(signature.key, signature)
    return result


def _written_actuals(call: FuncCall) -> list[ASTNode]:
    """Recover written order lost by the parser's args/kwargs split."""

    fallback = [*call.args, *call.kwargs.values()]
    recorded = (call.annotations or {}).get("call_arg_order", ())
    if (
        len(recorded) == len(fallback)
        and {id(node) for node in recorded} == {id(node) for node in fallback}
    ):
        return list(recorded)
    if not fallback or any(getattr(node, "loc", None) is None for node in fallback):
        return fallback
    indexed = list(enumerate(fallback))
    indexed.sort(
        key=lambda item: (
            item[1].loc.line,
            item[1].loc.col,
            item[0],
        )
    )
    return [node for _index, node in indexed]


def bind_method_call(
    signature: MethodSignature,
    call: FuncCall,
) -> BoundMethodArgs:
    """Bind one method call strictly, excluding the receiver argument."""

    names = signature.param_names
    if len(call.args) > len(names):
        raise MethodBindError(
            f"{signature.key}: too many positional arguments "
            f"(expected {len(names)}, got {len(call.args)})"
        )

    for name in call.kwargs:
        if name not in names:
            raise MethodBindError(
                f"{signature.key}: unknown keyword argument '{name}'"
            )

    bound: list[ASTNode | None] = [None] * len(names)
    for index, value in enumerate(call.args):
        bound[index] = value
    for name, value in call.kwargs.items():
        index = names.index(name)
        if bound[index] is not None:
            raise MethodBindError(
                f"{signature.key}: argument '{name}' passed both "
                "positionally and by keyword"
            )
        bound[index] = value

    inserted_defaults: list[ASTNode] = []
    for index, name in enumerate(names):
        if bound[index] is not None:
            continue
        default = (
            signature.param_defaults[index]
            if index < len(signature.param_defaults)
            else None
        )
        if default is None:
            raise MethodBindError(
                f"{signature.key}: missing required argument '{name}'"
            )
        bound[index] = default
        inserted_defaults.append(default)

    return BoundMethodArgs(
        args_by_param=tuple(value for value in bound if value is not None),
        evaluation_order=tuple([*_written_actuals(call), *inserted_defaults]),
    )
