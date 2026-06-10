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
    ASTNode, BoolLiteral, FuncCall, Identifier, MemberAccess,
    NaLiteral, NumberLiteral, StringLiteral, UnaryOp,
)
from ..symbols import PineType, TypeSpec


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
        return None

    def _template_args_from_call(self, node: FuncCall) -> list[str]:
        callee = node.callee
        ann = getattr(callee, "annotations", None) or {}
        raw = ann.get("template_args") or []
        return [str(x).replace(" ", "") for x in raw]

    def _type_spec_from_expr(self, value: ASTNode | None) -> TypeSpec | None:
        if value is None:
            return None
        if isinstance(value, FuncCall):
            cal = value.callee
            func = cal.member if isinstance(cal, MemberAccess) else None
            ns = cal.object.name if isinstance(cal, MemberAccess) and isinstance(cal.object, Identifier) else None
            targs = self._template_args_from_call(value)
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
                    if func in ("get", "remove"):
                        return recv_spec.value
                    if func == "keys":
                        return TypeSpec.array(recv_spec.key or TypeSpec.primitive("string"))
                    if func == "values":
                        return TypeSpec.array(recv_spec.value or TypeSpec.primitive("float"))
                if recv_spec is not None and recv_spec.kind == "matrix":
                    if func in ("copy", "submatrix", "transpose", "concat"):
                        return recv_spec
                    if func in ("row", "col"):
                        return TypeSpec.array(recv_spec.element)
                    if func == "get":
                        return recv_spec.element
                    if func == "eigenvalues":
                        return TypeSpec.array(TypeSpec.primitive("float"))
        if isinstance(value, Identifier):
            sym = self._symbols.resolve(value.name)
            if sym is not None and sym.type_spec is not None:
                return sym.type_spec
        if isinstance(value, MemberAccess):
            owner = self._type_spec_from_expr(value.object)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(value.member)
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
