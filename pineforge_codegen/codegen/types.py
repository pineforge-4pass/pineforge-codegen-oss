"""Type inference + collection-type lowering for the codegen.

The historic ``codegen.py`` had ~15 methods scattered across the file
that all answered one question: "given this Pine expression / hint /
declaration, what C++ type should we emit?" This mixin collects them
in one place. ``CodeGen`` mixes ``TypeInferer`` in alongside
``NamingHelper`` and the future visitor mixins.

Mixin contract — host class must provide the following attributes:

- ``self.ctx`` (``AnalyzerContext``): symbol table source.
- ``self._udt_defs`` (``dict``): UDT name -> field info.
- ``self._udt_var_types`` (``dict[str, str]``): variable name -> UDT name.
- ``self._udt_field_type_specs`` (``dict[str, dict[str, TypeSpec]]``).
- ``self._collection_types`` (``dict[str, TypeSpec]``).
- ``self._matrix_specs`` (``dict[str, TypeSpec]``).
- ``self._known_vars`` (``dict[str, Any]``): compile-time-known values.
- ``self._enum_defs`` (``dict[str, list[str]]``).
- ``self._current_func_param_types`` (``dict[str, str]``).
- ``self._func_info_map`` (``dict[str, FuncInfo]``).

And the following methods (expected to come from sibling mixins):

- ``self._resolve_callee`` (``NamingHelper``).
- ``self._codegen_error`` (``CodeGen.base``).
- ``self._get_ta_site`` / ``self._ta_name_from_site`` (TA helper, currently
  on ``CodeGen.base`` — will move into a ``TaSiteHelper`` mixin in a
  later refactor step).

The mixin avoids importing from ``base.py`` to stay free of cycles; all
tables it needs come from ``codegen/tables.py``.
"""

from __future__ import annotations

from ..ast_nodes import (
    BinOp, BoolLiteral, ExprStmt, FuncCall, FuncDef, Identifier, IfStmt,
    MemberAccess, NaLiteral, NumberLiteral, StringLiteral, SwitchStmt,
    Ternary, TupleLiteral, UnaryOp, VarDecl,
)
from ..symbols import PineType, TypeSpec
from .. import signatures as sigs
from .tables import (
    ARRAY_METHODS,
    BAR_BUILTINS,
    BAR_FIELDS,
    DRAWING_NS,
    DRAWING_TYPE_TO_CPP,
    PINE_TYPE_TO_CPP,
    TA_RETURNS_BOOL,
)


class TypeInferer:
    """Type-spec / C++-type inference helpers shared across visitor mixins.

    Mixed into ``CodeGen``; not intended to be instantiated standalone."""

    # ------------------------------------------------------------------
    # Hint-name + spec helpers
    # ------------------------------------------------------------------

    def _template_args_from_call(self, node: FuncCall) -> list[str]:
        """Pull ``<T>`` template-style hints off a ``FuncCall`` callee.

        The parser stores ``<T>`` annotations on the ``callee`` node; this
        helper normalizes them by stripping whitespace so call-sites can
        compare strings directly."""
        callee = node.callee
        ann = getattr(callee, "annotations", None) or {}
        return [str(x).replace(" ", "") for x in (ann.get("template_args") or [])]

    def _type_spec_from_hint_name(self, name: str | None) -> TypeSpec | None:
        """Parse a Pine type-hint string (e.g. ``array<float>``) into a TypeSpec."""
        if not name:
            return None
        name = name.strip().replace(" ", "")
        primitives = {"float", "int", "bool", "string", "color"}
        if name in primitives:
            return TypeSpec.primitive(name)
        if name.startswith("array<") and name.endswith(">"):
            inner = name[len("array<"):-1]
            return TypeSpec.array(self._type_spec_from_hint_name(inner) or TypeSpec.udt(inner))
        if name.startswith("matrix<") and name.endswith(">"):
            inner = name[len("matrix<"):-1]
            return TypeSpec.matrix(self._type_spec_from_hint_name(inner) or TypeSpec.udt(inner))
        if name.startswith("map<") and name.endswith(">"):
            inner = name[len("map<"):-1]
            depth = 0
            split = None
            for i, ch in enumerate(inner):
                if ch == "<":
                    depth += 1
                elif ch == ">":
                    depth -= 1
                elif ch == "," and depth == 0:
                    split = i
                    break
            if split is not None:
                key = self._type_spec_from_hint_name(inner[:split]) or TypeSpec.udt(inner[:split])
                val = self._type_spec_from_hint_name(inner[split + 1:]) or TypeSpec.udt(inner[split + 1:])
                return TypeSpec.map(key, val)
        if name in self._udt_defs:
            return TypeSpec.udt(name)
        # Drawing-objects-as-data (spec §4.1 / P3): scalar ``line``/``box``/
        # ``label``/``linefill``/``chart.point`` hints carry the handle identity
        # via a udt TypeSpec. (``array<line>`` already resolves via the array
        # fallback above.) Drawing names are NOT in _udt_defs.
        if name in DRAWING_TYPE_TO_CPP:
            return TypeSpec.udt(name)
        return None

    def _type_spec_to_cpp(self, spec: TypeSpec | None) -> str:
        """Render a TypeSpec as the equivalent C++ declaration string."""
        if spec is None:
            return "double"
        if spec.kind == "primitive":
            return {"float": "double", "int": "int", "bool": "bool",
                    "string": "std::string", "color": "int"}.get(spec.name or "float", "double")
        if spec.kind == "udt" and spec.name:
            # Drawing handle structs (P1): map BEFORE the _udt_defs check so
            # array<line> -> std::vector<Line> and scalar line -> Line instead
            # of the old collapse to double / unknown-type-name.
            if spec.name in DRAWING_TYPE_TO_CPP:
                return DRAWING_TYPE_TO_CPP[spec.name]
            return spec.name if spec.name in self._udt_defs else "double"
        if spec.kind == "array":
            return f"std::vector<{self._type_spec_to_cpp(spec.element)}>"
        if spec.kind == "map":
            return f"std::unordered_map<{self._type_spec_to_cpp(spec.key)}, {self._type_spec_to_cpp(spec.value)}>"
        if spec.kind == "matrix":
            elem = self._type_spec_to_cpp(spec.element)
            if spec.element.kind == "primitive" and spec.element.name == "float":
                return "PineMatrix"
            return f"PineGenericMatrix<{elem}>"
        return "double"

    @staticmethod
    def _default_for_type(cpp_type: str) -> str:
        """Default initialiser for a primitive C++ type (matches Pine ``na``)."""
        if cpp_type == "std::string":
            return 'std::string("")'
        if cpp_type == "bool":
            return "false"
        if cpp_type == "int":
            return "0"
        if cpp_type.startswith("std::vector") or cpp_type.startswith("std::unordered_map"):
            return f"{cpp_type}()"
        return "0.0"

    def _default_for_spec(self, spec: TypeSpec | None) -> str:
        """Default initialiser for a TypeSpec; vector/map specs get ``T()``.

        UDT specs always brace-init (``T{}``) regardless of whether the UDT was
        declared in the current translation unit — imported / forward-declared
        UDTs would otherwise fall through to ``0`` which is type-incompatible.
        """
        if spec is not None and spec.kind == "udt" and spec.name:
            # Drawing handle default (P2): brace-init the C++ struct name
            # (Line{} = na handle), NOT the lowercase Pine name (line{}).
            if spec.name in DRAWING_TYPE_TO_CPP:
                return f"{DRAWING_TYPE_TO_CPP[spec.name]}{{}}"
            return f"{spec.name}{{}}"
        cpp_type = self._type_spec_to_cpp(spec)
        if cpp_type.startswith("std::vector") or cpp_type.startswith("std::unordered_map"):
            return f"{cpp_type}()"
        return self._default_for_type(cpp_type)

    def _array_spec_for_name(self, name: str) -> TypeSpec:
        """Spec for ``array<...>`` variable ``name`` (falls back to array<float>)."""
        spec = self._collection_types.get(name)
        if spec is not None and spec.kind == "array":
            return spec
        return TypeSpec.array(TypeSpec.primitive("float"))

    def _map_spec_for_name(self, name: str) -> TypeSpec:
        """Spec for ``map<...>`` variable ``name`` (falls back to map<string, float>)."""
        spec = self._collection_types.get(name)
        if spec is not None and spec.kind == "map":
            return spec
        return TypeSpec.map(TypeSpec.primitive("string"), TypeSpec.primitive("float"))

    def _type_spec_from_expr(self, node) -> TypeSpec | None:
        """Best-effort TypeSpec inference for an expression node.

        Returns ``None`` when the node's type cannot be narrowed beyond
        the runtime default (most callers fall back to ``double``)."""
        if isinstance(node, NumberLiteral):
            return TypeSpec.primitive("float" if isinstance(node.value, float) else "int")
        if isinstance(node, BoolLiteral):
            return TypeSpec.primitive("bool")
        if isinstance(node, StringLiteral):
            return TypeSpec.primitive("string")
        if isinstance(node, Identifier):
            if node.name in self._collection_types:
                return self._collection_types[node.name]
            if node.name in self._udt_var_types:
                return TypeSpec.udt(self._udt_var_types[node.name])
            # Drawing-typed method/function parameter (L.6d / U.5): a ``line ln``
            # method receiver registers in _udt_param_udt so its body getters
            # resolve to the drawing udt and dispatch through the §4.3 path.
            _pu = getattr(self, "_udt_param_udt", None)
            if _pu and node.name in _pu and _pu[node.name] in DRAWING_TYPE_TO_CPP:
                return TypeSpec.udt(_pu[node.name])
            sym = self.ctx.symbols.resolve(node.name)
            if sym is not None and getattr(sym, "type_spec", None) is not None:
                return sym.type_spec
            return None
        if isinstance(node, MemberAccess):
            owner = self._type_spec_from_expr(node.object)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(node.member)
            return None
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            targs = self._template_args_from_call(node)
            # Drawing-objects-as-data return typing (spec §4.5 DRAWING_RETURN_SPECS):
            # *.new / *.copy -> handle of the self-type; linefill.get_line* -> line.
            if namespace in DRAWING_NS:
                if func_name in ("new", "copy"):
                    return TypeSpec.udt(namespace)
                if namespace == "linefill" and func_name in ("get_line1", "get_line2"):
                    return TypeSpec.udt("line")
            if self._is_chart_point_callee(node.callee):
                return TypeSpec.udt("chart.point")
            if namespace == "str" and func_name == "split":
                return TypeSpec.array(TypeSpec.primitive("string"))
            if namespace == "array" and func_name in (
                "new", "new_float", "new_int", "new_bool", "new_string", "from",
            ):
                if func_name == "new_int":
                    return TypeSpec.array(TypeSpec.primitive("int"))
                if func_name == "new_bool":
                    return TypeSpec.array(TypeSpec.primitive("bool"))
                if func_name == "new_string":
                    return TypeSpec.array(TypeSpec.primitive("string"))
                if func_name == "new_float":
                    return TypeSpec.array(TypeSpec.primitive("float"))
                if targs:
                    return TypeSpec.array(self._type_spec_from_hint_name(targs[0]) or TypeSpec.udt(targs[0]))
                if func_name == "from" and node.args:
                    return TypeSpec.array(self._type_spec_from_expr(node.args[0]) or TypeSpec.primitive("float"))
                return TypeSpec.array(TypeSpec.primitive("float"))
            # Functional-form array element/copy accessors: the receiver is
            # the first argument (``array.copy(arr)``), mirroring the
            # method-form handling below (``arr.copy()``).
            if (namespace == "array" and node.args
                    and func_name in ("copy", "slice", "get", "first", "last",
                                      "pop", "shift", "remove")):
                arg_spec = self._type_spec_from_expr(node.args[0])
                if arg_spec is not None and arg_spec.kind == "array":
                    if func_name in ("copy", "slice"):
                        return arg_spec
                    return arg_spec.element
            if namespace == "map" and func_name == "new":
                key = self._type_spec_from_hint_name(targs[0]) if len(targs) > 0 else TypeSpec.primitive("string")
                val = self._type_spec_from_hint_name(targs[1]) if len(targs) > 1 else TypeSpec.primitive("float")
                return TypeSpec.map(key or TypeSpec.primitive("string"), val or TypeSpec.primitive("float"))
            if namespace in self._udt_defs and func_name == "new":
                return TypeSpec.udt(namespace)
            if isinstance(node.callee, MemberAccess):
                recv_spec = self._type_spec_from_expr(node.callee.object)
                if recv_spec is not None and recv_spec.kind == "array":
                    if func_name in ("get", "first", "last", "pop", "shift", "remove"):
                        return recv_spec.element
                    if func_name in ("copy", "slice"):
                        return recv_spec
                if recv_spec is not None and recv_spec.kind == "map":
                    if func_name in ("get", "remove"):
                        return recv_spec.value
                    if func_name == "keys":
                        return TypeSpec.array(recv_spec.key or TypeSpec.primitive("string"))
                    if func_name == "values":
                        return TypeSpec.array(recv_spec.value or TypeSpec.primitive("float"))
                if recv_spec is not None and recv_spec.kind == "matrix":
                    if func_name in ("copy", "submatrix", "transpose", "concat"):
                        return recv_spec
                    if func_name in ("row", "col"):
                        return TypeSpec.array(recv_spec.element)
                    if func_name == "get":
                        return recv_spec.element
                    if func_name == "eigenvalues":
                        return TypeSpec.array(TypeSpec.primitive("float"))
                # Drawing method-form: ``a.copy()`` -> same handle type;
                # ``lf.get_line1()`` -> line. (L-N6 alias-vs-copy typing.)
                if (recv_spec is not None and recv_spec.kind == "udt"
                        and recv_spec.name in DRAWING_TYPE_TO_CPP):
                    if func_name == "copy":
                        return recv_spec
                    if recv_spec.name == "linefill" and func_name in ("get_line1", "get_line2"):
                        return TypeSpec.udt("line")
        return None

    # ------------------------------------------------------------------
    # Method lowering for collection types (used by visit_call paths)
    # ------------------------------------------------------------------

    def _array_method_expr(
        self, array_expr: str, method: str, args: list[str], spec: TypeSpec | None = None,
    ) -> str:
        """Lower ``arr.method(...)`` to its C++ form, validating numeric requirements."""
        spec = spec or TypeSpec.array(TypeSpec.primitive("float"))
        arr_cpp_type = self._type_spec_to_cpp(spec)
        elem_cpp = self._type_spec_to_cpp(spec.element) if spec.element is not None else "double"
        if method == "copy":
            return f"{arr_cpp_type}({array_expr})"
        if method == "slice":
            return f"{arr_cpp_type}({array_expr}.begin()+(int)({args[0]}),{array_expr}.begin()+(int)({args[1]}))"
        if method == "join":
            sep = args[0] if args else 'std::string(",")'
            if elem_cpp == "std::string":
                return f"[&](){{ std::string r; for(size_t i=0;i<{array_expr}.size();i++){{ if(i>0)r+={sep}; r+={array_expr}[i]; }} return r; }}()"
        numeric_only = {
            "sum", "avg", "min", "max", "range", "stdev", "variance", "median",
            "mode", "percentile_linear_interpolation", "percentile_nearest_rank",
            "percentrank", "abs", "standardize", "covariance", "binary_search",
            "binary_search_leftmost", "binary_search_rightmost", "sort_indices",
        }
        if method in numeric_only and elem_cpp not in ("double", "int"):
            self._codegen_error(
                None,
                f"array.{method} requires a numeric array",
                hint="Use numeric arrays for aggregate/statistical array functions.",
            )
        if method in ARRAY_METHODS:
            return ARRAY_METHODS[method](array_expr, args)
        # Defensive: support_checker rejects any array.* method not in
        # SUPPORTED_ARRAY (derived from ARRAY_METHODS). Reaching here means the
        # checker was bypassed or the tables drifted.
        raise ValueError(
            f"codegen: unhandled array method '{method}' — analyzer should have "
            f"rejected. Add it to ARRAY_METHODS."
        )

    def _map_method_expr(
        self, map_expr: str, method: str, args: list[str], spec: TypeSpec | None = None,
    ) -> str:
        """Lower ``map.method(...)`` to its C++ form using the receiver's spec for default-key/value typing."""
        spec = spec or TypeSpec.map(TypeSpec.primitive("string"), TypeSpec.primitive("float"))
        key_cpp = self._type_spec_to_cpp(spec.key)
        value_cpp = self._type_spec_to_cpp(spec.value)
        map_cpp = self._type_spec_to_cpp(spec)
        default_value = (
            'std::string("")' if value_cpp == "std::string"
            else ("false" if value_cpp == "bool" else self._default_for_type(value_cpp))
        )
        if method == "put":
            return f"({map_expr}[{args[0]}] = {args[1]})"
        if method == "get":
            return f"({map_expr}.count({args[0]}) ? {map_expr}[{args[0]}] : {default_value})"
        if method == "remove":
            return f"[&](){{ auto it={map_expr}.find({args[0]}); if(it!={map_expr}.end()){{ auto v=it->second; {map_expr}.erase(it); return v; }} return {default_value}; }}()"
        if method == "contains":
            return f"({map_expr}.count({args[0]}) > 0)"
        if method == "size":
            return f"(double){map_expr}.size()"
        if method == "clear":
            return f"{map_expr}.clear()"
        if method == "keys":
            return f"[&](){{ std::vector<{key_cpp}> v; for(auto& p:{map_expr}) v.push_back(p.first); return v; }}()"
        if method == "values":
            return f"[&](){{ std::vector<{value_cpp}> v; for(auto& p:{map_expr}) v.push_back(p.second); return v; }}()"
        if method == "copy":
            return f"{map_cpp}({map_expr})"
        if method == "put_all":
            return f"{map_expr}.insert({args[0]}.begin(), {args[0]}.end())"
        # Defensive: support_checker rejects any map.* method not in SUPPORTED_MAP
        # (derived from MAP_METHODS, which mirrors this if-chain). Reaching here
        # means the checker was bypassed or the tables drifted.
        raise ValueError(
            f"codegen: unhandled map method '{method}' — analyzer should have "
            f"rejected. Add it to MAP_METHODS and the if-chain above."
        )

    # ------------------------------------------------------------------
    # Whole-expression / declaration-level inference
    # ------------------------------------------------------------------

    def _type_for_decl(self, node: VarDecl) -> str:
        """Determine the C++ type for a ``VarDecl``: explicit hint, then symbol, then RHS inference."""
        if node.type_hint:
            spec = self._type_spec_from_hint_name(node.type_hint)
            if spec is not None:
                return self._type_spec_to_cpp(spec)
            if node.type_hint in self._udt_defs:
                return node.type_hint
            return PINE_TYPE_TO_CPP.get(node.type_hint, "double")
        # Drawing handle local (L-N6): a hintless local whose RHS resolves to a
        # drawing udt must declare as the handle struct, not the analyzer's
        # scalar default. Covers ``ln = arr.get(i)``, alias ``b = a``, field read
        # ``lvl.ln``, and ``c = a.copy()``. Also records _udt_var_types so later
        # uses (``ln.set_x2(...)`` / ``ln.slope()``) resolve to the drawing udt.
        if getattr(self, "_uses_drawing", False):
            rhs_spec = self._type_spec_from_expr(node.value)
            if (rhs_spec is not None and rhs_spec.kind == "udt"
                    and rhs_spec.name in DRAWING_TYPE_TO_CPP):
                self._udt_var_types.setdefault(node.name, rhs_spec.name)
                return DRAWING_TYPE_TO_CPP[rhs_spec.name]
            # Scalar drawing getter local (get_text -> std::string, etc.).
            if isinstance(node.value, FuncCall):
                _dret = self._drawing_call_return_cpp(node.value)
                if _dret is not None:
                    return _dret
        sym = self.ctx.symbols.resolve(node.name)
        if sym is not None:
            inferred = self._infer_type(node.value)
            if inferred == "std::vector<double>":
                return inferred
            cpp_type = PINE_TYPE_TO_CPP.get(sym.pine_type, "double")
            if cpp_type != "double" or sym.pine_type != PineType.UNKNOWN:
                return cpp_type
        return self._infer_type(node.value)

    def _series_type_for(self, name: str) -> str:
        """C++ element type for a series variable's history buffer."""
        from .tables import INT64_BUILTINS
        # A bare int64 bar builtin used as a history series (``time[1]``) needs an
        # int64_t buffer: epoch-ms overflow int32 and the na sentinel would be
        # misdetected. ``_is_int64_builtin_init`` only matches user vars whose
        # init RHS is such a builtin, so also match the builtin name directly.
        if name in INT64_BUILTINS or self._is_int64_builtin_init(name):
            return "int64_t"
        sym = self.ctx.symbols.resolve(name)
        if sym is not None:
            return PINE_TYPE_TO_CPP.get(sym.pine_type, "double")
        return "double"

    def _is_int64_builtin_init(self, name: str) -> bool:
        """True if ``name``'s defining expression is a top-level call to a
        Pine builtin that returns ``int64_t`` (``time``, ``time_close``,
        ``timestamp``). The Pine type system collapses these to ``int``
        but the engine encodes the ``na`` sentinel in the upper 32 bits,
        so storing into ``Series<int>`` would silently corrupt na detection.
        """
        from .tables import INT64_BUILTINS
        expr = (
            self.ctx.global_expr_map.get(name)
            or self.ctx.var_member_init_exprs.get(name)
        )
        if expr is None:
            return False
        if isinstance(expr, FuncCall):
            func_name, namespace = self._resolve_callee(expr.callee)
            if namespace is None and func_name in INT64_BUILTINS:
                return True
        return False

    def _infer_cpp_type_for_security_elem(self, node) -> str:
        """C++ type for one element of the ``request.security(..., expr, ...)`` payload.

        Special-cases the few payload shapes that resolve to vectors
        (e.g. ``ta.pivot_point_levels``, the historical pivot-point
        local arrays) before falling back to generic spec inference.

        The trailing ``_infer_type`` call lets boolean-producing
        expressions (``close > open``, ``a and b``) and arithmetic
        expressions land on the right C++ scalar type when used as the
        ``request.security_lower_tf`` payload — without it the
        per-sub-bar accumulator vector would always default to
        ``std::vector<double>`` regardless of the source expression."""
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "ta" and func_name == "pivot_point_levels":
                return "std::vector<double>"
            spec = self._type_spec_from_expr(node)
            if spec is not None:
                return self._type_spec_to_cpp(spec)
        if isinstance(node, Identifier):
            if node.name in (
                "localPivots", "securityPivotPointsArray", "pivotPointsArray",
            ):
                return "std::vector<double>"
            sym = self.ctx.symbols.resolve(node.name)
            if sym is not None and sym.pine_type != PineType.UNKNOWN:
                return PINE_TYPE_TO_CPP.get(sym.pine_type, "double")
        inferred = self._infer_type(node)
        if inferred in ("bool", "int", "double", "std::string"):
            return inferred
        if inferred.startswith("std::vector"):
            return "std::vector<double>"
        return "double"

    def _infer_type(self, node) -> str:
        """Infer the C++ type for an expression node — workhorse used everywhere.

        Falls through a layered set of checks: literals first, then
        identifiers (bar fields, known-constants, function params,
        symbol-table lookup), then function calls (built-in dispatch,
        UDT methods, TA sites, intrinsic signatures), then operators
        and ternaries / if / switch expressions. Returns the string
        ``"double"`` as the safe fallback when no narrower type can be
        determined."""
        if isinstance(node, NumberLiteral):
            return "double" if isinstance(node.value, float) else "int"
        if isinstance(node, BoolLiteral):
            return "bool"
        if isinstance(node, StringLiteral):
            return "std::string"
        if isinstance(node, NaLiteral):
            return "double"
        if isinstance(node, Identifier):
            if node.name in ("time", "time_close", "timenow"):
                return "int64_t"
            if node.name in BAR_FIELDS or node.name in BAR_BUILTINS:
                return "double"
            if node.name in self._known_vars:
                val = self._known_vars[node.name]
                if isinstance(val, bool):
                    return "bool"
                if isinstance(val, str):
                    return "std::string"
                if isinstance(val, int):
                    return "int"
                if isinstance(val, float):
                    return "double"
            if node.name in self._current_func_param_types:
                return self._current_func_param_types[node.name]
            sym = self.ctx.symbols.resolve(node.name)
            if sym is not None and getattr(sym, "type_spec", None) is not None:
                return self._type_spec_to_cpp(sym.type_spec)
            if sym is not None and sym.pine_type != PineType.UNKNOWN:
                return PINE_TYPE_TO_CPP.get(sym.pine_type, "double")
            return "double"
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            # Drawing scalar getter return type (get_text -> std::string,
            # get_x* -> int64_t, get_y*/get_price/get_top/get_bottom -> double).
            if getattr(self, "_uses_drawing", False):
                _dret = self._drawing_call_return_cpp(node)
                if _dret is not None:
                    return _dret
            if func_name in ("time", "time_close") and namespace is None and node.args:
                return "int64_t"
            if func_name == "timestamp" and namespace is None:
                return "int64_t"
            if func_name == "na":
                return "bool"
            if namespace == "input" or (namespace is None and func_name == "input"):
                if func_name in ("string", "timeframe", "session", "symbol", "text_area"):
                    return "std::string"
                if func_name == "bool":
                    return "bool"
                if func_name == "int":
                    return "int"
                # ``input.time`` returns an epoch-MS timestamp and ``input.color``
                # a packed ARGB int — both use the ``get_input_int64`` getter
                # (input.py), so their storage must be ``int64_t`` or the value
                # truncates under int32 (e.g. a date-window bound flips sign and
                # the guard is permanently false).
                if func_name in ("color", "time"):
                    return "int64_t"
                return "double"
            if namespace == "str":
                if func_name == "split":
                    return "std::vector<std::string>"
                return "std::string"
            if namespace == "ta" and func_name == "pivot_point_levels":
                return "std::vector<double>"
            # array.join returns string in both the functional and the
            # method-call forms.
            if namespace == "array" and func_name == "join":
                return "std::string"
            if isinstance(node.callee, MemberAccess):
                member_name = func_name or node.callee.member
                recv_spec = self._type_spec_from_expr(node.callee.object)
                if recv_spec is not None and recv_spec.kind == "array" and member_name == "join":
                    return "std::string"
                if recv_spec is not None and recv_spec.kind == "udt" and recv_spec.name:
                    fi_u = self._func_info_map.get(f"{recv_spec.name}.{member_name}")
                    if fi_u is not None:
                        return PINE_TYPE_TO_CPP.get(fi_u.return_type, "double")
            spec = self._type_spec_from_expr(node)
            if spec is not None:
                return self._type_spec_to_cpp(spec)
            if namespace in self._udt_defs and func_name == "new":
                return namespace
            if namespace is None and func_name in self._func_info_map:
                return PINE_TYPE_TO_CPP.get(self._func_info_map[func_name].return_type, "double")
            site = self._get_ta_site(node)
            if site is not None:
                ta_name = self._ta_name_from_site(site)
                return "bool" if ta_name in TA_RETURNS_BOOL else "double"
            if func_name and sigs.is_intrinsic_function(namespace, func_name):
                ret = sigs.get_return_type(namespace, func_name, len(node.args))
                return PINE_TYPE_TO_CPP.get(ret, "double")
        if isinstance(node, BinOp):
            if node.op in ("==", "!=", ">", "<", ">=", "<=", "and", "or"):
                return "bool"
            lt = self._infer_type(node.left)
            rt = self._infer_type(node.right)
            if lt == "std::string" or rt == "std::string":
                return "std::string"
            return "double"
        if isinstance(node, UnaryOp) and node.op == "not":
            return "bool"
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            ename = node.object.name
            if ename in self._enum_defs and node.member in self._enum_defs[ename]:
                return "int"
            # format.* constants emit std::string literals (consumed by
            # pine_str_tostring); bare reads must declare std::string.
            if ename == "format":
                return "std::string"
            # syminfo.* type inference: look up in SYMINFO_MEMBER_MAP
            # and derive C++ type from the expression (na<T>() or function call).
            if ename == "syminfo":
                from .. import signatures as _pf_sigs
                sym_key = f"syminfo.{node.member}"
                if sym_key in _pf_sigs.SYMINFO_VARIABLES:
                    return PINE_TYPE_TO_CPP.get(_pf_sigs.SYMINFO_VARIABLES[sym_key], "double")
        if isinstance(node, Ternary):
            tt = self._infer_type(node.true_val)
            ft = self._infer_type(node.false_val)
            if tt.startswith("std::vector") or ft.startswith("std::vector"):
                return tt if tt.startswith("std::vector") else ft
            if tt == "std::string" or ft == "std::string":
                return "std::string"
            return tt
        # Block-as-expression cases: read the type of the last statement of
        # the first branch / case; matches Pine semantics for ``x = if...``.
        if isinstance(node, IfStmt):
            if node.body:
                last = node.body[-1]
                if isinstance(last, ExprStmt):
                    return self._infer_type(last.expr)
            return "double"
        if isinstance(node, SwitchStmt):
            if node.cases:
                _, case_body = node.cases[0]
                if case_body:
                    last = case_body[-1]
                    if isinstance(last, ExprStmt):
                        return self._infer_type(last.expr)
            return "double"
        return "double"

    def _infer_tuple_types(self, func_node: FuncDef, count: int) -> list[str]:
        """Infer the C++ type of each element returned by a tuple-returning function.

        Builds a lightweight local-type map from the function's
        ``VarDecl``s so identifiers referenced inside the final
        ``[a, b, c]`` literal resolve precisely; falls back to
        ``_infer_type`` when no local declaration matches."""
        if not func_node.body:
            return ["double"] * count

        local_types: dict[str, str] = {}
        for stmt in func_node.body:
            if isinstance(stmt, VarDecl) and stmt.value is not None:
                local_types[stmt.name] = self._infer_type(stmt.value)

        last_stmt = func_node.body[-1]
        expr = None
        if isinstance(last_stmt, ExprStmt) and isinstance(last_stmt.expr, TupleLiteral):
            expr = last_stmt.expr
        elif isinstance(last_stmt, TupleLiteral):
            expr = last_stmt
        if expr is not None:
            result: list[str] = []
            for e in expr.elements:
                if isinstance(e, Identifier) and e.name in local_types:
                    result.append(local_types[e.name])
                else:
                    result.append(self._infer_type(e))
            return result
        return ["double"] * count
