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
    ARRAY_DRAWING_NEW_CTORS,
    ARRAY_METHODS,
    BAR_BUILTINS,
    BAR_FIELDS,
    DRAWING_NS,
    DRAWING_TYPE_TO_CPP,
    PINE_TYPE_TO_CPP,
    TA_RETURNS_BOOL,
)

# Collection (array / map / matrix) methods that MUTATE the receiver in place.
# Used by the BUG-2 collection-lvalue-alias path to decide whether a local
# bound to an existing collection lvalue must alias (mutated) or may value-copy
# (read-only). A method missing here only costs the alias optimization; a
# non-mutating method accidentally present would alias a read-only local, which
# is still correct (reads through a reference equal reads through a copy).
COLLECTION_MUTATING_METHODS = frozenset({
    # array
    "push", "unshift", "insert", "remove", "pop", "shift", "clear",
    "set", "fill", "sort", "reverse", "concat",
    # map
    "put", "put_all",
    # matrix
    "add_row", "add_col", "remove_row", "remove_col", "reshape",
    "swap_rows", "swap_columns",
})


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

    def _collection_spec_for_name(self, name: str) -> TypeSpec | None:
        """Resolve collection metadata with lexical precedence.

        Source-ordered callable locals shadow loop bindings and parameters once
        their declaration has executed; before that point, loop/parameter
        bindings shadow top-level/on_bar state. The process-wide raw-name maps
        are retained for top-level compatibility, but no callable may infer a
        collection kind from a same-named sibling callable.
        """
        local_specs = getattr(self, "_current_func_collection_specs", {})
        if name in local_specs:
            return local_specs[name]
        # A scalar/UDT local still shadows a same-named top-level collection.
        # Returning None is semantically different from "not found locally": it
        # prevents legacy global kind registries from resurrecting the hidden
        # binding during member dispatch or alias analysis.
        if name in getattr(self, "_current_func_collection_shadows", set()):
            return None
        loop_specs = getattr(self, "_current_loop_var_specs", None)
        if loop_specs and name in loop_specs:
            return loop_specs[name]
        if name in getattr(self, "_current_loop_vars", set()):
            return None
        param_specs = getattr(self, "_current_func_param_specs", {})
        if name in param_specs:
            param_spec = param_specs[name]
            # Keep the established unresolved/untyped-parameter compatibility
            # route: scalar TypeSpecs inferred only from a call site did not
            # historically mask a same-named top-level collection registry.
            # Declared scalar/UDT parameters do shadow it, while inferred or
            # declared collection parameters always carry their exact kind.
            if (param_spec.kind in {"array", "map", "matrix"}
                    or name in getattr(
                        self, "_current_func_declared_param_names", set()
                    )):
                return param_spec
        # During callable emission this is the copy-on-write lexical overlay;
        # outside a callable it is the live top-level registry, including
        # aliases discovered by codegen after construction.
        return self._collection_types.get(name)

    def _collection_name_is_lexically_shadowed(self, name: str) -> bool:
        """Whether ``name`` is bound in the active callable/block scope.

        A ``None`` collection lookup alone cannot distinguish a real scalar or
        UDT tombstone from an absent name.  Callers that otherwise fall back to
        the analyzer's popped symbol table use this predicate to avoid
        resurrecting a hidden top-level collection. Untyped parameters retain
        their established compatibility path and are intentionally not tested
        here.
        """
        if name in getattr(self, "_current_loop_vars", set()):
            return True
        if name in getattr(self, "_current_func_collection_specs", {}):
            return True
        return name in getattr(self, "_current_func_collection_shadows", set())

    def _activate_callable_collection_binding(
        self, name: str, spec: TypeSpec | None
    ) -> None:
        """Install one callable-local binding after its declaration RHS.

        Every raw kind marker is removed first. A collection installs its exact
        TypeSpec; a scalar/UDT leaves a tombstone in the lexical shadow set.
        Function and block entry already established copy-on-write state, so
        this mutation is restored at the appropriate lexical boundary.
        """
        self._array_vars.discard(name)
        self._map_vars.discard(name)
        self._matrix_specs.pop(name, None)
        self._current_func_collection_specs.pop(name, None)
        self._collection_types.pop(name, None)
        self._current_func_collection_shadows.add(name)
        if spec is None or spec.kind not in {"array", "map", "matrix"}:
            return
        self._current_func_collection_specs[name] = spec
        self._collection_types[name] = spec
        if spec.kind == "array":
            self._array_vars.add(name)
        elif spec.kind == "map":
            self._map_vars.add(name)
        else:
            self._matrix_specs[name] = spec

    def _collection_receiver_expr(self, name: str) -> str:
        """C++ receiver for an active collection identifier."""
        return getattr(self, "_pending_decl_outer_alias", {}).get(
            name, self._safe_name(name)
        )

    def _array_spec_for_name(self, name: str) -> TypeSpec:
        """Spec for ``array<...>`` variable ``name`` (falls back to array<float>)."""
        spec = self._collection_spec_for_name(name)
        if spec is not None and spec.kind == "array":
            return spec
        return TypeSpec.array(TypeSpec.primitive("float"))

    def _map_spec_for_name(self, name: str) -> TypeSpec:
        """Spec for ``map<...>`` variable ``name`` (falls back to map<string, float>)."""
        spec = self._collection_spec_for_name(name)
        if spec is not None and spec.kind == "map":
            return spec
        return TypeSpec.map(TypeSpec.primitive("string"), TypeSpec.primitive("float"))

    def _array_from_element_spec(self, node) -> TypeSpec | None:
        """Exact scalar element spec used only by ``array.from`` lowering.

        This intentionally does not make BinOp TypeSpecs globally visible:
        doing so changes unrelated float-band comparator output.  Collection
        construction needs the narrower fact so its vector type agrees with
        the analyzer-captured declaration type, including lexical loop binders.
        """
        if isinstance(node, BinOp):
            left = self._array_from_element_spec(node.left)
            right = self._array_from_element_spec(node.right)
            if node.op in ("==", "!=", ">", "<", ">=", "<=", "and", "or"):
                return TypeSpec.primitive("bool")
            if (left is not None and right is not None
                    and left.kind == "primitive" and right.kind == "primitive"):
                if left.name == "string" or right.name == "string":
                    return TypeSpec.primitive("string")
                if node.op == "/" or left.name == "float" or right.name == "float":
                    return TypeSpec.primitive("float")
                if left.name == "int" and right.name == "int":
                    return TypeSpec.primitive("int")
            return None
        return self._type_spec_from_expr(node)

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
            collection_spec = self._collection_spec_for_name(node.name)
            if collection_spec is not None:
                return collection_spec
            if node.name in self._udt_var_types:
                return TypeSpec.udt(self._udt_var_types[node.name])
            # Drawing-typed method/function parameter (L.6d / U.5): a ``line ln``
            # method receiver registers in _udt_param_udt so its body getters
            # resolve to the drawing udt and dispatch through the §4.3 path.
            _pu = getattr(self, "_udt_param_udt", None)
            if _pu and node.name in _pu and _pu[node.name] in DRAWING_TYPE_TO_CPP:
                return TypeSpec.udt(_pu[node.name])
            if self._collection_name_is_lexically_shadowed(node.name):
                return None
            sym = self.ctx.symbols.resolve(node.name)
            if sym is not None and getattr(sym, "type_spec", None) is not None:
                return sym.type_spec
            return None
        if isinstance(node, Ternary):
            true_spec = self._type_spec_from_expr(node.true_val)
            false_spec = self._type_spec_from_expr(node.false_val)
            if true_spec is not None and true_spec == false_spec:
                return true_spec
            return None
        if isinstance(node, MemberAccess):
            owner = self._type_spec_from_expr(node.object)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(node.member)
            return None
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace is None:
                func_info = self._func_info_map.get(func_name)
                return_spec = getattr(func_info, "return_type_spec", None)
                if return_spec is not None:
                    return return_spec
            # ticker.* constructors (inherit/standard/heikinashi) return a symbol
            # string; without this the member-type inference defaults to double
            # and a ``haTicker = ticker.heikinashi(...)`` global mis-declares as
            # double then assigns a std::string. (Analyzer agrees: ticker.* -> STRING.)
            if namespace == "ticker":
                return TypeSpec.primitive("string")
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
            ) or (namespace == "array" and func_name in ARRAY_DRAWING_NEW_CTORS):
                if func_name == "new_int":
                    return TypeSpec.array(TypeSpec.primitive("int"))
                if func_name == "new_bool":
                    return TypeSpec.array(TypeSpec.primitive("bool"))
                if func_name == "new_string":
                    return TypeSpec.array(TypeSpec.primitive("string"))
                if func_name == "new_float":
                    return TypeSpec.array(TypeSpec.primitive("float"))
                if func_name in ARRAY_DRAWING_NEW_CTORS:
                    # array.new_line()/new_box()/new_label()/new_linefill() ->
                    # std::vector<Line/Box/Label/Linefill> (typed alias of new<T>).
                    return TypeSpec.array(TypeSpec.udt(ARRAY_DRAWING_NEW_CTORS[func_name]))
                if targs:
                    return TypeSpec.array(self._type_spec_from_hint_name(targs[0]) or TypeSpec.udt(targs[0]))
                if func_name == "from" and node.args:
                    return TypeSpec.array(
                        self._array_from_element_spec(node.args[0])
                        or TypeSpec.primitive("float")
                    )
                return TypeSpec.array(TypeSpec.primitive("float"))
            # Functional-form array element/copy accessors: the receiver is
            # the first argument (``array.copy(arr)``), mirroring the
            # method-form handling below (``arr.copy()``).
            if (namespace == "array"
                    and func_name in ("copy", "slice", "get", "first", "last",
                                      "pop", "shift", "remove")):
                receiver_node = node.args[0] if node.args else node.kwargs.get("id")
                arg_spec = self._type_spec_from_expr(receiver_node)
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

    def _array_receiver_once_expr(
        self, array_expr: str, args: list[str], lower_receiver,
    ) -> str:
        """Lower an array method without duplicating its receiver evaluation.

        ``ARRAY_METHODS`` is intentionally a compact table of expression
        templates.  Many templates need the receiver more than once (for
        example ``begin()`` + ``end()``).  Substituting a temporary-producing
        receiver directly into those slots creates distinct objects, so the
        resulting iterator range is invalid.  Render first with a fresh token;
        when the template uses it repeatedly and the real receiver is not a
        plain identifier lvalue, bind it to one lambda-local forwarding
        reference and render every use through that binding.  The lambda's
        deduced ``auto`` return copies scalar results before a temporary
        receiver dies; mutations still reach lvalue arrays through the
        forwarding reference.  Single-use and plain-identifier lowerings
        remain byte-for-byte unchanged.

        This is receiver-only by design: Pine argument evaluation and the
        separate empty-array semantics are outside this fix.
        """
        counter = getattr(self, "_array_receiver_counter", 0)
        occupied = "\n".join((array_expr, *args))
        while True:
            receiver = f"__pf_array_receiver_{counter}"
            counter += 1
            if receiver not in occupied:
                break
        self._array_receiver_counter = counter

        lowered = lower_receiver(receiver)
        if lowered.count(receiver) <= 1 or array_expr.isidentifier():
            return lower_receiver(array_expr)
        return (
            f"[&]() {{ auto&& {receiver} = ({array_expr}); "
            f"return {lowered}; }}()"
        )

    def _array_method_expr(
        self, array_expr: str, method: str, args: list[str], spec: TypeSpec | None = None,
    ) -> str:
        """Lower ``arr.method(...)`` to its C++ form, validating numeric requirements."""
        spec = spec or TypeSpec.array(TypeSpec.primitive("float"))
        arr_cpp_type = self._type_spec_to_cpp(spec)
        elem_cpp = self._type_spec_to_cpp(spec.element) if spec.element is not None else "double"
        if method == "copy":
            lower_receiver = lambda recv: f"{arr_cpp_type}({recv})"
        elif method == "slice":
            lower_receiver = lambda recv: f"{arr_cpp_type}({recv}.begin()+(int)({args[0]}),{recv}.begin()+(int)({args[1]}))"
        elif method == "join" and elem_cpp == "std::string":
            sep = args[0] if args else 'std::string(",")'
            lower_receiver = lambda recv: f"[&](){{ std::string r; for(size_t i=0;i<{recv}.size();i++){{ if(i>0)r+={sep}; r+={recv}[i]; }} return r; }}()"
        else:
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
            if method not in ARRAY_METHODS:
                # Defensive: support_checker rejects any array.* method not in
                # SUPPORTED_ARRAY (derived from ARRAY_METHODS). Reaching here means the
                # checker was bypassed or the tables drifted.
                raise ValueError(
                    f"codegen: unhandled array method '{method}' — analyzer should have "
                    f"rejected. Add it to ARRAY_METHODS."
                )
            # Pine evaluates call arguments before entering the array
            # calculation, even when an empty receiver makes the result ``na``.
            # The empty guards in these methods must therefore consume a
            # one-evaluation binding rather than leaving the original argument
            # expression after the guard.  Build the binding into
            # ``lower_receiver`` so a temporary receiver is still evaluated
            # first by ``_array_receiver_once_expr``.
            eager_scalar_arg_methods = {
                "stdev": (0,),
                "variance": (0,),
                "percentile_linear_interpolation": (0,),
                "percentile_nearest_rank": (0,),
            }
            bound_args = list(args)
            arg_bindings: list[tuple[str, str]] = []
            occupied = "\n".join((array_expr, *args))
            counter = getattr(self, "_array_arg_counter", 0)
            for arg_index in eager_scalar_arg_methods.get(method, ()):
                if arg_index >= len(args):
                    continue
                while True:
                    token = f"__pf_array_arg_{counter}"
                    counter += 1
                    if token not in occupied:
                        break
                bound_args[arg_index] = token
                arg_bindings.append((token, args[arg_index]))
            self._array_arg_counter = counter

            def lower_receiver(recv: str) -> str:
                lowered = ARRAY_METHODS[method](recv, bound_args)
                for token, original in reversed(arg_bindings):
                    lowered = (
                        f"[&](){{ auto {token}=({original}); "
                        f"return {lowered}; }}()"
                    )
                return lowered

        return self._array_receiver_once_expr(array_expr, args, lower_receiver)

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
        # Analyzer scopes are popped before codegen, so ctx.symbols.resolve may
        # find a same-named global instead of this active callable's plain
        # local.  The local binding is known from the emitted body inventory;
        # infer it from its own RHS rather than borrowing the global's PineType.
        if node.name in getattr(self, "_current_func_locals", set()):
            return self._infer_type(node.value)
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

    def _expr_is_int64_builtin(self, expr) -> bool:
        """True if ``expr`` is a top-level int64-returning Pine builtin: either a
        call to one of ``INT64_BUILTINS`` (``time(...)``, ``timestamp(...)``, …)
        or a bare ``Identifier`` spelled like ``time`` / ``time_close`` /
        ``timenow`` (which Pine exposes as a value, not a call)."""
        from .tables import INT64_BUILTINS, INT64_BUILTIN_IDENTIFIERS
        if expr is None:
            return False
        if isinstance(expr, FuncCall):
            func_name, namespace = self._resolve_callee(expr.callee)
            return namespace is None and func_name in INT64_BUILTINS
        if isinstance(expr, Identifier):
            return expr.name in INT64_BUILTIN_IDENTIFIERS
        return False

    def _int64_reassign_targets(self) -> set[str]:
        """Names of vars that are reassigned (``:=``/``=``) anywhere in the AST
        with an RHS that is a top-level int64-returning builtin. Cached on the
        instance. Pine ``int`` collapses these to 32-bit, but the runtime stores
        the epoch in 64 bits, so the member must be promoted to ``int64_t``."""
        cached = getattr(self, "_int64_reassign_cache", None)
        if cached is not None:
            return cached
        from ..ast_nodes import Assignment
        targets: set[str] = set()
        ast = getattr(self.ctx, "ast", None)
        if ast is not None:
            for node in self._walk_ast(ast):
                if (isinstance(node, Assignment)
                        and isinstance(node.target, Identifier)
                        and self._expr_is_int64_builtin(node.value)):
                    targets.add(node.target.name)
        self._int64_reassign_cache = targets
        return targets

    def _is_int64_builtin_init(self, name: str) -> bool:
        """True if ``name``'s initializer OR any ``:=``/``=`` reassignment has an
        RHS that is a top-level int64-returning builtin (``time``, ``time_close``,
        ``timenow``, ``timestamp``, ``time_tradingday``). The Pine type system
        collapses these to ``int`` but the engine encodes the ``na`` sentinel
        (and the full epoch-ms value, which overflows int32) in 64 bits, so
        storing into ``int`` silently corrupts both the value and na detection.
        A reassignment like ``var int entryTime = na`` then ``entryTime := time``
        must promote even though the *initializer* alone is ``na``.
        """
        expr = (
            self.ctx.global_expr_map.get(name)
            or self.ctx.var_member_init_exprs.get(name)
        )
        if self._expr_is_int64_builtin(expr):
            return True
        return name in self._int64_reassign_targets()

    def _na_reassign_cpp_type(self, name: str) -> str | None:
        """Declared scalar C++ type of a ``:=`` reassignment target ``name``, so a
        bare-``na`` RHS (``x := na``) can be spelled ``na<T>()`` matching the
        member/local type instead of the default ``na<double>()``.

        Assigning a double quiet-NaN into an ``int``/``int64_t``/``bool`` member is
        undefined behaviour (NaN->int is unspecified; on ARM64 it saturates to 0,
        which is not the ``na<T>()`` sentinel) and defeats ``is_na<T>()``. Mirrors
        the member-declaration type logic (``base._emit_class_members`` /
        ``_typed_na_init``): ``PINE_TYPE_TO_CPP`` plus the int->int64_t epoch
        promotion. Returns ``None`` for collections / UDT / drawing handles and
        for ``double`` (already the default lowering), so those paths are
        unchanged.
        """
        # Collections / UDT / drawing handles never take a scalar ``na<T>()``:
        # leave them to the drawing-na / default lowering in _visit_rhs_value.
        collection_spec = self._collection_spec_for_name(name)
        if ((collection_spec is not None
                and collection_spec.kind in {"array", "map", "matrix"})
                or name in self._udt_var_types):
            return None
        cpp_type: str | None = None
        # 1. ``var`` member (class-scope OR function-local: both are recorded in
        #    ctx.var_members). This is the authoritative declaration source.
        for vname, ptype, _init in self.ctx.var_members:
            if vname == name:
                cpp_type = PINE_TYPE_TO_CPP.get(ptype, "double")
                break
        # 2. Function-local plain (non-``var``) scalar: its declared type was
        #    remembered at the VarDecl (``_type_for_decl``).
        if cpp_type is None:
            cpp_type = getattr(self, "_current_func_local_types", {}).get(name)
        # 3. Function parameter.
        if cpp_type is None:
            cpp_type = getattr(self, "_current_func_param_types", {}).get(name)
        # 4. Global-scope non-``var`` class member.
        if cpp_type is None:
            for gname, gptype in self.ctx.global_var_decls:
                if gname == name:
                    cpp_type = PINE_TYPE_TO_CPP.get(gptype, "double")
                    break
        if cpp_type is None:
            return None
        # int -> int64_t promotion for epoch-ms builtins, mirroring the member
        # declaration so the na sentinel width matches the storage width.
        if cpp_type == "int" and self._is_int64_builtin_init(name):
            cpp_type = "int64_t"
        # Only the retypeable scalar types are meaningful; ``double`` already
        # lowers to ``na<double>()`` and everything else is left untouched.
        if cpp_type in ("int", "int64_t", "bool", "std::string"):
            return cpp_type
        return None

    # ------------------------------------------------------------------
    # BUG C: user-defined-UDT lvalue aliasing
    # ------------------------------------------------------------------

    def _is_stable_lvalue_expr(self, expr) -> bool:
        """Whether ``expr`` denotes storage that can safely back a C++ ref.

        Checked array access deliberately returns by reference for lvalue
        receivers and by value for temporary receivers.  The UDT alias pass
        must make the same distinction or it can emit ``T&`` bound to the
        checked helper's safe rvalue copy.
        """
        if isinstance(expr, Identifier):
            return True
        if isinstance(expr, MemberAccess):
            return self._is_stable_lvalue_expr(expr.object)
        if isinstance(expr, FuncCall):
            # An element selected from a stable array is itself stable.  This
            # must recurse independently of the element type so nested
            # ``array<array<UDT>>`` access keeps the inner array lvalue and the
            # eventual UDT element can still alias it.  Constructors, user
            # function returns, matrix.row(), and other temporary producers do
            # not enter this checked array-access shape.
            func_name, namespace = self._resolve_callee(expr.callee)
            receiver = None
            if namespace == "array" and func_name in ("get", "first", "last"):
                receiver = expr.args[0] if expr.args else expr.kwargs.get("id")
            elif (isinstance(expr.callee, MemberAccess)
                  and func_name in ("get", "first", "last")):
                candidate = expr.callee.object
                candidate_spec = self._type_spec_from_expr(candidate)
                if candidate_spec is not None and candidate_spec.kind == "array":
                    receiver = candidate
            return (
                receiver is not None
                and self._is_stable_lvalue_expr(receiver)
            )
        if isinstance(expr, Ternary):
            return (
                self._is_stable_lvalue_expr(expr.true_val)
                and self._is_stable_lvalue_expr(expr.false_val)
            )
        return False

    def _is_udt_lvalue(self, expr) -> str | None:
        """If ``expr`` is a *user-defined* UDT lvalue (a bare ``Identifier`` that
        names a class-scope ``var``/global UDT member, e.g. ``wyckoffSwingLow``,
        or an element selected from ``array<UDT>``), return its UDT type name;
        else ``None``.

        Pine UDTs are reference types, so a local initialised from such an lvalue
        and then mutated through must write back to the global. Drawing UDTs are
        handled by the separate ``_uses_drawing`` path and are excluded here."""
        if isinstance(expr, FuncCall):
            callee = expr.callee
            func_name, namespace = self._resolve_callee(callee)
            receiver = None
            if namespace == "array" and func_name in ("get", "first", "last"):
                receiver = expr.args[0] if expr.args else expr.kwargs.get("id")
            elif (isinstance(callee, MemberAccess)
                  and func_name in ("get", "first", "last")):
                receiver = callee.object
            if receiver is not None:
                if not self._is_stable_lvalue_expr(receiver):
                    return None
                spec = self._type_spec_from_expr(receiver)
                elem = spec.element if spec is not None and spec.kind == "array" else None
                if (elem is not None and elem.kind == "udt" and elem.name in self._udt_defs
                        and elem.name not in DRAWING_TYPE_TO_CPP):
                    return elem.name
            return None
        if not isinstance(expr, Identifier):
            return None
        udt_t = self._udt_var_types.get(expr.name)
        if udt_t is None or udt_t not in self._udt_defs:
            return None
        if udt_t in DRAWING_TYPE_TO_CPP:
            return None
        # Must be a known global/class-scope member (not a function param or a
        # plain local snapshot) for write-through to be observable.
        if expr.name in getattr(self, "_current_func_locals", set()):
            # A function-local of UDT type that is itself a persistent ``var``
            # member still write-through aliases; but a plain inline local does
            # not represent shared state. Only treat ``var`` func-locals (in
            # func_var_members) as aliasable shared state.
            fname = getattr(self, "_active_func_name", None)
            var_locals = {n for n, _, _ in self.ctx.func_var_members.get(fname, [])} if fname else set()
            if expr.name not in var_locals:
                return None
        return udt_t

    def _udt_lvalue_selection_type(self, expr) -> str | None:
        """UDT type if ``expr`` is a UDT lvalue OR a ternary/switch whose every
        selectable branch is a UDT lvalue of the SAME user-defined UDT type.
        Returns ``None`` otherwise (so plain ``UDT a = b`` value-snapshots, calls,
        ``.new(...)`` ctors, and mixed/non-lvalue selections never alias)."""
        direct = self._is_udt_lvalue(expr)
        if direct is not None:
            return direct
        branches: list = []
        if isinstance(expr, Ternary):
            branches = [expr.true_val, expr.false_val]
        elif isinstance(expr, SwitchStmt):
            for _case_expr, stmts in (expr.cases or []):
                if not stmts:
                    return None
                last = stmts[-1]
                branches.append(last.expr if isinstance(last, ExprStmt) else last)
            if expr.default_body:
                last = expr.default_body[-1]
                branches.append(last.expr if isinstance(last, ExprStmt) else last)
        else:
            return None
        if not branches:
            return None
        types = {self._is_udt_lvalue(b) for b in branches}
        if len(types) == 1 and None not in types:
            return next(iter(types))
        return None

    def _udt_local_alias_kind(self, node: VarDecl) -> tuple[str, str] | None:
        """Decide whether a hintless/typed local UDT declaration must ALIAS the
        global(s) it selects rather than value-copy (BUG C).

        Returns ``("ref", udt_type)`` for a non-rebinding reference alias,
        ``("ptr", udt_type)`` for a pointer alias (the local is later reassigned
        to a *different* UDT lvalue, which a C++ reference cannot do), or
        ``None`` to keep the existing value-copy semantics.

        Conditions (all required):
          * RHS is a UDT lvalue or a ternary/switch selecting same-typed UDT
            lvalues (``_udt_lvalue_selection_type``).
          * The local is MUTATED later in the enclosing function body
            (``local.field := ...``) — a pure read-only snapshot needn't alias.

        The mutation requirement is the safety guard: a local that is only read
        keeps value semantics, and a local initialised from a non-lvalue (a
        ``.new()`` ctor, a function return, or a plain local copy) returns
        ``None`` here, preserving intentional independent-copy semantics."""
        from ..ast_nodes import Assignment
        body = getattr(self, "_current_func_body", None)
        if body is None:
            return None
        udt_t = self._udt_lvalue_selection_type(node.value)
        if udt_t is None:
            return None
        name = node.name
        mutated = False
        rebinds_to_other_lvalue = False
        for stmt in self._walk_ast_list(body):
            if not isinstance(stmt, Assignment):
                continue
            tgt = stmt.target
            # Mutation through the local: ``p.field := ...``
            if (isinstance(tgt, MemberAccess)
                    and isinstance(tgt.object, Identifier)
                    and tgt.object.name == name):
                mutated = True
            # Rebind of the local itself to another UDT lvalue: ``p := other``
            elif isinstance(tgt, Identifier) and tgt.name == name:
                if self._udt_lvalue_selection_type(stmt.value) is not None:
                    rebinds_to_other_lvalue = True
                else:
                    # Reassigned to a non-lvalue (e.g. ``.new()`` / a copy):
                    # aliasing would be wrong; bail to value-copy.
                    return None
        if not mutated:
            return None
        return ("ptr" if rebinds_to_other_lvalue else "ref"), udt_t

    # ------------------------------------------------------------------
    # BUG 2: collection (array / map / matrix) lvalue aliasing
    # ------------------------------------------------------------------

    def _collection_lvalue_spec(self, expr):
        """If ``expr`` is a bare ``Identifier`` naming an array/map/matrix
        var/global member, return its ``TypeSpec``; else ``None``. Pine
        collections are reference types, so a local bound to such an lvalue and
        then mutated through must ALIAS it, not value-copy."""
        if not isinstance(expr, Identifier):
            return None
        name = expr.name
        spec = self._collection_spec_for_name(name)
        if spec is not None and spec.kind in ("array", "map", "matrix"):
            return spec
        return None

    def _collection_lvalue_selection_spec(self, expr):
        """``TypeSpec`` if ``expr`` is a collection lvalue OR a ternary/switch
        whose every selectable branch is a collection lvalue of the SAME C++
        type; ``None`` otherwise (so ``array.new(...)`` ctors, copies, function
        returns, and mixed selections keep value-copy semantics). Mirrors
        ``_udt_lvalue_selection_type`` for the BUG-2 collection-alias path."""
        direct = self._collection_lvalue_spec(expr)
        if direct is not None:
            return direct
        branches: list = []
        if isinstance(expr, Ternary):
            branches = [expr.true_val, expr.false_val]
        elif isinstance(expr, SwitchStmt):
            for _case_expr, stmts in (expr.cases or []):
                if not stmts:
                    return None
                last = stmts[-1]
                branches.append(last.expr if isinstance(last, ExprStmt) else last)
            if expr.default_body:
                last = expr.default_body[-1]
                branches.append(last.expr if isinstance(last, ExprStmt) else last)
        else:
            return None
        if not branches:
            return None
        specs = [self._collection_lvalue_spec(b) for b in branches]
        if any(s is None for s in specs):
            return None
        cpp_types = {self._type_spec_to_cpp(s) for s in specs}
        if len(cpp_types) == 1:
            return specs[0]
        return None

    def _collection_local_must_alias(self, node) -> bool:
        """True when the local ``node`` declares an alias of an existing
        collection lvalue that is later MUTATED in the enclosing function body
        (``local.push/unshift/insert/remove/set/clear/pop/...``). A purely-read
        local needn't alias; a local REASSIGNED to a different value can't be a
        C++ reference, so it bails to value-copy (returns ``False``)."""
        from ..ast_nodes import Assignment
        body = getattr(self, "_current_func_body", None)
        if body is None:
            return False
        name = node.name
        mutated = False
        for stmt in self._walk_ast_list(body):
            # Rebind of the local itself (``orderBlocks := other``) — a C++
            # reference cannot rebind, so keep value-copy semantics.
            if (isinstance(stmt, Assignment)
                    and isinstance(stmt.target, Identifier)
                    and stmt.target.name == name):
                return False
            if (isinstance(stmt, FuncCall)
                    and isinstance(stmt.callee, MemberAccess)
                    and isinstance(stmt.callee.object, Identifier)
                    and stmt.callee.object.name == name
                    and stmt.callee.member in COLLECTION_MUTATING_METHODS):
                mutated = True
        return mutated

    def _walk_ast_list(self, stmts):
        """Yield every node within a list of statements (depth-first)."""
        for s in stmts:
            yield from self._walk_ast(s)

    def _addr_of_udt_selection(self, expr, local_name: str):
        """Render the address-of form of a UDT lvalue selection for a pointer
        alias (BUG C rebind case): ``other`` -> ``&(other)``;
        ``cond ? a : b`` -> ``(cond ? &(a) : &(b))``. The selectable branches are
        guaranteed (by ``_udt_lvalue_selection_type``) to be UDT lvalues."""
        if isinstance(expr, Identifier):
            return f"&({self._safe_name(expr.name)})"
        if isinstance(expr, Ternary):
            cond = self._visit_expr(expr.condition)
            t = self._addr_of_udt_selection(expr.true_val, local_name)
            f = self._addr_of_udt_selection(expr.false_val, local_name)
            return f"({cond} ? {t} : {f})"
        # Switch selection: lower to nested ternaries over case equality. Rare in
        # practice; fall back to address-of the whole lowered expression.
        return f"&({self._visit_expr(expr)})"

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
            if node.name in getattr(self, "_current_func_local_types", {}):
                return self._current_func_local_types[node.name]
            if node.name in getattr(self, "_current_loop_vars", set()):
                return "double"
            sym = self.ctx.symbols.resolve(node.name)
            if sym is not None and getattr(sym, "type_spec", None) is not None:
                return self._type_spec_to_cpp(sym.type_spec)
            if sym is not None and sym.pine_type != PineType.UNKNOWN:
                return PINE_TYPE_TO_CPP.get(sym.pine_type, "double")
            return "double"
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            # Nested trade-accessor calls bypass the flat namespace signature
            # table.  Their textual metadata accessors return std::string from
            # the runtime, so hintless locals must not use the double fallback.
            if (isinstance(node.callee, MemberAccess)
                    and isinstance(node.callee.object, MemberAccess)
                    and isinstance(node.callee.object.object, Identifier)
                    and node.callee.object.object.name == "strategy"
                    and node.callee.object.member in ("closedtrades", "opentrades")
                    and func_name in (
                        "entry_id", "exit_id", "entry_comment", "exit_comment",
                    )):
                return "std::string"
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
                if func_name in ("contains", "startswith", "endswith"):
                    return "bool"
                if func_name == "tonumber":
                    return "double"
                if func_name in ("length", "pos"):
                    return "int"
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
            if ename == "timeframe":
                if node.member in ("period", "main_period"):
                    return "std::string"
                if node.member == "multiplier":
                    return "int"
                return "bool"
            # syminfo.* type inference: look up in SYMINFO_MEMBER_MAP
            # and derive C++ type from the expression (na<T>() or function call).
            if ename == "syminfo":
                from .. import signatures as _pf_sigs
                sym_key = f"syminfo.{node.member}"
                if sym_key in _pf_sigs.SYMINFO_VARIABLES:
                    return PINE_TYPE_TO_CPP.get(_pf_sigs.SYMINFO_VARIABLES[sym_key], "double")
            spec = self._type_spec_from_expr(node)
            if spec is not None:
                return self._type_spec_to_cpp(spec)
        if isinstance(node, Ternary):
            tt = self._infer_type(node.true_val)
            ft = self._infer_type(node.false_val)
            if tt.startswith("std::vector") or ft.startswith("std::vector"):
                return tt if tt.startswith("std::vector") else ft
            if tt == "std::string" or ft == "std::string":
                return "std::string"
            if tt == "double" or ft == "double":
                return "double"
            if tt == "int64_t" or ft == "int64_t":
                return "int64_t"
            if tt == "bool" and ft == "bool":
                return "bool"
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

        Builds a lightweight local-type map from the function's ``VarDecl``s
        (including ones nested inside if/for/switch blocks) so identifiers
        referenced inside the final ``[a, b, c]`` literal resolve precisely.
        An explicit type hint wins (``string tag = na`` -> ``std::string``,
        not the ``double`` implied by ``na``); otherwise the initializer
        expression is inferred. Falls back to ``_infer_type`` when no local
        declaration matches."""
        if not func_node.body:
            return ["double"] * count

        local_types: dict[str, str] = {}
        for stmt in self._walk_ast(func_node):
            if isinstance(stmt, VarDecl) and stmt.value is not None and stmt.name:
                captured = self._callable_collection_bindings.get(id(stmt))
                if (captured is not None
                        and captured.kind in {"array", "map", "matrix"}):
                    local_types[stmt.name] = self._type_spec_to_cpp(captured)
                    continue
                if stmt.type_hint:
                    spec = self._type_spec_from_hint_name(stmt.type_hint)
                    if spec is not None:
                        local_types[stmt.name] = self._type_spec_to_cpp(spec)
                        continue
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
