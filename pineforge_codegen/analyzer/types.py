"""Pine type-hint and expression -> TypeSpec / PineType inference for the analyzer.

The historic ``analyzer.py`` had a small cluster of helpers that
answered one question: "given this Pine type hint string or AST
expression, what TypeSpec / PineType should we record on the
symbol?" This mixin collects them in one place. ``Analyzer`` mixes
``TypeHelper`` in alongside the future visitor mixins.

Mixin contract -- host class must provide the following attributes:

- ``self._symbols`` (``SymbolTable``): symbol-table source for
  ``Identifier`` lookups in ``_type_spec_from_expr``.
- ``self._udt_fields`` (``dict[str, dict[str, PineType]]``): UDT
  type name -> field schema. Read by ``_type_spec_from_hint`` (to
  fall back to ``TypeSpec.udt(name)``) and by
  ``_type_spec_from_expr`` (to recognise ``TypeName.new(...)``).
- ``self._udt_field_type_specs``
  (``dict[str, dict[str, TypeSpec]]``): structured UDT field
  metadata, used to resolve ``a.field`` member access.
- ``self._enum_defs`` (``dict[str, list[str]]``): enum name ->
  member list, used by ``_extract_literal_value`` when constant-
  folding ``EnumName.MEMBER`` to its ordinal.

And the following sibling methods (expected to come from
``Analyzer.base`` in the current step; future steps may move them
into their own mixins):

- ``self._visit`` -- visitor entry; used by ``_type_spec_from_expr``
  to type-check ``array.from(...)``'s first arg.

Consumed by visitor methods to convert Pine type hints / expression
nodes into ``TypeSpec`` and ``PineType`` values, and by
``_handle_*_call`` paths in the upcoming ``CallHandlers`` mixin.
The mixin avoids importing from ``base.py`` to stay free of import
cycles.
"""

from __future__ import annotations

from typing import Any

from .. import signatures as sigs
from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, ExprStmt, FuncCall, Identifier, IfStmt,
    MemberAccess, NaLiteral, NumberLiteral, StringLiteral, Subscript, Ternary,
    SwitchStmt, TupleAssign, TupleLiteral, UnaryOp, VarDecl,
)
from ..errors import Phase
from ..matrix_overloads import matrix_sum_has_rhs
from ..symbols import (
    PineType,
    TypeSpec,
    method_receiver_type_name,
    unify_type_specs,
)
from .operators import join_value_types, reference_value_type

# Drawing-objects-as-data type names (spec §4.1). Defined locally — the
# analyzer must not import from ``codegen`` (codegen imports analyzer, so the
# reverse would be a cycle). Mirrors codegen.tables.DRAWING_TYPE_TO_CPP keys.
_DRAWING_TYPE_NAMES = frozenset({"line", "box", "label", "linefill", "chart.point"})
_DRAWING_NS = frozenset({"line", "box", "label", "linefill"})
_DIRECT_ARRAY_VALUE_PRODUCERS = frozenset({
    "from",
    "new",
    "new_float",
    "new_int",
    "new_bool",
    "new_string",
    "copy",
})

# Keep this analyzer-owned mirror in sync with
# codegen.tables.MATRIX_RETURNING_METHODS.  The analyzer cannot import from
# codegen (codegen already imports analyzer), but nullable selections need the
# exact matrix result type before codegen registers global aggregate members.
_MATRIX_RETURNING_METHODS = frozenset({
    "copy", "submatrix", "transpose", "concat", "diff", "mult", "pow",
    "inv", "pinv", "eigenvectors", "kron",
})


class TypeHelper:
    """Pine type-hint / expression inference.

    Mixed into ``Analyzer``; not meant to be instantiated standalone.
    Methods that need shared state (``self._symbols``,
    ``self._udt_fields``, ``self._udt_field_type_specs``,
    ``self._enum_defs``) document the contract in the module
    docstring above.
    """

    def _split_top_level_type_args(self, text: str) -> list[str]:
        args: list[str] = []
        cur: list[str] = []
        depth = 0
        for ch in text:
            if ch == "<":
                depth += 1
                cur.append(ch)
            elif ch == ">":
                depth -= 1
                cur.append(ch)
            elif ch == "," and depth == 0:
                args.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        tail = "".join(cur).strip()
        if tail:
            args.append(tail)
        return args

    def _named_element_spec(self, name: str) -> TypeSpec:
        """The TypeSpec of a type name written as a collection's generic
        argument that no primitive or UDT hint resolved: a declared enum is
        the enum (``Choice.second`` is one, and two kinds of one name never
        compare), any other name keeps the udt identity it always had."""
        if name in self._enum_defs:
            return TypeSpec("enum", name=name)
        return TypeSpec.udt(name)

    def _type_spec_from_hint(self, hint: str | None) -> TypeSpec | None:
        if not hint:
            return None
        hint = hint.strip().replace(" ", "")
        primitive = {"int", "float", "bool", "string", "color"}
        if hint in primitive:
            return TypeSpec.primitive(hint)
        if hint.startswith("array<") and hint.endswith(">"):
            inner = hint[len("array<"):-1]
            elem = self._type_spec_from_hint(inner) or self._named_element_spec(inner)
            return TypeSpec.array(elem)
        if hint.startswith("matrix<") and hint.endswith(">"):
            inner = hint[len("matrix<"):-1]
            return TypeSpec.matrix(
                self._type_spec_from_hint(inner) or self._named_element_spec(inner))
        if hint.startswith("map<") and hint.endswith(">"):
            inner = hint[len("map<"):-1]
            parts = self._split_top_level_type_args(inner)
            if len(parts) == 2:
                key = self._type_spec_from_hint(parts[0]) or self._named_element_spec(parts[0])
                val = self._type_spec_from_hint(parts[1]) or self._named_element_spec(parts[1])
                return TypeSpec.map(key, val)
        if hint in self._udt_fields:
            return TypeSpec.udt(hint)
        # Drawing-objects-as-data (P3): scalar ``line``/``box``/``label``/
        # ``linefill``/``chart.point`` carry the handle identity via a udt
        # TypeSpec. Without this the analyzer field-spec filter (base.py ~847)
        # erases a scalar drawing field, collapsing it to double. Drawing names
        # are NOT in _udt_fields.
        if hint in _DRAWING_TYPE_NAMES:
            return TypeSpec.udt(hint)
        return None

    def _param_type_specs_from_def(self, func_def) -> list:
        """Per-parameter ``TypeSpec`` (or ``None``) from a function's DECLARED
        parameter type hints — the authoritative source for typed params
        (``pivot hi``, ``string tf``, ``line[] arr``). Untyped params are
        ``None`` here so regular-function call-site inference can fill them.
        """
        hints = (getattr(func_def, "annotations", None) or {}).get("param_type_hints", [])
        specs: list = []
        for i in range(len(func_def.params)):
            hint = hints[i] if i < len(hints) else None
            specs.append(self._type_spec_from_hint(hint) if hint else None)
        return specs

    def _template_args_from_call(self, node: FuncCall) -> list[str]:
        callee = node.callee
        ann = getattr(callee, "annotations", None) or {}
        raw = ann.get("template_args") or []
        return [str(x).replace(" ", "") for x in raw]

    def _array_from_element_spec(self, value: ASTNode | None) -> TypeSpec | None:
        """Exact scalar element type for one ``array.from`` argument.

        Keep this refinement local to array construction.  General BinOp
        TypeSpec inference changes unrelated scalar comparator lowering across
        the corpus; array declarations only need enough structure to keep the
        analyzer's captured LHS type aligned with codegen's RHS vector type.
        """
        if value is None:
            return None
        if isinstance(value, NumberLiteral):
            return TypeSpec.primitive(
                "float" if isinstance(value.value, float) else "int"
            )
        if isinstance(value, BoolLiteral):
            return TypeSpec.primitive("bool")
        if isinstance(value, StringLiteral):
            return TypeSpec.primitive("string")
        if isinstance(value, UnaryOp):
            if value.op == "not":
                return TypeSpec.primitive("bool")
            operand = self._array_from_element_spec(value.operand)
            if operand is not None and operand.kind == "primitive" and operand.name in ("int", "int64", "float"):
                return operand
            return None
        if isinstance(value, BinOp):
            left = self._array_from_element_spec(value.left)
            right = self._array_from_element_spec(value.right)
            if value.op in ("==", "!=", ">", "<", ">=", "<=", "and", "or"):
                return TypeSpec.primitive("bool")
            if (left is not None and right is not None
                    and left.kind == "primitive" and right.kind == "primitive"):
                if left.name == "string" or right.name == "string":
                    return TypeSpec.primitive("string")
                if value.op == "/" or left.name == "float" or right.name == "float":
                    return TypeSpec.primitive("float")
                if left.name == "int" and right.name == "int":
                    return TypeSpec.primitive("int")
            return None
        if self._is_color_constructor(value):
            # A packed-ARGB color, as codegen's ``_is_color_value`` reads it:
            # the declaration and the constructor agree on ``int64_t``.
            return TypeSpec.primitive("color")
        spec = self._type_spec_from_expr(value)
        if spec is not None:
            return spec
        if isinstance(value, Identifier):
            sym = self._symbols.resolve(value.name)
            if sym is not None and sym.pine_type in {
                PineType.INT, PineType.FLOAT, PineType.BOOL,
                PineType.STRING, PineType.COLOR,
            }:
                return self._pine_type_to_spec(sym.pine_type)
        return None

    @classmethod
    def _is_color_constructor(cls, value: ASTNode | None) -> bool:
        """A color literal, ``color.<name>``, a ``color.new`` / ``rgb`` /
        ``from_gradient`` call, or a conditional selecting one."""
        if isinstance(value, ColorLiteral):
            return True
        if isinstance(value, MemberAccess):
            return (isinstance(value.object, Identifier)
                    and value.object.name == "color")
        if isinstance(value, FuncCall):
            callee = value.callee
            return (isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "color"
                    and callee.member in ("new", "rgb", "from_gradient"))
        if isinstance(value, Ternary):
            return (cls._is_color_constructor(value.true_val)
                    or cls._is_color_constructor(value.false_val))
        return False

    @staticmethod
    def _selection_terminal_expr(
        body: list[ASTNode] | None,
    ) -> ASTNode | None:
        """Return one if/switch branch's value expression, if present."""
        if not body:
            return None
        terminal = body[-1]
        return terminal.expr if isinstance(terminal, ExprStmt) else terminal

    @staticmethod
    def _selection_node_is_na(node: ASTNode | None) -> bool:
        """Whether a selection arm is explicit or implicit Pine ``na``."""
        return (
            node is None
            or isinstance(node, NaLiteral)
            or (isinstance(node, Identifier) and node.name == "na")
        )

    @classmethod
    def _selection_contains_na(cls, node: ASTNode | None) -> bool:
        """Whether a selected value has a bare or implicit ``na`` arm."""
        if cls._selection_node_is_na(node):
            return True
        if isinstance(node, Ternary):
            return (cls._selection_contains_na(node.true_val)
                    or cls._selection_contains_na(node.false_val))
        if isinstance(node, IfStmt):
            return any(cls._selection_contains_na(cls._selection_terminal_expr(body))
                       for body in (node.body, node.else_body))
        if isinstance(node, SwitchStmt):
            return (
                any(cls._selection_contains_na(cls._selection_terminal_expr(body))
                    for _, body in node.cases)
                or cls._selection_contains_na(
                    cls._selection_terminal_expr(node.default_body)
                )
            )
        return False

    def _selection_type_spec(
        self,
        branches: list[tuple[ASTNode | None, TypeSpec | None]],
        *,
        nullable: bool | None = None,
    ) -> TypeSpec | None:
        """Keep exact collection joins; extend other families only around ``na``."""
        if nullable is None:
            nullable = any(self._selection_contains_na(node) for node, _ in branches)
        result: TypeSpec | None = None
        for node, spec in branches:
            if self._selection_node_is_na(node):
                continue
            if (nullable and spec is None
                    and isinstance(node, (ColorLiteral, MemberAccess, FuncCall))
                    and self._is_color_constructor(node)):
                spec = TypeSpec.primitive("color")
            if spec is None:
                return None
            if not nullable and spec.kind not in {"map", "matrix"}:
                return None
            result = (
                spec
                if result is None
                else unify_type_specs(result, spec)
            )
            if result is None:
                return None
        return result

    def _ternary_type_specs(
        self, value: Ternary,
    ) -> dict[int, tuple[TypeSpec | None, bool]]:
        """Derive arm specs and nullability once in the current lexical context."""
        # Keep results within this query: a later query on the same AST can
        # have different parameter, local or callable return types.
        specs: dict[int, tuple[TypeSpec | None, bool]] = {}
        pending = [(value, False)]
        while pending:
            node, ready = pending.pop()
            if id(node) in specs:
                continue
            if isinstance(node, Ternary):
                if not ready:
                    pending.append((node, True))
                    pending.append((node.false_val, False))
                    pending.append((node.true_val, False))
                    continue
                true_spec, true_na = specs[id(node.true_val)]
                false_spec, false_na = specs[id(node.false_val)]
                nullable = true_na or false_na
                spec = self._type_spec_from_expr(
                    node, _ternary_arms=(true_spec, false_spec, nullable),
                )
            else:
                spec = self._type_spec_from_expr(node)
                nullable = self._selection_contains_na(node)
            specs[id(node)] = (spec, nullable)
        return specs

    def _type_spec_from_expr(
        self,
        value: ASTNode | None,
        *,
        _ternary_arms: tuple[TypeSpec | None, TypeSpec | None, bool] | None = None,
    ) -> TypeSpec | None:
        if value is None:
            return None
        budget = getattr(self, "_budget", None)
        if budget is not None:
            # A method chain re-infers each receiver more than once, so this
            # recursion can outgrow the visitor checkpoints.
            self._budget_visit_count += 1
            if self._budget_visit_count % 128 == 0:
                budget.check(value.loc, Phase.ANALYZER)
        if isinstance(value, NumberLiteral):
            return TypeSpec.primitive(
                "float" if isinstance(value.value, float) else "int"
            )
        if isinstance(value, BoolLiteral):
            return TypeSpec.primitive("bool")
        if isinstance(value, StringLiteral):
            return TypeSpec.primitive("string")
        if isinstance(value, Ternary):
            if _ternary_arms is None:
                return self._ternary_type_specs(value)[id(value)][0]
            true_spec, false_spec, nullable = _ternary_arms
            selection_spec = self._selection_type_spec([
                (value.true_val, true_spec),
                (value.false_val, false_spec),
            ], nullable=nullable)
            if selection_spec is not None:
                if (selection_spec.kind == "primitive"
                        and selection_spec.name in {"int", "int64"}
                        and id(value) in getattr(self, "_ternary_int_fallbacks", ())):
                    # An integer spec must not retype the analyzed missing arm.
                    return None
                return selection_spec

            def direct_user_udt_ctor_name(node: ASTNode) -> str | None:
                if not isinstance(node, FuncCall):
                    return None
                callee = node.callee
                if not (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.member == "new"
                ):
                    return None
                name = callee.object.name
                return name if name in self._udt_fields else None

            # Selecting between two values of the same user-defined type
            # preserves that receiver type.  Codegen already applies this
            # rule; the analyzer must agree so stateful method calls on a UDT
            # ternary enter the written-callsite clone graph.
            if (true_spec is not None
                    and true_spec.kind == "udt"
                    and true_spec == false_spec):
                return true_spec
            # A direct user-UDT constructor selected against bare ``na`` has
            # one unambiguous value type.  Require the constructor AST itself,
            # not merely an inferred UDT expression, so temporary array-element
            # identity returns continue to fail closed on their own surface.
            true_ctor = direct_user_udt_ctor_name(value.true_val)
            if (true_spec is not None
                    and true_spec.kind == "udt"
                    and true_spec.name == true_ctor
                    and isinstance(value.false_val, NaLiteral)):
                return true_spec
            false_ctor = direct_user_udt_ctor_name(value.false_val)
            if (false_spec is not None
                    and false_spec.kind == "udt"
                    and false_spec.name == false_ctor
                    and isinstance(value.true_val, NaLiteral)):
                return false_spec
            # Drawing handles are nullable reference-like values in Pine.  A
            # bare ``na`` arm therefore acquires the other arm's exact handle
            # type, just like the established PineMap path above.  Keep this
            # intentionally narrower than arbitrary UDTs/collections: their
            # target-typed selection semantics are not established here.
            if (true_spec is not None
                    and true_spec.kind == "udt"
                    and true_spec.name in _DRAWING_TYPE_NAMES
                    and isinstance(value.false_val, NaLiteral)):
                return true_spec
            if (false_spec is not None
                    and false_spec.kind == "udt"
                    and false_spec.name in _DRAWING_TYPE_NAMES
                    and isinstance(value.true_val, NaLiteral)):
                return false_spec
            return None
        if isinstance(value, Subscript):
            # Pine's history operator preserves the value type: a
            # ``Series<line>`` read such as ``h[1]`` is a scalar ``line``
            # handle and ``c[1]`` of a user-defined object the reference ``c``
            # held (fixtures/udt_history_tv), not the legacy numeric fallback.
            # Collection subscripts have separate array/map semantics and
            # primitive history inference already flows through PineType in
            # ``_visit_Subscript``.
            receiver_spec = self._type_spec_from_expr(value.object)
            if (receiver_spec is not None
                    and receiver_spec.kind == "udt"
                    and (receiver_spec.name in _DRAWING_TYPE_NAMES
                         or receiver_spec.name in self._udt_fields)):
                return receiver_spec
            if (receiver_spec is not None
                    and receiver_spec.kind in ("array", "matrix")
                    and isinstance(value.object, Identifier)
                    and self._collection_history_is_supported(value)):
                # An array's or a matrix's history is a collection of its
                # type (fixtures/array_history_tv; pineforge_codegen/
                # collection_history.py); a legacy read keeps the element
                # typing it had.
                return receiver_spec
            return None
        if isinstance(value, IfStmt):
            true_node = self._selection_terminal_expr(value.body)
            false_node = self._selection_terminal_expr(value.else_body)
            true_spec = self._type_spec_from_expr(true_node)
            false_spec = self._type_spec_from_expr(false_node)
            selection_spec = self._selection_type_spec([
                (true_node, true_spec),
                (false_node, false_spec),
            ])
            if selection_spec is not None:
                return selection_spec
            true_is_na = (
                isinstance(true_node, NaLiteral)
                or (isinstance(true_node, Identifier)
                    and true_node.name == "na")
            )
            false_is_na = (
                isinstance(false_node, NaLiteral)
                or (isinstance(false_node, Identifier)
                    and false_node.name == "na")
            )
            if (true_spec is not None
                    and true_spec.kind == "udt"
                    and true_spec.name in _DRAWING_TYPE_NAMES
                    and true_spec == false_spec):
                return true_spec
            if (true_spec is not None
                    and true_spec.kind == "udt"
                    and true_spec.name in _DRAWING_TYPE_NAMES
                    and false_is_na):
                return true_spec
            if (false_spec is not None
                    and false_spec.kind == "udt"
                    and false_spec.name in _DRAWING_TYPE_NAMES
                    and true_is_na):
                return false_spec
            return None
        if isinstance(value, SwitchStmt):
            branches: list[tuple[ASTNode | None, TypeSpec | None]] = []
            for _case_expr, case_body in value.cases:
                terminal = self._selection_terminal_expr(case_body)
                branches.append((
                    terminal,
                    self._type_spec_from_expr(terminal),
                ))
            default_terminal = self._selection_terminal_expr(
                value.default_body
            )
            branches.append((
                default_terminal,
                self._type_spec_from_expr(default_terminal),
            ))
            return self._selection_type_spec(branches)
        if isinstance(value, FuncCall):
            cal = value.callee
            func = cal.member if isinstance(cal, MemberAccess) else None
            ns = cal.object.name if isinstance(cal, MemberAccess) and isinstance(cal.object, Identifier) else None
            targs = self._template_args_from_call(value)
            if isinstance(cal, MemberAccess):
                typed_receiver_spec = self._type_spec_from_expr(cal.object)
                typed_receiver_name = method_receiver_type_name(
                    typed_receiver_spec
                )
                method_key = (
                    f"{typed_receiver_name}.{func}"
                    if typed_receiver_name is not None
                    else None
                )
                method_info = next(
                    (
                        info
                        for info in getattr(self, "_func_infos", ())
                        if info.name == method_key
                        and getattr(info, "is_udt_method", False)
                    ),
                    None,
                ) if typed_receiver_name is not None else None
                if method_info is not None:
                    return_spec = getattr(
                        method_info, "return_type_spec", None
                    )
                    if return_spec is not None:
                        return return_spec
                    udt_return = getattr(
                        method_info, "udt_return_type", None
                    )
                    if udt_return is not None:
                        return TypeSpec.udt(udt_return)
                    if method_info.return_type in {
                        PineType.INT,
                        PineType.FLOAT,
                        PineType.BOOL,
                        PineType.STRING,
                        PineType.COLOR,
                    }:
                        return self._pine_type_to_spec(
                            method_info.return_type
                        )
                    return None
                if (
                    method_key is not None
                    and method_key in getattr(self, "_method_signatures", {})
                ):
                    # A later authored method declaration owns this surface.
                    # Its body-derived return type is not available yet, but a
                    # same-named builtin must not lend it a false type.
                    return None
                if (
                    typed_receiver_spec is not None
                    and typed_receiver_spec.kind == "udt"
                    and typed_receiver_spec.name in self._udt_fields
                    and func == "copy"
                ):
                    return typed_receiver_spec
            # Drawing-objects-as-data return typing: *.new / *.copy -> handle of
            # the self-type; linefill.get_line* -> line; chart.point.* -> point.
            if ns in _DRAWING_NS:
                if func in ("new", "copy"):
                    return TypeSpec.udt(ns)
                if ns == "linefill" and func in ("get_line1", "get_line2"):
                    return TypeSpec.udt("line")
            if (isinstance(cal, MemberAccess) and isinstance(cal.object, MemberAccess)
                    and isinstance(cal.object.object, Identifier)
                    and cal.object.object.name == "chart" and cal.object.member == "point"):
                return TypeSpec.udt("chart.point")
            if ns == "array" and func in ("new", "new_float", "new_int", "new_bool", "new_string", "from"):
                if func == "new_float":
                    return TypeSpec.array(TypeSpec.primitive("float"))
                if func == "new_int":
                    return TypeSpec.array(TypeSpec.primitive("int"))
                if func == "new_bool":
                    return TypeSpec.array(TypeSpec.primitive("bool"))
                if func == "new_string":
                    return TypeSpec.array(TypeSpec.primitive("string"))
                if targs:
                    elem = self._type_spec_from_hint(targs[0]) or self._named_element_spec(targs[0])
                    return TypeSpec.array(elem)
                if func == "from" and value.args:
                    elem_specs = [
                        self._array_from_element_spec(arg)
                        or self._pine_type_to_spec(self._visit(arg))
                        for arg in value.args
                    ]
                    if any(
                        s is not None and s.kind == "primitive" and s.name == "float"
                        for s in elem_specs
                    ) and all(
                        s is not None and s.kind == "primitive" and s.name in ("int", "int64", "float")
                        for s in elem_specs
                    ):
                        return TypeSpec.array(TypeSpec.primitive("float"))
                    first_spec = elem_specs[0]
                    if first_spec is not None:
                        return TypeSpec.array(first_spec)
                    first = self._visit(value.args[0])
                    return TypeSpec.array(self._pine_type_to_spec(first))
                return TypeSpec.array(TypeSpec.primitive("float"))
            # Functional-form array element/copy accessors: the receiver is
            # the first argument (``array.copy(arr)``), or the exact ``id``
            # keyword for ``array.copy(id=arr)``.  The latter deliberately
            # uses the shape validator shared by terminal-return recovery so
            # duplicate/unknown keyword forms do not acquire a type by
            # accident.
            if (ns == "array"
                    and func in ("copy", "slice", "get", "first", "last",
                                 "pop", "shift", "remove")):
                receiver = None
                if func == "copy":
                    receiver = self._direct_namespace_array_copy_source(value)
                elif value.args:
                    receiver = value.args[0]
                arg_spec = self._type_spec_from_expr(receiver)
                if arg_spec is not None and arg_spec.kind == "array":
                    if func in ("copy", "slice"):
                        return arg_spec
                    return arg_spec.element
            if ns == "matrix" and func == "new":
                if targs:
                    elem = self._type_spec_from_hint(targs[0]) or self._named_element_spec(targs[0])
                else:
                    elem = TypeSpec.primitive("float")
                return TypeSpec.matrix(elem)
            if ns == "matrix" and (func in _MATRIX_RETURNING_METHODS or (
                    func == "sum" and matrix_sum_has_rhs(value, namespace=True))):
                receiver = (value.args[0] if value.args else
                            value.kwargs.get("id1", value.kwargs.get("id")))
                receiver_spec = self._type_spec_from_expr(receiver)
                if receiver_spec is not None and receiver_spec.kind == "matrix":
                    return receiver_spec
            if ns == "map" and func == "new":
                key = self._type_spec_from_hint(targs[0]) if len(targs) > 0 else TypeSpec.primitive("string")
                val = self._type_spec_from_hint(targs[1]) if len(targs) > 1 else TypeSpec.primitive("float")
                return TypeSpec.map(key or TypeSpec.primitive("string"), val or TypeSpec.primitive("float"))
            if ns == "map" and func in {
                "put", "get", "remove", "contains", "size", "keys",
                "values", "copy", "put_all", "clear",
            }:
                receiver = value.args[0] if value.args else value.kwargs.get("id")
                recv_spec = self._type_spec_from_expr(receiver)
                if recv_spec is not None and recv_spec.kind == "map":
                    if func in ("put", "get", "remove"):
                        return recv_spec.value
                    if func == "keys":
                        return TypeSpec.array(
                            recv_spec.key or TypeSpec.primitive("string")
                        )
                    if func == "values":
                        return TypeSpec.array(
                            recv_spec.value or TypeSpec.primitive("float")
                        )
                    if func == "copy":
                        return recv_spec
                    if func == "contains":
                        return TypeSpec.primitive("bool")
                    if func == "size":
                        return TypeSpec.primitive("int")
            if ns == "str" and func == "split":
                return TypeSpec.array(TypeSpec.primitive("string"))
            if ns == "ta" and func == "pivot_point_levels":
                return TypeSpec.array(TypeSpec.primitive("float"))
            if ns == "request" and func == "security_lower_tf":
                # ``request.security_lower_tf`` returns array<T> where T
                # is the Pine type of the expression argument. We use the
                # PineType cached on the FuncCall by the analyzer's
                # ``_handle_request_security_lower_tf`` to avoid a second
                # ``_visit`` (which would double-allocate TA call sites
                # for TA-bearing expressions).
                anns = getattr(value, "annotations", None) or {}
                inner = anns.get("lower_tf_element_pine_type")
                if inner is None:
                    return TypeSpec.array(TypeSpec.primitive("float"))
                return TypeSpec.array(self._pine_type_to_spec(inner))
            if ns in self._udt_fields and func in {"new", "copy"}:
                return TypeSpec.udt(ns)
            if isinstance(cal, MemberAccess):
                recv_spec = self._type_spec_from_expr(cal.object)
                if recv_spec is not None and recv_spec.kind == "array":
                    if func in ("get", "first", "last", "pop", "shift", "remove"):
                        return recv_spec.element
                    if func in ("copy", "slice"):
                        return recv_spec
                if recv_spec is not None and recv_spec.kind == "map":
                    if func in ("put", "get", "remove"):
                        return recv_spec.value
                    if func == "keys":
                        return TypeSpec.array(recv_spec.key or TypeSpec.primitive("string"))
                    if func == "values":
                        return TypeSpec.array(recv_spec.value or TypeSpec.primitive("float"))
                    if func == "copy":
                        return recv_spec
                    if func == "contains":
                        return TypeSpec.primitive("bool")
                    if func == "size":
                        return TypeSpec.primitive("int")
                if recv_spec is not None and recv_spec.kind == "matrix":
                    if func in _MATRIX_RETURNING_METHODS or (
                            func == "sum" and matrix_sum_has_rhs(value)):
                        return recv_spec
                    if func in ("row", "col"):
                        return TypeSpec.array(recv_spec.element)
                    if func == "get":
                        return recv_spec.element
                    if func == "eigenvalues":
                        return TypeSpec.array(TypeSpec.primitive("float"))
                receiver_name = method_receiver_type_name(recv_spec)
                if receiver_name is not None:
                    method_key = f"{receiver_name}.{func}"
                    method_info = next(
                        (
                            info
                            for info in getattr(self, "_func_infos", ())
                            if info.name == method_key
                            and getattr(info, "is_udt_method", False)
                        ),
                        None,
                    )
                    return_spec = getattr(
                        method_info, "return_type_spec", None
                    )
                    if return_spec is not None:
                        return return_spec
                    if method_key in getattr(self, "_method_signatures", {}):
                        return None
                # Drawing method-form: a.copy() -> same handle; lf.get_line*() -> line.
                if (recv_spec is not None and recv_spec.kind == "udt"
                        and recv_spec.name in _DRAWING_TYPE_NAMES):
                    if func == "copy":
                        return recv_spec
                    if recv_spec.name == "linefill" and func in ("get_line1", "get_line2"):
                        return TypeSpec.udt("line")
        if isinstance(value, Identifier):
            sym = self._symbols.resolve(value.name)
            if sym is not None:
                if sym.type_spec is not None:
                    return sym.type_spec
                if sym.pine_type in {
                    PineType.INT,
                    PineType.FLOAT,
                    PineType.BOOL,
                    PineType.STRING,
                    PineType.COLOR,
                }:
                    return self._pine_type_to_spec(sym.pine_type)
        if isinstance(value, FuncCall):
            # User-function return spec (e.g. an array-returning
            # ``buildPDLevels() => array.from(...)``), so a caller's
            # ``allLevels = buildPDLevels()`` infers an array TypeSpec.
            cal = value.callee
            if isinstance(cal, MemberAccess):
                receiver_name = method_receiver_type_name(
                    self._type_spec_from_expr(cal.object)
                )
                fname = (
                    f"{receiver_name}.{cal.member}"
                    if receiver_name is not None
                    else cal.member
                )
            else:
                fname = cal.name if isinstance(cal, Identifier) else None
            if fname and fname in getattr(self, "_func_return_type_specs", {}):
                return self._func_return_type_specs[fname]
            if fname and fname in getattr(self, "_func_udt_return_types", {}):
                udt_return = self._func_udt_return_types[fname]
                if udt_return in _DRAWING_TYPE_NAMES:
                    return TypeSpec.udt(udt_return)
        if isinstance(value, MemberAccess):
            owner = self._type_spec_from_expr(value.object)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(value.member)
        return None

    @staticmethod
    def _direct_terminal_return_expr(func_def) -> ASTNode | None:
        """Return a UDF's direct terminal expression, if it has one."""
        if not func_def.body:
            return None
        last_stmt = func_def.body[-1]
        if isinstance(last_stmt, ExprStmt):
            return last_stmt.expr
        if not isinstance(last_stmt, TupleLiteral) and hasattr(last_stmt, "loc"):
            return last_stmt
        return None

    def _terminal_map_selection_return_spec(
        self,
        value: ASTNode | None,
        parameter_specs: dict[str, TypeSpec | None],
    ) -> TypeSpec | None:
        """Infer a map returned by a terminal ternary/block selection.

        Untyped UDF parameters do not have a lexical ``TypeSpec`` while their
        definition is first analyzed.  At a concrete call site, however, the
        argument specs are known.  Propagate those specs through only the two
        Pine selection shapes whose result type is determined by compatible
        branches: ``condition ? a : b`` and terminal ``if`` expressions.

        A direct ``na`` arm is context-typed by the opposite map arm.  All
        other unresolved or incompatible shapes return ``None`` so this helper
        cannot turn a scalar/non-map UDF into a map-returning function.
        """

        def branch_terminal(node: ASTNode | None) -> ASTNode | None:
            if not isinstance(node, IfStmt):
                return node
            if not node.body or not node.else_body:
                return None
            return node

        def body_terminal(body: list[ASTNode] | None) -> ASTNode | None:
            if not body:
                return None
            terminal = body[-1]
            return terminal.expr if isinstance(terminal, ExprStmt) else terminal

        def resolve(node: ASTNode | None) -> TypeSpec | None:
            if node is None or isinstance(node, NaLiteral):
                return None
            if isinstance(node, Identifier) and node.name in parameter_specs:
                spec = parameter_specs[node.name]
                return spec if spec is not None and spec.kind == "map" else None
            if isinstance(node, Ternary):
                return compatible(node.true_val, node.false_val)
            if isinstance(node, IfStmt):
                return compatible(
                    body_terminal(node.body),
                    body_terminal(node.else_body),
                )
            spec = self._type_spec_from_expr(node)
            return spec if spec is not None and spec.kind == "map" else None

        def compatible(
            left_node: ASTNode | None,
            right_node: ASTNode | None,
        ) -> TypeSpec | None:
            left_node = branch_terminal(left_node)
            right_node = branch_terminal(right_node)
            if left_node is None or right_node is None:
                return None
            left = resolve(left_node)
            right = resolve(right_node)
            if left is not None and right is not None:
                return left if left == right else None
            if left is not None and isinstance(right_node, NaLiteral):
                return left
            if right is not None and isinstance(left_node, NaLiteral):
                return right
            return None

        if isinstance(value, Ternary):
            return compatible(value.true_val, value.false_val)
        if isinstance(value, IfStmt):
            return compatible(
                body_terminal(value.body),
                body_terminal(value.else_body),
            )
        return None

    def _terminal_map_call_return(
        self,
        value: ASTNode | None,
        parameter_specs: dict[str, TypeSpec | None] | None = None,
    ) -> tuple[PineType, TypeSpec | None] | None:
        """Return metadata for a direct terminal map call in a UDF.

        This deliberately does not participate in general expression or
        declaration inference. During function-definition analysis it uses
        the active lexical scope for locals and typed parameters; for untyped
        parameters it may be called again with call-site ``parameter_specs``.
        Arbitrary-expression receivers stay out of scope because their map
        method routing is not supported yet.
        """
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None

        callee = value.callee
        method = callee.member
        param_names = sigs.get_param_names("map", method)
        if param_names is None:
            return None
        if isinstance(callee.object, Identifier) and callee.object.name == "map":
            receiver = value.args[0] if value.args else value.kwargs.get("id")
        elif isinstance(callee.object, Identifier):
            # Global/local/typed-parameter method forms only. Do not infer an
            # arbitrary expression receiver that codegen cannot route.
            param_names = param_names[1:]
            receiver = callee.object
        else:
            return None

        # Match the map binder's positional-prefix/named-slot rule without
        # changing the codegen diagnostics of malformed calls.
        if len(value.args) > len(param_names):
            return None
        if any(name not in param_names for name in value.kwargs):
            return None
        if any(name in value.kwargs for name in param_names[:len(value.args)]):
            return None
        provided = set(param_names[:len(value.args)]) | set(value.kwargs)
        if provided != set(param_names):
            return None
        if receiver is None:
            return None

        recv_spec = None
        if (
            isinstance(receiver, Identifier)
            and parameter_specs is not None
            and receiver.name in parameter_specs
        ):
            recv_spec = parameter_specs.get(receiver.name)
            # An unresolved parameter still shadows any same-named global.
            if recv_spec is None:
                return None
        else:
            recv_spec = self._type_spec_from_expr(receiver)
        if recv_spec is None or recv_spec.kind != "map":
            return None

        if method in ("clear", "put_all"):
            return PineType.VOID, None
        if method == "keys":
            return (
                PineType.VOID,
                TypeSpec.array(recv_spec.key or TypeSpec.primitive("string")),
            )
        if method == "values":
            return (
                PineType.VOID,
                TypeSpec.array(recv_spec.value or TypeSpec.primitive("float")),
            )
        if method == "copy":
            return PineType.VOID, recv_spec
        if (
            method in ("put", "get", "remove")
            and recv_spec.value is not None
            and recv_spec.value.kind == "primitive"
            and recv_spec.value.name == "string"
        ):
            return PineType.STRING, None
        return None

    def _terminal_array_get_receiver(
        self,
        value: ASTNode | None,
    ) -> ASTNode | None:
        """Return a lexically valid receiver for a direct terminal get."""
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None
        callee = value.callee
        if callee.member != "get":
            return None

        functional = (
            isinstance(callee.object, Identifier)
            and callee.object.name == "array"
            and self._symbols.resolve("array") is None
        )
        if functional:
            if len(value.args) == 2 and not value.kwargs:
                return value.args[0]
            if len(value.args) == 1 and set(value.kwargs) == {"index"}:
                return value.args[0]
            if not value.args and set(value.kwargs) == {"id", "index"}:
                return value.kwargs["id"]
            return None

        if len(value.args) == 1 and not value.kwargs:
            return callee.object
        if not value.args and set(value.kwargs) == {"index"}:
            return callee.object
        return None

    @staticmethod
    def _direct_namespace_array_copy_source(
        value: ASTNode | None,
    ) -> ASTNode | None:
        """Return the sole receiver of an exact ``array.copy`` call shape.

        Pine v6 accepts either ``array.copy(source)`` or
        ``array.copy(id=source)``.  Keep this structural helper exact so an
        invalid duplicate receiver or an unrelated keyword stays fail closed.
        Namespace shadowing is intentionally checked by the callers whose
        lexical scope is still live.
        """
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None
        callee = value.callee
        if not (
            isinstance(callee.object, Identifier)
            and callee.object.name == "array"
            and callee.member == "copy"
        ):
            return None
        if len(value.args) == 1 and not value.kwargs:
            return value.args[0]
        if not value.args and set(value.kwargs) == {"id"}:
            return value.kwargs["id"]
        return None

    def _is_unshadowed_direct_array_value_producer(
        self,
        value: ASTNode | None,
    ) -> bool:
        """Whether ``value`` is one direct built-in array value producer."""
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return False
        callee = value.callee
        namespace_producer = (
            isinstance(callee.object, Identifier)
            and callee.object.name == "array"
            and self._symbols.resolve("array") is None
            and callee.member in _DIRECT_ARRAY_VALUE_PRODUCERS
        )
        if namespace_producer:
            if callee.member != "copy":
                return True
            source = self._direct_namespace_array_copy_source(value)
            if source is None:
                return False
            if isinstance(source, Identifier):
                source_spec = self._type_spec_from_expr(source)
                return source_spec is not None and source_spec.kind == "array"
            return self._is_unshadowed_direct_array_value_producer(source)
        if callee.member != "copy" or value.args or value.kwargs:
            return False
        if isinstance(callee.object, Identifier):
            receiver_spec = self._type_spec_from_expr(callee.object)
            return receiver_spec is not None and receiver_spec.kind == "array"
        # A no-argument method copy preserves the value type of an existing
        # direct producer. Recurse only through that already-bounded shape;
        # arbitrary UDF/slice/map receivers remain excluded.
        return self._is_unshadowed_direct_array_value_producer(callee.object)

    def _direct_array_value_spec_without_visiting(
        self,
        value: ASTNode | None,
    ) -> TypeSpec | None:
        """Exact direct-producer spec without re-visiting a nested call."""
        cached = self._cached_direct_array_value_spec(value)
        if cached is not None:
            return cached
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None
        callee = value.callee
        source: ASTNode | None = None
        if (
            isinstance(callee.object, Identifier)
            and callee.object.name == "array"
            and self._symbols.resolve("array") is None
            and callee.member == "copy"
        ):
            candidate = self._direct_namespace_array_copy_source(value)
            if isinstance(candidate, Identifier):
                source = candidate
        elif callee.member == "copy" and isinstance(
            callee.object, Identifier
        ):
            source = callee.object
        if source is None:
            return None
        spec = self._type_spec_from_expr(source)
        return spec if spec is not None and spec.kind == "array" else None

    def _terminal_array_get_uses_direct_temporary(
        self,
        value: ASTNode | None,
    ) -> bool:
        """Capture the narrow temporary shape while lexical scope is live."""
        receiver = self._terminal_array_get_receiver(value)
        return (
            receiver is not None
            and not isinstance(receiver, Identifier)
            and self._is_unshadowed_direct_array_value_producer(receiver)
        )

    def _cached_primitive_expr_spec(
        self,
        value: ASTNode | None,
    ) -> TypeSpec | None:
        """Resolve a primitive without visiting calls or lexical symbols.

        This helper exists only for the bounded forward-order reconciliation
        of direct temporary array producers.  Calling ``_visit`` here would
        allocate stateful UDF call sites a second time.
        """
        if isinstance(value, NumberLiteral):
            return TypeSpec.primitive(
                "float" if isinstance(value.value, float) else "int"
            )
        if isinstance(value, BoolLiteral):
            return TypeSpec.primitive("bool")
        if isinstance(value, StringLiteral):
            return TypeSpec.primitive("string")
        if isinstance(value, UnaryOp) and value.op in ("+", "-"):
            return self._cached_primitive_expr_spec(value.operand)
        if isinstance(value, FuncCall) and isinstance(value.callee, Identifier):
            name = value.callee.name
            spec = getattr(self, "_func_return_type_specs", {}).get(name)
            if spec is not None and spec.kind == "primitive":
                return spec
            pine_type = getattr(self, "_func_return_types", {}).get(name)
            primitive = {
                PineType.INT: "int",
                PineType.FLOAT: "float",
                PineType.BOOL: "bool",
                PineType.STRING: "string",
                PineType.COLOR: "color",
            }.get(pine_type)
            if primitive is not None:
                return TypeSpec.primitive(primitive)
        return None

    def _cached_direct_array_value_spec(
        self,
        value: ASTNode | None,
    ) -> TypeSpec | None:
        """Resolve one registered direct producer without analyzer effects."""
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None
        callee = value.callee
        if callee.member == "copy" and not value.args and not value.kwargs:
            # Method syntax carries its source in the callee object rather
            # than in ``args``. Peel only a direct producer and stay entirely
            # on cached metadata so a stateful element call is never revisited.
            return self._cached_direct_array_value_spec(callee.object)
        if not (
            isinstance(callee.object, Identifier)
            and callee.object.name == "array"
            and callee.member in _DIRECT_ARRAY_VALUE_PRODUCERS
        ):
            return None

        producer = callee.member
        typed = {
            "new_float": "float",
            "new_int": "int",
            "new_bool": "bool",
            "new_string": "string",
        }.get(producer)
        if typed is not None:
            return TypeSpec.array(TypeSpec.primitive(typed))
        if producer == "new":
            targs = self._template_args_from_call(value)
            if targs:
                element = self._type_spec_from_hint(targs[0])
                if element is None or element.kind != "primitive":
                    return None
                return TypeSpec.array(element)
            return TypeSpec.array(TypeSpec.primitive("float"))
        if producer == "from" and value.args:
            element = self._cached_primitive_expr_spec(value.args[0])
            return TypeSpec.array(element) if element is not None else None
        if producer == "copy":
            source = self._direct_namespace_array_copy_source(value)
            if source is not None:
                return self._cached_direct_array_value_spec(source)
        return None

    def _cached_terminal_temporary_array_get_spec(
        self,
        value: ASTNode | None,
    ) -> TypeSpec | None:
        """Side-effect-free element spec for a preregistered temporary get."""
        if not isinstance(value, FuncCall) or not isinstance(
            value.callee, MemberAccess
        ):
            return None
        callee = value.callee
        if callee.member != "get":
            return None
        if isinstance(callee.object, Identifier) and callee.object.name == "array":
            if len(value.args) == 2 and not value.kwargs:
                receiver = value.args[0]
            elif len(value.args) == 1 and set(value.kwargs) == {"index"}:
                receiver = value.args[0]
            elif not value.args and set(value.kwargs) == {"id", "index"}:
                receiver = value.kwargs["id"]
            else:
                return None
        elif len(value.args) == 1 and not value.kwargs:
            receiver = callee.object
        elif not value.args and set(value.kwargs) == {"index"}:
            receiver = callee.object
        else:
            return None

        receiver_spec = self._cached_direct_array_value_spec(receiver)
        if (
            receiver_spec is None
            or receiver_spec.kind != "array"
            or receiver_spec.element is None
            or receiver_spec.element.kind != "primitive"
        ):
            return None
        return receiver_spec.element

    def _terminal_array_get_return(
        self,
        value: ASTNode | None,
        parameter_specs: dict[str, TypeSpec | None] | None = None,
        resolved_return_spec: TypeSpec | None = None,
    ) -> tuple[PineType, TypeSpec] | None:
        """Return exact metadata for a direct terminal ``array.get`` call.

        The regular expression ``TypeSpec`` pass already knows the element
        type of both ``array.get(values, index)`` and ``values.get(index)``.
        The coarse visitor, however, deliberately returns ``VOID`` for most
        array methods.  When such a call is the final expression of a UDF,
        that ``VOID`` used to make codegen emit a ``double`` return type even
        for primitive elements such as strings, booleans, and integers.

        Keep the refinement intentionally narrow: only the established
        positional/keyword shapes of ``get`` participate, and the receiver
        must resolve to an exact array ``TypeSpec``.  In addition to exact
        identifiers, direct built-in array producers (``array.from/new/copy``)
        may use the terminal expression spec that the caller already captured
        while the lexical scope was active.  Reusing that snapshot is
        load-bearing: re-inferring an effectful temporary here would revisit
        nested calls and mint phantom call-site state.  Other array accessors,
        mutations, range/view semantics, arbitrary UDF receivers,
        reference/ID-like elements, and unresolved receivers remain on their
        existing paths.  Returning UDTs or nested collections by value would
        lose Pine reference identity even when the generated C++ happens to
        compile, so this helper must not expose those specs as an apparent fix.
        """
        receiver = self._terminal_array_get_receiver(value)
        if receiver is None:
            return None
        if not isinstance(receiver, Identifier):
            # Both functional and method forms stay inside the same narrow
            # contract.  A temporary UDF, slice/view, map result, or a local
            # binding merely named ``array`` must not enter this refinement.
            if not self._is_unshadowed_direct_array_value_producer(receiver):
                return None
            if resolved_return_spec is None:
                producer_spec = self._direct_array_value_spec_without_visiting(
                    receiver
                )
                if producer_spec is not None:
                    resolved_return_spec = producer_spec.element
            if (
                resolved_return_spec is None
                or resolved_return_spec.kind != "primitive"
            ):
                return None
            return_type = self._element_pine_type(resolved_return_spec)
            if return_type == PineType.VOID:
                return None
            return return_type, resolved_return_spec

        recv_spec = None
        if parameter_specs is not None and receiver.name in parameter_specs:
            recv_spec = parameter_specs.get(receiver.name)
            # An unresolved parameter shadows any same-named global.
            if recv_spec is None:
                return None
        else:
            recv_spec = self._type_spec_from_expr(receiver)
        if (
            recv_spec is None
            or recv_spec.kind != "array"
            or recv_spec.element is None
        ):
            return None

        element_spec = recv_spec.element
        if element_spec.kind != "primitive":
            return None
        return_type = self._element_pine_type(element_spec)
        if return_type == PineType.VOID:
            return None
        return return_type, element_spec

    @staticmethod
    def _pine_type_to_spec(pine_type: PineType) -> TypeSpec:
        mapping = {
            PineType.INT: "int",
            PineType.FLOAT: "float",
            PineType.BOOL: "bool",
            PineType.STRING: "string",
            PineType.COLOR: "color",
        }
        return TypeSpec.primitive(mapping.get(pine_type, "float"))

    def _type_hint_to_pine(self, hint: str) -> PineType:
        """Convert a type hint string to PineType."""
        if "<" in hint:
            return PineType.UNKNOWN
        mapping = {
            "int": PineType.INT,
            "float": PineType.FLOAT,
            "bool": PineType.BOOL,
            "string": PineType.STRING,
            "color": PineType.COLOR,
        }
        return mapping.get(hint, PineType.UNKNOWN)

    def _extract_literal_value(self, node: ASTNode) -> Any:
        """Extract a Python literal value from an AST node."""
        if isinstance(node, NumberLiteral):
            return node.value
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, NaLiteral):
            return None
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            ename = node.object.name
            if ename in self._enum_defs:
                members = self._enum_defs[ename]
                if node.member in members:
                    return members.index(node.member)
                return None
        if isinstance(node, MemberAccess):
            # strategy.fixed, strategy.percent_of_equity, strategy.cash,
            # strategy.commission.percent, currency.USD, etc.
            obj = self._extract_literal_value(node.object)
            if obj is not None:
                return f"{obj}.{node.member}"
            return node.member
        if isinstance(node, UnaryOp) and node.op == "-":
            val = self._extract_literal_value(node.operand)
            if isinstance(val, (int, float)):
                return -val
        return None


class OperatorTypeQuery(TypeHelper):
    """Read refusal proofs without changing storage or callable return facts."""

    def __init__(self, analyzer, parameter_types):
        self._analyzer = analyzer
        self._parameter_types = parameter_types

    def __getattr__(self, name):
        return getattr(self._analyzer, name)

    def check_body(self, body):
        for node in body:
            self._check_node(node)

    def _check_node(self, node):
        if not isinstance(node, ASTNode):
            if isinstance(node, (tuple, list)):
                for child in node:
                    self._check_node(child)
            elif isinstance(node, dict):
                for child in node.values():
                    self._check_node(child)
            return
        if isinstance(node, VarDecl):
            self._check_node(node.value)
            spec = (
                TypeSpec("enum", name=node.type_hint)
                if node.type_hint in self._enum_defs else
                self._type_spec_from_hint(node.type_hint) or reference_value_type(node.type_hint)
                if node.type_hint else
                self._analyzer._operator_operand_type(node.value, self._parameter_types)
            )
            self._parameter_types[node.name] = spec
            return
        if isinstance(node, Assignment):
            self._check_node(node.value)
            if node.op != ":=":
                self._analyzer._check_operator_operands(
                    node, node.op[:-1], (node.target, node.value), self._parameter_types,
                )
            if isinstance(node.target, Identifier):
                name = node.target.name
                if self._parameter_types.get(name) is None:
                    self._parameter_types[name] = self._analyzer._operator_operand_type(
                        node.value, self._parameter_types,
                    )
            return
        if isinstance(node, TupleAssign):
            self._check_node(node.value)
            # A tuple's legacy scalar slots do not establish operand proofs.
            for name in node.names:
                self._parameter_types[name] = None
            return
        if isinstance(node, (IfStmt, SwitchStmt)):
            if isinstance(node, IfStmt):
                self._check_node(node.condition)
                bodies = [node.body, node.else_body]
            else:
                self._check_node(node.expr)
                for condition, _body in node.cases:
                    self._check_node(condition)
                    if node.expr is not None and condition is not None:
                        self._analyzer._check_operator_operands(
                            node, "==", (node.expr, condition), self._parameter_types,
                        )
                bodies = [body for _condition, body in node.cases] + [node.default_body]
            branches = []
            for body in bodies:
                local_types = dict(self._parameter_types)
                OperatorTypeQuery(self._analyzer, local_types).check_body(body)
                branches.append(local_types)
            for name in list(self._parameter_types):
                spec = branches[0].get(name)
                for branch in branches[1:]:
                    other = branch.get(name)
                    spec = join_value_types(spec, other) if spec is not None and other is not None else None
                self._parameter_types[name] = spec
            return
        # Loop bodies have their own lexical bindings. Shadow loop variables
        # before checking their operators, rather than borrowing a global.
        body = getattr(node, "body", None)
        if isinstance(body, list):
            local_types = dict(self._parameter_types)
            names = [getattr(node, "var", None), *(getattr(node, "vars", None) or ())]
            for name in names:
                if name is not None:
                    local_types[name] = None
            for field, value in vars(node).items():
                if field not in {"loc", "annotations", "body"}:
                    self._check_node(value)
            OperatorTypeQuery(self._analyzer, local_types).check_body(body)
            return
        for field, value in vars(node).items():
            if field not in {"loc", "annotations"}:
                self._check_node(value)
        if isinstance(node, BinOp):
            op, operands = node.op, (node.left, node.right)
        elif isinstance(node, UnaryOp):
            op, operands = node.op, (node.operand,)
        elif isinstance(node, Ternary):
            op, operands = "?:", (node.condition, node.true_val, node.false_val)
        else:
            return
        self._analyzer._check_operator_operands(node, op, operands, self._parameter_types)

    def _type_spec_from_expr(self, value, **kwargs):
        if isinstance(value, Identifier):
            return self._analyzer._operator_operand_type(
                value, self._parameter_types,
            )
        return super()._type_spec_from_expr(value, **kwargs)

    def _visit(self, node):
        # TypeHelper's array.from fallback normally visits its arguments.
        # Here it asks for facts only, so no TA or request is registered again.
        spec = self._analyzer._operator_operand_type(node, self._parameter_types)
        if spec is not None and spec.kind == "primitive":
            return next((ptype for ptype in PineType if ptype.value == spec.name),
                        PineType.UNKNOWN)
        return PineType.UNKNOWN

    def _array_from_element_spec(self, value):
        # Array construction must not recover a coarse symbol/call result
        # after the operator query deliberately left its source unresolved.
        return self._analyzer._operator_operand_type(value, self._parameter_types)

    @staticmethod
    def _pine_type_to_spec(pine_type):
        if pine_type in {PineType.UNKNOWN, PineType.VOID}:
            return None
        return TypeSpec.primitive(pine_type.value)
