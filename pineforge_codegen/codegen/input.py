"""Pine ``input.*`` call helpers for the codegen.

Mixin holding the family of ``_is_input_*`` / ``_get_input_*`` /
``_input_type_to_getter`` / ``_enforce_enum_declared_before_input_enum``
methods. They share a small piece of state (``self._enum_defs``) and a
common dependency on the analyzer's signature registry.

Mixin contract — host class must provide:

- ``self._enum_defs`` (``dict[str, list[str]]``).
- ``self._resolve_callee`` (``NamingHelper``).
- ``self._visit_expr`` (visitor mixin, currently on ``base.py``;
  used to render the title-arg fallback expression).
"""

from __future__ import annotations

from ..ast_nodes import (
    BoolLiteral,
    FuncCall,
    Identifier,
    MemberAccess,
    NumberLiteral,
    StringLiteral,
    VarDecl,
)
from .. import signatures as sigs


class InputHelper:
    """``input.*`` call analysis helpers — defaults, titles, getter dispatch, enum guard."""

    # Maps the input function short-name -> the form-facing type tag emitted in
    # the input manifest (consumed by the host UI's override form). ``price``
    # is a float slider, ``time`` an int timestamp; everything string-like
    # collapses to "string".
    _FORM_TYPE = {
        "int": "int", "float": "float", "bool": "bool", "string": "string",
        "source": "source", "enum": "enum", "price": "float", "time": "int",
        "color": "string", "timeframe": "string", "session": "string",
        "symbol": "string", "text_area": "string",
    }

    def _is_input_call(self, node: FuncCall) -> bool:
        """True if ``node`` is an ``input(...)`` or ``input.<type>(...)`` call."""
        func_name, namespace = self._resolve_callee(node.callee)
        return self._is_input_call_by_name(func_name, namespace)

    @staticmethod
    def _is_input_call_by_name(func_name: str | None, namespace: str | None) -> bool:
        """Stateless variant: classify a ``(func_name, namespace)`` pair as input call.

        Used during prescan when only the resolved callee tuple is
        available (no FuncCall node)."""
        if func_name == "input" and namespace is None:
            return True
        if namespace == "input":
            return True
        return False

    def _get_input_default(self, node: FuncCall):
        """Pull the default-value AST node out of an ``input(...)`` / ``input.<t>(...)`` call.

        Honors keyword arguments by merging them in declaration order via
        the signatures registry; falls back to the first positional
        argument or the ``defval=`` kwarg when the registry has no entry."""
        func_name, namespace = self._resolve_callee(node.callee)
        param_names = None
        if namespace == "input" and func_name in sigs.INPUT_FUNCTIONS:
            param_names = sigs.get_param_names("input", func_name)
        elif func_name == "input" and namespace is None:
            param_names = sigs.get_param_names(None, "input")
        if param_names:
            merged = list(node.args)
            for i, pname in enumerate(param_names):
                if pname in node.kwargs:
                    while len(merged) <= i:
                        merged.append(None)
                    if i >= len(merged) or merged[i] is None:
                        merged[i] = node.kwargs[pname]
            while merged and merged[-1] is None:
                merged.pop()
            if merged and merged[0] is not None:
                return merged[0]
            return None
        if node.args:
            return node.args[0]
        if "defval" in node.kwargs:
            return node.kwargs["defval"]
        return None

    def _get_input_title(self, node: FuncCall, var_name: str | None = None) -> str:
        """Pull the title string from an ``input(...)`` call.

        Reads positional arg #2 or the ``title=`` kwarg via the
        signatures registry. When the title isn't a ``StringLiteral``
        (e.g. computed at runtime), the rendered C++ expression is
        returned. Falls back to ``var_name`` when no title is provided
        so generated UI labels still have a stable identity."""
        func_name, namespace = self._resolve_callee(node.callee)
        param_names = None
        if namespace == "input" and func_name in sigs.INPUT_FUNCTIONS:
            param_names = sigs.get_param_names("input", func_name)
        elif func_name == "input" and namespace is None:
            param_names = sigs.get_param_names(None, "input")
        if param_names:
            merged = list(node.args)
            for i, pname in enumerate(param_names):
                if pname in node.kwargs:
                    while len(merged) <= i:
                        merged.append(None)
                    if i >= len(merged) or merged[i] is None:
                        merged[i] = node.kwargs[pname]
            if len(merged) > 1 and merged[1] is not None:
                title_node = merged[1]
                if isinstance(title_node, StringLiteral):
                    return title_node.value
                return self._visit_expr(title_node)
        return var_name if var_name else ""

    # Native price series accepted as an input.source defval. The analyzer
    # hard-rejects anything else (see support_checker), so a defval reaching
    # codegen is one of these or a user-series identifier.
    _NATIVE_SOURCE_SERIES = frozenset(
        {"open", "high", "low", "close", "volume",
         "hl2", "hlc3", "ohlc4", "hlcc4"}
    )

    @staticmethod
    def _input_type_to_getter(func_name: str | None, namespace: str | None) -> str:
        """Map an ``input.<type>`` call to its C++ runtime getter name.

        ``input.float`` / ``input.price`` collapse to ``get_input_double``;
        ``input.int`` / ``input.enum`` map to ``get_input_int`` (C++ int32);
        ``input.color`` maps to ``get_input_int64`` (a packed ARGB color
        ``0xAARRGGBB`` overflows signed int32); ``input.time`` maps to
        ``get_input_int64`` because Pine v6 ``input.time`` returns a series
        int Unix timestamp in MILLISECONDS, which overflows int32 for any
        modern date. ``input.source`` is rendered separately by
        ``_render_input_value`` (it returns a Series<double>&, not a scalar)
        and never routes through this table. Bare ``input(...)`` and any
        unrecognised variant default to double."""
        if func_name in ("int",):
            return "get_input_int"
        if func_name in ("float", "source", "price"):
            return "get_input_double"
        if func_name in ("bool",):
            return "get_input_bool"
        if func_name in ("string", "timeframe", "session", "symbol", "text_area"):
            return "get_input_string"
        if func_name == "color":
            return "get_input_int64"
        if func_name == "time":
            return "get_input_int64"
        if func_name == "enum":
            return "get_input_int"
        return "get_input_double"

    def _is_source_input(self, node: FuncCall) -> bool:
        """True if an ``input.*`` call yields a *live per-bar source series*.

        Covers both ``input.source(<native series>)`` and a bare
        ``input(<native series>)`` — in Pine v6 ``input(close)`` is the
        source-input overload and behaves like ``input.source(close)``,
        returning a series that tracks ``close`` every bar (not a constant
        frozen at the first bar). The defval is restricted to the native
        OHLCV series the engine can resolve at runtime (the same set
        ``input.source`` is restricted to); a bare ``input(14)`` /
        ``input(\"x\")`` / ``input(true)`` stays a frozen scalar."""
        func_name, namespace = self._resolve_callee(node.callee)
        if namespace == "input" and func_name == "source":
            return True
        if func_name == "input" and namespace is None:
            default = self._get_input_default(node)
            if (isinstance(default, Identifier)
                    and default.name in self._NATIVE_SOURCE_SERIES):
                return True
        return False

    def _source_defval_to_base_series(self, default) -> str:
        """Map an input.source defval (close/high/hl2/…) to its engine base
        source series member (``_src_close_`` …). Falls back to
        ``_src_close_`` for a non-native defval — unreachable once the
        analyzer guard is in place, but keeps codegen total."""
        name = default.name if isinstance(default, Identifier) else None
        if name in self._NATIVE_SOURCE_SERIES:
            return f"_src_{name}_"
        return "_src_close_"

    def _render_input_value(self, node: FuncCall, func_name: str | None,
                            namespace: str | None, title: str) -> str:
        """Render the C++ value expression for an ``input.*`` call site.

        ``input.source`` resolves to ``get_input_source("title", _src_<field>_)[0]``
        — the engine returns the (optionally operator-overridden) native
        series and we read its current value; a subscripted source var is
        already tracked as a series var by the analyzer so ``src[1]`` lowers
        to a Series subscript. Every other input type routes through the
        scalar getter table. A bare ``input(<native series>)`` is the Pine
        source-input overload and is rendered identically to
        ``input.source``."""
        if self._is_source_input(node):
            default = self._get_input_default(node)
            base = self._source_defval_to_base_series(default)
            return f'get_input_source("{title}", {base})[0]'
        default = self._get_input_default(node)
        default_cpp = self._visit_expr(default) if default is not None else "0"
        getter = self._input_type_to_getter(func_name, namespace)
        default_cpp = self._coerce_string_input_default(getter, default_cpp)
        return f'{getter}("{title}", {default_cpp})'

    def _coerce_string_input_default(self, getter: str, default_cpp: str) -> str:
        """String getters take a ``std::string`` default. An unresolved default
        (e.g. ``input.string(size.tiny, ...)`` where ``size.tiny`` lowers to
        ``0``) would pass a null char* and crash ``strlen`` inside the getter,
        so coerce any non-string default to a safe empty string. (Label size is
        a visual no-op in the backtest, so the exact default does not affect
        trading logic.)"""
        if getter == "get_input_string":
            stripped = default_cpp.strip()
            if not stripped.startswith("std::string(") and not stripped.startswith('"'):
                return 'std::string("")'
        return default_cpp

    def _enforce_enum_declared_before_input_enum(self, node: FuncCall) -> None:
        """Mirror the analyzer: ``input.enum(Enum.member)`` requires Enum to be defined above the call.

        Codegen runs after the analyzer so the constraint should already
        be enforced; we duplicate the check defensively because
        violations would otherwise lead to a silent ``KeyError`` during
        member resolution."""
        dv = self._get_input_default(node)
        if not isinstance(dv, MemberAccess) or not isinstance(dv.object, Identifier):
            return
        ename = dv.object.name
        if ename not in self._enum_defs:
            raise ValueError(
                f"enum '{ename}' must be declared above input.enum() "
                "(internal: Analyzer should reject this first)"
            )
        if dv.member not in self._enum_defs[ename]:
            raise ValueError(
                f"{ename}.{dv.member} is not a member of enum {ename} "
                "(internal: Analyzer should reject this first)"
            )

    # ------------------------------------------------------------------
    # Input manifest extraction (host UI override-form source of truth)
    # ------------------------------------------------------------------

    def _literal_or_none(self, node):
        """Return a JSON scalar for a *const* literal AST node, else None.

        ``None`` signals non-const (an identifier, computed expression, …) so
        callers can omit a bound/option that references a runtime value.
        Enum member refs (``Dir.Up``) collapse to the ``"Dir.Up"`` string tag.
        """
        if isinstance(node, StringLiteral):
            return node.value
        # BoolLiteral must be checked before NumberLiteral: a Pine ``true`` is a
        # BoolLiteral (not a NumberLiteral), but guarding the order keeps intent
        # explicit and future-proof against bool/int node overlap.
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, NumberLiteral):
            return node.value
        # enum member ref like ``Dir.Up`` -> "Dir.Up" (string tag)
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            return f"{node.object.name}.{node.member}"
        return None

    def _merged_args(self, node: FuncCall, func_name, namespace):
        """Merge positional args + kwargs into signature-positional order.

        Returns ``(param_names | None, merged_list)``. Mirrors the merge logic
        in :meth:`_get_input_title` / :meth:`_get_input_default` so manifest
        extraction reads bounds/options off the same positions codegen does.
        """
        if namespace == "input" and func_name in sigs.INPUT_FUNCTIONS:
            names = sigs.get_param_names("input", func_name)
        elif func_name == "input" and namespace is None:
            names = sigs.get_param_names(None, "input")
        else:
            names = None
        merged = list(node.args)
        if names:
            for i, pname in enumerate(names):
                if pname in node.kwargs:
                    while len(merged) <= i:
                        merged.append(None)
                    if merged[i] is None:
                        merged[i] = node.kwargs[pname]
        return names, merged

    def extract_input_manifest(self) -> list[dict]:
        """Walk top-level ``var = input.*(...)`` decls into an InputDef list.

        Each entry: ``{title, type, default[, min, max, step, options]}``. The
        optional keys are emitted only when the corresponding signature
        argument is a const literal; a bound/option referencing a non-literal
        is omitted (never crashes). One pass over ``self.ctx.ast.body``.
        """
        out: list[dict] = []
        for stmt in self.ctx.ast.body:
            if not (
                isinstance(stmt, VarDecl)
                and isinstance(stmt.value, FuncCall)
                and self._is_input_call(stmt.value)
            ):
                continue
            node = stmt.value
            func_name, namespace = self._resolve_callee(node.callee)
            names, merged = self._merged_args(node, func_name, namespace)
            title = self._get_input_title(node, var_name=stmt.name)
            default_node = self._get_input_default(node)
            default_val = (
                self._literal_or_none(default_node)
                if default_node is not None
                else None
            )
            if namespace == "input":
                form_type = self._FORM_TYPE.get(func_name, "string")
            else:
                # Plain ``input(...)``: Pine types the result by its defval.
                # The codegen already emits the matching scalar getter, so the
                # manifest must mirror that — infer from the resolved default's
                # Python type. ``bool`` MUST be tested before ``int`` because
                # ``isinstance(True, int)`` is True. A None/non-literal default
                # falls back to "string".
                if isinstance(default_val, bool):
                    form_type = "bool"
                elif isinstance(default_val, int):
                    form_type = "int"
                elif isinstance(default_val, float):
                    form_type = "float"
                elif isinstance(default_val, str):
                    form_type = "string"
                else:
                    form_type = "string"
            entry: dict = {
                "title": title,
                "type": form_type,
                "default": default_val,
            }
            # Pull min/max/step/options by signature param name; emit only
            # const literals so the override form never references a runtime
            # value it can't reproduce.
            if names:
                idx = {n: i for i, n in enumerate(names)}
                for key, pname in (("min", "minval"), ("max", "maxval"), ("step", "step")):
                    i = idx.get(pname)
                    if i is not None and i < len(merged) and merged[i] is not None:
                        v = self._literal_or_none(merged[i])
                        # bool is an int subclass — exclude it from numeric bounds
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            entry[key] = v
                oi = idx.get("options")
                if oi is not None and oi < len(merged) and merged[oi] is not None:
                    opts_node = merged[oi]
                    elems = getattr(opts_node, "elements", None)
                    if elems is not None:
                        vals = [self._literal_or_none(e) for e in elems]
                        # any non-const element -> omit the whole options list
                        if vals and all(isinstance(v, str) for v in vals):
                            entry["options"] = vals
            out.append(entry)
        return out
