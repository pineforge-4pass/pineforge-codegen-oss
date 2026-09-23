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
- ``self._cpp_string_escape`` (``NamingHelper``).
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode,
    BinOp,
    BoolLiteral,
    FuncCall,
    Identifier,
    MemberAccess,
    NumberLiteral,
    StringLiteral,
    VarDecl,
)
from .. import signatures as sigs
from ..pine_spelling import expr_start, global_input_calls, input_binding_names


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

    def _input_default_value(self, node: FuncCall) -> tuple[bool, object]:
        """``(True, value)`` when the defval is the constant a ``v = input...``
        binding records as ``v``'s known value -- a number, bool or string
        literal, or a declared enum member (its index) -- else ``(False, None)``.
        Only such an input is a stable scalar the TA reset can re-read."""
        default = self._get_input_default(node)
        if isinstance(default, (NumberLiteral, BoolLiteral, StringLiteral)):
            return True, default.value
        if isinstance(default, MemberAccess) and isinstance(default.object, Identifier):
            members = self._enum_defs.get(default.object.name)
            if members is not None and default.member in members:
                return True, members.index(default.member)
        return False, None

    def _input_title_node(self, node: FuncCall):
        """The title argument of an ``input(...)`` call -- positional arg #2 or
        the ``title=`` kwarg, via the signatures registry -- or None."""
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
                return merged[1]
        return None

    def _input_binding_names(self) -> dict[int, str]:
        """``id(call) -> name`` for the global-scope input calls a declaration
        names (``pine_spelling.input_binding_names``)."""
        names = getattr(self, "_input_binding_names_cache", None)
        if names is None:
            names = input_binding_names(self.ctx.ast.body)
            self._input_binding_names_cache = names
        return names

    def _get_input_title(self, node: FuncCall, var_name: str | None = None) -> str:
        """The key an ``input(...)`` call is read and listed by: its title's
        string value, else the name of the declaration binding it, else
        ``var_name``, else "".

        The binding name comes from the call node itself, so every getter of
        one input -- the member, the TA reset, a request.security timeframe,
        an alias's read (``b = a``) -- keys it like the manifest does, whatever
        name the caller passes. A title that is not a compile-time string
        constant is refused (``_input_title_value``)."""
        title_node = self._input_title_node(node)
        if title_node is not None:
            title = self._input_title_value(title_node)
            if title is None:
                self._refuse_input_title(title_node)
            return title
        bound = self._input_binding_names().get(id(node))
        if bound is not None:
            return bound
        return var_name if var_name else ""

    def _input_title_value(self, node, _seen: frozenset = frozenset()) -> str | None:
        """A title expression's compile-time string value, or None. TradingView
        takes ``title (const string)``: a string literal, a ``+`` of constant
        strings, or a name bound once at global scope, never reassigned and not
        ``var``, to one (``ctx.global_expr_map``)."""
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BinOp) and node.op == "+":
            left = self._input_title_value(node.left, _seen)
            right = self._input_title_value(node.right, _seen)
            return None if left is None or right is None else left + right
        if (isinstance(node, Identifier) and node.name not in _seen
                and not self._known_var_is_lexically_shadowed(node.name)):
            value = (getattr(self.ctx, "global_expr_map", None) or {}).get(node.name)
            if value is not None:
                return self._input_title_value(value, _seen | {node.name})
        return None

    def _refuse_input_title(self, title_node) -> None:
        spelled = f"'{title_node.name}' " if isinstance(title_node, Identifier) else ""
        self._codegen_error(
            expr_start(title_node),
            f"input title {spelled}is not a constant string: TradingView declares "
            "title (const string), and PineForge keys an input override by its title.",
            hint='Use a string literal, or a name bound once at global scope to one '
                 '(T = "Length").',
        )

    def _check_input_titles(self) -> None:
        """Refuse the first input call anywhere in the script whose title is not
        a compile-time string constant, before any getter keys it: its C++ text
        used to become the key (``std::string("Len")``)."""
        stack: list = [self.ctx.ast]
        while stack:
            node = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend(reversed(node))
                continue
            if isinstance(node, dict):
                stack.extend(reversed(list(node.values())))
                continue
            if not isinstance(node, ASTNode):
                continue
            if isinstance(node, FuncCall) and self._is_input_call(node):
                title_node = self._input_title_node(node)
                if title_node is not None and self._input_title_value(title_node) is None:
                    self._refuse_input_title(title_node)
            stack.extend(reversed([v for k, v in vars(node).items()
                                   if k not in ("loc", "annotations")]))

    def _check_input_keys(self) -> None:
        """Warn about inputs of the script that share an override key.

        An override reaches every input keyed by its title, else by the name of
        the declaration holding the call (``_get_input_title``, the manifest's
        ``title``), so inputs that share one cannot be overridden apart: two
        untitled calls in one declaration (``n = input.int(9) +
        input.int(3)``), two outside any declaration (keyed ""), an untitled
        call and another input titled with its name, or one title repeated
        across input groups (TradingView tells them apart; PineForge's
        override protocol does not). The script still transpiles unchanged --
        at its defaults every input reads its own default -- and one warning
        per shared key names every input that key reaches.
        """
        calls_by_key: dict[str, list[FuncCall]] = {}
        for node, name in self._global_input_calls_with_names():
            calls_by_key.setdefault(self._get_input_title(node, var_name=name), []).append(node)
        for key, calls in calls_by_key.items():
            if len(calls) < 2:
                continue
            spots = [f"{loc.line}:{loc.col}" for loc in (expr_start(c).loc for c in calls)]
            listed = f"{', '.join(spots[:-1])} and {spots[-1]}"
            self._codegen_warning(
                expr_start(calls[1]),
                f"input key '{key}' is shared by the inputs at {listed}: PineForge "
                "sets an input override by its title, else by the name of the "
                "declaration holding it, so one override sets all of them.",
                hint="Give each input its own title to override them apart.",
            )

    def _input_spelling_title(self, node: FuncCall) -> str | None:
        """The key an untitled call's re-spelling must carry as ``title=``:
        its binding name. A spelled call is re-parsed into a new node, which
        no declaration names."""
        if self._input_title_node(node) is not None:
            return None
        return self._input_binding_names().get(id(node))

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
            return f'get_input_source({self._input_key_literal(title)}, {base})[0]'
        default = self._get_input_default(node)
        default_cpp = self._visit_expr(default) if default is not None else "0"
        getter = self._input_type_to_getter(func_name, namespace)
        # The generic ``input(...)`` overload is typed by its defval in Pine
        # v6 (an int default yields an int input). The static getter table
        # cannot see the default, so infer the getter from the default's
        # literal type here. This matters for TA lengths: ``input(15)`` must
        # route to ``get_input_int`` so the RMA/EMA ctor receives an int.
        if func_name == "input" and namespace is None:
            if isinstance(default, BoolLiteral):
                getter = "get_input_bool"
            elif isinstance(default, NumberLiteral):
                if isinstance(default.value, bool):
                    getter = "get_input_bool"
                elif isinstance(default.value, int):
                    getter = "get_input_int"
                else:
                    getter = "get_input_double"
            elif isinstance(default, StringLiteral):
                getter = "get_input_string"
        default_cpp = self._coerce_string_input_default(getter, default_cpp)
        return f'{getter}({self._input_key_literal(title)}, {default_cpp})'

    def _input_key_literal(self, title: str) -> str:
        """``title`` as the C++ string literal the input getters key it by.

        A title is arbitrary Pine string text; pasted verbatim, a quote in it
        ends the literal early and a backslash escapes the next character, so
        the TU no longer compiles."""
        return f'"{self._cpp_string_escape(title)}"'

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
        """Walk the script's global-scope ``input.*()`` calls into an InputDef
        list, one entry per call site in source order: a top-level
        ``var = input.*(...)`` declaration and an inline call inside an
        expression (``ta.ema(close, input.int(9, "Fast"))``) alike.

        Each entry: ``{title, type, default[, min, max, step, options]}``. The
        optional keys are emitted only when the corresponding signature
        argument is a const literal; a bound/option referencing a non-literal
        is omitted (never crashes). ``title`` is the key the emitted C++ reads
        the input by: the title argument, else the name of the declaration
        holding the call (``pine_spelling.input_binding_names``), else "".
        """
        return [self._input_manifest_entry(node, name)
                for node, name in self._global_input_calls_with_names()]

    def _global_input_calls_with_names(self) -> list[tuple[FuncCall, str | None]]:
        """Every global-scope input call in source order, with the name of the
        declaration holding it (None outside a declaration)."""
        out = []
        for stmt in self.ctx.ast.body:
            for node in global_input_calls(stmt):
                out.append((node, stmt.name if isinstance(stmt, VarDecl) else None))
        return out

    def _input_manifest_entry(self, node: FuncCall, var_name: str | None) -> dict:
        func_name, namespace = self._resolve_callee(node.callee)
        names, merged = self._merged_args(node, func_name, namespace)
        title = self._get_input_title(node, var_name=var_name)
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
        return entry
