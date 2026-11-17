"""Intrinsic binding and proven builtin argument-domain diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ast_nodes import (
    ASTNode, BoolLiteral, FuncCall, NaLiteral, NumberLiteral, StringLiteral,
)
from .diagnostic_codes import render_diagnostic
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import expr_start
from .signatures import FuncSig, IntrinsicFunc, Param
from .symbols import TypeSpec


_STRATEGY_ORDER_FUNCTIONS = frozenset({
    "strategy.entry", "strategy.order", "strategy.exit", "strategy.close",
    "strategy.close_all", "strategy.cancel", "strategy.cancel_all",
})


@dataclass(frozen=True)
class BoundIntrinsicCall:
    """Selected signature slots and types, without rewriting ASTs.

    A storage type does not itself authorize a refusal: Param.na_policy
    carries that separate semantic/admission contract.
    """

    intrinsic: IntrinsicFunc
    signature: FuncSig
    arguments: tuple[ASTNode | None, ...]
    parameter_types: tuple[TypeSpec | None, ...]
    receiver_type: TypeSpec | None = None


def _instantiated_type(
    param: Param, intrinsic: IntrinsicFunc, signature: FuncSig,
    supplied: dict[str, ASTNode], actual_types: dict[int, TypeSpec | None],
    receiver_type: TypeSpec | None, template_type: TypeSpec | None,
) -> TypeSpec | None:
    if param.type_from == "template":
        return template_type
    if param.type_from is not None:
        root, _, component = param.type_from.partition(".")
        spec = actual_types.get(id(supplied.get(root)))
        if not component:
            return spec
        if component == "element" and spec is not None:
            return spec.element
        if component == "key" and spec is not None:
            return spec.key
        if component == "value" and spec is not None:
            return spec.value
        return None
    if intrinsic.receiver_kind is not None and param is signature.params[0]:
        return receiver_type
    return TypeSpec.primitive(param.pine_type.value)


def parent_na_contract_params(
    intrinsic: IntrinsicFunc, pine_version: int | None,
) -> tuple[Param, ...]:
    """Eligible eager slots, before binding or querying any expression type."""
    if len(intrinsic.signatures) != 1:
        return ()
    return tuple(
        param for param in intrinsic.signatures[0].params
        if param.na_policy == "parent_eager"
        and (param.na_versions is None or pine_version in param.na_versions)
    )


def bind_parent_na_contract(
    node: FuncCall, intrinsic: IntrinsicFunc,
    type_of: Callable[[ASTNode], TypeSpec | None], *,
    pine_version: int | None,
    receiver: ASTNode | None = None, template_type: TypeSpec | None = None,
) -> BoundIntrinsicCall | None:
    """Project the parent's eager admission check onto one registered signature.

    This is not proof that the whole call is a valid builtin invocation.
    The parent checked its target slot before arity, unrelated arguments and
    user-method dispatch. Position won over a duplicate keyword. Preserve
    those outcomes, without mutating the AST or guessing an overload. Filter
    policies by the actual caller version before binding or type queries.
    """
    eligible = parent_na_contract_params(intrinsic, pine_version)
    if not eligible:
        return None
    signature = intrinsic.signatures[0]
    positional = ([receiver] if receiver is not None else []) + list(node.args)
    supplied = {
        param.name: positional[index] if index < len(positional)
        else node.kwargs[param.name]
        for index, param in enumerate(signature.params)
        if index < len(positional) or param.name in node.kwargs
    }
    if not any(
        isinstance(supplied.get(param.name), NaLiteral) for param in eligible
    ):
        return None
    receiver_type = None
    actual_types: dict[int, TypeSpec | None] = {}
    if intrinsic.receiver_kind is not None and signature.params:
        value = supplied.get(signature.params[0].name)
        if value is not None:
            receiver_type = type_of(value)
            actual_types[id(value)] = receiver_type
    parameter_types = tuple(
        _instantiated_type(
            param, intrinsic, signature, supplied, actual_types,
            receiver_type, template_type,
        ) if any(param is active for active in eligible) else None
        for param in signature.params
    )
    return BoundIntrinsicCall(
        intrinsic, signature,
        tuple(supplied.get(param.name) for param in signature.params),
        parameter_types, receiver_type,
    )


def bind_intrinsic_call(
    node: FuncCall,
    intrinsic: IntrinsicFunc,
    type_of: Callable[[ASTNode], TypeSpec | None],
    *,
    receiver: ASTNode | None = None,
    template_type: TypeSpec | None = None,
    needs: list[str] | None = None,
) -> BoundIntrinsicCall | None:
    """Select one signature using names, arity and proved argument types.

    A bare na does not choose between numeric and bool overloads. Unknown
    argument types keep alternatives viable; tied alternatives, invalid
    binding and unresolved receiver types leave the old admission in place.
    Missing defaults are not synthesized and the call is never mutated.
    """
    def unproved(reason: str) -> None:
        if needs is not None:
            needs.append(reason)

    if intrinsic.variadic:
        unproved("variadic signature has no proved fixed parameter binding")
        return None
    if receiver is not None and intrinsic.receiver_kind is None:
        unproved("the registered signature has no instance receiver")
        return None
    positional = ([receiver] if receiver is not None else []) + list(node.args)
    expressions = [*positional, *node.kwargs.values()]
    # Query once per expression, outside the overload loop.
    actual_types = {
        id(value): type_of(value)
        for value in expressions if not isinstance(value, NaLiteral)
    }
    candidates: list[BoundIntrinsicCall] = []
    for signature in intrinsic.signatures:
        names = [param.name for param in signature.params]
        if (len(positional) > len(names)
                or not node.kwargs.keys() <= set(names[len(positional):])):
            continue
        supplied = dict(zip(names, positional))
        supplied.update(node.kwargs)
        if any(
            param.name not in supplied
            and param.default is None and not param.optional
            for param in signature.params
        ):
            continue
        receiver_type = None
        if intrinsic.receiver_kind is not None:
            value = supplied.get(names[0]) if names else None
            receiver_type = actual_types.get(id(value))
            if (receiver_type is None
                    or receiver_type.kind != intrinsic.receiver_kind):
                continue

        parameter_types = tuple(
            _instantiated_type(
                param, intrinsic, signature, supplied, actual_types,
                receiver_type, template_type,
            ) for param in signature.params
        )
        arguments = tuple(supplied.get(name) for name in names)
        # Discriminate overloads only with actual proved types. A single
        # signature already proves its parameter slots; other validators own
        # incompatible values in unrelated slots.
        if len(intrinsic.signatures) > 1:
            compatible = True
            for value, expected in zip(arguments, parameter_types):
                if value is None or isinstance(value, NaLiteral):
                    continue
                actual = actual_types.get(id(value))
                if actual is None or expected is None:
                    continue
                if actual == expected:
                    continue
                if (actual.kind == expected.kind == "primitive"
                        and actual.name in {"int", "int64"}
                        and expected.name == "float"):
                    continue
                compatible = False
                break
            if not compatible:
                continue
        candidates.append(BoundIntrinsicCall(
            intrinsic, signature, arguments, parameter_types, receiver_type,
        ))
    if not candidates:
        unproved("no signature binds the supplied slots and proved receiver type")
        return None
    if len(candidates) != 1:
        unproved("overload remains ambiguous with the available argument types")
        return None
    return candidates[0]


def array_method_domain_error(
    method: str, receiver: TypeSpec | None,
) -> tuple[str, str | None] | None:
    """Return a proven builtin receiver mismatch, leaving other domains open.

    Join independently admits string, int and float arrays and refuses bool
    arrays. The bool refusal does not prove any other array domain invalid.
    Callers resolve authored methods before applying this builtin contract.
    """
    if (method != "join" or receiver is None or receiver.kind != "array"
            or receiver.element is None or receiver.element.kind != "primitive"
            or receiver.element.name != "bool"):
        return None
    return render_diagnostic(
        "PF-E1088", {
            "name": "array.join",
            "message": "cannot accept an array<bool> argument for parameter 'id'",
            "spelled": "CE10123",
        },
    )


def intrinsic_keyword_diagnostics(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
    *,
    filename: str | None = None,
    pine_version: int | None = None,
) -> list[Diagnostic]:
    """Check record-driven keyword prohibitions and verified keyword names.

    Other records retain their existing binding, including ignored names.
    This checks names, leaving argument order and other binding policies to
    their existing validators. A caller's filename fills the parser's default
    source label without replacing a location from an explicitly named source.
    Version-specific migration wording requires an explicit source version.
    """
    if intrinsic is None or not kwargs:
        return []
    is_math = intrinsic.name.startswith("math.")
    if intrinsic.variadic:
        value = next(iter(kwargs.values()))
        location = ((value.annotations or {}).get("keyword_location")
                    or expr_start(value).loc or value.loc
                    or SourceLocation("<input>", 1, 1, 2))
        if filename is not None and location.file == "<input>":
            location = SourceLocation(
                filename, location.line, location.col, location.end_col,
            )
        if is_math:
            message, hint = render_diagnostic(
                "PF-E5089", {"function": intrinsic.name},
            )
        else:
            message, hint = render_diagnostic(
                "PF-E1088", {
                    "name": intrinsic.name,
                    "message": "cannot accept keyword arguments because it accepts "
                               "a variable number of arguments",
                    "spelled": "CE10119: Functions that accept a variable number "
                               "of arguments cannot accept keyword arguments",
                },
            )
        return [Diagnostic(
            level=Level.ERROR, phase=Phase.ANALYZER, location=location,
            message=message, hint=hint,
        )]
    if not intrinsic.keyword_names_verified:
        return []
    names = tuple(dict.fromkeys(
        param.name for signature in intrinsic.signatures
        for param in signature.params
    ))
    diagnostics = []
    for name, value in kwargs.items():
        if name in names:
            continue
        location = ((value.annotations or {}).get("keyword_location")
                    or expr_start(value).loc or value.loc
                    or SourceLocation("<input>", 1, 1, 2))
        if filename is not None and location.file == "<input>":
            location = SourceLocation(
                filename, location.line, location.col, location.end_col,
            )
        if is_math:
            message, hint = render_diagnostic(
                "PF-E5090", {"function": intrinsic.name, "name": name,
                             "accepted": ", ".join(names)},
            )
        elif (pine_version == 6 and name == "when"
              and intrinsic.name in _STRATEGY_ORDER_FUNCTIONS):
            message, hint = render_diagnostic(
                "PF-E2081", {"key": intrinsic.name, "name": name},
            )
        else:
            message, hint = render_diagnostic(
                "PF-E2027", {"key": intrinsic.name, "name": name},
            )
        diagnostics.append(Diagnostic(
            level=Level.ERROR, phase=Phase.ANALYZER, location=location,
            message=message, hint=hint,
        ))
    return diagnostics


def validate_intrinsic_keywords(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
    *,
    filename: str | None = None,
    pine_version: int | None = None,
) -> None:
    """Validate the record's keyword policy for direct codegen clients."""
    diagnostics = intrinsic_keyword_diagnostics(
        intrinsic, kwargs, filename=filename, pine_version=pine_version,
    )
    if diagnostics:
        raise CompileError(diagnostics)


def _default_literal(param: Param, anchor: ASTNode) -> ASTNode | None:
    """The literal a parameter's declared default stands for, else None.

    A parameter that declares no default (``None``) or a default that is not a
    plain number, string or bool has none to stand for."""
    default = param.default
    location = expr_start(anchor).loc or anchor.loc
    if isinstance(default, bool):
        return BoolLiteral(value=default, loc=location)
    if isinstance(default, (int, float)):
        return NumberLiteral(value=default, loc=location)
    if isinstance(default, str):
        return StringLiteral(value=default, loc=location)
    return None


def bind_intrinsic_arguments(
    node: FuncCall, intrinsic: IntrinsicFunc, *, filename: str | None = None,
) -> None:
    """Move supplied keyword expressions to their canonical argument slots.

    Keep the original AST nodes, including their locations and annotations.
    A keyword that skips parameters takes its place after them, and each
    skipped parameter becomes the literal of its declared default
    (``matrix.new<float>(columns = 4)`` is ``matrix.new<float>(0, 4)``,
    ``math.random(seed = 3)`` is ``math.random(0.0, 1.0, 3)``). A skipped
    parameter with no declared default leaves the call as written.
    Argument-count, duplicate-slot and required-slot diagnostics remain with
    the existing signature validators.
    """
    validate_intrinsic_keywords(intrinsic, node.kwargs, filename=filename)
    if not node.kwargs:
        return
    widest = max(intrinsic.signatures, key=lambda signature: len(signature.params))
    remaining = widest.params[len(node.args):]
    names = [param.name for param in remaining]
    if not set(node.kwargs) <= set(names):
        return
    last = max(names.index(name) for name in node.kwargs)
    anchor = next(iter(node.kwargs.values()))
    bound = list(node.args)
    for param in remaining[:last + 1]:
        if param.name in node.kwargs:
            bound.append(node.kwargs[param.name])
            continue
        literal = _default_literal(param, anchor)
        if literal is None:
            return
        bound.append(literal)
    node.args = bound
    node.kwargs = {}
