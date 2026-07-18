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

from ..ast_nodes import (
    ASTNode, BinOp, BoolLiteral, ExprStmt, FuncCall, Identifier, IfStmt,
    MemberAccess, NaLiteral, NumberLiteral, StringLiteral, Subscript, Ternary,
    TupleLiteral, UnaryOp,
)
from ..symbols import PineType, TypeSpec

# Drawing-objects-as-data type names (spec §4.1). Defined locally — the
# analyzer must not import from ``codegen`` (codegen imports analyzer, so the
# reverse would be a cycle). Mirrors codegen.tables.DRAWING_TYPE_TO_CPP keys.
_DRAWING_TYPE_NAMES = frozenset({"line", "box", "label", "linefill", "chart.point"})
_DRAWING_NS = frozenset({"line", "box", "label", "linefill"})


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

    def _type_spec_from_hint(self, hint: str | None) -> TypeSpec | None:
        if not hint:
            return None
        hint = hint.strip().replace(" ", "")
        primitive = {"int", "float", "bool", "string", "color"}
        if hint in primitive:
            return TypeSpec.primitive(hint)
        if hint.startswith("array<") and hint.endswith(">"):
            inner = hint[len("array<"):-1]
            elem = self._type_spec_from_hint(inner) or TypeSpec.udt(inner)
            return TypeSpec.array(elem)
        if hint.startswith("matrix<") and hint.endswith(">"):
            inner = hint[len("matrix<"):-1]
            return TypeSpec.matrix(self._type_spec_from_hint(inner) or TypeSpec.udt(inner))
        if hint.startswith("map<") and hint.endswith(">"):
            inner = hint[len("map<"):-1]
            parts = self._split_top_level_type_args(inner)
            if len(parts) == 2:
                key = self._type_spec_from_hint(parts[0]) or TypeSpec.udt(parts[0])
                val = self._type_spec_from_hint(parts[1]) or TypeSpec.udt(parts[1])
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

    def _type_spec_from_expr(self, value: ASTNode | None) -> TypeSpec | None:
        if value is None:
            return None
        if isinstance(value, Ternary):
            true_spec = self._type_spec_from_expr(value.true_val)
            false_spec = self._type_spec_from_expr(value.false_val)
            if (true_spec is not None
                    and true_spec.kind == "map"
                    and true_spec == false_spec):
                return true_spec
            # A Pine ``na`` arm acquires the other arm's map type.  Keep this
            # narrow to maps so unrelated scalar/array inference and generated
            # output retain their established behavior.
            if (true_spec is not None
                    and true_spec.kind == "map"
                    and isinstance(value.false_val, NaLiteral)):
                return true_spec
            if (false_spec is not None
                    and false_spec.kind == "map"
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
            # handle, not the legacy numeric fallback.  Keep this refinement
            # drawing-only; collection subscripts have separate array/map
            # semantics and primitive history inference already flows through
            # PineType in ``_visit_Subscript``.
            receiver_spec = self._type_spec_from_expr(value.object)
            if (receiver_spec is not None
                    and receiver_spec.kind == "udt"
                    and receiver_spec.name in _DRAWING_TYPE_NAMES):
                return receiver_spec
            return None
        if isinstance(value, IfStmt):
            def terminal_expr(body):
                if not body:
                    return None
                terminal = body[-1]
                return terminal.expr if isinstance(terminal, ExprStmt) else terminal

            true_node = terminal_expr(value.body)
            false_node = terminal_expr(value.else_body)
            if true_node is None or false_node is None:
                return None
            true_spec = self._type_spec_from_expr(true_node)
            false_spec = self._type_spec_from_expr(false_node)
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
                    and true_spec.kind == "map"
                    and true_spec == false_spec):
                return true_spec
            if (true_spec is not None
                    and true_spec.kind == "map"
                    and isinstance(false_node, NaLiteral)):
                return true_spec
            if (false_spec is not None
                    and false_spec.kind == "map"
                    and isinstance(true_node, NaLiteral)):
                return false_spec
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
        if isinstance(value, FuncCall):
            cal = value.callee
            func = cal.member if isinstance(cal, MemberAccess) else None
            ns = cal.object.name if isinstance(cal, MemberAccess) and isinstance(cal.object, Identifier) else None
            targs = self._template_args_from_call(value)
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
                    elem = self._type_spec_from_hint(targs[0]) or TypeSpec.udt(targs[0])
                    return TypeSpec.array(elem)
                if func == "from" and value.args:
                    first_spec = self._array_from_element_spec(value.args[0])
                    if first_spec is not None:
                        return TypeSpec.array(first_spec)
                    first = self._visit(value.args[0])
                    return TypeSpec.array(self._pine_type_to_spec(first))
                return TypeSpec.array(TypeSpec.primitive("float"))
            # Functional-form array element/copy accessors: the receiver is
            # the first argument (``array.copy(arr)``), mirroring the
            # method-form handling below (``arr.copy()``).
            if (ns == "array" and value.args
                    and func in ("copy", "slice", "get", "first", "last",
                                 "pop", "shift", "remove")):
                arg_spec = self._type_spec_from_expr(value.args[0])
                if arg_spec is not None and arg_spec.kind == "array":
                    if func in ("copy", "slice"):
                        return arg_spec
                    return arg_spec.element
            if ns == "matrix" and func == "new":
                if targs:
                    elem = self._type_spec_from_hint(targs[0]) or TypeSpec.udt(targs[0])
                else:
                    elem = TypeSpec.primitive("float")
                return TypeSpec.matrix(elem)
            if ns == "map" and func == "new":
                key = self._type_spec_from_hint(targs[0]) if len(targs) > 0 else TypeSpec.primitive("string")
                val = self._type_spec_from_hint(targs[1]) if len(targs) > 1 else TypeSpec.primitive("float")
                return TypeSpec.map(key or TypeSpec.primitive("string"), val or TypeSpec.primitive("float"))
            if ns == "map" and func in {
                "put", "get", "remove", "contains", "size", "keys",
                "values", "copy", "put_all", "clear",
            } and value.args:
                recv_spec = self._type_spec_from_expr(value.args[0])
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
            if ns in self._udt_fields and func == "new":
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
                    if func in ("copy", "submatrix", "transpose", "concat"):
                        return recv_spec
                    if func in ("row", "col"):
                        return TypeSpec.array(recv_spec.element)
                    if func == "get":
                        return recv_spec.element
                    if func == "eigenvalues":
                        return TypeSpec.array(TypeSpec.primitive("float"))
                if (recv_spec is not None
                        and recv_spec.kind == "udt"
                        and recv_spec.name):
                    method_key = f"{recv_spec.name}.{func}"
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
                # Drawing method-form: a.copy() -> same handle; lf.get_line*() -> line.
                if (recv_spec is not None and recv_spec.kind == "udt"
                        and recv_spec.name in _DRAWING_TYPE_NAMES):
                    if func == "copy":
                        return recv_spec
                    if recv_spec.name == "linefill" and func in ("get_line1", "get_line2"):
                        return TypeSpec.udt("line")
        if isinstance(value, Identifier):
            sym = self._symbols.resolve(value.name)
            if sym is not None and sym.type_spec is not None:
                return sym.type_spec
        if isinstance(value, FuncCall):
            # User-function return spec (e.g. an array-returning
            # ``buildPDLevels() => array.from(...)``), so a caller's
            # ``allLevels = buildPDLevels()`` infers an array TypeSpec.
            cal = value.callee
            fname = cal.member if isinstance(cal, MemberAccess) else (
                cal.name if isinstance(cal, Identifier) else None)
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
        receiver = None
        if isinstance(callee.object, Identifier) and callee.object.name == "map":
            # Functional form: map.method(id, ...). Keyword-only receiver
            # routing remains a separate residual, so require the established
            # positional receiver here.
            functional_arity = {
                "clear": 1,
                "keys": 1,
                "values": 1,
                "copy": 1,
                "put_all": 2,
                "get": 2,
                "remove": 2,
                "put": 3,
            }.get(method)
            if (
                functional_arity is not None
                and len(value.args) == functional_arity
                and not value.kwargs
            ):
                receiver = value.args[0]
        elif isinstance(callee.object, Identifier):
            # Global/local/typed-parameter method forms only. Do not infer an
            # arbitrary expression receiver that codegen cannot route.
            is_parameter = (
                parameter_specs is not None
                and callee.object.name in parameter_specs
            )
            valid_method_shape = False
            expected_arity = {
                "clear": 0,
                "keys": 0,
                "values": 0,
                "copy": 0,
                "put_all": 1,
                "get": 1,
                "remove": 1,
                "put": 2,
            }.get(method)
            if expected_arity is not None:
                valid_method_shape = (
                    len(value.args) == expected_arity and not value.kwargs
                )
            if is_parameter and not valid_method_shape:
                if method == "put_all":
                    valid_method_shape = (
                        not value.args and set(value.kwargs) == {"id2"}
                    )
                elif method in ("get", "remove"):
                    valid_method_shape = (
                        not value.args and set(value.kwargs) == {"key"}
                    )
                elif method == "put":
                    valid_method_shape = (
                        (
                            len(value.args) == 1
                            and set(value.kwargs) == {"value"}
                        )
                        or (
                            not value.args
                            and set(value.kwargs) == {"key", "value"}
                        )
                    )
            if valid_method_shape:
                receiver = callee.object
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
