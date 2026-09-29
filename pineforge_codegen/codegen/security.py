"""``request.security()`` lowering for the codegen.

This is the most stateful mixin in the ``codegen/`` package. It owns the
~30 helpers that lower Pine ``request.security(...)`` calls into
per-security ``_eval_security_N()`` methods, an ``evaluate_security()``
dispatch, the ``clear_security()`` reset path, and the supporting binding,
TA-variant, and mutable-global rebind machinery.

Mixin contract — the host class (``CodeGen``) must provide the following
attributes (all set by ``CodeGen.__init__`` unless noted):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. Reads
  ``ctx.ast.body``, ``ctx.ta_call_sites``, ``ctx.global_expr_map``,
  ``ctx.func_series_vars``, and ``ctx.global_mutable_infos``.
- ``self._global_mutable_infos`` (``dict[str, MutableInfo]``):
  per-mutable-global metadata captured by the analyzer
  (``is_var``/``is_series``/``pine_type``/``source_stmts``).
- ``self._security_calls`` (``list[dict]``): normalized security-call
  records. Built by this mixin's ``_normalize_security_call``.
- ``self._security_eval_info`` (``list[dict]``): per ``sec_id`` eval
  metadata (``ta_indices``, ``ta_variants``, ``ta_binding_stacks``,
  ``inline_helper_ta_indices``, ``mutable_globals``, ...).
- ``self._security_inline_counter`` (``int``): used by
  ``_security_next_inline_name`` for unique helper temporary names.
- ``self._security_ta_variant_names``
  (``dict[tuple[int, int, tuple], str]``):
  ``(sec_id, ta_idx, signature) -> C++ member name``.
- ``self._security_ohlc_hist_fields_by_sec`` (``dict[int, set[str]]``):
  set in ``CodeGen.generate()`` before ``_emit_security_evaluators`` runs.
- ``self._security_source_hist_fields`` (``dict[tuple[str, str], tuple]``):
  a source input's ``(key, default)`` -> ``(its call, the history field
  its payload reads)`` (``_security_bar_history_field``).
- ``self._ta_index_by_site_id`` (``dict[int, int]``): TA call-site
  identity → index in ``ctx.ta_call_sites``.
- ``self._func_names`` (``set[str]``): user-defined function names.
- ``self._func_info_map`` (``dict[str, FuncInfo]``): name -> FuncInfo.
- ``self._security_payload_depth`` (``int``): raised around the
  ``_visit_expr`` fallback of ``_build_security_expr``; the expression
  visitor keeps the session.* time-of-day predicates while it is nonzero.

Sibling-mixin methods consumed via ``self``:

- ``self._safe_name`` / ``self._get_target_name`` (``NamingHelper``).
- ``self._series_type_for`` / ``self._type_for_decl`` /
  ``self._infer_cpp_type_for_security_elem`` (``TypeInferer``).
- ``self._get_ta_site`` / ``self._security_ta_compute_args_for_site`` /
  ``self._ta_name_from_site`` (``TaSiteHelper``).
  ``_security_ta_compute_args_for_site`` stays on ``TaSiteHelper`` because
  it is structurally a TA helper that calls back into this mixin via
  ``self._build_security_expr``.
- ``self._merge_ta_call_args`` (``CodeGen.base``): not security-specific,
  kept on base.
- ``self._visit_expr`` (``CodeGen.base``): the fallback expression
  renderer used by ``_build_security_expr``.
- ``self._codegen_error`` (``CodeGen.base``).
- ``self._emit_ta_runtime_reset`` (``CodeGen.base``): called from
  ``_emit_security_evaluators`` to gate the TA reset before the dispatch
  switch.

The mixin avoids importing from ``base.py`` to stay free of cycles; all
tables and types come from ``codegen/tables.py``, ``..ast_nodes``,
``..analyzer``, and ``..symbols``.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, BreakStmt, ColorLiteral, ContinueStmt, ExprStmt,
    ForStmt, ForInStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess,
    NaLiteral, NumberLiteral, StringLiteral, Subscript, SwitchStmt, Ternary,
    TupleAssign, TupleLiteral, UnaryOp, VarDecl, WhileStmt,
)
from ..ast_nodes import MethodDef
from ..analyzer import (
    FuncInfo, TACallSite, TA_MULTI_CTOR, TA_NO_CTOR, TA_PERIOD_ARG,
)
from .. import signatures as sigs
from ..errors import CompileError, Phase
from ..external_requests import (
    FOOTPRINT_COLUMN_ANNOTATION, RECORDED_KEY_ANNOTATION, REQUEST_REF_ANNOTATION,
)
from ..external_requests import _nodes as walk_request_nodes
from ..limits import iter_ast_nodes
from .helpers import na_preserving_int_cast, unary_sign_cpp
from ..security_contexts import GLOBAL_ANNOTATION, UNREACHED_ANNOTATION
from ..symbols import PineType, method_receiver_type_name
from .helpers import na_preserving_int_cast
from .tables import (
    BAR_BUILTINS, MATH_FUNC_MAP, PINE_TYPE_TO_CPP, SECURITY_BAR_FIELDS,
    SECURITY_BAR_FIELD_EXPRS, SECURITY_BAR_FIELD_TYPES, TA_TUPLE_FIELDS,
    _math_minmax_na_expr, _math_round_digits_expr, _merge_kwargs,
)


# Statements a multi-statement request.security helper may hold before its
# final expression: declarations (``[a, b] = rhs`` too), assignments,
# if-branches and a block's trailing value.
_SECURITY_HELPER_STMTS = (VarDecl, Assignment, IfStmt, TupleAssign, ExprStmt)
# The bare expression statements a helper body admits: a block's value.
_SECURITY_BLOCK_VALUES = (Identifier, NumberLiteral, StringLiteral, BoolLiteral, NaLiteral)


# C++ scalar types a request.security helper argument or method receiver may
# have for the payload to inline the call.
_SECURITY_SCALAR_CPP = frozenset({"double", "int", "int64_t", "bool", "std::string"})
# What a builtin wrapping a payload's user call (``nz(f())``) may read besides
# what it hands back to the builder, for the call to be inlined on the
# requested bar: pure calls, namespace constants, and names the evaluator
# spells from the requested bar (``current_bar_`` becomes ``bar``).
_SECURITY_PURE_CALLS = frozenset({
    "nz", "na", "int", "float", "bool", "string", "input",
    "hour", "minute", "second", "dayofmonth", "dayofweek", "month", "year", "weekofyear",
})
_SECURITY_PURE_CALL_NAMESPACES = frozenset({"math", "str", "color", "input"})
# ``math.random`` draws per chart bar (its stream is keyed by ``bar_index_``).
_SECURITY_IMPURE_CALLS = frozenset({("math", "random")})
_SECURITY_PURE_MEMBER_NAMESPACES = frozenset({"math", "color", "syminfo", "format"})
# The session flags the payload reads at the requested bar's time; the
# session-day facts (``session.isfirstbar``) are the chart kernel's.
_SECURITY_PURE_SESSION_MEMBERS = frozenset({
    "ismarket", "ispremarket", "ispostmarket", "regular", "extended",
})
_SECURITY_REQUESTED_NAMES = frozenset(
    name for name, cpp in BAR_BUILTINS.items() if "current_bar_" in cpp
) | {"open", "high", "low", "close", "volume"}


# The signature under which ``inline_helper_ta_indices`` records a global's
# TA site that a multi-statement helper reads: the evaluator's prologue
# computes that site, but every earlier build computed it where the helper
# was inlined (``_security_check_tuple_element_history``).
_SECURITY_THROUGH_GLOBAL = "@through-global"


# The requested bar's fields a pure helper body may read
# (``_security_pure_body``): the builder spells each from the evaluator's
# ``bar``.
_SECURITY_SHARED_BAR_FIELDS = frozenset(SECURITY_BAR_FIELD_EXPRS) | {"hl2", "hlc3", "ohlc4"}
# A pure call whose inlined text reaches this length is computed once, where
# the evaluator opens (``_security_share_pure_call``); a shorter text is
# inlined at every reach, as every earlier build inlined it.
_SECURITY_SHARED_CALL_MIN_CHARS = 256


def _security_tuple_binding(func_name: str, name: str) -> str:
    """Opaque prepass binding of a name a helper's tuple declaration binds:
    the prepasses only need to know the name is a local, the emitter binds
    it to the element's C++ local."""
    return f"@tuple:{func_name}:{name}"


class _SecurityKeepChart(Exception):
    """An evaluator decided before its emission to keep every earlier
    build's lowering (``_security_chart_evaluators``)."""


class _SecurityHelperArgumentFrame(dict):
    """Callee bindings whose argument ASTs belong to the caller scope.

    A callee may reuse a caller parameter name. Keeping the caller binding
    stack with the argument prevents that new callee frame from capturing
    identifiers inside its own argument expression and falsely recursing.
    """

    def __init__(self, values: dict, caller_stack: tuple[dict, ...],
                 method: bool = False) -> None:
        super().__init__(values)
        self.caller_stack = caller_stack
        # Bound by a typed method call, which every earlier build called on
        # the chart (``_security_info_without_methods``).
        self.method = method


class SecurityEmitter:
    """Mixin owning ``request.security()`` lowering: evaluators, dispatch,
    rebind/binding/TA-variant machinery, and the per-call helper plan.

    Mixed into ``CodeGen``; not intended to be instantiated standalone."""

    def _resolve_security_tf(self, tf_node, containing_func: str):
        """Resolve a ``request.security`` timeframe argument to ``(tf_str, tf_expr)``.

        ``tf_str`` is a compile-time string literal value; ``tf_expr`` is a runtime
        C++ expression used at evaluator-registration time. Exactly one is non-None
        for a usable tf (both None is acceptable only as an explicit "unknown").

        A function-parameter tf (e.g. ``f(tf) => request.security(sym, tf, ...)``)
        is not visible at class scope (the evaluator is a class method), so it is
        resolved from the function's call sites (a timeframe reaching the
        helper through another helper arrives resolved by
        ``security_contexts``). A dead-code UDF (never called) registers the
        chart timeframe — its evaluator result is never read. Any other
        timeframe registration cannot compute is refused: it used to register
        the chart timeframe, silently.
        """
        if isinstance(tf_node, StringLiteral):
            return tf_node.value, None
        if isinstance(tf_node, SwitchStmt):
            # Keep diagnostics from the registration-time switch renderer
            # visible; the broad expression fallback below intentionally
            # catches ordinary unresolved expressions.
            return None, self._security_tf_runtime_expr(tf_node)
        if isinstance(tf_node, Identifier):
            name = tf_node.name
            if name in self._timeframe_period_vars:
                return None, "script_tf_"
            if (name in self._known_vars and name not in self._input_backed_vars
                    and not self._known_var_is_lexically_shadowed(name)
                    and isinstance(self._known_vars[name], str)):
                return self._known_vars[name], None
            if (name in self._input_backed_vars
                    and name in self._input_var_to_call
                    and not self._known_var_is_lexically_shadowed(name)):
                return None, self._visit_expr(self._input_var_to_call[name])
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if name in global_expr_map:
                expanded = self._security_tf_runtime_expr(
                    global_expr_map[name], resolving={name}
                )
                if expanded is not None:
                    return None, expanded
            if (name in self._global_mutable_infos
                    and self._security_identifier_is_global_binding(tf_node)):
                self._security_tf_mutable_reads.add(name)
            # class-scope resolvable (global / input member)?
            if self._ident_is_resolvable(name):
                try:
                    return None, self._visit_expr(tf_node)
                except Exception:
                    pass
            # function-parameter tf -> resolve from the call sites
            if containing_func:
                resolved = self._resolve_param_tf_from_callsites(containing_func, name)
                if resolved is not None:
                    return resolved
            self._security_tf_unresolved(tf_node, f"timeframe '{name}'")
        # any other expression — visit if it resolves at class scope
        try:
            expanded = self._security_tf_runtime_expr(tf_node)
            return None, expanded if expanded is not None else self._visit_expr(tf_node)
        except CompileError:
            raise
        except Exception:
            self._security_tf_unresolved(tf_node, "timeframe expression")

    def _security_tf_unresolved(self, tf_node, what: str) -> None:
        """Refuse a request.security timeframe registration cannot compute."""
        self._codegen_error(
            tf_node,
            f"request.security {what} cannot be resolved before the first bar: "
            "PineForge registers every requested timeframe before the script "
            "runs.",
            hint=(
                "Pass a literal, an input or a global to the helper that holds "
                "request.security."
            ),
        )

    def _security_tf_runtime_expr(self, node, resolving: set[str] | None = None) -> str | None:
        """Render a request.security timeframe expression for registration time.

        Security evaluators are registered before ``on_bar()`` initializes global
        variables, so input-backed aliases such as ``tf = useChart ? timeframe.period
        : inputTf`` must be expanded to their source expression with direct
        ``get_input_*`` reads. Emitting the member name would register with its
        default-constructed value (usually an empty string).

        Delegates to :meth:`_substitute_tf_input_reads`, which rewrites the
        input-derived *leaves* of the expression tree — including leaves buried
        inside a ternary CONDITION or any BinOp / UnaryOp / FuncCall — and then
        renders the substituted tree through the normal expression visitor.
        """
        if node is None:
            return None
        if isinstance(node, SwitchStmt):
            return self._security_tf_switch_runtime_expr(node, resolving or set())
        substituted = self._substitute_tf_input_reads(node, resolving or set())
        return self._visit_expr(substituted)

    def _security_tf_switch_runtime_expr(
        self, node: SwitchStmt, resolving: set[str]
    ) -> str:
        """Render a pure switch expression for evaluator registration.

        Pine parses ``tf = switch timeframe.period ...`` as a ``SwitchStmt``
        even though it is the right-hand side of a declaration.  The regular
        expression visitor deliberately does not render statement nodes, so
        passing that alias to ``request.security`` previously produced the
        truthy placeholder ``/* unknown */`` in generated C++.  Registration
        only needs the value selection, which can be represented as nested
        conditional expressions over setup-time-safe leaves such as
        ``script_tf_`` and direct input getters.

        Only pure, single-expression arms are accepted here.  Other shapes
        cannot be registered deterministically and produce a clear codegen
        diagnostic.  Without a default arm an unmatched switch yields ``na``,
        with which TradingView's ``request.security`` reads the chart's
        timeframe (tests/test_e2e_popfix_tf_switch_no_default.py replays its
        tape): the fallback is ``script_tf_``, ``timeframe.period``'s value.
        ``request.security_lower_tf`` reads the chart's timeframe too (one
        intrabar per chart bar), which the engine's lower-timeframe request
        rejects, so there the default arm stays required. An ``na`` arm is
        refused: the switch itself would store it in the timeframe string as a
        number.
        """

        def arm_value(body: list) -> str:
            if len(body) != 1 or not isinstance(body[0], ExprStmt):
                self._codegen_error(
                    node,
                    "request.security timeframe switch arms must contain one expression",
                    hint=(
                        "Compute multi-statement timeframe logic before the "
                        "request.security call or rewrite it as pure switch arms."
                    ),
                )
            if isinstance(body[0].expr, NaLiteral):
                if getattr(self, "_security_tf_lower", False):
                    self._codegen_error(
                        node,
                        "request.security_lower_tf timeframe switch arm is na",
                        hint=(
                            "TradingView reads an na timeframe as the chart's, which "
                            "the engine's lower-timeframe request rejects: spell a "
                            "strictly finer timeframe."
                        ),
                    )
                self._codegen_error(
                    node,
                    "request.security timeframe switch arm is na",
                    hint=(
                        "TradingView reads an na timeframe as the chart's: spell "
                        "the arm timeframe.period."
                    ),
                )
            value = self._security_tf_runtime_expr(body[0].expr, resolving)
            if value is None:
                self._codegen_error(
                    node,
                    "request.security timeframe switch arm could not be resolved at setup",
                )
            return value

        if node.default_body:
            result = arm_value(node.default_body)
        elif getattr(self, "_security_tf_lower", False):
            self._codegen_error(
                node,
                "request.security_lower_tf timeframe switch requires a default arm",
                hint=(
                    "When no arm matches, TradingView reads the chart's timeframe "
                    "(one intrabar per chart bar); the engine's lower-timeframe "
                    "request needs a strictly finer timeframe."
                ),
            )
        else:
            result = "script_tf_"

        selector = None
        if node.expr is not None:
            selector = self._security_tf_runtime_expr(node.expr, resolving)
            if selector is None:
                self._codegen_error(
                    node,
                    "request.security timeframe switch selector could not be resolved at setup",
                )

        for case_expr, body in reversed(node.cases):
            value = arm_value(body)
            case = self._security_tf_runtime_expr(case_expr, resolving)
            if case is None:
                self._codegen_error(
                    node,
                    "request.security timeframe switch case could not be resolved at setup",
                )
            condition = (
                f"(({selector}) == ({case}))" if selector is not None else case
            )
            result = f"(({condition}) ? ({value}) : ({result}))"
        return result

    def _substitute_tf_input_reads(self, node, resolving: set[str]):
        """Return ``node`` with input-derived leaves rewritten to expressions
        that are valid at security-registration time (before ``on_bar()``
        assigns members from their inputs):

        * an input-backed var -> its ``input.*()`` source call (``get_input_*``);
        * a ``timeframe.period`` alias var -> ``timeframe.period`` (``script_tf_``);
        * a known compile-time string var -> that string literal;
        * a global alias -> its defining expression, expanded recursively.

        The walk descends through Ternary / BinOp / UnaryOp / FuncCall /
        Subscript, so an input-backed identifier nested inside e.g. a ternary
        condition (``mode == "15" ? "240" : "60"``) resolves to its input read
        instead of the uninitialised member. A subtree containing no
        input-derived leaf is returned unchanged (same object) so unaffected
        timeframe expressions render byte-identically to before the fix.
        MemberAccess and literals are left verbatim: ``timeframe.period`` is
        already lowered to ``script_tf_`` by the expression visitor.
        """
        if not isinstance(node, ASTNode):
            return node
        if isinstance(node, Identifier):
            name = node.name
            if name in self._timeframe_period_vars:
                return MemberAccess(
                    object=Identifier(name="timeframe", loc=node.loc),
                    member="period",
                    loc=node.loc,
                    annotations=node.annotations,
                )
            if (name in self._input_backed_vars
                    and name in self._input_var_to_call
                    and not self._known_var_is_lexically_shadowed(name)):
                return self._input_var_to_call[name]
            if (name in self._known_vars and name not in self._input_backed_vars
                    and not self._known_var_is_lexically_shadowed(name)
                    and isinstance(self._known_vars[name], str)):
                return StringLiteral(
                    value=self._known_vars[name],
                    loc=node.loc,
                    annotations=node.annotations,
                )
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            global_binding = self._security_identifier_is_global_binding(node)
            if name in global_expr_map and name not in resolving:
                if (global_binding
                        and self._security_tf_reads_reassigned(global_expr_map[name])
                        and self._security_tf_replay_closure({name})[0]):
                    # Built from a reassigned global: the first bar computes it
                    # at its declaration (``_security_tf_replay_prologue``),
                    # before the reassignments after it.
                    self._security_tf_mutable_reads.add(name)
                    return node
                return self._substitute_tf_input_reads(
                    global_expr_map[name], resolving | {name})
            if name in self._global_mutable_infos and global_binding:
                # Reassigned: its member holds its initial value before the
                # first bar; registration replays what that bar computes
                # (``_security_tf_replay_prologue``).
                self._security_tf_mutable_reads.add(name)
            return node
        if isinstance(node, Ternary):
            cond = self._substitute_tf_input_reads(node.condition, resolving)
            tv = self._substitute_tf_input_reads(node.true_val, resolving)
            fv = self._substitute_tf_input_reads(node.false_val, resolving)
            if cond is node.condition and tv is node.true_val and fv is node.false_val:
                return node
            return replace(node, condition=cond, true_val=tv, false_val=fv)
        if isinstance(node, BinOp):
            left = self._substitute_tf_input_reads(node.left, resolving)
            right = self._substitute_tf_input_reads(node.right, resolving)
            if left is node.left and right is node.right:
                return node
            return replace(node, left=left, right=right)
        if isinstance(node, UnaryOp):
            operand = self._substitute_tf_input_reads(node.operand, resolving)
            if operand is node.operand:
                return node
            return replace(node, operand=operand)
        if isinstance(node, FuncCall):
            new_args = [self._substitute_tf_input_reads(a, resolving) for a in node.args]
            new_kwargs = {
                k: (self._substitute_tf_input_reads(v, resolving)
                    if isinstance(v, ASTNode) else v)
                for k, v in node.kwargs.items()
            }
            unchanged = (
                all(a is b for a, b in zip(new_args, node.args))
                and all(new_kwargs[k] is node.kwargs[k] for k in node.kwargs)
            )
            if unchanged:
                return node
            # Preserve the source span and annotations of the original call.
            # Namespace/receiver resolution uses that provenance to decide
            # whether a same-named global binding was visible at this call's
            # source position.  Rebuilding a bare FuncCall here used to turn
            # the span into synthetic 1:1 and could let a later ``map`` global
            # capture this earlier built-in ``map.get(...)``.
            return replace(node, args=new_args, kwargs=new_kwargs)
        if isinstance(node, Subscript):
            obj = self._substitute_tf_input_reads(node.object, resolving)
            idx = self._substitute_tf_input_reads(node.index, resolving)
            if obj is node.object and idx is node.index:
                return node
            return replace(node, object=obj, index=idx)
        return node

    # Calls a timeframe's first-bar value may make: pure, per-run values.
    _SECURITY_TF_REPLAY_CALLS = frozenset({"nz", "na", "int", "float", "bool", "string", "input"})
    _SECURITY_TF_REPLAY_NAMESPACES = frozenset({"math", "str", "input"})
    _SECURITY_TF_REPLAY_TIMEFRAME_CALLS = frozenset({"in_seconds", "from_seconds"})
    _SECURITY_TF_REPLAY_MEMBERS = frozenset({"timeframe", "syminfo", "math", "format"})
    _SECURITY_TF_REPLAY_OPS = frozenset({":=", "+=", "-=", "*=", "/=", "%="})

    def _security_tf_replay_prologue(self) -> list[str]:
        """The first bar's computation of the reassigned globals the requests'
        timeframes read, as locals of ``configure_security_evaluators()``.

        The engine registers every request before the first bar, where a
        global the script reassigns (``lowerSeconds := math.max(60,
        lowerSeconds)``) still holds its member's initial value: iamalala's
        lower-timeframe sites registered "1" where TradingView computes "72".
        TradingView computes a simple timeframe on the first bar. The locals
        shadow the members the rendered timeframes name (a global built from
        a reassigned one is one of them, computed at its declaration), and
        are computed by the top-level declarations, reassignments and ``if``
        blocks that give them their value, in source order, from literals,
        inputs (their getters), ``timeframe.*`` / ``syminfo.*`` / ``format.*``
        and pure ``math`` / ``str`` calls. A name that cannot be computed so
        keeps the registration every earlier build emitted, with a warning
        naming what stops it."""
        replayed: set[str] = set()
        for name in sorted(self._security_tf_mutable_reads):
            closure, blocker = self._security_tf_replay_closure({name})
            if closure:
                replayed.update(closure)
                continue
            where, reason = blocker
            info = self._global_mutable_infos.get(name)
            self._codegen_warning(
                getattr(info, "decl_node", None),
                f"request.security timeframe reads '{name}', which the script "
                "reassigns: PineForge registers every request before the first "
                "bar, where it holds its initial value, and cannot compute it "
                f"there ('{where}' {reason})",
            )
        if not replayed:
            return []
        names, stmts = self._security_tf_replay_closure(replayed)
        pad = "        "
        lines = [
            f"{pad}// The first bar's values of the reassigned globals the "
            "timeframes read; registration runs before it."
        ]
        for name in names:
            safe = self._safe_name(name)
            lines.append(f"{pad}decltype(this->{safe}) {safe}{{}};")
        for stmt in stmts:
            self._security_tf_replay_stmt(stmt, set(names), lines, pad)
        return lines

    def _security_tf_replay_target(self, name: str, body: list):
        """``(declaration, top-level statements)`` that give global ``name``
        its first-bar value, or ``(None, reason)``: a reassigned global's
        declaration and reassignments, a global built from one's declaration."""
        info = self._global_mutable_infos.get(name)
        decl = getattr(info, "decl_node", None) if info is not None else next(
            (stmt for stmt in body if isinstance(stmt, VarDecl) and stmt.name == name),
            None,
        )
        sym = self.ctx.symbols.resolve(name)
        pine_type = getattr(info, "pine_type", None) if info is not None else getattr(
            sym, "pine_type", None)
        if not isinstance(decl, VarDecl) or not any(decl is stmt for stmt in body):
            return None, "is not declared at the top level"
        if (getattr(info, "is_series", False) or name in self.ctx.series_vars
                or pine_type not in (
                    PineType.INT, PineType.FLOAT, PineType.BOOL, PineType.STRING)):
            return None, "is not a scalar int, float, bool or string"
        return decl, (list(info.source_stmts) if info is not None else [decl])

    def _security_tf_replay_closure(self, names: set[str]):
        """``(closure names, top-level statements)``, each in source order,
        that compute ``names`` on the first bar before any request, or
        ``(None, (name, reason))`` for the first one that cannot be."""
        body = list(getattr(self.ctx.ast, "body", None) or ())
        position = {id(stmt): i for i, stmt in enumerate(body)}
        first_request = self._security_first_request_position(body)
        closure = set(names)
        while True:
            stmts: dict[int, ASTNode] = {}
            decls: dict[str, ASTNode] = {}
            for name in sorted(closure):
                decl, source = self._security_tf_replay_target(name, body)
                if decl is None:
                    return None, (name, source)
                decls[name] = decl
                for stmt in source:
                    if position.get(id(stmt), first_request) >= first_request:
                        return None, (name, "is assigned after the first request")
                    stmts[id(stmt)] = stmt
            reads: set[str] = set()
            for stmt in stmts.values():
                if not self._security_tf_replay_stmt_ok(stmt, closure, reads, True):
                    culprit = next(
                        (n for n, d in decls.items() if self._security_tf_assigns(stmt, {n})),
                        sorted(closure)[0],
                    )
                    return None, (culprit, "is assigned a value it cannot compute there")
            if reads <= closure:
                names_in_order = sorted(closure, key=lambda n: position[id(decls[n])])
                return names_in_order, sorted(stmts.values(), key=lambda s: position[id(s)])
            closure |= reads

    def _security_first_request_position(self, body: list) -> int:
        """Index of the first top-level statement that makes a request, or
        calls a user function or method that (transitively) does; len(body)
        if none."""
        def makes_request(node) -> bool:
            for child in self._walk_ast(node):
                if isinstance(child, FuncCall):
                    func_name, namespace = self._resolve_callee(child.callee)
                    if namespace == "request" or func_name in requesting:
                        return True
            return False

        requesting: set[str] = set()
        functions = [stmt for stmt in body if isinstance(stmt, (FuncDef, MethodDef))]
        changed = True
        while changed:
            changed = False
            for fdef in functions:
                if fdef.name not in requesting and any(
                    makes_request(stmt) for stmt in fdef.body
                ):
                    requesting.add(fdef.name)
                    changed = True
        for i, stmt in enumerate(body):
            if not isinstance(stmt, (FuncDef, MethodDef)) and makes_request(stmt):
                return i
        return len(body)

    def _security_tf_reads_reassigned(self, node, seen: frozenset = frozenset()) -> bool:
        """Whether ``node`` reads a reassigned global, directly or through
        the declarations of the globals it reads."""
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        for child in self._walk_ast(node):
            if (not isinstance(child, Identifier)
                    or not self._security_identifier_is_global_binding(child)):
                continue
            if child.name in self._global_mutable_infos:
                return True
            if (child.name in global_expr_map and child.name not in seen
                    and self._security_tf_reads_reassigned(
                        global_expr_map[child.name], seen | {child.name})):
                return True
        return False

    def _security_tf_assigns(self, stmt, closure: set[str]) -> bool:
        """Whether ``stmt`` binds or assigns a name of ``closure`` anywhere."""
        for node in self._walk_ast(stmt):
            if isinstance(node, VarDecl) and node.name in closure:
                return True
            if (isinstance(node, Assignment) and isinstance(node.target, Identifier)
                    and node.target.name in closure):
                return True
            if isinstance(node, TupleAssign) and set(node.names) & closure:
                return True
            if isinstance(node, (ForStmt, ForInStmt)) and (
                {getattr(node, "var", None), *(getattr(node, "vars", None) or ())}
                & closure
            ):
                return True
        return False

    def _security_tf_replay_stmt_ok(
        self, stmt, closure: set[str], reads: set[str], top: bool
    ) -> bool:
        """Whether registration can replay ``stmt``: a top-level declaration
        (``var`` too: its first-bar value is its initializer's) or an
        assignment of a ``closure`` name with a first-bar value, or an ``if``
        on a first-bar condition around such assignments. A statement that
        assigns no ``closure`` name is left out (a function cannot assign a
        global, so a call cannot either)."""
        if isinstance(stmt, VarDecl):
            if stmt.name not in closure:
                # A block local: a closure value reading it is no global read.
                return not top
            return (
                top and stmt.value is not None
                and not isinstance(stmt.value, (IfStmt, SwitchStmt))
                and self._security_tf_replay_reads(stmt.value, reads)
            )
        if isinstance(stmt, Assignment) and isinstance(stmt.target, Identifier):
            if stmt.target.name not in closure:
                return True
            return (
                stmt.op in self._SECURITY_TF_REPLAY_OPS
                and not isinstance(stmt.value, (IfStmt, SwitchStmt))
                and self._security_tf_replay_reads(stmt.value, reads)
            )
        if not self._security_tf_assigns(stmt, closure):
            return True
        if isinstance(stmt, IfStmt):
            return (
                self._security_tf_replay_reads(stmt.condition, reads)
                and all(self._security_tf_replay_stmt_ok(child, closure, reads, False)
                        for child in stmt.body)
                and all(self._security_tf_replay_stmt_ok(child, closure, reads, False)
                        for child in stmt.else_body or ())
            )
        return False

    def _security_tf_replay_reads(
        self, node, reads: set[str], seen: frozenset = frozenset()
    ) -> bool:
        """Whether ``node``'s first-bar value is computed from literals,
        inputs, ``timeframe.*`` / ``syminfo.*`` / ``format.*``, pure ``math``
        / ``str`` calls and globals built from them; collects into ``reads``
        the reassigned globals it reads and the globals built from one (both
        computed where they are declared)."""
        if node is None or isinstance(
            node, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral)
        ):
            return True
        if isinstance(node, Identifier):
            name = node.name
            if not self._security_identifier_is_global_binding(node):
                return False
            if name in self._timeframe_period_vars or (
                name in self._input_backed_vars and name in self._input_var_to_call
            ):
                return True
            if name in self._global_mutable_infos:
                reads.add(name)
                return True
            value = (getattr(self.ctx, "global_expr_map", {}) or {}).get(name)
            if value is None or name in seen:
                return value is not None
            if self._security_tf_reads_reassigned(value):
                reads.add(name)
                return True
            return self._security_tf_replay_reads(value, reads, seen | {name})
        if isinstance(node, MemberAccess):
            return (isinstance(node.object, Identifier)
                    and node.object.name in self._SECURITY_TF_REPLAY_MEMBERS)
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if not (
                (namespace is None and func_name in self._SECURITY_TF_REPLAY_CALLS)
                or (namespace in self._SECURITY_TF_REPLAY_NAMESPACES
                    and (namespace, func_name) != ("math", "random"))
                or (namespace == "timeframe"
                    and func_name in self._SECURITY_TF_REPLAY_TIMEFRAME_CALLS)
            ):
                return False
            return all(
                self._security_tf_replay_reads(arg, reads, seen)
                for arg in [*node.args, *node.kwargs.values()]
                if isinstance(arg, ASTNode)
            )
        if isinstance(node, BinOp):
            return (self._security_tf_replay_reads(node.left, reads, seen)
                    and self._security_tf_replay_reads(node.right, reads, seen))
        if isinstance(node, UnaryOp):
            return self._security_tf_replay_reads(node.operand, reads, seen)
        if isinstance(node, Ternary):
            return all(
                self._security_tf_replay_reads(part, reads, seen)
                for part in (node.condition, node.true_val, node.false_val)
            )
        return False

    def _security_tf_replay_stmt(self, stmt, closure: set[str], lines: list[str],
                                 pad: str) -> None:
        """``stmt``'s replay (``_security_tf_replay_stmt_ok``), rendered as the
        chart renders the assignment, its values read at registration."""
        if isinstance(stmt, VarDecl) and stmt.name in closure:
            self._security_tf_replay_assign(stmt.name, ":=", stmt.value, lines, pad)
        elif (isinstance(stmt, Assignment) and isinstance(stmt.target, Identifier)
                and stmt.target.name in closure):
            self._security_tf_replay_assign(
                stmt.target.name, stmt.op, stmt.value, lines, pad)
        elif isinstance(stmt, IfStmt) and self._security_tf_assigns(stmt, closure):
            condition = self._substitute_tf_input_reads(stmt.condition, set())
            cond = self._coerce_bool_expr(self._visit_expr(condition), condition)
            lines.append(f"{pad}if ({cond}) {{")
            for child in stmt.body:
                self._security_tf_replay_stmt(child, closure, lines, pad + "    ")
            if stmt.else_body:
                lines.append(f"{pad}}} else {{")
                for child in stmt.else_body:
                    self._security_tf_replay_stmt(child, closure, lines, pad + "    ")
            lines.append(f"{pad}}}")

    def _security_tf_replay_assign(self, name: str, op: str, value, lines: list[str],
                                   pad: str) -> None:
        """One replayed write, coerced into its slot as ``_visit_assignment``
        writes a global scalar."""
        safe = self._safe_name(name)
        value = self._substitute_tf_input_reads(value, set())
        target_cpp_type = self._na_reassign_cpp_type(name) if self._is_na_expr(value) else None
        val_cpp = self._visit_rhs_value(value, name, target_cpp_type=target_cpp_type)
        int_slot = self._int_slot_cpp_type(name)
        if op == ":=":
            lines.append(f"{pad}{safe} = {self._coerce_int_slot(val_cpp, value, int_slot)};")
            return
        rhs = self._compound_assign_rhs(safe, op, val_cpp)
        if rhs is not None:
            rhs = self._coerce_int_slot(rhs, value, int_slot, value_is_double=True)
        else:
            rhs = self._coerce_int_slot(f"{safe} {op[0]} {val_cpp}", value, int_slot)
        lines.append(f"{pad}{safe} = {rhs};")

    def _resolve_param_tf_from_callsites(self, func_name: str, param_name: str):
        """For a ``request.security`` whose tf is function parameter ``param_name``
        of user function ``func_name``, return ``(tf_str, tf_expr)`` resolved from
        the call sites, or None. Every call passes the same timeframe (the
        analyzer clones a request whose call sites differ), which is used; a
        never-called (dead-code) function registers the chart timeframe
        (``input_tf_``), whose evaluator is never read."""
        fdef = None
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, FuncDef) and node.name == func_name:
                fdef = node
                break
        if fdef is None or param_name not in fdef.params:
            return None
        pidx = fdef.params.index(param_name)
        resolved: list = []
        found_call = False
        for node in self._walk_ast(self.ctx.ast):
            if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                    and node.callee.name == func_name):
                found_call = True
                arg = node.args[pidx] if pidx < len(node.args) else None
                if arg is None:
                    continue
                # Resolve the call-site arg (no further containing func — these
                # are global-scope / input args).
                resolved.append(self._resolve_security_tf(arg, ""))
        if not found_call:
            # dead code — evaluator never read; register with chart tf.
            return (None, "input_tf_")
        valid = [r for r in resolved if r is not None]
        strs = {r[0] for r in valid}
        exprs = {r[1] for r in valid}
        if len(strs) == 1 and next(iter(strs), None) is not None:
            return (next(iter(strs)), None)
        if len(exprs) == 1 and next(iter(exprs), None) is not None:
            return (None, next(iter(exprs)))
        self._codegen_error(
            fdef,
            f"request.security timeframe parameter '{param_name}' of '{func_name}' "
            "has no single timeframe across its call sites",
            hint="Pass the timeframe as a positional literal, input or global.",
        )

    def _normalize_security_call(self, item) -> dict:
        if hasattr(item, "sec_id"):
            return {
                "sec_id": item.sec_id,
                "tf_node": item.timeframe,
                "expr_node": item.expression,
                "returns_tuple": item.returns_tuple,
                "tuple_size": item.tuple_size,
                "tuple_element_types": tuple(
                    getattr(item, "tuple_element_types", ()) or ()
                ),
                "gaps_node": item.gaps,
                "lookahead_node": item.lookahead,
                "ta_range": item.ta_range,
                "heikinashi": bool(getattr(item, "heikinashi", False)),
                "depends_on_mutable_globals": bool(getattr(item, "depends_on_mutable_globals", False)),
                "mutable_globals": list(getattr(item, "mutable_globals", ()) or ()),
                "is_lower_tf_array": bool(getattr(item, "is_lower_tf_array", False)),
                "containing_func": getattr(item, "containing_func", "") or "",
                "callsite_idx": getattr(item, "callsite_idx", None),
                "string_result": bool(getattr(item, "string_result", False)),
                "dead": bool(getattr(item, "dead", False)),
                "foreign": bool(getattr(item, "foreign", False)),
                "symbol_node": getattr(item, "symbol", None),
                "ignore_invalid_node": getattr(item, "ignore_invalid", None),
            }
        return {
            "sec_id": item[0],
            "tf_node": item[1] if len(item) > 1 else None,
            "expr_node": item[2] if len(item) > 2 else None,
            "returns_tuple": item[3] if len(item) > 3 else False,
            "tuple_size": item[4] if len(item) > 4 else 0,
            "tuple_element_types": (),
            "gaps_node": item[5] if len(item) > 5 else None,
            "lookahead_node": item[6] if len(item) > 6 else None,
            "ta_range": item[7] if len(item) > 7 else None,
            "depends_on_mutable_globals": False,
            "mutable_globals": [],
            "is_lower_tf_array": False,
            "containing_func": "",
            "callsite_idx": None,
        }

    def _security_ta_tuple_element_field(self, name: str) -> str | None:
        """The result field a global names when a top-level tuple declaration
        binds it to an element of a TA tuple call (``[m, s, h] =
        ta.macd(...)``: ``s`` is ``signal``), else None."""
        fields = getattr(self, "_security_ta_tuple_fields_cache", None)
        if fields is None:
            fields = {}
            for stmt in self.ctx.ast.body:
                if not isinstance(stmt, TupleAssign):
                    continue
                site = self._get_ta_site(stmt.value)
                if site is None or not getattr(site, "returns_tuple", False):
                    continue
                names = TA_TUPLE_FIELDS.get(self._ta_name_from_site(site)) or []
                for index, element in enumerate(stmt.names):
                    if element != "_" and index < len(names):
                        fields[element] = names[index]
            self._security_ta_tuple_fields_cache = fields
        return fields.get(name)

    def _security_tuple_binding_names(self) -> frozenset[str]:
        """Names a top-level ``[a, b] = request.security(...)`` binds."""
        names = getattr(self, "_security_tuple_names_cache", None)
        if names is None:
            found: set[str] = set()
            for stmt in self.ctx.ast.body:
                if not (isinstance(stmt, TupleAssign)
                        and isinstance(stmt.value, FuncCall)):
                    continue
                func_name, namespace = self._resolve_callee(stmt.value.callee)
                if namespace == "request" and func_name == "security":
                    found.update(name for name in stmt.names if name != "_")
            names = frozenset(found)
            self._security_tuple_names_cache = names
        return names

    def _security_call_returns_string(self, node: FuncCall) -> bool:
        """Whether this ``request.security(...)`` call's payload is a string
        (its registered call carries ``string_result``)."""
        args = list(node.args)
        for idx, name in enumerate(("symbol", "timeframe", "expression")):
            if name in node.kwargs:
                while len(args) <= idx:
                    args.append(None)
                args[idx] = node.kwargs[name]
        expr_node = args[2] if len(args) > 2 else None
        return expr_node is not None and any(
            item.get("string_result")
            for item in self._security_calls
            if not item.get("is_lower_tf_array") and item["expr_node"] is expr_node
        )

    def _security_state_name(self, sec_id: int, name: str) -> str:
        return f"_sec{sec_id}_{self._safe_name(name)}"

    def _security_init_flag_name(self, sec_id: int, name: str) -> str:
        return f"{self._security_state_name(sec_id, name)}_initialized"

    def _security_cpp_type_for_mutable(self, name: str, info) -> str:
        if getattr(info, "is_series", False):
            return self._series_type_for(name)
        cpp_type = PINE_TYPE_TO_CPP.get(getattr(info, "pine_type", PineType.FLOAT), "double")
        if cpp_type == "int" and self._security_copy_is_arithmetic_wide(name):
            return "int64_t"
        return cpp_type

    def _security_copy_is_arithmetic_wide(self, name: str) -> bool:
        """Whether the chart slot of a script variable a payload re-evaluates
        is ``int64_t`` only by 64-bit integer arithmetic
        (``_int_arith_leaves_int32``: ``w := q * 7200000``), whose value the
        payload's copy must hold too: typed from the analyzer's ``int``, it
        read the na-aware double form past int32 as undefined behaviour
        (quirk 9). A name an epoch makes wide keeps the ``int`` copy every
        earlier build emitted."""
        if name not in self._wide_int_provenance()[0]:
            return False
        with self._int_width_scan(epoch_only=True):
            return name not in self._wide_int_provenance()[0]

    def _security_copy_store_cpp(self, name: str, info, value_node, value_cpp: str) -> str:
        """``value_cpp`` stored into a payload's copy of ``name``: the
        na-aware double form of a 64-bit product narrows na-preserving into
        an integer copy (quirk 9), as every integer store of it does -- the
        value's own product, or one the payload expands a global into
        (``w := a`` over ``a = q * 7200000``), which only the emitted C++
        shows (``_wide_int_arith_cpp``'s ``_pf_wide_l``)."""
        cpp_type = self._security_cpp_type_for_mutable(name, info)
        if (cpp_type in ("int", "int64_t")
                and (self._holds_na_aware_wide_double(value_node)
                     or "_pf_wide_l" in value_cpp)):
            return na_preserving_int_cast(value_cpp, cpp_type)
        return value_cpp

    def _security_relevant_top_level_stmts(self, mutable_globals: list[str]) -> list[ASTNode]:
        if not mutable_globals:
            return []
        source_ids: set[int] = set()
        for name in mutable_globals:
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            for stmt in getattr(info, "source_stmts", []) or []:
                source_ids.add(id(stmt))
        return [stmt for stmt in self.ctx.ast.body if id(stmt) in source_ids]

    def _rewrite_security_cpp(
        self,
        cpp: str,
        sec_id: int,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> str:
        import re

        # The names below are C++ tokens of the rendered text: a string
        # literal's contents (``"bull"`` beside a helper local ``bull``) stay.
        literals = re.compile(r'"(?:[^"\\\n]|\\.)*"' + r"|'(?:[^'\\\n]|\\.)*'")

        def sub(pattern: str, repl: str, text: str) -> str:
            out, last = [], 0
            for match in literals.finditer(text):
                out.append(re.sub(pattern, repl, text[last:match.start()]))
                out.append(match.group(0))
                last = match.end()
            out.append(re.sub(pattern, repl, text[last:]))
            return "".join(out)

        result = cpp.replace("current_bar_.", "bar.")
        for name in sorted(security_mutable_names, key=len, reverse=True):
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            safe = self._safe_name(name)
            state = self._security_state_name(sec_id, name)
            if getattr(info, "is_series", False):
                result = sub(rf"\b{re.escape(safe)}\b(?=\s*\[)", state, result)
                result = sub(rf"\b{re.escape(safe)}\b(?!\s*\[)", f"{state}[0]", result)
            else:
                result = sub(rf"\b{re.escape(safe)}\b", state, result)
        if helper_binding_stack:
            for frame in helper_binding_stack:
                for name, bound in frame.items():
                    if not isinstance(bound, str):
                        continue
                    series_name = self._security_series_binding_target(bound)
                    if series_name is not None:
                        ref = self._security_helper_series_ref(series_name)
                        result = sub(
                            rf"\b{re.escape(name)}\b(?=\s*\[)",
                            ref.replace("\\", "\\\\"),
                            result,
                        )
                        result = sub(
                            rf"\b{re.escape(name)}\b(?!\s*\[)",
                            f"{ref}[0]".replace("\\", "\\\\"),
                            result,
                        )
                    else:
                        result = sub(rf"\b{re.escape(name)}\b", bound, result)
        return result

    def _security_lookup_helper_binding(
        self,
        name: str,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ):
        resolved = self._security_lookup_helper_binding_context(
            name, helper_binding_stack
        )
        return resolved[0] if resolved is not None else None

    # Depth of the helper-local value re-walks under way in
    # ``_collect_security_ta_binding_stacks``.
    _security_local_rewalks = 0

    @staticmethod
    def _security_binding_is_helper_local(
        name: str,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ) -> bool:
        """Whether ``name`` resolves (as ``_security_lookup_helper_binding_context``
        does) to a helper's local, not to a helper's argument."""
        for frame in reversed(helper_binding_stack or ()):
            if name not in frame:
                continue
            bound = frame[name]
            if isinstance(bound, Identifier) and bound.name == name:
                continue
            return not isinstance(frame, _SecurityHelperArgumentFrame)
        return False

    def _security_lookup_helper_binding_context(
        self,
        name: str,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ):
        """Return ``(bound_value, lexical_stack_for_value)``.

        Ordinary helper-local bindings are authored in the current stack.
        Callee argument bindings are authored in the caller stack captured by
        ``_SecurityHelperArgumentFrame`` and must be expanded there.
        """
        if not helper_binding_stack:
            return None
        for frame in reversed(helper_binding_stack):
            if name not in frame:
                continue
            bound = frame[name]
            if isinstance(bound, Identifier) and bound.name == name:
                continue
            lexical_stack = (
                frame.caller_stack
                if isinstance(frame, _SecurityHelperArgumentFrame)
                else helper_binding_stack
            )
            return bound, lexical_stack
        return None

    def _security_index_reads_helper_local(
        self,
        index,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ) -> bool:
        """Whether a history index reads a helper-local name. The linear
        emitter binds a local to its C++ variable, so it lowers such an index
        at run time even where the prepasses could fold the local's value
        (``k = 0`` then ``src[k]``): they must declare the history it reads."""
        stack = [(index, helper_binding_stack or ())]
        for _ in range(4096):
            if not stack:
                return False
            n, frames = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend((child, frames) for child in n)
                continue
            if not isinstance(n, ASTNode):
                continue
            if (isinstance(n, Identifier)
                    and not self._security_identifier_is_global_binding(n)):
                for frame in reversed(frames):
                    if n.name in frame:
                        if not isinstance(frame, _SecurityHelperArgumentFrame):
                            return True
                        # An argument: read it in the caller's scope, where
                        # it may be the caller's local (``f(close, k)``).
                        if isinstance(frame[n.name], ASTNode):
                            stack.append((frame[n.name], frame.caller_stack))
                        break
            stack.extend(
                (v, frames) for k, v in vars(n).items() if k != "annotations"
            )
        return True

    def _literal_int_for_security_index(self, node) -> int | None:
        """Integer index for bar-field[n] inside request.security (must be literal)."""
        if isinstance(node, NumberLiteral):
            v = node.value
            if isinstance(v, bool):
                return None
            if float(v) == int(v):
                return int(v)
            return None
        if (
            isinstance(node, UnaryOp)
            and node.op == "-"
            and isinstance(node.operand, NumberLiteral)
        ):
            v = -node.operand.value
            if float(v) == int(v):
                return int(v)
            return None
        return None

    # Narrow approximations used only while resolving a request.security()
    # history index to a literal. This is not chart-scope barstate lowering:
    # visit_expr emits runtime state for islast/islastconfirmedhistory, whereas
    # this security-index folder currently treats them as false. Other flags
    # (isfirst/isnew/isconfirmed) remain unknown and are deliberately absent.
    _SECURITY_CONST_BARSTATE = {
        "isrealtime": False,
        "islast": False,
        "ishistory": True,
        "islastconfirmedhistory": False,
    }

    def _fold_security_const_bool(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        resolving: set[str] | None = None,
    ) -> bool | None:
        """Fold a boolean expression to a compile-time constant, or None.

        Only reduces expressions whose non-literal leaves have a narrow
        request.security history-index approximation above; anything else
        returns None. Used to resolve a request.security history index
        expressed as ``barstate.isrealtime ? 1 : 0`` and friends."""
        if resolving is None:
            resolving = set()
        if isinstance(node, BoolLiteral):
            return bool(node.value)
        if (
            isinstance(node, MemberAccess)
            and isinstance(node.object, Identifier)
            and node.object.name == "barstate"
        ):
            return self._SECURITY_CONST_BARSTATE.get(node.member)
        if isinstance(node, UnaryOp) and node.op == "not":
            inner = self._fold_security_const_bool(
                node.operand, helper_binding_stack, resolving
            )
            return None if inner is None else (not inner)
        if isinstance(node, BinOp) and node.op in ("and", "or"):
            left = self._fold_security_const_bool(
                node.left, helper_binding_stack, resolving
            )
            right = self._fold_security_const_bool(
                node.right, helper_binding_stack, resolving
            )
            if left is None or right is None:
                return None
            return (left and right) if node.op == "and" else (left or right)
        if isinstance(node, Identifier):
            binding = None
            if not self._security_identifier_is_global_binding(node):
                binding = self._security_lookup_helper_binding_context(
                    node.name, helper_binding_stack
                )
            if binding is not None:
                bound, bound_stack = binding
                if isinstance(bound, str):
                    return None
                return self._fold_security_const_bool(
                    bound, bound_stack, resolving
                )
            param_key = f"param:{id(node)}"
            param_binding = self._security_index_param_callsite_binding(node)
            if param_binding is not None and param_key not in resolving:
                return self._fold_security_const_bool(
                    param_binding, (), resolving | {param_key}
                )
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if (
                self._security_identifier_is_global_binding(node)
                and node.name in global_expr_map
                and node.name not in resolving
            ):
                resolving.add(node.name)
                out = self._fold_security_const_bool(
                    global_expr_map[node.name], (), resolving
                )
                resolving.remove(node.name)
                return out
        return None

    def _resolve_security_index_literal(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        resolving: set[str] | None = None,
    ) -> int | None:
        """Resolve a request.security TA/bar-field history index to a literal int.

        Extends ``_literal_int_for_security_index`` by also resolving the index
        through helper-parameter bindings and global aliases and constant-folding
        a ternary whose condition has a security-index barstate approximation
        (e.g. ``idxHigher = barstate.isrealtime ? 1 : 0`` -> 0). A literal index
        short-circuits on the first line, so behaviour is unchanged for every
        already-literal index. Returns None when the index cannot be reduced to
        a compile-time literal, so the caller keeps its existing rejection."""
        direct = self._literal_int_for_security_index(node)
        if direct is not None:
            return direct
        if resolving is None:
            resolving = set()
        if isinstance(node, Identifier):
            binding = None
            if not self._security_identifier_is_global_binding(node):
                binding = self._security_lookup_helper_binding_context(
                    node.name, helper_binding_stack
                )
            if binding is not None:
                bound, bound_stack = binding
                if isinstance(bound, str):
                    return None
                return self._resolve_security_index_literal(
                    bound, bound_stack, resolving
                )
            param_key = f"param:{id(node)}"
            param_binding = self._security_index_param_callsite_binding(node)
            if param_binding is not None and param_key not in resolving:
                return self._resolve_security_index_literal(
                    param_binding, (), resolving | {param_key}
                )
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if (
                self._security_identifier_is_global_binding(node)
                and node.name in global_expr_map
                and node.name not in resolving
            ):
                resolving.add(node.name)
                out = self._resolve_security_index_literal(
                    global_expr_map[node.name], (), resolving
                )
                resolving.remove(node.name)
                return out
            return None
        if isinstance(node, Ternary):
            cond = self._fold_security_const_bool(node.condition, helper_binding_stack)
            if cond is None:
                return None
            chosen = node.true_val if cond else node.false_val
            return self._resolve_security_index_literal(
                chosen, helper_binding_stack, resolving
            )
        return None

    def _security_identifier_is_global_binding(self, node: Identifier) -> bool:
        """Whether this exact identifier AST resolved to a global binding.

        ``global_expr_map`` is keyed only by spelling.  Consulting it without
        node provenance lets a UDF parameter, block local, or loop iterator
        capture a same-named global input after analysis scopes have unwound.
        A global's read that ``security_contexts`` put in a helper's copy is
        one too where the analyzer bound nothing: the global is declared
        after the helper (``GLOBAL_ANNOTATION``).
        """
        scopes = getattr(self.ctx, "identifier_binding_scopes", {}) or {}
        scope = scopes.get(id(node))
        return scope == "global" or (
            scope is None and bool((node.annotations or {}).get(GLOBAL_ANNOTATION))
        )

    def _security_warn_unbound_param(self, node: Identifier) -> None:
        """Warn once that a payload reads the history of a parameter of the
        helper holding the request, which nothing binds in the evaluator (a
        class method): it reads ``na``, or a global of the parameter's name.
        ``security_contexts`` puts the argument in the parameter's place where
        the requested bars recompute it; every other argument keeps this
        lowering. A helper no top-level statement reaches never runs, and
        does not warn."""
        scopes = getattr(self.ctx, "identifier_binding_scopes", {}) or {}
        scope = scopes.get(id(node))
        if not isinstance(scope, str) or not scope.startswith("func_"):
            return
        func_info = self._func_info_map.get(scope[5:])
        if (
            func_info is None
            or func_info.node is None
            or node.name not in func_info.node.params
            or (node.annotations or {}).get(UNREACHED_ANNOTATION)
        ):
            return
        # A helper's copies (``h__pfctx1``) share the authored read: warn once.
        helper = re.sub(r"__pfctx\d+$", "", scope[5:])
        key = (helper, node.name, getattr(node.loc, "line", None), getattr(node.loc, "col", None))
        warned = getattr(self, "_security_warned_params", None)
        if warned is None:
            warned = self._security_warned_params = set()
        if key in warned:
            return
        warned.add(key)
        # The C++ names the parameter: a global of that name is read instead.
        is_global = node.name in (getattr(self.ctx, "global_expr_map", {}) or {}) or (
            node.name in self._global_mutable_infos
        )
        reads = f"the global '{node.name}' instead" if is_global else "na"
        self._codegen_warning(
            node,
            f"request.security payload reads the history of '{node.name}', a "
            f"parameter of '{helper}' whose argument PineForge does not "
            f"recompute on the requested bars: it reads {reads}",
        )

    def _security_index_param_callsite_binding(self, node: Identifier):
        """Resolve one UDF parameter index from its call sites, conservatively.

        A request.security evaluator is a class method, so a parameter of the
        UDF that *contains* the request call is not otherwise in scope there.
        Reuse the established timeframe-callsite model for the narrow offset
        case: one call site is exact; several are accepted only for the same
        literal or the same proven-global identifier.  Mixed bindings fail
        closed instead of sharing one evaluator across different offsets.
        """
        scopes = getattr(self.ctx, "identifier_binding_scopes", {}) or {}
        scope = scopes.get(id(node))
        if not isinstance(scope, str) or not scope.startswith("func_"):
            return None
        func_name = scope[5:]
        func_info = self._func_info_map.get(func_name)
        if (
            func_info is None
            or func_info.node is None
            or node.name not in func_info.node.params
        ):
            return None
        # A parameter is only equal to its call-site argument until the UDF
        # reassigns it.  Specializing a later ``ta.*[param]`` from the authored
        # call argument would otherwise ignore legal ``param := ...`` writes
        # and silently select the wrong requested-context history bar.
        for child in self._walk_ast(func_info.node):
            if (
                isinstance(child, Assignment)
                and isinstance(child.target, Identifier)
                and child.target.name == node.name
            ):
                return None
            if isinstance(child, VarDecl) and child.name == node.name:
                return None
            if isinstance(child, TupleAssign) and node.name in child.names:
                return None
            if isinstance(child, ForStmt) and child.var == node.name:
                return None
            if isinstance(child, ForInStmt) and (
                child.var == node.name or node.name in (child.vars or [])
            ):
                return None
        param_index = list(func_info.node.params).index(node.name)
        bound_args = []
        for call in self._walk_ast(self.ctx.ast):
            if not (
                isinstance(call, FuncCall)
                and isinstance(call.callee, Identifier)
                and call.callee.name == func_name
            ):
                continue
            arg = call.args[param_index] if param_index < len(call.args) else None
            if node.name in call.kwargs:
                arg = call.kwargs[node.name]
            if arg is None:
                return None
            bound_args.append(arg)
        if not bound_args:
            return None

        first = bound_args[0]

        def equivalent(left, right) -> bool:
            if isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
                return left.value == right.value
            if isinstance(left, Identifier) and isinstance(right, Identifier):
                return (
                    left.name == right.name
                    and self._security_identifier_is_global_binding(left)
                    and self._security_identifier_is_global_binding(right)
                )
            return left is right

        if not all(equivalent(first, other) for other in bound_args[1:]):
            return None
        return first

    def _resolve_security_immutable_input_int(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        resolving: set[int] | None = None,
    ) -> tuple[FuncCall, tuple[dict[str, ASTNode], ...]] | None:
        """Resolve an immutable security-history offset to its ``input.int``.

        Pine v6 permits a bar-invariant input integer as a history offset.  A
        request.security TA offset needs special admission because its backing
        history advances in the requested context, not in chart context.  Keep
        this proof deliberately narrower than arbitrary ``series int`` support:

        * the leaf must be exactly ``input.int``;
        * helper-parameter and top-level immutable alias chains are followed;
        * ``var``/``varip`` or any globally reassigned alias is rejected; and
        * arithmetic/ternary expressions remain unsupported for now.

        Return the proven input call plus the lexical stack in which that call
        was authored.  Rendering the original identifier under a helper's
        stack would let a same-named helper parameter capture a global alias.
        Crossing ``global_expr_map`` therefore resets the stack to global.

        The generated ``Series`` safely returns ``na`` for insufficient depth;
        negative runtime overrides are also lowered to ``na`` by the caller.
        """
        if resolving is None:
            resolving = set()

        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "input" and func_name == "int":
                return node, tuple(helper_binding_stack or ())
            return None

        if not isinstance(node, Identifier) or id(node) in resolving:
            return None

        binding = None
        if not self._security_identifier_is_global_binding(node):
            binding = self._security_lookup_helper_binding_context(
                node.name, helper_binding_stack
            )
        if binding is not None:
            bound, bound_stack = binding
            if isinstance(bound, str):
                return None
            return self._resolve_security_immutable_input_int(
                bound,
                bound_stack,
                resolving | {id(node)},
            )

        param_binding = self._security_index_param_callsite_binding(node)
        if param_binding is not None:
            return self._resolve_security_immutable_input_int(
                param_binding,
                (),
                resolving | {id(node)},
            )

        # Mutable globals have requested-context state of their own and cannot
        # be replaced by their authored initializer without changing Pine
        # semantics.  Reject them before following global_expr_map.
        if node.name in getattr(self, "_global_mutable_infos", {}):
            return None

        if not self._security_identifier_is_global_binding(node):
            return None

        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        if node.name not in global_expr_map:
            return None
        return self._resolve_security_immutable_input_int(
            global_expr_map[node.name],
            (),
            resolving | {id(node)},
        )

    _SECURITY_STABLE_INT_MATH = frozenset({"round", "floor", "ceil"})
    _SECURITY_STABLE_SAME_MATH = frozenset({"abs", "max", "min"})
    _SECURITY_STABLE_FLOAT_MATH = frozenset({
        "sqrt", "pow", "log", "log10", "exp", "avg", "sin", "cos", "tan",
        "asin", "acos", "atan", "todegrees", "toradians", "round_to_mintick",
        "sign",
    })

    def _security_stable_value_type(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        depth: int = 0,
    ) -> str | None:
        """Pine type (``int``/``float``/``bool``/``string``) of a bar-invariant
        request.security expression, else None.

        Admits literals, ``input.*`` values, ``timeframe.multiplier``, and
        math, casts, arithmetic, comparisons and ternaries over those, reached
        through helper parameters and immutable globals by their lexical
        binding -- a block local or loop variable sharing a global's name is
        not the global. Mutable globals (``var``, reassigned) and the
        containing function's parameters stay out, as in
        ``_resolve_security_immutable_input_int``.
        """
        if node is None or depth > 64:
            return None
        nxt = depth + 1
        if isinstance(node, NumberLiteral):
            if isinstance(node.value, bool):
                return None
            return "float" if isinstance(node.value, float) else "int"
        if isinstance(node, BoolLiteral):
            return "bool"
        if isinstance(node, StringLiteral):
            return "string"
        if isinstance(node, Identifier):
            if not self._security_identifier_is_global_binding(node):
                binding = self._security_lookup_helper_binding_context(
                    node.name, helper_binding_stack
                )
                if binding is None or isinstance(binding[0], str):
                    return None
                return self._security_stable_value_type(binding[0], binding[1], nxt)
            if node.name in self._global_mutable_infos:
                return None
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if node.name not in global_expr_map:
                return None
            return self._security_stable_value_type(global_expr_map[node.name], (), nxt)
        if isinstance(node, MemberAccess):
            if (isinstance(node.object, Identifier)
                    and node.object.name == "timeframe"
                    and node.member == "multiplier"):
                return "int"
            return None
        if isinstance(node, UnaryOp):
            operand = self._security_stable_value_type(node.operand, helper_binding_stack, nxt)
            if node.op == "not":
                return "bool" if operand == "bool" else None
            return operand if operand in ("int", "float") else None
        if isinstance(node, BinOp):
            left = self._security_stable_value_type(node.left, helper_binding_stack, nxt)
            right = self._security_stable_value_type(node.right, helper_binding_stack, nxt)
            if left is None or right is None:
                return None
            if node.op in ("and", "or"):
                return "bool" if left == right == "bool" else None
            if node.op in ("==", "!=", "<", ">", "<=", ">="):
                return "bool"
            numeric = {left, right} <= {"int", "float"}
            if node.op in ("+", "-", "*", "%") and numeric:
                return "int" if left == right == "int" else "float"
            if node.op == "/" and numeric:
                return "float"
            return None
        if isinstance(node, Ternary):
            if self._security_stable_value_type(
                node.condition, helper_binding_stack, nxt
            ) != "bool":
                return None
            arms = {
                self._security_stable_value_type(node.true_val, helper_binding_stack, nxt),
                self._security_stable_value_type(node.false_val, helper_binding_stack, nxt),
            }
            if None in arms:
                return None
            if len(arms) == 1:
                return arms.pop()
            return "float" if arms <= {"int", "float"} else None
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "input":
                return {
                    "int": "int", "float": "float", "bool": "bool",
                    "string": "string", "timeframe": "string",
                }.get(func_name)
            args = [
                self._security_stable_value_type(arg, helper_binding_stack, nxt)
                for arg in node.args
            ]
            if node.kwargs or not args or None in args:
                return None
            if namespace is None and func_name in ("int", "float"):
                return func_name if args[0] in ("int", "float") else None
            if namespace == "math" and set(args) <= {"int", "float"}:
                if func_name in self._SECURITY_STABLE_INT_MATH:
                    # ``math.round(x, precision)`` is a float.
                    return "int" if len(args) == 1 else "float"
                if func_name in self._SECURITY_STABLE_SAME_MATH:
                    return "int" if set(args) == {"int"} else "float"
                if func_name in self._SECURITY_STABLE_FLOAT_MATH:
                    return "float"
            return None
        return None

    def _compose_security_helper_history_subscript(
        self,
        bound,
        local_index,
        source_node,
    ) -> Subscript:
        """Apply helper-local history to a supported bound bar series.

        The security runtime currently has native requested-context history
        storage only for direct OHLC/time bar fields.  Keep that support fence
        explicit instead of emitting a C++ subscript on a scalar expression
        for bindings such as ``hl2`` or ``ta.ema(...)``.
        """
        if isinstance(bound, Subscript):
            if not (
                isinstance(bound.object, Identifier)
                and self._security_bar_history_field(bound.object) is not None
            ):
                self._codegen_error(
                    source_node,
                    "request.security helper-parameter history currently requires "
                    "a direct OHLC/time bar-series binding",
                )
            bound_idx = self._literal_int_for_security_index(bound.index)
            local_idx = self._literal_int_for_security_index(local_index)
            if bound_idx is not None and local_idx is not None:
                combined_index = NumberLiteral(value=bound_idx + local_idx)
            else:
                combined_index = BinOp(
                    left=bound.index,
                    op="+",
                    right=local_index,
                )
            return Subscript(object=bound.object, index=combined_index)
        if (
            isinstance(bound, Identifier)
            and self._security_bar_history_field(bound) is not None
        ):
            return Subscript(object=bound, index=local_index)
        self._codegen_error(
            source_node,
            "request.security helper-parameter history currently requires "
            "a direct OHLC/time bar-series binding",
        )

    def _collect_security_ohlc_hist_fields(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        resolving: set[str] | None = None,
    ) -> set[str]:
        """Which requested-context bar fields need completed-bar history.

        This is the declaration prepass for ``_build_security_expr``.  It must
        follow the same helper-argument bindings and offset composition as the
        emitter; otherwise ``f(close)`` plus ``src[1]`` can emit a history read
        without declaring, pushing, or clearing its backing Series.
        """
        out: set[str] = set()
        if resolving is None:
            resolving = set()

        def walk(n, bindings) -> None:
            if n is None or isinstance(n, str):
                return

            if isinstance(n, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(n):
                    binding = self._security_lookup_helper_binding_context(
                        n.name, bindings
                    )
                if binding is not None:
                    bound, bound_stack = binding
                    if not isinstance(bound, str):
                        key = f"bind:{id(bound)}"
                        if key not in resolving:
                            resolving.add(key)
                            walk(bound, bound_stack)
                            resolving.remove(key)
                    return
                mutable_info = self._global_mutable_infos.get(n.name)
                if mutable_info is not None and n.name not in resolving:
                    resolving.add(n.name)
                    for stmt in getattr(mutable_info, "source_stmts", []) or []:
                        walk(stmt, bindings)
                    resolving.remove(n.name)
                    return
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (
                    self._security_identifier_is_global_binding(n)
                    and n.name in global_expr_map
                    and n.name not in resolving
                ):
                    resolving.add(n.name)
                    walk(global_expr_map[n.name], ())
                    resolving.remove(n.name)
                    return

            if isinstance(n, Subscript) and isinstance(n.object, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(n.object):
                    binding = self._security_lookup_helper_binding_context(
                        n.object.name, bindings
                    )
                if binding is not None:
                    bound, bound_stack = binding
                    if not isinstance(bound, str):
                        local_index = n.index
                        resolved_local_index = (
                            None
                            if self._security_index_reads_helper_local(n.index, bindings)
                            else self._resolve_security_index_literal(n.index, bindings)
                        )
                        if resolved_local_index is not None:
                            local_index = NumberLiteral(
                                value=resolved_local_index
                            )
                        else:
                            # Lowered in this helper's scope by the emitter
                            # (``__pf_security_index_``): a dynamic read of the
                            # bound series, never resolved in the caller's.
                            local_index = Identifier(name="__pf_security_index_dynamic")
                        walk(
                            self._compose_security_helper_history_subscript(
                                bound,
                                local_index,
                                n,
                            ),
                            bound_stack,
                        )
                    return
                field = self._security_bar_history_field(n.object)
                if field is not None:
                    idx = self._resolve_security_index_literal(n.index, bindings)
                    # field[0] uses the current requested bar; k>=1 reads the
                    # completed-bar Series. Dynamic indices need that Series too.
                    if idx is None or idx >= 1:
                        out.add(field)
                    return
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (
                    self._security_identifier_is_global_binding(n.object)
                    and n.object.name in global_expr_map
                    and n.object.name not in resolving
                ):
                    resolving.add(n.object.name)
                    walk(
                        Subscript(
                            object=global_expr_map[n.object.name],
                            index=n.index,
                        ),
                        bindings,
                    )
                    resolving.remove(n.object.name)
                    return

            if isinstance(n, FuncCall):
                func_name = self._security_user_call_key(n)
                if func_name is not None:
                    if self._security_shared_call_key(n, bindings) is not None:
                        return
                    call_key = f"func:{func_name}"
                    if call_key in resolving:
                        return
                    resolving.add(call_key)
                    plan = self._security_helper_call_plan(n, bindings)
                    if plan["mode"] == "expr":
                        walk(plan["expr"], plan["binding_stack"])
                    else:
                        local_series_names = set(plan.get("local_series_names", ()))
                        active: dict[str, object] = {}

                        def walk_stmt(stmt, current: dict[str, object]) -> None:
                            local_stack = plan["binding_stack"] + (current,)
                            if isinstance(stmt, VarDecl):
                                if stmt.value is not None:
                                    walk(stmt.value, local_stack)
                                if stmt.name in local_series_names or stmt.is_var:
                                    current[stmt.name] = self._security_series_binding(
                                        f"{plan['func_info'].name}:{stmt.name}"
                                    )
                                elif stmt.value is not None:
                                    current[stmt.name] = stmt.value
                                return
                            if isinstance(stmt, Assignment):
                                if stmt.value is not None:
                                    walk(stmt.value, local_stack)
                                target = self._get_target_name(stmt.target)
                                existing = current.get(target) if target is not None else None
                                if (
                                    target in local_series_names
                                    or (
                                        isinstance(existing, str)
                                        and self._security_series_binding_target(existing)
                                        is not None
                                    )
                                ):
                                    current[target] = self._security_series_binding(
                                        f"{plan['func_info'].name}:{target}"
                                    )
                                elif target is not None and stmt.value is not None:
                                    current[target] = stmt.value
                                return
                            if isinstance(stmt, IfStmt):
                                walk(stmt.condition, local_stack)
                                body_bindings = dict(current)
                                for child in stmt.body:
                                    walk_stmt(child, body_bindings)
                                else_bindings = dict(current)
                                for child in stmt.else_body:
                                    walk_stmt(child, else_bindings)
                                return
                            if isinstance(stmt, TupleAssign):
                                walk(stmt.value, local_stack)
                                for name in stmt.names:
                                    if name == "_":
                                        continue
                                    current[name] = (
                                        self._security_series_binding(
                                            f"{plan['func_info'].name}:{name}"
                                        )
                                        if name in local_series_names
                                        else _security_tuple_binding(
                                            plan["func_info"].name, name
                                        )
                                    )
                                return
                            if isinstance(stmt, (ForStmt, WhileStmt)):
                                header, counter = self._security_loop_parts(stmt)
                                for part in header:
                                    if part is not None:
                                        walk(part, local_stack)
                                body_bindings = dict(current)
                                if counter:
                                    body_bindings[counter] = (
                                        self._security_loop_counter_binding(plan, counter))
                                for child in stmt.body:
                                    walk_stmt(child, body_bindings)
                                return
                            if isinstance(stmt, ExprStmt):
                                walk(stmt.expr, local_stack)

                        for stmt in plan["body"]:
                            walk_stmt(stmt, active)
                        walk(
                            plan["expr"],
                            plan["binding_stack"] + (active,),
                        )
                    resolving.remove(call_key)
                    return

            if isinstance(n, (list, tuple)):
                for child in n:
                    walk(child, bindings)
                return
            for value in getattr(n, "__dict__", {}).values():
                if isinstance(value, ASTNode):
                    walk(value, bindings)
                elif isinstance(value, (list, tuple)):
                    for child in value:
                        if isinstance(child, ASTNode):
                            walk(child, bindings)

        walk(node, helper_binding_stack or ())
        return out

    def _collect_security_ohlc_hist_fields_for_call(self, item: dict) -> set[str]:
        """Collect HTF OHLC history needed by a security expression and any
        mutable-global rebinds replayed inside that security evaluator."""
        fields = self._collect_security_ohlc_hist_fields(item.get("expr_node"))
        for name in item.get("mutable_globals", []) or []:
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            for stmt in getattr(info, "source_stmts", []) or []:
                fields |= self._collect_security_ohlc_hist_fields(stmt)
        return fields

    def _security_ohlc_hist_series_cpp(self, sec_id: int, field: str) -> str:
        return f"_sec{sec_id}_hist_{field}"

    def _security_bar_hist_type(self, field: str) -> str:
        return SECURITY_BAR_FIELD_TYPES.get(field, "double")

    def _security_call_for_request(self, node) -> dict | None:
        """The ``request.security`` site ``node`` registered, as the value
        read (``visit_call``) finds it: by its expression, and among the
        analyzer's call-site clones by the call site being emitted."""
        payload = node.args[2] if len(node.args) > 2 else node.kwargs.get("expression")
        candidates = [item for item in self._security_calls
                      if not item.get("is_lower_tf_array") and payload is not None
                      and item["expr_node"] is payload]
        if len(candidates) > 1:
            return next((c for c in candidates
                         if c.get("callsite_idx") == self._active_call_site_idx),
                        candidates[0])
        return candidates[0] if candidates else None

    # The stored result struct of a request whose payload is a TA tuple.
    def _security_helper_request_struct(self, func_node) -> str | None:
        """The C++ result struct a helper returns when its value is a
        ``request.security`` of a TA tuple (``htf() => request.security(t,
        "D", ta.macd(close, 12, 26, 9))``): the request stores that struct
        (``_req_sec_N``), which the helper returned as a ``double`` or a
        ``std::tuple`` that cannot hold it, so ``[m, s, h] = htf()`` did not
        compile. None for any other helper."""
        body = getattr(func_node, "body", None) or []
        if not body:
            return None
        terminal = body[-1].expr if isinstance(body[-1], ExprStmt) else body[-1]
        if not (isinstance(terminal, FuncCall)
                and self._resolve_callee(terminal.callee) == ("security", "request")):
            return None
        item = self._security_call_for_request(terminal)
        if item is None or not item.get("returns_tuple"):
            return None
        payload = item.get("expr_node")
        if not isinstance(payload, FuncCall):
            return None
        site = self._get_ta_site(payload)
        if site is None or not getattr(site, "returns_tuple", False):
            return None
        # The chart's result type of the same TA call (``TA_TUPLE_RESULT_TYPES``).
        return self._ta_return_type(site)

    def _request_data_missing(self, ref) -> str:
        """C++ that is true when the pinned data of the request carrying
        ``ref`` (``external_requests.RequestRef``) is missing: another
        symbol's site the run did not register, or the recorded series of
        the key the request was last evaluated with."""
        requests = getattr(self, "_pf_request_refs", None)
        if requests is None:
            requests = self._pf_request_refs = {
                id(node.annotations[REQUEST_REF_ANNOTATION]): node
                for node in walk_request_nodes(self.ctx.ast)
                if isinstance(node, FuncCall)
                and REQUEST_REF_ANNOTATION in (node.annotations or {})}
        request = requests.get(id(ref))
        if request is not None and RECORDED_KEY_ANNOTATION in (request.annotations or {}):
            return f"_pf_rec_missing_{self._recorded_site(request)}"
        item = self._security_call_for_request(request) if request is not None else None
        if item is None or not item.get("foreign"):
            return "true"
        return f"_pf_sec_missing_{item['sec_id']}"

    def _recorded_key_expr(self, request) -> str:
        """The run-time key of a recorded request: its constant parts around
        its symbol string (``fn|symbol|field|period|gaps_*|lookahead_*``)."""
        parts = request.annotations[RECORDED_KEY_ANNOTATION]
        tail = (f"|{parts['field']}|{parts['period']}|gaps_{parts['gaps']}"
                f"|lookahead_{parts['lookahead']}")
        return (f'(std::string("{parts["fn"]}|") + {self._visit_expr(request.args[0])} + '
                f'std::string("{tail}"))')

    def _recorded_sites(self) -> dict[int, int]:
        """Each recorded request's index N: ``_pf_recorded`` sets its
        ``_pf_rec_missing_N`` where the request is evaluated, from the key
        computed there, and its reads test that flag."""
        sites = getattr(self, "_pf_recorded_site_ids", None)
        if sites is None:
            sites = self._pf_recorded_site_ids = {
                id(node): n for n, node in enumerate(
                    node for node in walk_request_nodes(self.ctx.ast)
                    if isinstance(node, FuncCall)
                    and RECORDED_KEY_ANNOTATION in (node.annotations or {}))}
        return sites

    def _recorded_site(self, request) -> int:
        return self._recorded_sites()[id(request)]

    def _uses_recorded_requests(self) -> bool:
        return bool(self._recorded_sites())

    def _security_footprint_column(self, sec_id: int) -> str | None:
        """The feed column another symbol's site reads when its whole
        expression is ``request.footprint(...)`` (``fp_delta_100_70``)."""
        if not self._security_foreign(sec_id):
            return None
        item = next((i for i in self._security_calls if i["sec_id"] == sec_id), None)
        payload = item.get("expr_node") if item is not None else None
        return (getattr(payload, "annotations", None) or {}).get(FOOTPRINT_COLUMN_ANNOTATION)

    def _security_foreign(self, sec_id: int | None) -> bool:
        """The site reads another symbol's feed: its bars close when the
        feed says they do, and the host answers for its context."""
        return (sec_id is not None and 0 <= sec_id < len(self._security_eval_info)
                and bool(self._security_eval_info[sec_id].get("foreign")))

    def _security_bar_field_expr(self, field: str, sec_id: int | None = None) -> str:
        if field == "time_close" and sec_id is not None:
            # The requested bar's close on the requested timeframe, as the
            # chart's ``time_close()`` reads its own bar on the chart's.
            chart = (
                "pine_time_close(bar.timestamp, "
                f"{self._security_timeframe_expr(sec_id)}, "
                "syminfo_.session, syminfo_.timezone, script_tf_)"
            )
            if self._security_foreign(sec_id):
                # Another symbol's bar closes when its feed says it does: the
                # host's time_close() while that symbol's payload runs (a
                # generated time_close member would shadow the plain name).
                # A symbol string equal to the chart's registers on the chart.
                return ("(foreign_context_ != nullptr ? "
                        f"pineforge::source::PineStrategyHost::time_close() : {chart})")
            return chart
        for call, source_field in self._security_source_hist_fields.values():
            if source_field == field:
                return self._security_source_input_expr(call)
        return SECURITY_BAR_FIELD_EXPRS.get(field, f"bar.{field}")

    def _security_source_input_call(self, node, seen: frozenset = frozenset()):
        """The ``input.source(<native series>)`` call (or bare ``input(close)``)
        ``node`` is, or a global name bound to one reads, else None."""
        if isinstance(node, FuncCall):
            default = self._get_input_default(node) if self._is_source_input(node) else None
            if isinstance(default, Identifier) and default.name in self._NATIVE_SOURCE_SERIES:
                return node
            return None
        if (
            isinstance(node, Identifier)
            and node.name not in seen
            and self._security_identifier_is_global_binding(node)
            and node.name not in self._global_mutable_infos
        ):
            value = (getattr(self.ctx, "global_expr_map", {}) or {}).get(node.name)
            if value is not None:
                return self._security_source_input_call(value, seen | {node.name})
        return None

    def _security_source_input_expr(self, call: FuncCall) -> str:
        """The requested bar's value of the series a source input selects.

        TradingView evaluates the input in the requested context like any
        series; its override picks another native series there too. The
        engine's ``get_input_source`` resolves the override (or the default)
        to one of the chart's source series, the one whose requested-bar
        value is read here."""
        default = self._get_input_default(call).name
        selected = (
            f"&get_input_source({self._input_key_literal(self._get_input_title(call))}, "
            f"_src_{default}_)"
        )
        arms = "".join(
            f"_pf_src == &_src_{name}_ ? {SECURITY_BAR_FIELD_EXPRS[name]} : "
            for name in sorted(self._NATIVE_SOURCE_SERIES) if name != default
        )
        return (
            f"([&]() -> double {{ const Series<double>* _pf_src = {selected}; "
            f"return {arms}{SECURITY_BAR_FIELD_EXPRS[default]}; }}())"
        )

    def _security_bar_history_field(self, node: Identifier) -> str | None:
        """The requested-bar series a payload's ``node[k]`` reads the history
        of: a bar field, or a source input's selected series (its own
        ``_sec<N>_hist_`` member, one per input); else None."""
        if node.name in SECURITY_BAR_FIELDS:
            return node.name
        call = self._security_source_input_call(node)
        if call is None:
            return None
        key = (self._get_input_title(call), self._get_input_default(call).name)
        if key not in self._security_source_hist_fields:
            self._security_source_hist_fields[key] = (
                call, f"input_source_{len(self._security_source_hist_fields)}"
            )
        return self._security_source_hist_fields[key][1]

    @staticmethod
    def _security_tuple_element_cpp_types(
        tuple_size: int,
        tuple_element_types: tuple[PineType, ...] = (),
    ) -> list[str]:
        """Per-element C++ storage of a helper tuple of arbitrary arity.

        A bool element keeps a real ``bool`` so true/false semantics survive
        the requested-context boundary, and a string element a
        ``std::string``. Every numeric element, and one whose type was not
        inferred, retains the established double-coercing representation.
        """
        if len(tuple_element_types) != tuple_size:
            return ["double"] * max(0, tuple_size)
        return [
            "bool" if item == PineType.BOOL
            else "std::string" if item == PineType.STRING
            else "double"
            for item in tuple_element_types
        ]

    @classmethod
    def _security_tuple_result_default(
        cls,
        cpp_type: str,
        tuple_size: int,
        tuple_element_types: tuple[PineType, ...] = (),
    ) -> str:
        # TradingView reads a bool element false and a numeric or string
        # element na before the first requested value and on a gaps_on bar
        # that completes none (tests/test_e2e_security_helper_tuple_elements.py).
        defaults = {
            "bool": "false",
            "std::string": "na<std::string>()",
        }
        vals = ", ".join(
            defaults.get(element, "na<double>()")
            for element in cls._security_tuple_element_cpp_types(
                tuple_size, tuple_element_types
            )
        )
        return f"{cpp_type}{{{vals}}}"

    @classmethod
    def _security_helper_tuple_cpp_type(
        cls,
        tuple_size: int,
        tuple_element_types: tuple[PineType, ...] = (),
    ) -> str:
        """C++ storage type for a supported helper tuple of arbitrary arity
        (``_security_tuple_element_cpp_types`` per element)."""
        return "std::tuple<" + ", ".join(
            cls._security_tuple_element_cpp_types(tuple_size, tuple_element_types)
        ) + ">"

    def _collect_security_ta_hist_indices(self, node) -> set[int]:
        """Which security TA call-site indices need HTF history (subscript index >= 1).

        ``request.security(..., ta.ema(close, 55)[1], ...)`` reads a *confirmed*
        HTF TA value at a past-bar offset. The inner TA call runs in the security
        (HTF) context and commits one value per COMPLETED HTF bar; offsets read a
        per-site ``Series`` filled (gated on ``is_complete``) in
        ``_eval_security_N``. Mirrors ``_collect_security_ohlc_hist_fields`` for
        OHLC offsets. Offset 0 reuses the current committed value (``_secval_*``)
        and needs no Series, so only index >= 1 registers here.  A dynamic
        index is registered conservatively; the expression emitter separately
        admits only an immutable ``input.int`` chain and rejects everything
        else.  This declaration prepass must expand helper/global bindings just
        like ``_build_security_expr`` or a valid helper-wrapped offset could
        emit a history read without storage, pushes, or reset."""
        out: set[int] = set()
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        resolving: set[str] = set()

        def resolve_ta_site(obj, bindings, seen: set[str] | None = None):
            """_get_ta_site only matches the literal ta.* FuncCall node by
            identity; fall back through helper/global bindings for an indirect
            binding (``v = ta.ema(close, 55)`` then ``...v[1]...``)."""
            site = self._get_ta_site(obj)
            if site is not None:
                return site
            if isinstance(obj, Identifier):
                if seen is None:
                    seen = set()
                if obj.name in seen:
                    return None
                binding = None
                if not self._security_identifier_is_global_binding(obj):
                    binding = self._security_lookup_helper_binding_context(
                        obj.name, bindings
                    )
                if binding is not None:
                    bound, bound_stack = binding
                    if not isinstance(bound, str):
                        return resolve_ta_site(
                            bound, bound_stack, seen | {obj.name}
                        )
                if (
                    self._security_identifier_is_global_binding(obj)
                    and obj.name in global_expr_map
                ):
                    return resolve_ta_site(
                        global_expr_map[obj.name],
                        (),
                        seen | {obj.name},
                    )
            return None

        def walk(n, bindings) -> None:
            if n is None or isinstance(n, str):
                return

            if isinstance(n, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(n):
                    binding = self._security_lookup_helper_binding_context(
                        n.name, bindings
                    )
                if binding is not None:
                    bound, bound_stack = binding
                    if not isinstance(bound, str):
                        key = f"bind:{id(bound)}"
                        if key not in resolving:
                            resolving.add(key)
                            walk(bound, bound_stack)
                            resolving.remove(key)
                    return
                mutable_info = self._global_mutable_infos.get(n.name)
                if mutable_info is not None and n.name not in resolving:
                    resolving.add(n.name)
                    for stmt in getattr(mutable_info, "source_stmts", []) or []:
                        walk(stmt, bindings)
                    resolving.remove(n.name)
                    return
                if (
                    self._security_identifier_is_global_binding(n)
                    and n.name in global_expr_map
                    and n.name not in resolving
                ):
                    resolving.add(n.name)
                    walk(global_expr_map[n.name], ())
                    resolving.remove(n.name)
                    return

            if isinstance(n, Subscript):
                site = resolve_ta_site(n.object, bindings)
                if site is not None:
                    idx_lit = self._resolve_security_index_literal(n.index, bindings)
                    if idx_lit is None or idx_lit >= 1:
                        site_idx = self._ta_index_by_site_id.get(id(site))
                        if site_idx is not None:
                            out.add(site_idx)

            if isinstance(n, FuncCall):
                func_name = self._security_user_call_key(n)
                if func_name is not None:
                    if self._security_shared_call_key(n, bindings) is not None:
                        return
                    call_key = f"func:{func_name}"
                    if call_key in resolving:
                        return
                    resolving.add(call_key)
                    plan = self._security_helper_call_plan(n, bindings)
                    if plan["mode"] == "expr":
                        walk(plan["expr"], plan["binding_stack"])
                    else:
                        local_series_names = set(
                            plan.get("local_series_names", ())
                        )
                        active: dict[str, object] = {}

                        def walk_stmt(stmt, current: dict[str, object]) -> None:
                            local_stack = plan["binding_stack"] + (current,)
                            if isinstance(stmt, VarDecl):
                                if stmt.value is not None:
                                    walk(stmt.value, local_stack)
                                if stmt.name in local_series_names or stmt.is_var:
                                    current[stmt.name] = self._security_series_binding(
                                        f"{plan['func_info'].name}:{stmt.name}"
                                    )
                                elif stmt.value is not None:
                                    current[stmt.name] = stmt.value
                                return
                            if isinstance(stmt, Assignment):
                                if stmt.value is not None:
                                    walk(stmt.value, local_stack)
                                target = self._get_target_name(stmt.target)
                                existing = current.get(target) if target else None
                                if (
                                    target in local_series_names
                                    or (
                                        isinstance(existing, str)
                                        and self._security_series_binding_target(
                                            existing
                                        )
                                        is not None
                                    )
                                ):
                                    current[target] = self._security_series_binding(
                                        f"{plan['func_info'].name}:{target}"
                                    )
                                elif target is not None and stmt.value is not None:
                                    current[target] = stmt.value
                                return
                            if isinstance(stmt, IfStmt):
                                walk(stmt.condition, local_stack)
                                body_bindings = dict(current)
                                for child in stmt.body:
                                    walk_stmt(child, body_bindings)
                                else_bindings = dict(current)
                                for child in stmt.else_body:
                                    walk_stmt(child, else_bindings)
                                return
                            if isinstance(stmt, TupleAssign):
                                walk(stmt.value, local_stack)
                                for name in stmt.names:
                                    if name == "_":
                                        continue
                                    current[name] = (
                                        self._security_series_binding(
                                            f"{plan['func_info'].name}:{name}"
                                        )
                                        if name in local_series_names
                                        else _security_tuple_binding(
                                            plan["func_info"].name, name
                                        )
                                    )
                                return
                            if isinstance(stmt, (ForStmt, WhileStmt)):
                                header, counter = self._security_loop_parts(stmt)
                                for part in header:
                                    if part is not None:
                                        walk(part, local_stack)
                                body_bindings = dict(current)
                                if counter:
                                    body_bindings[counter] = (
                                        self._security_loop_counter_binding(plan, counter))
                                for child in stmt.body:
                                    walk_stmt(child, body_bindings)
                                return
                            if isinstance(stmt, ExprStmt):
                                walk(stmt.expr, local_stack)

                        for stmt in plan["body"]:
                            walk_stmt(stmt, active)
                        walk(plan["expr"], plan["binding_stack"] + (active,))
                    resolving.remove(call_key)
                    return

            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x, bindings)
                return
            for _k, v in getattr(n, "__dict__", {}).items():
                if isinstance(v, ASTNode):
                    walk(v, bindings)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, ASTNode):
                            walk(x, bindings)

        walk(node, ())
        return out

    def _security_ta_hist_series_cpp(self, member_name: str) -> str:
        """Per-(sec, site) ``Series<double>`` backing ``ta.<fn>(...)[k>=1]`` HTF history."""
        return f"{member_name}_hist"

    def _security_ta_hist_series_names(self, sec_id: int) -> list[str]:
        """Hist Series names for every security TA site (and variant) read at an
        offset >= 1 in sec ``sec_id``."""
        info = self._security_eval_info[sec_id]
        names: list[str] = []
        for idx in sorted(self._security_ta_hist_idx_by_sec.get(sec_id, ())):
            for variant in (info.get("ta_variants") or {}).get(idx, []):
                names.append(self._security_ta_hist_series_cpp(variant["member_name"]))
        return names

    def _collect_security_expr_hist_subscripts(
        self, node, resolving: set[str] | None = None
    ) -> list[Subscript]:
        """Subscripted helper-call results needing security-context history:
        in the payload, the globals it reads, and the bodies of the user
        functions it calls (``h() => nz(g()[1])``). One inside a helper body
        is kept only where the payload reaches it once: two inlines of it
        (``h() + h()``) would each need their own history, and are refused
        where lowered, as every earlier build refused them."""
        if node is None:
            return []
        if resolving is None:
            resolving = set()

        out: list[Subscript] = []
        seen: set[int] = set()
        in_helper: set[int] = set()
        reached: dict[int, int] = {}
        helpers: list[str] = []

        def add(n: Subscript) -> None:
            key = id(n)
            if helpers:
                in_helper.add(key)
                reached[key] = reached.get(key, 0) + 1
            if key not in seen:
                seen.add(key)
                out.append(n)

        def walk(n) -> None:
            if n is None:
                return
            if isinstance(n, Identifier):
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if n.name in global_expr_map and n.name not in resolving:
                    resolving.add(n.name)
                    walk(global_expr_map[n.name])
                    resolving.remove(n.name)
                return
            if (isinstance(n, FuncCall) and isinstance(n.callee, Identifier)
                    and n.callee.name in self._func_names
                    and n.callee.name not in helpers):
                info = self._func_info_map.get(n.callee.name)
                if (info is not None and getattr(info, "node", None) is not None
                        and not self._security_pure_body(info.node)):
                    helpers.append(n.callee.name)
                    walk(info.node.body)
                    helpers.pop()
            if (
                isinstance(n, Subscript)
                and (
                    (isinstance(n.object, FuncCall)
                     and self._get_ta_site(n.object) is None)
                    or self._is_compound_history_object(n.object)
                    or self._security_global_history_value(n) is not None
                )
            ):
                add(n)
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x)
                return
            for _k, v in getattr(n, "__dict__", {}).items():
                if isinstance(v, ASTNode):
                    walk(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, ASTNode):
                            walk(x)

        walk(node)
        return [n for n in out if id(n) not in in_helper or reached[id(n)] == 1]

    def _security_global_history_value(self, node) -> ASTNode | None:
        """The value of ``g`` in a payload's ``g[k]`` when ``g`` is a global
        bound to a user function call or an operator expression, else None.

        TradingView evaluates ``g`` on every requested bar and ``g[k]`` reads
        it ``k`` requested bars back. Such a value has no series on the
        requested clock, so the payload keeps one, like an inline call's
        (``_collect_security_expr_hist_subscripts``), pushed with what the
        payload reads as ``g`` on each completed requested bar. The builder
        used to put the global's value under a subscript of its own, which no
        prepass had sized: a user call was refused ("helper call history is
        only supported in the payload itself") and an operator expression
        was indexed as a C++ scalar, which did not compile."""
        if not (isinstance(node, Subscript) and isinstance(node.object, Identifier)):
            return None
        name = node.object.name
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        if (
            not self._security_identifier_is_global_binding(node.object)
            or name not in global_expr_map
            or name in self._direct_program_tuple_binding_names
            or name in self._global_mutable_infos
        ):
            return None
        value = global_expr_map[name]
        if (isinstance(value, FuncCall) and isinstance(value.callee, Identifier)
                and self._security_user_call_key(value) is not None):
            return value
        if isinstance(value, (BinOp, UnaryOp, Ternary)) and self._is_compound_history_object(value):
            return value
        return None

    def _security_reads_helper_binding(self, node, helper_binding_stack) -> bool:
        """Whether ``node`` reads a name a helper binds on ``helper_binding_stack``
        (its parameter or local)."""
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, Identifier):
                if (not self._security_identifier_is_global_binding(n)
                        and self._security_lookup_helper_binding_context(
                            n.name, helper_binding_stack) is not None):
                    return True
                continue
            if isinstance(n, ASTNode):
                stack.extend(v for k, v in vars(n).items()
                             if k not in ("annotations", "loc") and isinstance(v, ASTNode))
                stack.extend(x for v in vars(n).values() if isinstance(v, (list, tuple))
                             for x in v if isinstance(x, ASTNode))
        return False

    def _security_nested_heikinashi_request(self, node) -> bool:
        """Whether ``node``, met while lowering a request's payload, is a
        ``request.security`` of Heikin-Ashi bars: a ``ticker.heikinashi(...)``
        symbol, written in the call or held by a global."""
        if not isinstance(node, FuncCall):
            return False
        if self._resolve_callee(node.callee) != ("security", "request"):
            return False
        symbol = node.args[0] if node.args else node.kwargs.get("symbol")
        if isinstance(symbol, Identifier) and self._security_identifier_is_global_binding(symbol):
            symbol = (getattr(self.ctx, "global_expr_map", {}) or {}).get(symbol.name, symbol)
        return (isinstance(symbol, FuncCall)
                and self._resolve_callee(symbol.callee) == ("heikinashi", "ticker"))

    def _security_reads_global_history(self, sec_id: int, node) -> bool:
        """Whether ``node`` is a ``g[k]`` whose requested-clock history the
        evaluator of ``sec_id`` keeps (``_security_global_history_value``)."""
        return ((sec_id, id(node)) in self._security_expr_hist_by_node
                and self._security_global_history_value(node) is not None)

    def _security_expr_hist_series_names(self, sec_id: int) -> list[str]:
        names = []
        for (sid, _node_id), meta in sorted(self._security_expr_hist_by_node.items()):
            if sid == sec_id:
                names.append(meta["name"])
        return names

    def _emit_security_expr_hist_members(
        self, sec_id: int, expr_node, lines: list[str], mbb_suffix: str
    ) -> None:
        if self._security_reads_bar_index(expr_node):
            # The requested bar's ``bar_index``: one count per requested bar,
            # advanced where the evaluator opens its slot.
            self._security_bar_index_secs.add(sec_id)
            lines.append(f"    int {self._security_bar_index_member(sec_id)} = -1;")
        for idx, node in enumerate(self._collect_security_expr_hist_subscripts(expr_node)):
            # A session.* flag is a bool: its history reads false, not na,
            # before the first requested bar.
            cpp_t = ("bool" if self._is_session_flag(node.object)
                     else self._infer_type(node.object))
            if cpp_t not in ("double", "int", "bool"):
                cpp_t = "double"
            name = f"_sec{sec_id}_expr_hist_{idx}"
            self._security_expr_hist_by_node[(sec_id, id(node))] = {
                "name": name,
                "type": cpp_t,
            }
            lines.append(f"    Series<{cpp_t}> {name}{mbb_suffix};")

    def _build_security_math_call(
        self,
        sec_id: int,
        func_name: str,
        node: FuncCall,
        ta_range,
        ta_results: dict,
        resolving: set[str],
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
        emitted_lines: list[str] | None,
    ) -> str:
        visit = lambda arg: self._build_security_expr(
            sec_id,
            arg,
            ta_range,
            ta_results,
            resolving,
            security_mutable_names,
            helper_binding_stack,
            emitted_lines,
        )
        args = _merge_kwargs(
            node.args,
            node.kwargs,
            sigs.get_param_names("math", func_name),
            visit,
        )
        if func_name == "round" and len(args) == 2:
            return _math_round_digits_expr(args)
        if func_name == "round_to_mintick":
            x = args[0] if args else "0.0"
            return f"round_to_mintick({x})"
        if func_name == "todegrees":
            x = args[0] if args else "0.0"
            return f"({x} * 180.0 / M_PI)"
        if func_name == "toradians":
            x = args[0] if args else "0.0"
            return f"({x} * M_PI / 180.0)"
        if func_name == "random":
            # Its stream is keyed by the chart's bar_index_.
            self._security_note_chart_read(
                node, "the payload reads 'math.random' on the chart's bar"
            )
            lo = args[0] if len(args) > 0 else "0.0"
            hi = args[1] if len(args) > 1 else "1.0"
            seed = args[2] if len(args) > 2 else "0"
            call_site = self._random_call_counter
            self._random_call_counter += 1
            return f"pine_random({lo}, {call_site}u, {hi}, (uint32_t)({seed}), bar_index_)"
        if func_name == "avg" and len(args) > 2:
            sum_expr = " + ".join(f"(double)({a})" for a in args)
            return f"(({sum_expr}) / {len(args)}.0)"
        if func_name in ("min", "max"):
            return _math_minmax_na_expr(func_name, args)
        if func_name in MATH_FUNC_MAP:
            mapped = MATH_FUNC_MAP[func_name]
            if "{0}" in mapped:
                return mapped.format(*args)
            return f"{mapped}({', '.join(args)})"
        return f"0.0 /* unsupported: math.{func_name} */"

    def _security_timeframe_expr(self, sec_id: int) -> str:
        """C++ expression for the timeframe of a request.security evaluator."""
        info = self._security_eval_info[sec_id]
        if info.get("tf"):
            return f'"{info["tf"]}"'
        if info.get("tf_expr"):
            return info["tf_expr"]
        return "input_tf_"

    def _build_security_timeframe_member(self, sec_id: int, member: str) -> str | None:
        """Lower timeframe.* reads inside request.security to the requested TF."""
        tf = self._security_timeframe_expr(sec_id)
        if member == "period":
            return tf
        if member == "main_period":
            return "main_period()"
        if member == "multiplier":
            return f"tf_multiplier({tf})"
        if member == "isintraday":
            return f"tf_is_intraday({tf})"
        if member == "isminutes":
            return f"(tf_is_intraday({tf}) && !tf_is_seconds({tf}))"
        if member == "isdaily":
            return f"tf_is_daily({tf})"
        if member == "isweekly":
            return f"tf_is_weekly({tf})"
        if member == "ismonthly":
            return f"tf_is_monthly({tf})"
        if member == "isdwm":
            return f"(tf_is_daily({tf}) || tf_is_weekly({tf}) || tf_is_monthly({tf}))"
        if member == "isseconds":
            return f"tf_is_seconds({tf})"
        if member == "in_seconds":
            return f"tf_to_seconds({tf})"
        if member == "isticks":
            return "false"
        return None

    def _security_local_value(
        self,
        expr_cpp: str,
        value,
        cpp_type: str | None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
    ) -> str:
        """``expr_cpp`` as stored in an evaluator local of ``cpp_type``:
        ``na`` spelled for that type, and a value narrowed to ``int`` or
        ``bool`` without losing ``na`` (quirk 9), as the chart declares its
        locals -- ``na<double>()`` in a ``bool`` is true, in an ``int``
        undefined. The value is judged as the evaluator renders it: a helper
        series or ``var`` state is a double whatever the chart's type."""
        if cpp_type not in _SECURITY_SCALAR_CPP:
            return expr_cpp
        if value is None or isinstance(value, NaLiteral):
            return f"na<{cpp_type}>()"
        if cpp_type in ("int", "int64_t", "bool"):
            return self._coerce_int_slot(
                expr_cpp, value, cpp_type,
                value_is_double=self._security_emits_double(value, helper_binding_stack),
            )
        return expr_cpp

    def _security_helper_series_ref(self, series_name: str) -> str:
        """The map entry holding one helper series (string values in their
        own map, every other scalar in the historical double map)."""
        store = (
            "_security_helper_series_str_"
            if series_name in self._security_string_series
            else "_security_helper_series_"
        )
        return f'{store}["{series_name}"]'

    def _security_helper_var_state_type(self, stmt: VarDecl) -> str:
        """The type family of a helper ``var`` whose declaration reads
        ``int64_t``. Its state is a double series (``_security_helper_series_``),
        which holds a 64-bit integer arithmetic value exactly, so a width only
        such arithmetic gives -- the ``var``'s own or a same-spelled name's in
        another callable, since ``_wide_int_provenance`` is keyed by spelling
        (``g() => n = days * 86400000`` beside a helper's ``var int n``) --
        keeps the ``int`` family it compiled with (the width of constants does
        not reach helper state either: ``_literal_wide_global``). Only an
        epoch reaching it (the lane's epoch-only reading) reads ``int64_t``,
        which stays refused as it always was. The flag is set directly, not
        through ``_int_width_scan``, whose depth counter would detach the
        emitting callable's names (``_narrow_int_name_info``) that an
        unhinted ``var``'s ``_infer_type`` can still read."""
        saved = getattr(self, "_wide_int_epoch_only", False)
        self._wide_int_epoch_only = True
        try:
            return self._type_for_decl(stmt)
        finally:
            self._wide_int_epoch_only = saved

    def _security_store_string_series(self, node, series_name: str) -> None:
        """Keep a string helper series (and its ``var`` seed) in the string
        map, which ``_security_needs_string_series`` declared."""
        if not self._security_string_series_declared:
            self._codegen_error(
                node,
                "Internal: request.security string helper state without its "
                "declared series map",
            )
        self._security_string_series.update(
            (series_name, f"{series_name}@var_seed")
        )

    def _security_needs_string_series(self) -> bool:
        """Whether a helper a payload reaches holds string state: a ``var``
        string or a string local read with history. Decided before the
        members are declared, so the string map is emitted only then."""
        seen: set[str] = set()
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}

        def is_string_decl(stmt: VarDecl) -> bool:
            # The emitter's own rule (``_type_for_decl``), read in the same
            # class-scope context it runs in.
            try:
                return self._type_for_decl(stmt) == "std::string"
            except Exception:
                return stmt.type_hint == "string" or isinstance(stmt.value, StringLiteral)

        def nodes(node):
            if isinstance(node, ASTNode):
                yield node
                for name, value in vars(node).items():
                    if name != "annotations":
                        yield from nodes(value)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    yield from nodes(item)
            elif isinstance(node, dict):
                for item in node.values():
                    yield from nodes(item)

        def scan_expr(node) -> bool:
            for child in nodes(node):
                if isinstance(child, Identifier):
                    # A payload re-evaluates the globals it reads (and the
                    # statements rebinding a mutable one) on the requested bar.
                    name_key = f"global:{child.name}"
                    if name_key in seen:
                        continue
                    seen.add(name_key)
                    info = self._global_mutable_infos.get(child.name)
                    if info is not None:
                        if any(scan_expr(stmt) for stmt in
                               getattr(info, "source_stmts", []) or []):
                            return True
                    elif child.name in global_expr_map and scan_expr(
                            global_expr_map[child.name]):
                        return True
                    continue
                key = self._security_user_call_key(child)
                if key is None or key in seen:
                    continue
                seen.add(key)
                info = self._func_info_map.get(key)
                if info is None or info.node is None:
                    continue
                series = set(self.ctx.func_series_vars.get(info.name, set()))
                if scan_body(info.node.body, series):
                    return True
            return False

        def scan_body(body, series: set[str]) -> bool:
            for stmt in body or []:
                if isinstance(stmt, VarDecl):
                    if ((stmt.is_var or stmt.name in series)
                            and is_string_decl(stmt)):
                        return True
                    if scan_expr(stmt.value):
                        return True
                elif isinstance(stmt, IfStmt):
                    if (scan_expr(stmt.condition)
                            or scan_body(stmt.body, series)
                            or scan_body(stmt.else_body, series)):
                        return True
                elif scan_expr(stmt):
                    return True
            return False

        return any(
            scan_expr(item.get("expr_node")) for item in self._security_calls
        )

    @staticmethod
    def _security_series_binding(series_name: str) -> str:
        return f"@series:{series_name}"

    @staticmethod
    def _security_series_binding_target(binding: str) -> str | None:
        if isinstance(binding, str) and binding.startswith("@series:"):
            return binding[len("@series:") :]
        return None

    def _emit_security_linear_helper_call(
        self,
        sec_id: int,
        plan: dict,
        ta_results: dict,
        security_mutable_names: set[str],
        lines: list[str],
        resolving: set[str] | None = None,
    ) -> str:
        """Inline one linear helper against isolated lexical collection state."""
        saved = (
            self._current_func_collection_specs,
            self._current_func_collection_shadows,
            self._collection_types,
            self._array_vars,
            self._map_vars,
            self._matrix_specs,
        )
        self._current_func_collection_specs = {}
        self._current_func_collection_shadows = set()
        self._collection_types = dict(self._collection_types)
        self._array_vars = set(self._array_vars)
        self._map_vars = set(self._map_vars)
        self._matrix_specs = dict(self._matrix_specs)
        try:
            return self._emit_security_linear_helper_call_scoped(
                sec_id,
                plan,
                ta_results,
                security_mutable_names,
                lines,
                resolving,
            )
        finally:
            (
                self._current_func_collection_specs,
                self._current_func_collection_shadows,
                self._collection_types,
                self._array_vars,
                self._map_vars,
                self._matrix_specs,
            ) = saved

    def _emit_security_linear_helper_call_scoped(
        self,
        sec_id: int,
        plan: dict,
        ta_results: dict,
        security_mutable_names: set[str],
        lines: list[str],
        resolving: set[str] | None = None,
    ) -> str:
        if plan["mode"] != "linear":
            self._codegen_error(
                plan["func_info"].node,
                "Internal security helper emission requested for a non-linear helper plan",
            )
        local_cpp_bindings: dict[str, str] = {}
        runtime_stack = plan["binding_stack"] + (local_cpp_bindings,)
        local_series_names = set(plan.get("local_series_names", ()))

        def _series_expr(binding_name: str, index_expr: str) -> str:
            return f"{self._security_helper_series_ref(binding_name)}[{index_expr}]"

        def emit_series_value(series_name: str, expr_cpp: str, pad: str) -> None:
            """One requested bar's value of a helper local read with history:
            pushed on a new requested bar, rewritten on a recomputation."""
            ref = self._security_helper_series_ref(series_name)
            lines.append(f'{pad}if ({ref}.size() == 0) {{')
            lines.append(f'{pad}    {ref}.push({expr_cpp});')
            lines.append(f'{pad}}} else if (security_series_slot_is_new({sec_id})) {{')
            lines.append(f'{pad}    {ref}.push({expr_cpp});')
            lines.append(f'{pad}}} else {{')
            lines.append(f'{pad}    {ref}.update({expr_cpp});')
            lines.append(f'{pad}}}')

        def emit_stmt(stmt: ASTNode, active_bindings: dict[str, str], indent: int) -> None:
            pad = "    " * indent
            runtime_stack_local = plan["binding_stack"] + (active_bindings,)

            def activate_decl() -> None:
                spec = self._callable_collection_bindings.get(id(stmt))
                if spec is None:
                    inferred = self._type_spec_from_expr(stmt.value)
                    if (inferred is not None
                            and inferred.kind in {"array", "map", "matrix"}):
                        spec = inferred
                self._activate_callable_collection_binding(stmt.name, spec)

            if isinstance(stmt, VarDecl):
                is_persistent_var = stmt.is_var
                if stmt.name in local_series_names or is_persistent_var:
                    binding = active_bindings.get(stmt.name)
                    if binding is None:
                        binding = self._security_series_binding(
                            self._security_next_inline_name(sec_id, plan["func_info"].name, stmt.name)
                        )
                    series_name = self._security_series_binding_target(binding)
                    assert series_name is not None
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    active_bindings[stmt.name] = binding
                    cpp_type = None
                    if is_persistent_var or self._security_string_series_declared:
                        cpp_type = self._type_for_decl(stmt)
                    if is_persistent_var and cpp_type == "int64_t":
                        cpp_type = self._security_helper_var_state_type(stmt)
                    if cpp_type == "std::string":
                        self._security_store_string_series(stmt, series_name)
                        if stmt.value is None or isinstance(stmt.value, NaLiteral):
                            expr_cpp = "na<std::string>()"
                    if is_persistent_var:
                        if cpp_type not in {"double", "int", "bool", "std::string"}:
                            self._codegen_error(
                                stmt,
                                "request.security helper-local var state currently supports only int, float, bool and string values",
                                hint="Hoist collection, UDT, or drawing state outside request.security().",
                            )
                        # A Pine ``var`` initializer runs once per helper call
                        # site in the requested context.  On a new requested
                        # bar the prior committed value is carried forward; on
                        # a lookahead/realtime recomputation of the current
                        # requested bar it rolls back before the helper body is
                        # evaluated again.  ``Series[1]`` is that rollback
                        # value after the first bar.  The companion seed entry
                        # preserves the once-only initializer for first-bar
                        # recomputations without adding another generated
                        # member/state family.
                        seed_name = f"{series_name}@var_seed"
                        ref = self._security_helper_series_ref(series_name)
                        seed_ref = self._security_helper_series_ref(seed_name)
                        lines.append(f'{pad}if ({ref}.size() == 0) {{')
                        lines.append(f'{pad}    {seed_ref}.push({expr_cpp});')
                        lines.append(f'{pad}    {ref}.push({seed_ref}[0]);')
                        lines.append(f'{pad}}} else if (security_series_slot_is_new({sec_id})) {{')
                        lines.append(f'{pad}    {ref}.push({ref}[0]);')
                        lines.append(f'{pad}}} else {{')
                        lines.append(
                            f'{pad}    {ref}.update({ref}.size() > 1 '
                            f'? {ref}[1] : {seed_ref}[0]);'
                        )
                        lines.append(f'{pad}}}')
                    else:
                        emit_series_value(series_name, expr_cpp, pad)
                    activate_decl()
                    return

                local_name = active_bindings.get(stmt.name)
                if local_name is None:
                    local_name = self._security_next_inline_name(
                        sec_id,
                        plan["func_info"].name,
                        stmt.name,
                    )
                    cpp_type = self._type_for_decl(stmt)
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    expr_cpp = self._security_local_value(
                        expr_cpp, stmt.value, cpp_type, runtime_stack_local
                    )
                    active_bindings[stmt.name] = local_name
                    self._security_local_cpp_types[local_name] = cpp_type
                    lines.append(f"{pad}{cpp_type} {local_name} = {expr_cpp};")
                else:
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    expr_cpp = self._security_local_value(
                        expr_cpp, stmt.value, self._security_local_cpp_types.get(local_name),
                        runtime_stack_local,
                    )
                    lines.append(f"{pad}{local_name} = {expr_cpp};")
                activate_decl()
                return

            if isinstance(stmt, Assignment):
                target_name = self._get_target_name(stmt.target)
                if target_name is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helpers may only assign to local identifier temporaries",
                    )
                binding = active_bindings.get(target_name)
                if binding is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helper assignment target must be declared before use",
                    )
                expr_cpp = self._build_security_expr(
                    sec_id,
                    stmt.value,
                    None,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    runtime_stack_local,
                    lines,
                )
                series_name = self._security_series_binding_target(binding)
                if series_name is not None:
                    if series_name in self._security_string_series and isinstance(
                            stmt.value, NaLiteral):
                        expr_cpp = "na<std::string>()"
                elif stmt.op == ":=":
                    expr_cpp = self._security_local_value(
                        expr_cpp, stmt.value, self._security_local_cpp_types.get(binding),
                        runtime_stack_local,
                    )
                if series_name is not None:
                    ref = self._security_helper_series_ref(series_name)
                    if stmt.op == ":=":
                        lines.append(f'{pad}{ref}.update({expr_cpp});')
                    else:
                        op_char = stmt.op[0]
                        lines.append(
                            f'{pad}{ref}.update('
                            f'{_series_expr(series_name, "0")} {op_char} {expr_cpp});'
                        )
                    return

                if stmt.op == ":=":
                    lines.append(f"{pad}{binding} = {expr_cpp};")
                else:
                    lines.append(f"{pad}{binding} {stmt.op} {expr_cpp};")
                return

            if isinstance(stmt, IfStmt):
                cond_cpp = self._build_security_expr(
                    sec_id,
                    stmt.condition,
                    None,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    runtime_stack_local,
                    lines,
                )
                lines.append(
                    f"{pad}if ({self._coerce_bool_expr(cond_cpp, stmt.condition)}) {{"
                )
                body_bindings = dict(active_bindings)
                body_state = (
                    self._current_func_collection_specs,
                    self._current_func_collection_shadows,
                    self._collection_types,
                    self._array_vars,
                    self._map_vars,
                    self._matrix_specs,
                )
                self._current_func_collection_specs = dict(
                    self._current_func_collection_specs
                )
                self._current_func_collection_shadows = set(
                    self._current_func_collection_shadows
                )
                self._collection_types = dict(self._collection_types)
                self._array_vars = set(self._array_vars)
                self._map_vars = set(self._map_vars)
                self._matrix_specs = dict(self._matrix_specs)
                try:
                    for child in stmt.body:
                        emit_stmt(child, body_bindings, indent + 1)
                finally:
                    (
                        self._current_func_collection_specs,
                        self._current_func_collection_shadows,
                        self._collection_types,
                        self._array_vars,
                        self._map_vars,
                        self._matrix_specs,
                    ) = body_state
                if stmt.else_body:
                    lines.append(f"{pad}}} else {{")
                    else_bindings = dict(active_bindings)
                    else_state = (
                        self._current_func_collection_specs,
                        self._current_func_collection_shadows,
                        self._collection_types,
                        self._array_vars,
                        self._map_vars,
                        self._matrix_specs,
                    )
                    self._current_func_collection_specs = dict(
                        self._current_func_collection_specs
                    )
                    self._current_func_collection_shadows = set(
                        self._current_func_collection_shadows
                    )
                    self._collection_types = dict(self._collection_types)
                    self._array_vars = set(self._array_vars)
                    self._map_vars = set(self._map_vars)
                    self._matrix_specs = dict(self._matrix_specs)
                    try:
                        for child in stmt.else_body:
                            emit_stmt(child, else_bindings, indent + 1)
                    finally:
                        (
                            self._current_func_collection_specs,
                            self._current_func_collection_shadows,
                            self._collection_types,
                            self._array_vars,
                            self._map_vars,
                            self._matrix_specs,
                        ) = else_state
                lines.append(f"{pad}}}")
                return

            if isinstance(stmt, TupleAssign):
                # ``[a, b] = rhs``: evaluate the tuple once in the requested
                # context, then bind each named element to its own local (a
                # TA tuple result's field, else the tuple's element).
                value_cpp = self._build_security_expr(
                    sec_id,
                    stmt.value,
                    None,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    runtime_stack_local,
                    lines,
                )
                temp = self._security_next_inline_name(
                    sec_id, plan["func_info"].name, "tuple"
                )
                lines.append(f"{pad}auto {temp} = {value_cpp};")
                fields = self._security_tuple_value_fields(
                    stmt.value, runtime_stack_local
                )
                for idx, name in enumerate(stmt.names):
                    if name == "_":
                        continue
                    element = (
                        f"{temp}.{fields[idx]}"
                        if fields is not None and idx < len(fields)
                        else f"std::get<{idx}>({temp})"
                    )
                    if name in local_series_names:
                        if self._security_tuple_element_is_string(
                                stmt.value, idx, runtime_stack_local):
                            self._codegen_error(
                                stmt,
                                "request.security helper tuple declaration: a string "
                                "element read with history is not supported",
                                hint="Declare the element with its own string variable.",
                            )
                        binding = self._security_series_binding(
                            self._security_next_inline_name(
                                sec_id, plan["func_info"].name, name
                            )
                        )
                        emit_series_value(
                            self._security_series_binding_target(binding),
                            element,
                            pad,
                        )
                        active_bindings[name] = binding
                        continue
                    local_name = self._security_next_inline_name(
                        sec_id, plan["func_info"].name, name
                    )
                    lines.append(f"{pad}auto {local_name} = {element};")
                    active_bindings[name] = local_name
                return

            if isinstance(stmt, (ForStmt, WhileStmt)):
                emit_loop(stmt, active_bindings, indent)
                return

            if isinstance(stmt, (BreakStmt, ContinueStmt)) and loop_depth[0] > 0:
                lines.append(f"{pad}{'break' if isinstance(stmt, BreakStmt) else 'continue'};")
                return

            if isinstance(stmt, ExprStmt) and isinstance(stmt.expr, _SECURITY_BLOCK_VALUES):
                # A block's trailing value (``lastHigh := ph`` then
                # ``lastHigh``): no effect to lower. A call statement stays
                # refused below: it could mutate chart state.
                return

            self._codegen_error(
                stmt,
                "request.security multi-statement helpers may only use local declarations, assignments, and if-branches before the final expression",
            )

        loop_depth = [0]

        def emit_loop(stmt, active_bindings: dict[str, str], indent: int) -> None:
            """A ``for`` / ``while`` loop of the helper, run on the requested
            bar as TradingView runs it there: every iteration reads the
            requested context (``o[i]`` is the requested bar's open ``i``
            requested bars back). Pine's ``for`` infers its direction from
            the first ``from`` / ``to`` values, steps by the magnitude of
            ``by`` and re-reads ``to`` before every iteration (``_visit_for``).
            A loop body holds plain locals only: the evaluator computes each
            TA call once per requested bar, before the body runs, and pushes
            each history-read local once, so neither can repeat per
            iteration."""
            pad = "    " * indent
            self._security_check_loop_body(stmt, plan)
            runtime_stack_local = plan["binding_stack"] + (active_bindings,)

            def build(expr) -> tuple[str, bool]:
                before = len(lines)
                cpp = self._build_security_expr(
                    sec_id, expr, None, ta_results, resolving,
                    security_mutable_names, runtime_stack_local, lines,
                )
                return cpp, len(lines) == before

            body_bindings = dict(active_bindings)
            if isinstance(stmt, ForStmt):
                start_cpp, _ = build(stmt.start)
                end_cpp, end_inline = build(stmt.end)
                step_cpp = build(stmt.step)[0] if stmt.step is not None else "1"
                start_cpp = self._coerce_int_slot(f"({start_cpp})", stmt.start, "int")
                end_cpp = self._coerce_int_slot(f"({end_cpp})", stmt.end, "int")
                step_cpp = self._coerce_int_slot(f"({step_cpp})", stmt.step, "int")
                base = self._security_next_inline_name(
                    sec_id, plan["func_info"].name, "for")
                s_var, e_var, st_var, dn_var = (
                    f"{base}_start", f"{base}_end", f"{base}_step", f"{base}_down")
                var = self._security_next_inline_name(
                    sec_id, plan["func_info"].name, stmt.var or "i")
                lines.append(f"{pad}int {s_var} = {start_cpp};")
                lines.append(f"{pad}int {e_var} = {end_cpp};")
                lines.append(f"{pad}int {st_var} = {step_cpp};")
                lines.append(f"{pad}if (!is_na({st_var}) && {st_var} < 0) {st_var} = -{st_var};")
                lines.append(f"{pad}if ({st_var} == 0) {st_var} = 1;")
                lines.append(f"{pad}const bool {dn_var} = ({s_var} > {e_var});")
                refresh = f", {e_var} = {end_cpp}" if end_inline else ""
                lines.append(
                    f"{pad}for (int {var} = {s_var}; "
                    f"!is_na({s_var}) && !is_na({e_var}) && !is_na({st_var}) && "
                    f"({dn_var} ? ({var} >= {e_var}) : ({var} <= {e_var})); "
                    f"{var} += ({dn_var} ? -{st_var} : {st_var}){refresh}) {{"
                )
                if stmt.var:
                    body_bindings[stmt.var] = var
                    self._security_local_cpp_types[var] = "int"
            else:
                cond_cpp, cond_inline = build(stmt.condition)
                if not cond_inline:
                    self._codegen_error(
                        stmt,
                        "request.security helper while-loop conditions must be "
                        "plain expressions of the helper's values",
                    )
                lines.append(
                    f"{pad}while ({self._coerce_bool_expr(cond_cpp, stmt.condition)}) {{"
                )
            loop_depth[0] += 1
            try:
                for child in stmt.body:
                    emit_stmt(child, body_bindings, indent + 1)
            finally:
                loop_depth[0] -= 1
            lines.append(f"{pad}}}")

        for stmt in plan["body"]:
            emit_stmt(stmt, local_cpp_bindings, indent=2)

        return self._build_security_expr(
            sec_id,
            plan["expr"],
            None,
            ta_results,
            resolving,
            security_mutable_names,
            runtime_stack,
            lines,
        )

    def _security_binding_stack_signature(
        self,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ) -> tuple:
        if not helper_binding_stack:
            return ()
        def _sig_value(value):
            if isinstance(value, str):
                return value
            return id(value)

        sig_frames = []
        for idx, frame in enumerate(helper_binding_stack):
            if idx == 0 or isinstance(frame, _SecurityHelperArgumentFrame):
                sig_frames.append(
                    tuple((name, _sig_value(node)) for name, node in sorted(frame.items()))
                )
            else:
                # Helper-local bindings only need to preserve which locals have been
                # materialized at this point; using raw runtime names here causes
                # the declaration/lookup paths to disagree on the same TA variant.
                sig_frames.append(tuple(sorted(frame.keys())))
        return tuple(sig_frames)

    def _security_user_call_key(self, node) -> str | None:
        """The ``_func_info_map`` key of the user function or typed user
        method a request.security payload calls, else None.

        A plain call keeps its spelling, so an undefined name still reaches
        the binder's refusal. ``recv.m(...)`` resolves through its receiver's
        type exactly as the chart visitor does (``_typed_user_method_info``);
        the evaluator inlines it like any helper, so its body reads the
        requested bar instead of calling the chart-bar method.
        """
        if not isinstance(node, FuncCall):
            return None
        callee = node.callee
        if isinstance(callee, Identifier):
            return callee.name if callee.name in self._func_names else None
        if not isinstance(callee, MemberAccess) or not self._security_requested_calls:
            return None
        if callee.member not in self._security_method_member_names():
            return None
        _spec, info = self._typed_user_method_info(callee.object, callee.member)
        if info is None or info.node is None:
            return None
        cache = getattr(self, "_security_method_inlinable_cache", None)
        if cache is None:
            cache = self._security_method_inlinable_cache = {}
        if id(node) not in cache:
            cache[id(node)] = self._security_method_inlinable(info, node)
        return info.name if cache[id(node)] else None

    def _security_method_member_names(self) -> set[str]:
        """Member names of the script's typed user methods (``recv.m``)."""
        names = getattr(self, "_security_user_method_members", None)
        if names is None:
            names = self._security_user_method_members = {
                name.rsplit(".", 1)[-1]
                for name, info in self._func_info_map.items()
                if getattr(info, "is_udt_method", False)
            }
        return names

    def _security_method_inlinable(self, info: FuncInfo, node: FuncCall) -> bool:
        """Whether a payload inlines this typed method call on the requested
        bar: a scalar receiver and scalar arguments, and a single-expression
        body (``_security_body_is_expression``). Any other method keeps the
        chart call it always had, with a warning (``_security_warn_chart_call``)."""
        spec, _info = self._typed_user_method_info(node.callee.object, node.callee.member)
        if method_receiver_type_name(spec) not in ("float", "int", "bool", "string"):
            return False
        try:
            binding = self._bind_typed_method_args(info, node)
        except CompileError:
            return False
        args = [node.callee.object, *binding.args_by_param]
        if any(self._infer_type(arg) not in _SECURITY_SCALAR_CPP for arg in args):
            return False
        if not self._security_body_is_expression(info.node):
            return False
        # The payload's prepasses walk an inlined method's body. One that one
        # of them refuses keeps the chart call instead; the provisional entry
        # lets the walks enter this call.
        self._security_method_inlinable_cache[id(node)] = True
        try:
            self._validate_security_persistent_var_control_flow(node)
            self._collect_security_ohlc_hist_fields(node)
            self._collect_security_ta_hist_indices(node)
        except CompileError:
            return False
        return True

    def _security_body_is_expression(self, func_node) -> bool:
        """Whether a payload inlines this user function or method body on the
        requested bar where every earlier build called it on the chart: one
        expression, reading no mutable global and no ``request.*``, every
        user call in it such a body in turn. A multi-statement body keeps the
        chart call: the evaluator writes its statements ahead of the payload,
        so in a ternary arm or an ``and``/``or`` operand they would run, and
        advance their TA and ``var`` state, on every requested bar. Decided
        once per definition; a body reached again through its own calls is
        not inlined."""
        cache = getattr(self, "_security_body_expression_cache", None)
        if cache is None:
            cache = self._security_body_expression_cache = {}
        if id(func_node) in cache:
            return cache[id(func_node)]
        cache[id(func_node)] = False
        body = getattr(func_node, "body", None) or []
        cache[id(func_node)] = (
            len(body) == 1
            and isinstance(body[0], ExprStmt)
            and self._security_expression_inlinable(body[0].expr)
        )
        return cache[id(func_node)]

    def _security_expression_inlinable(self, expr) -> bool:
        """``_security_body_is_expression``'s test of one expression."""
        stack = [expr]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if isinstance(n, dict):
                stack.extend(n.values())
                continue
            if not isinstance(n, ASTNode):
                continue
            if isinstance(n, (IfStmt, SwitchStmt, ForStmt, ForInStmt, WhileStmt)):
                return False
            if (isinstance(n, Identifier)
                    and self._security_identifier_is_global_binding(n)):
                if n.name in self._global_mutable_infos:
                    return False
                if not self._security_global_value_inlinable(n.name):
                    return False
            if isinstance(n, FuncCall):
                _func, namespace = self._resolve_callee(n.callee)
                if namespace == "request":
                    return False
                if isinstance(n.callee, Identifier) and n.callee.name in self._func_names:
                    callee = self._func_info_map.get(n.callee.name)
                    if (callee is None or callee.node is None
                            or not self._security_body_is_expression(callee.node)):
                        return False
                elif (isinstance(n.callee, MemberAccess)
                        and n.callee.member in self._security_method_member_names()):
                    _spec, method = self._typed_user_method_info(
                        n.callee.object, n.callee.member)
                    if method is not None and self._security_user_call_key(n) is None:
                        return False
            stack.extend(v for k, v in vars(n).items() if k != "annotations")
        return True

    def _security_global_value_inlinable(self, name: str) -> bool:
        """Whether a global an inlined body reads can be re-evaluated on the
        requested bar at each read: its value, through the globals it reads,
        calls no multi-statement user function, whose statements -- and TA
        state -- the evaluator would emit again at every read."""
        cache = getattr(self, "_security_global_value_cache", None)
        if cache is None:
            cache = self._security_global_value_cache = {}
        if name in cache:
            return cache[name]
        cache[name] = False
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        value = global_expr_map.get(name)
        cache[name] = value is None or self._security_expression_inlinable(value)
        return cache[name]

    def _security_call_inlinable(self, node) -> bool:
        """Whether a payload inlines this user call where a builtin wraps it:
        a typed method its gate admits, or a plain call of a single-expression
        body (``_security_body_is_expression``)."""
        if self._security_user_call_key(node) is None:
            return False
        if isinstance(node.callee, MemberAccess):
            return True
        info = self._func_info_map.get(node.callee.name)
        return (info is not None and info.node is not None
                and self._security_body_is_expression(info.node))

    def _security_pure_body(self, func_node) -> bool:
        """Whether a user function's body is one expression over its
        parameters, the requested bar's fields and literals, through operators
        and positional calls of such functions only. Its value is then the
        same wherever the payload reaches it with the same arguments, and no
        prepass finds anything in it: no TA call, history, ``var`` state,
        global or builtin call. Decided once per definition; a body reached
        again through its own calls is not pure."""
        cache = getattr(self, "_security_pure_body_cache", None)
        if cache is None:
            cache = self._security_pure_body_cache = {}
        key = id(func_node)
        if key not in cache:
            cache[key] = False
            body = getattr(func_node, "body", None) or []
            cache[key] = (
                len(body) == 1
                and isinstance(body[0], ExprStmt)
                and self._security_pure_expr(body[0].expr, set(func_node.params))
            )
        return cache[key]

    def _security_pure_expr(self, expr, params: set[str]) -> bool:
        """``_security_pure_body``'s test of one expression."""
        stack = [expr]
        while stack:
            n = stack.pop()
            if isinstance(n, (NumberLiteral, BoolLiteral, NaLiteral)):
                continue
            if isinstance(n, Identifier):
                if self._security_identifier_is_global_binding(n):
                    # A builtin name the builder spells from ``bar``.
                    if n.name in _SECURITY_SHARED_BAR_FIELDS:
                        continue
                elif n.name in params:
                    continue
                return False
            if isinstance(n, BinOp):
                stack.extend((n.left, n.right))
            elif isinstance(n, UnaryOp):
                stack.append(n.operand)
            elif isinstance(n, Ternary):
                stack.extend((n.condition, n.true_val, n.false_val))
            elif isinstance(n, FuncCall) and self._security_pure_call(n):
                stack.extend(n.args)
            else:
                return False
        return True

    def _security_pure_call(self, node: FuncCall) -> bool:
        """A positional call binding every parameter of a pure user function
        (``_security_pure_body``)."""
        callee = node.callee
        if (not isinstance(callee, Identifier) or node.kwargs
                or callee.name not in self._func_names):
            return False
        info = self._func_info_map.get(callee.name)
        return (info is not None and info.node is not None
                and len(node.args) == len(info.node.params)
                and self._security_pure_body(info.node))

    def _security_shared_call_key(self, node, helper_binding_stack) -> tuple | None:
        """What a pure call (``_security_pure_call``) evaluates on the
        requested bar: its function and the canonical value of each argument
        (``_security_canonical_value``); two calls with one key inline one
        text. None for any other call, or an argument that reads anything
        else."""
        if not isinstance(node, FuncCall) or not self._security_pure_call(node):
            return None
        values = []
        for arg in node.args:
            value = self._security_canonical_value(arg, helper_binding_stack or ())
            if value is None:
                return None
            values.append(value)
        return (id(self._func_info_map[node.callee.name].node), tuple(values))

    def _security_canonical_value(self, node, helper_binding_stack) -> tuple | None:
        """A pure call's argument as the builder reads it, as a hashable tree:
        literals, the requested bar's fields, operators and pure calls, each
        name read through the helper bindings the way the builder reads it
        and each operator with its inferred type. None for anything else: a
        TA call, history, a global, an evaluator local."""
        if isinstance(node, NumberLiteral):
            return ("number", type(node.value).__name__, repr(node.value))
        if isinstance(node, BoolLiteral):
            return ("bool", bool(node.value))
        if isinstance(node, NaLiteral):
            return ("na",)
        if isinstance(node, Identifier):
            if node.name in self._security_raw_cpp:
                return None
            if not self._security_identifier_is_global_binding(node):
                binding = self._security_lookup_helper_binding_context(
                    node.name, helper_binding_stack
                )
                if binding is not None:
                    bound, bound_stack = binding
                    if isinstance(bound, str):
                        return None
                    value = self._security_canonical_value(bound, bound_stack)
                    return None if value is None else ("argument", self._infer_type(node), value)
            if node.name in _SECURITY_SHARED_BAR_FIELDS:
                return ("bar", node.name)
            return None
        if isinstance(node, BinOp):
            left = self._security_canonical_value(node.left, helper_binding_stack)
            right = self._security_canonical_value(node.right, helper_binding_stack)
            if left is None or right is None:
                return None
            return ("binary", node.op, self._infer_type(node), left, right)
        if isinstance(node, UnaryOp):
            operand = self._security_canonical_value(node.operand, helper_binding_stack)
            if operand is None:
                return None
            return ("unary", node.op, self._infer_type(node), operand)
        if isinstance(node, Ternary):
            parts = [
                self._security_canonical_value(part, helper_binding_stack)
                for part in (node.condition, node.true_val, node.false_val)
            ]
            if any(part is None for part in parts):
                return None
            return ("ternary", self._infer_type(node), *parts)
        key = self._security_shared_call_key(node, helper_binding_stack)
        return None if key is None else ("call", self._infer_type(node), key)

    def _security_user_call_site(self, node) -> bool:
        """Whether ``node`` calls a user function or typed user method,
        whether or not a payload can inline it."""
        if not isinstance(node, FuncCall):
            return False
        callee = node.callee
        if isinstance(callee, Identifier):
            return callee.name in self._func_names
        if (isinstance(callee, MemberAccess)
                and callee.member in self._security_method_member_names()):
            _spec, info = self._typed_user_method_info(callee.object, callee.member)
            return info is not None
        return False

    def _security_warn_chart_call(self, node, reason: str | None = None) -> None:
        """Warn once per call site that a payload's user call keeps the chart
        call it always had, which reads the chart's bar: a method whose
        receiver, arguments or body a payload does not inline, or a call whose
        inlining the evaluator refused (``reason``)."""
        if not self._security_user_call_site(node):
            return
        if reason is None and (not self._security_requested_calls
                               or self._security_call_inlinable(node)):
            return
        warned = getattr(self, "_security_warned_calls", None)
        if warned is None:
            warned = self._security_warned_calls = set()
        if id(node) in warned:
            return
        warned.add(id(node))
        if isinstance(node.callee, Identifier):
            what = f"function '{node.callee.name}'"
        else:
            what = f"method '{node.callee.member}'"
        if reason is None and isinstance(node.callee, MemberAccess):
            why = (
                "a payload inlines a method on the requested bar only for a "
                "scalar receiver and arguments and a single-expression body "
                "reading no mutable global, whose user calls, directly or "
                "through a global's value, are single expressions too"
            )
        elif reason is None:
            why = (
                "a payload inlines a function under a builtin call only when its "
                "body is a single expression and so is every user call it makes, "
                "directly or through a global's value"
            )
        else:
            why = f"the evaluator cannot inline it ({reason})"
        self._codegen_warning(
            node,
            f"request.security payload calls {what} on the chart's bar: {why}; "
            "TradingView evaluates it on the requested bar.",
        )

    def _security_bind_helper_args(
        self,
        node: FuncCall,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> tuple[FuncInfo, tuple[dict[str, ASTNode], ...]]:
        if isinstance(node.callee, MemberAccess):
            method_key = self._security_user_call_key(node)
            if method_key is not None:
                fi = self._func_info_map[method_key]
                binding = self._bind_typed_method_args(fi, node)
                params = list(fi.node.params)
                bound_args = [node.callee.object, *binding.args_by_param]
                if len(bound_args) != len(params) or any(
                    arg is None for arg in bound_args
                ):
                    self._codegen_error(
                        node,
                        "request.security helper calls must bind every parameter explicitly",
                    )
                base_stack = helper_binding_stack or ()
                # A default is authored in the method's declaration, at global
                # scope: bind it there, not in the caller's scope.
                defaults = list(getattr(fi, "param_defaults", ()) or ())
                written: dict[str, ASTNode] = {}
                declared: dict[str, ASTNode] = {}
                for index, (param, arg) in enumerate(zip(params, bound_args)):
                    default = defaults[index] if index < len(defaults) else None
                    target = declared if index and arg is default else written
                    target[param] = arg
                frames = (_SecurityHelperArgumentFrame(written, base_stack, method=True),)
                if declared:
                    frames += (_SecurityHelperArgumentFrame(declared, (), method=True),)
                return fi, base_stack + frames
        if not isinstance(node.callee, Identifier):
            self._codegen_error(
                node,
                "request.security helper calls must target named user-defined functions",
            )

        func_name = node.callee.name
        fi = self._func_info_map.get(func_name)
        if fi is None or fi.node is None:
            self._codegen_error(
                node,
                f"request.security helper function '{func_name}' is not defined",
            )

        params = list(fi.node.params)
        unknown_kwargs = set(node.kwargs) - set(params)
        if unknown_kwargs:
            unknown_list = ", ".join(sorted(unknown_kwargs))
            self._codegen_error(
                node,
                f"request.security helper call has unknown parameter(s): {unknown_list}",
            )

        bound_args = list(node.args)
        for idx, param_name in enumerate(params):
            if param_name in node.kwargs:
                while len(bound_args) <= idx:
                    bound_args.append(None)
                if bound_args[idx] is None:
                    bound_args[idx] = node.kwargs[param_name]

        if len(bound_args) != len(params) or any(arg is None for arg in bound_args):
            self._codegen_error(
                node,
                "request.security helper calls must bind every parameter explicitly",
            )

        base_stack = helper_binding_stack or ()
        new_frame = _SecurityHelperArgumentFrame(
            {
                param_name: bound_args[idx]
                for idx, param_name in enumerate(params)
            },
            base_stack,
        )
        return fi, base_stack + (new_frame,)

    def _security_helper_call_plan(
        self,
        node: FuncCall,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> dict:
        fi, bound_stack = self._security_bind_helper_args(node, helper_binding_stack)
        assert fi.node is not None
        body = fi.node.body
        params = list(fi.node.params)

        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return {
                "mode": "expr",
                "func_info": fi,
                "binding_stack": bound_stack,
                "expr": body[0].expr,
                "body": [],
            }

        if not body:
            self._codegen_error(
                node,
                "request.security multi-statement helpers must end with a final expression result",
            )

        stmt_body = list(body[:-1])
        final_stmt = body[-1]
        if isinstance(final_stmt, ExprStmt):
            final_expr = final_stmt.expr
        elif isinstance(final_stmt, Assignment):
            target_name = self._get_target_name(final_stmt.target)
            if target_name is None:
                self._codegen_error(
                    node,
                    "request.security multi-statement helpers must end with a final expression result",
                )
            stmt_body.append(final_stmt)
            final_expr = Identifier(name=target_name)
        elif isinstance(final_stmt, VarDecl):
            stmt_body.append(final_stmt)
            final_expr = Identifier(name=final_stmt.name)
        else:
            self._codegen_error(
                node,
                "request.security multi-statement helpers must end with a final expression result",
            )

        # ``for`` / ``while`` loops lower in the requested context
        # (``_emit_security_linear_helper_call_scoped``); a ``for ... in``
        # loop and a bare ``switch`` statement stay refused.
        unsupported_control_flow = (
            ForInStmt,
            SwitchStmt,
            BreakStmt,
            ContinueStmt,
        )
        for stmt in stmt_body:
            if isinstance(stmt, unsupported_control_flow):
                self._codegen_error(
                    node,
                    "request.security does not support multi-statement helpers with control flow",
                    hint="Inline a straight-line helper body or hoist the control-flow helper outside request.security().",
                )
            if isinstance(stmt, (ForStmt, WhileStmt)):
                continue
            if not isinstance(stmt, _SECURITY_HELPER_STMTS) or (
                    isinstance(stmt, ExprStmt)
                    and not isinstance(stmt.expr, _SECURITY_BLOCK_VALUES)):
                self._codegen_error(
                    node,
                    "request.security multi-statement helpers may only use local declarations, assignments, and if-branches before the final expression",
                )
            if isinstance(stmt, VarDecl):
                if stmt.is_varip:
                    self._codegen_error(
                        node,
                        "request.security does not support helper-local varip state",
                        hint="Use var for requested-context bar state; varip tick state is unavailable in batch backtests.",
                    )
            if isinstance(stmt, Assignment):
                target_name = self._get_target_name(stmt.target)
                if target_name is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helpers may only assign to local identifier temporaries",
                    )

        local_series_names = sorted(set(self.ctx.func_series_vars.get(fi.name, set())) - set(params))
        return {
            "mode": "linear",
            "func_info": fi,
            "binding_stack": bound_stack,
            "expr": final_expr,
            "body": stmt_body,
            "local_series_names": local_series_names,
        }

    def _security_tuple_value_fields(
        self,
        value,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        depth: int = 0,
    ) -> list[str] | None:
        """Field names of the TA tuple result a helper's ``[a, b] = rhs``
        destructures (``rhs`` a TA tuple call, directly or as the final
        expression of a helper it calls); None for a ``std::tuple``."""
        site = self._get_ta_site(value)
        if site is not None:
            return TA_TUPLE_FIELDS.get(self._ta_name_from_site(site))
        if depth < 32 and self._security_user_call_key(value) is not None:
            plan = self._security_helper_call_plan(value, helper_binding_stack)
            return self._security_tuple_value_fields(
                plan["expr"], plan["binding_stack"], depth + 1
            )
        return None

    def _security_tuple_element_is_string(
        self,
        value,
        index: int,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        depth: int = 0,
    ) -> bool:
        """Whether element ``index`` of the tuple a helper destructures is a
        string: the final tuple of the helper it calls (through a helper whose
        final expression calls another), each element read through the
        callee's parameters and top-level locals."""
        if depth > 32 or self._get_ta_site(value) is not None:
            return False
        if self._security_user_call_key(value) is None:
            return False
        try:
            plan = self._security_helper_call_plan(value, helper_binding_stack)
        except CompileError:
            return False
        final = plan["expr"]
        if self._security_user_call_key(final) is not None:
            return self._security_tuple_element_is_string(
                final, index, plan["binding_stack"], depth + 1
            )
        if isinstance(final, TupleLiteral) and index < len(final.elements):
            return self._security_value_is_string(
                final.elements[index], plan["binding_stack"], plan["body"], depth + 1
            )
        return False

    def _security_value_is_string(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        body: list,
        depth: int = 0,
    ) -> bool:
        """Whether a helper's value is a string, read through its top-level
        locals (``body``), its parameters' arguments and the helpers it
        calls; anything else by the chart's inference."""
        if node is None or depth > 64:
            return False
        nxt = depth + 1
        if isinstance(node, StringLiteral):
            return True
        if isinstance(node, Identifier) and not self._security_identifier_is_global_binding(node):
            for stmt in reversed(body or []):
                if isinstance(stmt, VarDecl) and stmt.name == node.name:
                    if stmt.type_hint:
                        return stmt.type_hint == "string"
                    return self._security_value_is_string(
                        stmt.value, helper_binding_stack, body, nxt
                    )
            binding = self._security_lookup_helper_binding_context(
                node.name, helper_binding_stack
            )
            if binding is not None:
                bound, bound_stack = binding
                if isinstance(bound, str):
                    series_name = self._security_series_binding_target(bound)
                    if series_name is not None:
                        return series_name in self._security_string_series
                    return self._security_local_cpp_types.get(bound) == "std::string"
                return self._security_value_is_string(bound, bound_stack, (), nxt)
        if isinstance(node, Ternary):
            return any(
                self._security_value_is_string(arm, helper_binding_stack, body, nxt)
                for arm in (node.true_val, node.false_val)
            )
        if isinstance(node, BinOp) and node.op == "+":
            return any(
                self._security_value_is_string(side, helper_binding_stack, body, nxt)
                for side in (node.left, node.right)
            )
        if self._security_user_call_key(node) is not None:
            if self._security_shared_call_key(node, helper_binding_stack) is not None:
                return False
            try:
                plan = self._security_helper_call_plan(node, helper_binding_stack)
            except CompileError:
                return False
            return self._security_value_is_string(
                plan["expr"], plan["binding_stack"], plan["body"], nxt
            )
        return self._infer_type(node) == "std::string"

    def _validate_security_persistent_var_control_flow(self, expr_node) -> None:
        """Reject helper ``var`` state whose rollback would be conditional.

        Persistent helper state is restored at its declaration before the body
        mutates it. That is safe only when the declaration executes on every
        requested-bar evaluation. Until rollback is hoisted to evaluator entry,
        reject declarations reached through ``if``/``?:``/short-circuit paths,
        including state owned by a nested helper called from such a path.
        """
        call_stack: set[str] = set()

        def visit_expr(node, conditional: bool) -> None:
            if node is None:
                return
            if isinstance(node, FuncCall):
                name = self._security_user_call_key(node)
                if name is not None:
                    if name in call_stack:
                        return
                    fi = self._func_info_map.get(name)
                    if (fi is not None and fi.node is not None
                            and not self._security_pure_body(fi.node)):
                        call_stack.add(name)
                        for stmt in fi.node.body:
                            visit_stmt(stmt, conditional)
                        call_stack.remove(name)
                    for arg in node.args:
                        visit_expr(arg, conditional)
                    for value in node.kwargs.values():
                        if isinstance(value, ASTNode):
                            visit_expr(value, conditional)
                    return
            if isinstance(node, Ternary):
                visit_expr(node.condition, conditional)
                visit_expr(node.true_val, True)
                visit_expr(node.false_val, True)
                return
            if isinstance(node, BinOp) and node.op in ("and", "or"):
                visit_expr(node.left, conditional)
                visit_expr(node.right, True)
                return
            for value in getattr(node, "__dict__", {}).values():
                if isinstance(value, ASTNode):
                    visit_expr(value, conditional)
                elif isinstance(value, (list, tuple)):
                    for child in value:
                        if isinstance(child, ASTNode):
                            visit_expr(child, conditional)

        def visit_stmt(stmt, conditional: bool) -> None:
            if isinstance(stmt, VarDecl):
                if stmt.is_var and conditional:
                    self._codegen_error(
                        stmt,
                        "request.security helper-local var declarations inside "
                        "conditional control flow are not supported",
                        hint="Declare persistent requested-context state at the helper's top level.",
                    )
                # A persistent initializer runs only when its requested-context
                # slot is first created.  Any nested persistent helper reached
                # from that initializer would therefore need its rollback and
                # mutation to remain inside the same once-only guard.  The
                # current linear emitter hoists nested helper statements ahead
                # of that guard, so treat the initializer as conditional and
                # fail closed until it can preserve that execution boundary.
                visit_expr(stmt.value, conditional or stmt.is_var)
                return
            if isinstance(stmt, IfStmt):
                visit_expr(stmt.condition, conditional)
                for child in stmt.body:
                    visit_stmt(child, True)
                for child in stmt.else_body:
                    visit_stmt(child, True)
                return
            if isinstance(stmt, ExprStmt):
                visit_expr(stmt.expr, conditional)
                return
            for value in getattr(stmt, "__dict__", {}).values():
                if isinstance(value, ASTNode):
                    visit_expr(value, conditional)
                elif isinstance(value, (list, tuple)):
                    for child in value:
                        if isinstance(child, ASTNode):
                            visit_expr(child, conditional)

        visit_expr(expr_node, False)

    def _security_next_inline_name(self, sec_id: int, func_name: str, base_name: str) -> str:
        self._security_inline_counter += 1
        # A typed method's key is ``Type.name``: not a C++ identifier part.
        return (
            f"_sec{sec_id}_{self._safe_name(func_name.replace('.', '_'))}_"
            f"{self._security_inline_counter}_{self._safe_name(base_name)}"
        )

    def _expr_depends_on_security_mutables(
        self,
        expr_node,
        security_mutable_names: set[str],
        resolving: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        if expr_node is None or not security_mutable_names:
            return False
        if resolving is None:
            resolving = set()

        if isinstance(expr_node, Identifier):
            binding = None
            if not self._security_identifier_is_global_binding(expr_node):
                binding = self._security_lookup_helper_binding_context(
                    expr_node.name, helper_binding_stack
                )
            if binding is not None:
                bound, bound_stack = binding
                return self._expr_depends_on_security_mutables(
                    bound,
                    security_mutable_names,
                    resolving,
                    bound_stack,
                )
            if expr_node.name in security_mutable_names:
                return True
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                depends = self._expr_depends_on_security_mutables(
                    global_expr_map[expr_node.name],
                    security_mutable_names,
                    resolving,
                    helper_binding_stack,
                )
                resolving.remove(expr_node.name)
                return depends
            return False

        if isinstance(expr_node, FuncCall):
            func_name = self._security_user_call_key(expr_node)
            if func_name is not None:
                if self._security_shared_call_key(expr_node, helper_binding_stack) is not None:
                    return False
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    return False
                resolving.add(call_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    depends = self._expr_depends_on_security_mutables(
                        plan["expr"],
                        security_mutable_names,
                        resolving,
                        plan["binding_stack"],
                    )
                else:
                    local_ast_bindings: dict[str, ASTNode] = {}
                    linear_stack = plan["binding_stack"] + (local_ast_bindings,)
                    depends = False
                    for stmt in plan["body"][:-1]:
                        if isinstance(stmt, (VarDecl, Assignment, TupleAssign)):
                            value = stmt.value
                        elif isinstance(stmt, ExprStmt):
                            value = stmt.expr
                        else:
                            value = None
                        if value is not None and self._expr_depends_on_security_mutables(
                            value,
                            security_mutable_names,
                            resolving,
                            linear_stack,
                        ):
                            depends = True
                            break
                        if isinstance(stmt, VarDecl):
                            target_name = stmt.name
                        elif isinstance(stmt, Assignment):
                            target_name = self._get_target_name(stmt.target)
                        else:
                            target_name = None
                        if target_name is not None and value is not None:
                            local_ast_bindings[target_name] = value
                    if not depends:
                        depends = self._expr_depends_on_security_mutables(
                            plan["expr"],
                            security_mutable_names,
                            resolving,
                            linear_stack,
                        )
                resolving.remove(call_key)
                return depends

        def walk(value) -> bool:
            if value is None:
                return False
            if hasattr(value, "__dict__"):
                return self._expr_depends_on_security_mutables(
                    value,
                    security_mutable_names,
                    resolving,
                    helper_binding_stack,
                )
            if isinstance(value, (list, tuple)):
                return any(walk(item) for item in value)
            if isinstance(value, dict):
                return any(walk(item) for item in value.values())
            return False

        return any(walk(child) for child in vars(expr_node).values())

    def _security_ta_depends_on_mutables(
        self,
        site: TACallSite,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        return any(
            self._expr_depends_on_security_mutables(
                arg,
                security_mutable_names,
                helper_binding_stack=helper_binding_stack,
            )
            for arg in site.compute_args
        )

    def _security_ta_ctor_arg_nodes(self, site: TACallSite) -> list:
        node = site.node
        if not isinstance(node, FuncCall):
            return []

        func_name = self._ta_name_from_site(site)
        all_args = self._merge_ta_call_args(func_name, node)
        effective_multi_ctor = TA_MULTI_CTOR.copy()
        if func_name in ("pivothigh", "pivotlow") and len(all_args) == 3:
            effective_multi_ctor[func_name] = [1, 2]

        ctor_indices: list[int] = []
        if func_name in TA_NO_CTOR:
            ctor_indices = []
        elif func_name in effective_multi_ctor:
            ctor_indices = list(effective_multi_ctor[func_name])
        elif func_name in TA_PERIOD_ARG:
            ctor_indices = [TA_PERIOD_ARG[func_name]]

        return [
            all_args[idx]
            for idx in ctor_indices
            if idx < len(all_args) and all_args[idx] is not None
        ]

    def _security_ta_ctor_args_for_variant(
        self,
        sec_id: int,
        site: TACallSite,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        fallback_args: list[str] | None = None,
    ) -> tuple[list[str], list[bool] | None]:
        """Resolve TA constructor args in one helper-call lexical context.

        ``TACallSite.ctor_args`` is a source-string snapshot which ordinary
        callable specialization mutates from the first call site.  A single
        TA AST can also be reached several times while lowering one
        ``request.security`` tuple (for example ``ema(src, 2)`` through
        ``ema(src, 5)``).  Those requested-context variants share the AST but
        must not share that first snapshot.  Re-lower the constructor AST
        through the exact helper binding stack used to key the variant.

        Calls outside an inlined helper retain the established string path so
        input-backed runtime resets and direct top-level TA sites stay
        byte-compatible.  The optional boolean list records, per argument,
        whether the source AST is a bar-invariant scalar after resolving its
        exact helper bindings.  Runtime-reset collection uses that provenance
        to reuse trusted lowered C++ without allowing a mixed input + series
        expression merely because it contains a generated ``get_input_*`` call.
        """
        fallback = list(
            site.ctor_args if fallback_args is None else fallback_args
        )
        if not helper_binding_stack:
            return fallback, None
        arg_nodes = self._security_ta_ctor_arg_nodes(site)
        if len(arg_nodes) != len(fallback):
            return fallback, None
        # A constructor argument is lowered as every earlier build did, except
        # in a typed method's inline, which needs the method's bindings. One
        # the evaluator cannot lower there keeps the TA object at a placeholder
        # length and its evaluator on the chart (``_security_chart_evaluators``).
        in_method = any(getattr(frame, "method", False) for frame in helper_binding_stack)
        saved_flag = self._security_requested_calls
        saved_index_inputs = self._security_index_inputs
        self._security_requested_calls = in_method
        # evaluate_security resets the TA object before on_bar has read the
        # inputs into their members: read each input through its getter.
        self._security_index_inputs = True
        try:
            lowered = [
                self._build_security_expr(
                    sec_id,
                    arg,
                    None,
                    {},
                    resolving=set(),
                    security_mutable_names=set(),
                    helper_binding_stack=helper_binding_stack,
                    emitted_lines=None,
                )
                for arg in arg_nodes
            ]
            stability = [
                self._security_ta_ctor_arg_is_stable(arg, helper_binding_stack)
                for arg in arg_nodes
            ]
            if in_method:
                for arg, cpp, stable in zip(arg_nodes, lowered, stability):
                    # What the runtime reset refuses (neither a per-run
                    # value nor a compile-time one) keeps the chart call.
                    if (not stable
                            and self._runtime_ctor_arg_for_reset(cpp) is None
                            and not self._is_compile_time_value(
                                self._resolve_ta_ctor_arg(cpp))):
                        self._codegen_error(
                            arg,
                            f"request.security method TA constructor argument '{cpp}' "
                            "is not a stable per-run scalar",
                        )
        except Exception as exc:  # noqa: BLE001 -- the method stays on the chart
            if getattr(exc, "limit", False) or not in_method:
                raise
            diagnostic = (
                exc.diagnostics[0]
                if isinstance(exc, CompileError) and exc.diagnostics else None
            )
            self._security_chart_evaluators.setdefault(sec_id, (
                (diagnostic.location, diagnostic.message.splitlines()[0])
                if diagnostic is not None
                else (None, f"a method's TA constructor argument ({type(exc).__name__})")
            ))
            return ["1"] * len(arg_nodes), None
        finally:
            self._security_requested_calls = saved_flag
            self._security_index_inputs = saved_index_inputs
        return lowered, stability

    def _security_ta_ctor_arg_is_stable(
        self,
        node: ASTNode,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ) -> bool:
        """Classify one helper-bound requested-context TA constructor arg.

        ``_expr_is_stable`` already owns the compiler's conservative definition
        of a per-run scalar.  Resolve helper parameters first, preserving the
        authored AST shape, and then delegate to that shared classifier.  This
        prevents a later helper call such as ``ema(src, inputLen + int(close))``
        from bypassing the ordinary TA-length guard just because an earlier
        call through the same source TA site used a safe length.
        """
        resolved = self._security_helper_bound_ast(node, helper_binding_stack)
        return resolved is not None and self._expr_is_stable(resolved)

    @staticmethod
    def _security_rebinding_reads_itself(name: str, stmt: Assignment,
                                         frame: dict | None = None) -> bool:
        """Whether ``stmt`` rebinds the helper local ``name`` from its own
        value: a compound assignment (``c += 1``), a value reading it
        (``c := c + 1``), or reading a local ``frame`` binds to an expression
        that reads it (``b = a`` then ``a := b + 1``)."""
        if stmt.op != ":=":
            return True
        seen: set[str] = set()

        def reads(node) -> bool:
            if isinstance(node, Identifier):
                if node.name == name:
                    return True
                bound = (frame or {}).get(node.name)
                if isinstance(bound, ASTNode) and node.name not in seen:
                    seen.add(node.name)
                    return reads(bound)
                return False
            if isinstance(node, ASTNode):
                return any(reads(child) for key, child in vars(node).items()
                           if key != "annotations")
            if isinstance(node, (list, tuple)):
                return any(reads(child) for child in node)
            if isinstance(node, dict):
                return any(reads(child) for child in node.values())
            return False

        return reads(stmt.value)

    @staticmethod
    def _security_loop_parts(stmt) -> tuple[list, str | None]:
        """A helper loop's header expressions and its counter's name."""
        if isinstance(stmt, ForStmt):
            return [stmt.start, stmt.end, stmt.step], stmt.var or None
        return [stmt.condition], None

    def _security_loop_counter_binding(self, plan: dict, name: str) -> str:
        """What the prepasses bind a helper loop's counter to: a local whose
        value is the requested bar's run-time one, never a folded constant,
        so ``o[i]`` reads the requested history at a run-time offset."""
        return self._security_series_binding(f"{plan['func_info'].name}:{name}@loop")

    def _security_check_loop_body(self, loop, plan: dict) -> None:
        """Refuse what a request.security helper loop cannot repeat per
        iteration. The evaluator computes a TA call once per requested bar
        at its place in the helper, pushes a local read with history once
        per requested bar and keeps a ``var`` local's state across requested
        bars, so none of them can sit in a loop's header or body; a user
        function call there would inline them too."""
        local_series = set(plan.get("local_series_names", ()))

        def refuse(node, what: str) -> None:
            self._codegen_error(
                node,
                f"request.security helper loops cannot hold {what}",
                hint="Compute it before the loop, on the requested bar.",
            )

        header, _ = self._security_loop_parts(loop)
        stack: list = [*header, *loop.body]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not isinstance(n, ASTNode) or isinstance(n, (FuncDef, MethodDef)):
                continue
            if isinstance(n, VarDecl):
                if n.is_var or n.is_varip:
                    refuse(n, "a var declaration")
                if n.name in local_series:
                    refuse(n, f"the local '{n.name}', which is read with history")
            if isinstance(n, FuncCall):
                callee = n.callee
                namespace = (
                    callee.object.name
                    if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                    else None
                )
                if self._get_ta_site(n) is not None or namespace == "ta":
                    refuse(n, "a TA call")
                if namespace in ("request", "strategy"):
                    refuse(n, f"a {namespace}.* call")
                if self._security_user_call_key(n) is not None:
                    refuse(n, "a user function call")
            stack.extend(v for k, v in vars(n).items() if k not in ("loc", "annotations"))

    @staticmethod
    def _security_arm_reassigned_names(body) -> set[str]:
        """The enclosing locals an if arm's statements reassign (``:=`` and
        the compound assignments), in nested blocks too; a name the arm
        declares shadows the enclosing one from its declaration on, so its
        reassignments there are the arm's own."""
        names: set[str] = set()

        def block(stmts, declared: frozenset) -> None:
            local = set(declared)
            for stmt in stmts or ():
                expr(stmt, frozenset(local))
                if isinstance(stmt, VarDecl):
                    local.add(stmt.name)

        def expr(node, declared: frozenset) -> None:
            if isinstance(node, (FuncDef, MethodDef)):
                return
            if (isinstance(node, Assignment) and isinstance(node.target, Identifier)
                    and node.target.name not in declared):
                names.add(node.target.name)
            if isinstance(node, IfStmt):
                expr(node.condition, declared)
                block(node.body, declared)
                block(node.else_body, declared)
                return
            if isinstance(node, ASTNode):
                for key, child in vars(node).items():
                    if key == "annotations":
                        continue
                    if isinstance(child, list) and child and all(
                            isinstance(item, ASTNode) for item in child):
                        block(child, declared)
                    else:
                        expr(child, declared)
            elif isinstance(node, (list, tuple)):
                for child in node:
                    expr(child, declared)

        block(body, frozenset())
        return names

    def _security_helper_state_reads(
        self,
        node: ASTNode,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        depth: int = 0,
    ) -> list[str]:
        """The helper locals ``node`` reads, through the bindings
        ``helper_binding_stack`` holds, that are values of each requested bar
        (a ``var``, a local read with history or rebound in an if arm), in
        reading order."""
        names: list[str] = []

        def walk(current, stack, level) -> None:
            if level > 32:
                return
            if isinstance(current, Identifier):
                if self._security_identifier_is_global_binding(current):
                    return
                binding = self._security_lookup_helper_binding_context(current.name, stack)
                if binding is None:
                    return
                bound, bound_stack = binding
                if isinstance(bound, str):
                    if (self._security_series_binding_target(bound) is not None
                            and current.name not in names):
                        names.append(current.name)
                    return
                walk(bound, bound_stack, level + 1)
                return
            if isinstance(current, ASTNode):
                for key, child in vars(current).items():
                    if key != "annotations":
                        walk(child, stack, level + 1)
            elif isinstance(current, (list, tuple)):
                for child in current:
                    walk(child, stack, level + 1)
            elif isinstance(current, dict):
                for child in current.values():
                    walk(child, stack, level + 1)

        walk(node, helper_binding_stack, depth)
        return names

    def _security_helper_bound_ast(
        self,
        node: ASTNode,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ):
        """``node`` with its helper parameters and locals replaced by the
        argument ASTs ``helper_binding_stack`` binds them to, each resolved in
        its own lexical stack; identifiers bound elsewhere (globals included)
        are kept as authored. None for a shape outside literals, identifiers,
        member reads, operators, ternaries and calls."""
        import dataclasses

        def resolve(
            current,
            stack: tuple[dict[str, ASTNode], ...] | None,
            seen: frozenset[tuple[str, int]] = frozenset(),
            depth: int = 0,
        ):
            if current is None or depth > 32:
                return None
            if isinstance(current, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(current):
                    binding = self._security_lookup_helper_binding_context(
                        current.name,
                        stack,
                    )
                if binding is None:
                    return current
                bound, bound_stack = binding
                if isinstance(bound, str):
                    return None
                key = (current.name, id(bound))
                if key in seen:
                    return None
                return resolve(
                    bound,
                    bound_stack,
                    seen | {key},
                    depth + 1,
                )
            if isinstance(
                current,
                (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral),
            ):
                return current
            if isinstance(current, MemberAccess):
                obj = resolve(current.object, stack, seen, depth + 1)
                if obj is None:
                    return None
                return dataclasses.replace(current, object=obj)
            if isinstance(current, BinOp):
                left = resolve(current.left, stack, seen, depth + 1)
                right = resolve(current.right, stack, seen, depth + 1)
                if left is None or right is None:
                    return None
                return dataclasses.replace(current, left=left, right=right)
            if isinstance(current, UnaryOp):
                operand = resolve(current.operand, stack, seen, depth + 1)
                if operand is None:
                    return None
                return dataclasses.replace(current, operand=operand)
            if isinstance(current, Ternary):
                condition = resolve(current.condition, stack, seen, depth + 1)
                true_val = resolve(current.true_val, stack, seen, depth + 1)
                false_val = resolve(current.false_val, stack, seen, depth + 1)
                if condition is None or true_val is None or false_val is None:
                    return None
                return dataclasses.replace(
                    current,
                    condition=condition,
                    true_val=true_val,
                    false_val=false_val,
                )
            if isinstance(current, FuncCall):
                args = [resolve(arg, stack, seen, depth + 1) for arg in current.args]
                kwargs = {
                    name: resolve(arg, stack, seen, depth + 1)
                    for name, arg in current.kwargs.items()
                }
                if any(arg is None for arg in args) or any(
                    arg is None for arg in kwargs.values()
                ):
                    return None
                return dataclasses.replace(current, args=args, kwargs=kwargs)
            # History, collections, and every other unhandled shape are not
            # stable TA lengths.  Fail closed instead of inventing a buffer size.
            return None

        return resolve(node, helper_binding_stack)

    def _security_ta_ctor_depends_on_mutables(
        self,
        site: TACallSite,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        # A top-level ``var int L = 5`` is normally classified as mutable by
        # request.security because persistent state is rebound per requested
        # context.  The TA constructor does not read that mutable member when
        # ``L`` passed the declaration-exact stable-literal admission proof:
        # all main/requested-context buffers are sized directly from ``5``.
        # Remove only those admitted names for this ctor-dependency query.
        # Compute arguments and every other security mutability path retain
        # the full set, and helper bindings still resolve transitively before
        # checking the reduced set.
        ctor_mutable_names = security_mutable_names.difference(
            getattr(self, "_stable_var_ctor_literals", {})
        )
        return any(
            self._expr_depends_on_security_mutables(
                arg,
                ctor_mutable_names,
                helper_binding_stack=helper_binding_stack,
            )
            for arg in self._security_ta_ctor_arg_nodes(site)
        )

    def _collect_security_ta_binding_stacks(
        self,
        expr_node,
        resolving: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        collected: dict[int, tuple[dict[str, ASTNode], ...]] | None = None,
        inline_ta_indices: set[tuple] | None = None,
        inline_helper: bool = False,
    ) -> dict[int, tuple[dict[str, ASTNode], ...]]:
        if collected is None:
            collected = {}
        if expr_node is None:
            return collected
        if resolving is None:
            resolving = set()

        if isinstance(expr_node, Identifier):
            binding = None
            if not self._security_identifier_is_global_binding(expr_node):
                binding = self._security_lookup_helper_binding_context(
                    expr_node.name, helper_binding_stack
                )
            if binding is not None:
                bound, bound_stack = binding
                if isinstance(bound, str):
                    return collected
                # Guard against a cyclic helper binding: an identifier bound to
                # an expression that transitively references the same binding
                # (e.g. mutually-referential helper params) would recurse here
                # forever — the mutable/global/func paths below already carry a
                # `resolving` guard, this path did not. Keyed by the bound node
                # so acyclic bindings are unaffected (byte-identical emission).
                bind_key = f"bind:{id(bound)}"
                if bind_key in resolving:
                    return collected
                resolving.add(bind_key)
                # A helper local's value was collected at its declaration, and
                # the evaluator reads the local's C++ variable, never its value
                # again: its re-walk finds only variants nothing emits.
                local = self._security_binding_is_helper_local(
                    expr_node.name, helper_binding_stack)
                self._security_local_rewalks += local
                try:
                    self._collect_security_ta_binding_stacks(
                        bound,
                        resolving,
                        bound_stack,
                        collected,
                        inline_ta_indices,
                        inline_helper,
                    )
                finally:
                    self._security_local_rewalks -= local
                resolving.discard(bind_key)
                return collected

            # A global's TA sites belong to the evaluator's prologue (or its
            # rebinds), computed once however many helpers read the global:
            # not to the helper that reads it.
            through_global = _SECURITY_THROUGH_GLOBAL if inline_helper else False
            mutable_info = self._global_mutable_infos.get(expr_node.name)
            if mutable_info is not None and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                for stmt in getattr(mutable_info, "source_stmts", []) or []:
                    self._collect_security_ta_binding_stacks(
                        stmt,
                        resolving,
                        helper_binding_stack,
                        collected,
                        inline_ta_indices,
                        through_global,
                    )
                resolving.remove(expr_node.name)
                return collected

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if (
                self._security_identifier_is_global_binding(expr_node)
                and expr_node.name in global_expr_map
                and expr_node.name not in resolving
            ):
                resolving.add(expr_node.name)
                self._collect_security_ta_binding_stacks(
                    global_expr_map[expr_node.name],
                    resolving,
                    (),
                    collected,
                    inline_ta_indices,
                    through_global,
                )
                resolving.remove(expr_node.name)
                return collected

        if isinstance(expr_node, FuncCall):
            func_name = self._security_user_call_key(expr_node)
            if func_name is not None:
                if self._security_shared_call_key(expr_node, helper_binding_stack) is not None:
                    return collected
                # Keyed by the written call: ``u(u(close))`` reaches the inner
                # call through the outer body's parameter, in the caller's
                # scope, and its TA site needs a variant of its own (it was
                # skipped as recursion and read an undeclared base member).
                call_key = f"func:{func_name}:{id(expr_node)}"
                name_key = f"func:{func_name}"
                if call_key in resolving:
                    return collected
                if self._security_local_rewalks and name_key in resolving:
                    # Re-walking a helper local's value (``a1 = u(a0)`` over
                    # ``a0 = u(close)``): a helper already being walked is not
                    # entered again, as before the call-keyed guard. Entering
                    # it re-walked every earlier local once per read, 2**n
                    # walks over a chain of n locals, each adding a variant.
                    return collected
                if self._budget is not None:
                    self._budget.check(expr_node.loc, Phase.CODEGEN)
                resolving.add(call_key)
                added_name_key = name_key not in resolving
                if added_name_key:
                    resolving.add(name_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    self._collect_security_ta_binding_stacks(
                        plan["expr"],
                        resolving,
                        plan["binding_stack"],
                        collected,
                        inline_ta_indices,
                        inline_helper,
                    )
                else:
                    local_series_names = set(plan.get("local_series_names", ()))
                    local_ast_bindings: dict[str, object] = {}
                    linear_stack = plan["binding_stack"] + (local_ast_bindings,)

                    def collect_stmt(stmt, active_bindings: dict[str, object]) -> None:
                        local_stack = plan["binding_stack"] + (active_bindings,)

                        if isinstance(stmt, VarDecl):
                            value = stmt.value
                            if value is not None:
                                self._collect_security_ta_binding_stacks(
                                    value,
                                    resolving,
                                    local_stack,
                                    collected,
                                    inline_ta_indices,
                                    True,
                                )
                            # A ``var`` local is state the evaluator keeps in
                            # the helper's series (``emit_stmt``): its value on
                            # a requested bar is not its initializer's. Bound
                            # by value, a TA length reading it was planned from
                            # the initializer -- or, after ``c += 1``, from the
                            # literal 1 -- as a constant (the history walkers
                            # above already bind it as series).
                            if stmt.name in local_series_names or stmt.is_var:
                                active_bindings[stmt.name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{stmt.name}"
                                )
                            elif value is not None:
                                active_bindings[stmt.name] = value
                            return

                        if isinstance(stmt, Assignment):
                            value = stmt.value
                            target_name = self._get_target_name(stmt.target)
                            if value is not None:
                                self._collect_security_ta_binding_stacks(
                                    value,
                                    resolving,
                                    local_stack,
                                    collected,
                                    inline_ta_indices,
                                    True,
                                )
                            existing = (
                                active_bindings.get(target_name)
                                if target_name is not None else None
                            )
                            if (
                                target_name in local_series_names
                                or (
                                    isinstance(existing, str)
                                    and self._security_series_binding_target(existing)
                                    is not None
                                )
                            ):
                                active_bindings[target_name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{target_name}"
                                )
                            elif (target_name is not None and value is not None
                                    and self._security_rebinding_reads_itself(
                                        target_name, stmt, active_bindings)):
                                # ``c += 1`` bound the local to ``1``, and
                                # ``c := c + 1`` to an expression reading its
                                # own binding (the planner recursed forever):
                                # the local now holds a requested-bar value.
                                active_bindings[target_name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{target_name}"
                                )
                            elif target_name is not None and value is not None:
                                active_bindings[target_name] = value
                            return

                        if isinstance(stmt, IfStmt):
                            self._collect_security_ta_binding_stacks(
                                stmt.condition,
                                resolving,
                                local_stack,
                                collected,
                                inline_ta_indices,
                                True,
                            )
                            body_bindings = dict(active_bindings)
                            for child in stmt.body:
                                collect_stmt(child, body_bindings)
                            else_bindings = dict(active_bindings)
                            for child in stmt.else_body:
                                collect_stmt(child, else_bindings)
                            # A local an arm reassigns holds that arm's value
                            # on the bars it runs and its own on the others: a
                            # requested-bar value, never one expression. (A
                            # local an arm declares shadows it there.)
                            reassigned = (
                                self._security_arm_reassigned_names(stmt.body)
                                | self._security_arm_reassigned_names(stmt.else_body)
                            )
                            for name in sorted(reassigned & set(active_bindings)):
                                active_bindings[name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{name}"
                                )
                            return

                        if isinstance(stmt, (ForStmt, WhileStmt)):
                            header, counter = self._security_loop_parts(stmt)
                            for part in header:
                                if part is not None:
                                    self._collect_security_ta_binding_stacks(
                                        part,
                                        resolving,
                                        local_stack,
                                        collected,
                                        inline_ta_indices,
                                        True,
                                    )
                            body_bindings = dict(active_bindings)
                            if counter:
                                body_bindings[counter] = (
                                    self._security_loop_counter_binding(plan, counter))
                            for child in stmt.body:
                                collect_stmt(child, body_bindings)
                            # A local the loop reassigns holds the value its
                            # last iteration left on each requested bar, as an
                            # if arm's does: never one expression.
                            reassigned = self._security_arm_reassigned_names(stmt.body)
                            for name in sorted(reassigned & set(active_bindings)):
                                active_bindings[name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{name}"
                                )
                            return

                        if isinstance(stmt, TupleAssign):
                            self._collect_security_ta_binding_stacks(
                                stmt.value,
                                resolving,
                                local_stack,
                                collected,
                                inline_ta_indices,
                                True,
                            )
                            for name in stmt.names:
                                if name == "_":
                                    continue
                                active_bindings[name] = (
                                    self._security_series_binding(
                                        f"{plan['func_info'].name}:{name}"
                                    )
                                    if name in local_series_names
                                    else _security_tuple_binding(
                                        plan["func_info"].name, name
                                    )
                                )
                            return

                        if isinstance(stmt, ExprStmt):
                            self._collect_security_ta_binding_stacks(
                                stmt.expr,
                                resolving,
                                local_stack,
                                collected,
                                inline_ta_indices,
                                True,
                            )
                            return

                    for stmt in plan["body"]:
                        collect_stmt(stmt, local_ast_bindings)

                    self._collect_security_ta_binding_stacks(
                        plan["expr"],
                        resolving,
                        linear_stack,
                        collected,
                        inline_ta_indices,
                        True,
                    )
                resolving.remove(call_key)
                if added_name_key:
                    resolving.discard(name_key)
                return collected

        site = self._get_ta_site(expr_node)
        if site is not None:
            idx = self._ta_index_by_site_id.get(id(site))
            if idx is not None:
                current_sig = self._security_binding_stack_signature(helper_binding_stack)
                existing = collected.setdefault(idx, {})
                existing[current_sig] = helper_binding_stack or ()
                if inline_helper and inline_ta_indices is not None:
                    # Per variant: the same TA site outside the helper is
                    # still computed once in the evaluator's prologue.
                    inline_ta_indices.add(
                        (idx, current_sig if inline_helper is True else inline_helper)
                    )

        def walk(value) -> None:
            if value is None:
                return
            if hasattr(value, "__dict__"):
                self._collect_security_ta_binding_stacks(
                    value,
                    resolving,
                    helper_binding_stack,
                    collected,
                    inline_ta_indices,
                    inline_helper,
                )
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)

        for child in vars(expr_node).values():
            walk(child)
        return collected

    def _emit_security_rebind_var_decl(
        self,
        sec_id: int,
        node: VarDecl,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        if node.name not in relevant_names:
            return
        info = self._global_mutable_infos.get(node.name)
        if info is None:
            return

        pad = "    " * indent
        state_name = self._security_state_name(sec_id, node.name)
        init_flag = self._security_init_flag_name(sec_id, node.name)
        expr_cpp = self._build_security_expr(
            sec_id,
            node.value,
            None,
            ta_results,
            security_mutable_names=relevant_names,
            emitted_lines=emitted_lines,
        )
        expr_cpp = self._security_copy_store_cpp(node.name, info, node.value, expr_cpp)

        if getattr(info, "is_var", False):
            if getattr(info, "is_series", False):
                lines.append(f"{pad}if (!{init_flag}) {{")
                lines.append(f"{pad}    {state_name}.push({expr_cpp});")
                lines.append(f"{pad}    {init_flag} = true;")
                lines.append(f"{pad}}} else if (security_series_slot_is_new({sec_id})) {{")
                lines.append(f"{pad}    {state_name}.push({state_name}[0]);")
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}if (!{init_flag}) {{")
                lines.append(f"{pad}    {state_name} = {expr_cpp};")
                lines.append(f"{pad}    {init_flag} = true;")
                lines.append(f"{pad}}}")
            return

        if getattr(info, "is_series", False):
            lines.append(f"{pad}if (security_series_slot_is_new({sec_id})) {{")
            lines.append(f"{pad}    {state_name}.push({expr_cpp});")
            lines.append(f"{pad}}} else {{")
            lines.append(f"{pad}    {state_name}.update({expr_cpp});")
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{state_name} = {expr_cpp};")

    def _emit_security_rebind_assignment(
        self,
        sec_id: int,
        node: Assignment,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        target_name = self._get_target_name(node.target)
        if target_name not in relevant_names:
            return
        info = self._global_mutable_infos.get(target_name)
        if info is None:
            return

        pad = "    " * indent
        state_name = self._security_state_name(sec_id, target_name)
        value_cpp = self._build_security_expr(
            sec_id,
            node.value,
            None,
            ta_results,
            security_mutable_names=relevant_names,
            emitted_lines=emitted_lines,
        )

        def store(cpp: str) -> str:
            return self._security_copy_store_cpp(target_name, info, node.value, cpp)

        # A compound store of the double form computes as the chart does
        # (``/`` in double, ``%`` through std::fmod: C++ ``%`` takes no
        # double) and narrows its result.
        wide = node.op != ":=" and store(value_cpp) != value_cpp

        def combined(target_read: str) -> str:
            return (self._compound_assign_rhs(target_read, node.op, value_cpp)
                    or f"({target_read} {node.op[0]} {value_cpp})")

        if getattr(info, "is_series", False):
            if node.op == ":=":
                lines.append(f"{pad}{state_name}.update({store(value_cpp)});")
            elif wide:
                lines.append(f"{pad}{state_name}.update("
                             f"{store(combined(f'{state_name}[0]'))});")
            else:
                op_char = node.op[0]
                lines.append(f"{pad}{state_name}.update({state_name}[0] {op_char} {value_cpp});")
            return

        if node.op == ":=":
            lines.append(f"{pad}{state_name} = {store(value_cpp)};")
        elif wide:
            lines.append(f"{pad}{state_name} = {store(combined(state_name))};")
        else:
            lines.append(f"{pad}{state_name} {node.op} {value_cpp};")

    def _emit_security_rebind_stmt(
        self,
        sec_id: int,
        node: ASTNode,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        if isinstance(node, VarDecl):
            self._emit_security_rebind_var_decl(
                sec_id, node, lines, relevant_names, ta_results, indent, emitted_lines
            )
            return
        if isinstance(node, Assignment):
            self._emit_security_rebind_assignment(
                sec_id, node, lines, relevant_names, ta_results, indent, emitted_lines
            )
            return
        if isinstance(node, IfStmt):
            body_lines: list[str] = []
            else_lines: list[str] = []
            for stmt in node.body:
                self._emit_security_rebind_stmt(
                    sec_id, stmt, body_lines, relevant_names, ta_results, indent + 1, emitted_lines
                )
            for stmt in node.else_body:
                self._emit_security_rebind_stmt(
                    sec_id, stmt, else_lines, relevant_names, ta_results, indent + 1, emitted_lines
                )
            if not body_lines and not else_lines:
                return
            pad = "    " * indent
            cond_cpp = self._build_security_expr(
                sec_id,
                node.condition,
                None,
                ta_results,
                security_mutable_names=relevant_names,
                emitted_lines=emitted_lines,
            )
            lines.append(
                f"{pad}if ({self._coerce_bool_expr(cond_cpp, node.condition)}) {{"
            )
            lines.extend(body_lines)
            if else_lines:
                lines.append(f"{pad}}} else {{")
                lines.extend(else_lines)
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}}}")
            return
        if isinstance(node, SwitchStmt):
            self._codegen_error(
                node,
                "request.security mutable global rebinding does not support top-level switch",
                hint="Rewrite the switch as if/else assignments before passing the value to request.security().",
            )
        if isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
            self._codegen_error(
                node,
                "request.security mutable global rebinding does not support top-level loops",
                hint="Move loop-driven mutable state out of request.security() expressions or rewrite it as direct assignments.",
            )

    def _emit_security_rebinds(
        self,
        sec_id: int,
        info: dict,
        lines: list[str],
        ta_results: dict[int, str],
        indent: int = 2,
        emitted_lines: list[str] | None = None,
    ) -> None:
        mutable_globals = info.get("mutable_globals") or []
        if not mutable_globals:
            return
        relevant_names = set(mutable_globals)
        for stmt in self._security_relevant_top_level_stmts(mutable_globals):
            self._emit_security_rebind_stmt(
                sec_id, stmt, lines, relevant_names, ta_results, indent, emitted_lines
            )

    def _collect_security_ta_indices(self, expr_node, resolving: set[str] | None = None) -> set[int]:
        """Collect TA call-site indices used by a security expression.

        Includes TA calls reachable through global identifier bindings.
        """
        if expr_node is None:
            return set()
        if resolving is None:
            resolving = set()

        out: set[int] = set()

        if isinstance(expr_node, Identifier):
            mutable_info = self._global_mutable_infos.get(expr_node.name)
            if mutable_info is not None and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                for stmt in getattr(mutable_info, "source_stmts", []) or []:
                    out |= self._collect_security_ta_indices(stmt, resolving)
                resolving.remove(expr_node.name)
                return out

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                out |= self._collect_security_ta_indices(global_expr_map[expr_node.name], resolving)
                resolving.remove(expr_node.name)
                return out

        if isinstance(expr_node, FuncCall):
            func_name = self._security_user_call_key(expr_node)
            if func_name is not None:
                return set(
                    self._collect_security_ta_binding_stacks(
                        expr_node,
                        resolving,
                    ).keys()
                )

        out |= set(
            self._collect_security_ta_binding_stacks(
                expr_node,
                resolving,
            ).keys()
        )
        return out

    def _security_lazy_ta_keys(
        self,
        sec_id: int,
        expr_node,
        info: dict,
    ) -> set[tuple[int, tuple]]:
        """``(ta_index, binding_signature)`` pairs Pine reaches only lazily.

        Pine evaluates ``and``/``or`` with left-to-right short-circuit and a
        ternary evaluates its condition plus exactly one branch. A ``ta.*``
        call sitting in a skipped operand is **not evaluated on that bar**, so
        its series state does not advance. The evaluator used to hoist every
        collected site into an unconditional ``auto _secval_N = ...compute(...)``
        prologue, which advanced every site on every HTF bar and desynchronised
        the series against TradingView.

        A site returned here is dropped from that prologue. ``_build_security_expr``
        then falls through to its inline
        ``(security_series_slot_is_new(N) ? m.compute(a) : m.recompute(a))``
        form, emitted in expression position — C++'s own ``&&`` / ``||`` /
        ``?:`` short-circuit then advances the state exactly when Pine would,
        and the relational lowering's ``_pna_l`` / ``_pna_r`` temporaries keep
        the evaluation order left-to-right.

        Deliberately conservative — anything not provably single-reach and
        conditional keeps the existing eager hoist, so scripts without
        conditional security TA regenerate byte-identically:

        - A site reached more than once must stay hoisted: sharing one
          ``_secval_*`` is what keeps it to a single advance per bar, whereas
          two inline copies would advance it twice (or zero times).
        - Laziness does **not** propagate through a global binding. In the
          requested context that global is its own unconditional top-level
          statement, so it evaluates on every HTF bar wherever it is read
          (this is what keeps chart-side ``t0``-shaped code exact).
        - Sites with a history offset (``ta.ema(close, 55)[1]``) keep the
          committed ``_secval_*`` their per-bar Series push reads.
        - Securities that rebind mutable globals, and multi-statement helper
          calls, bail entirely: both lower through statement emitters that
          consume ``ta_results`` outside this expression.
        """
        if info.get("mutable_globals"):
            return set()

        occurrences: dict[tuple[int, tuple], list[bool]] = {}
        unsafe = False

        def walk(node, lazy: bool, binding_stack, resolving: set[str]) -> None:
            nonlocal unsafe
            if node is None or unsafe:
                return

            if isinstance(node, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(node):
                    binding = self._security_lookup_helper_binding_context(
                        node.name, binding_stack
                    )
                if binding is not None:
                    bound, bound_stack = binding
                    if isinstance(bound, str):
                        return
                    bind_key = f"bind:{id(bound)}"
                    if bind_key in resolving:
                        return
                    resolving.add(bind_key)
                    walk(bound, lazy, bound_stack, resolving)
                    resolving.discard(bind_key)
                    return

                if self._global_mutable_infos.get(node.name) is not None:
                    unsafe = True
                    return

                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (
                    self._security_identifier_is_global_binding(node)
                    and node.name in global_expr_map
                    and node.name not in resolving
                ):
                    resolving.add(node.name)
                    walk(global_expr_map[node.name], False, (), resolving)
                    resolving.remove(node.name)
                    return

            if isinstance(node, FuncCall):
                func_name = self._security_user_call_key(node)
                if func_name is not None:
                    if self._security_shared_call_key(node, binding_stack) is not None:
                        return
                    call_key = f"func:{func_name}"
                    if call_key in resolving:
                        return
                    resolving.add(call_key)
                    plan = self._security_helper_call_plan(node, binding_stack)
                    if plan["mode"] == "expr":
                        walk(plan["expr"], lazy, plan["binding_stack"], resolving)
                    else:
                        unsafe = True
                    resolving.discard(call_key)
                    return

            if isinstance(node, BinOp) and node.op in ("and", "or"):
                walk(node.left, lazy, binding_stack, resolving)
                walk(node.right, True, binding_stack, resolving)
                return

            if isinstance(node, Ternary):
                walk(node.condition, lazy, binding_stack, resolving)
                walk(node.true_val, True, binding_stack, resolving)
                walk(node.false_val, True, binding_stack, resolving)
                return

            site = self._get_ta_site(node)
            if site is not None:
                idx = self._ta_index_by_site_id.get(id(site))
                if idx is not None:
                    key = (idx, self._security_binding_stack_signature(binding_stack))
                    occurrences.setdefault(key, []).append(lazy)

            for child in vars(node).values():
                walk_value(child, lazy, binding_stack, resolving)

        def walk_value(value, lazy: bool, binding_stack, resolving: set[str]) -> None:
            if value is None or unsafe:
                return
            if hasattr(value, "__dict__"):
                walk(value, lazy, binding_stack, resolving)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk_value(item, lazy, binding_stack, resolving)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk_value(item, lazy, binding_stack, resolving)

        walk(expr_node, False, (), set())
        if unsafe:
            return set()

        hist_indices = set(self._security_ta_hist_idx_by_sec.get(sec_id, ()))
        inline_helper_ta_keys = set(info.get("inline_helper_ta_indices", []))
        return {
            key
            for key, reaches in occurrences.items()
            if key[0] not in hist_indices
            and key not in inline_helper_ta_keys
            and len(reaches) == 1
            and reaches[0]
        }

    def _emit_security_ohlc_hist_pushes(self, sec_id: int, lines: list[str]) -> None:
        """Emit the OHLC history-offset Series pushes for ``sec_id``, gated on
        ``is_complete``.

        ``request.security(..., [high[1], low[1], ...], ...)`` reads HTF OHLC at
        past-bar offsets. Each offset is backed by a per-field Series whose
        history must advance once per COMPLETED HTF bar — not once per (partial)
        chart-bar evaluation. ``_eval_security_N`` fires on every chart bar; only
        the bar that completes the HTF aggregate has ``is_complete == true``.
        Pushing unconditionally advanced the offset history every chart bar, so
        ``high[1]`` resolved to a recent partial bar instead of the prior
        completed HTF bar. Gate all pushes for this sec in one combined block."""
        fields = sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ()))
        if not fields:
            return
        lines.append("        if (is_complete) {")
        for field in fields:
            lines.append(
                f"            {self._security_ohlc_hist_series_cpp(sec_id, field)}.push({self._security_bar_field_expr(field, sec_id)});"
            )
        lines.append("        }")

    def _emit_security_ta_hist_pushes(
        self, sec_id: int, info: dict, ta_results: dict, lines: list[str]
    ) -> None:
        """Emit the TA history-offset Series pushes for ``sec_id``, gated on
        ``is_complete`` (mirrors ``_emit_security_ohlc_hist_pushes``).

        ``request.security(..., ta.ema(close, 55)[1], ...)`` reads a confirmed
        HTF TA value at a past-bar offset. The committed value (``_secval_*``,
        produced with ``.compute()`` only when ``is_complete``) is pushed onto a
        per-site Series once per COMPLETED HTF bar, AFTER the expression
        assignment so the offset read sees the prior completed bar. Pushing on
        every chart-bar eval would otherwise advance the offset history per
        partial eval / chart tick (the bug this replaces, where the chart-context
        ``_hist_call`` buffer advanced on ``is_first_tick_``)."""
        indices = sorted(self._security_ta_hist_idx_by_sec.get(sec_id, ()))
        if not indices:
            return
        pushes: list[str] = []
        for idx in indices:
            if getattr(self.ctx.ta_call_sites[idx], "returns_tuple", False):
                # Its element history is refused where it is read; the
                # struct has no Series<double> to push into.
                continue
            for variant in (info.get("ta_variants") or {}).get(idx, []):
                result_name = ta_results.get((idx, variant["signature"]))
                if result_name is None:
                    continue
                hist = self._security_ta_hist_series_cpp(variant["member_name"])
                pushes.append(f"            {hist}.push({result_name});")
        if not pushes:
            return
        lines.append("        if (is_complete) {")
        lines.extend(pushes)
        lines.append("        }")

    def _emit_security_evaluator_requested(self, item: dict, lines: list[str]) -> None:
        """Emit one evaluator, lowering its payload's user calls under builtin
        calls (``nz(f())``) and its typed method calls on the requested bar.

        All or nothing, per evaluator: that lowering is kept only when it
        leaves nothing on the chart's terms beside what it inlines -- no
        builtin call that also reads a global, history the builder does not
        own or ``bar_index`` (``_security_root_chart_read``) -- and the
        evaluator refuses none of it. Otherwise the evaluator every earlier
        build emitted is emitted instead, its calls on the chart as they were,
        with a warning: a requested ``f()`` beside the chart's ``g`` would mix
        two bars. A failure of that emission is the script's own and
        propagates, as does a limit error."""
        snapshot = self._security_state_snapshot()
        body: list[str] = []
        self._security_requested_calls = True
        self._security_requested_used = False
        self._security_chart_read = None
        reason = self._security_chart_evaluators.get(item["sec_id"])
        try:
            if reason is not None:
                raise _SecurityKeepChart()
            self._emit_security_evaluator(item, body)
        except _SecurityKeepChart:
            pass
        except Exception as exc:  # noqa: BLE001 -- retried below as every earlier build
            if getattr(exc, "limit", False):
                raise
            diagnostic = (
                exc.diagnostics[0]
                if isinstance(exc, CompileError) and exc.diagnostics else None
            )
            reason = (
                (diagnostic.location, diagnostic.message.splitlines()[0])
                if diagnostic is not None
                else (None, f"the evaluator cannot inline them there ({type(exc).__name__})")
            )
        else:
            if self._security_requested_used and self._security_chart_read is not None:
                reason = self._security_chart_read
        if reason is None:
            lines.extend(body)
            return
        self._security_state_restore(snapshot)
        body = []
        self._security_requested_calls = False
        try:
            self._emit_security_evaluator(item, body)
        finally:
            self._security_requested_calls = True
        location, message = reason
        self._codegen_warning(
            item["expr_node"],
            "request.security payload keeps its user calls under builtin calls and its "
            f"methods on the chart's bar, as before: {message}"
            + (f" (line {location.line})" if location is not None else "")
            + "; TradingView evaluates them on the requested bar.",
        )
        lines.extend(body)

    def _security_state_snapshot(self) -> tuple:
        """The generator's state before an evaluator is emitted: each
        attribute, with the contents of its containers two levels deep kept
        in place (an alias sees the restore), and the diagnostics count."""
        def contents(value, depth):
            if isinstance(value, dict):
                return ("dict", value, [
                    (k, v, contents(v, depth - 1) if depth > 1 else None)
                    for k, v in value.items()
                ])
            if isinstance(value, list):
                return ("list", value, [
                    (None, v, contents(v, depth - 1) if depth > 1 else None)
                    for v in value
                ])
            if isinstance(value, set):
                return ("set", value, list(value))
            return None
        return (
            {name: (value, contents(value, 2)) for name, value in vars(self).items()},
            len(self.ctx.diagnostics),
        )

    def _security_state_restore(self, snapshot: tuple) -> None:
        """Put back ``_security_state_snapshot``'s state in place."""
        def restore(saved) -> None:
            if saved is None:
                return
            kind, obj, items = saved
            if kind == "set":
                obj.clear()
                obj.update(items)
                return
            for _k, _v, inner in items:
                restore(inner)
            if kind == "dict":
                obj.clear()
                obj.update((k, v) for k, v, _inner in items)
            else:
                obj[:] = [v for _k, v, _inner in items]

        attrs, n_diagnostics = snapshot
        for name in list(vars(self)):
            if name not in attrs:
                delattr(self, name)
        for name, (value, saved) in attrs.items():
            setattr(self, name, value)
            restore(saved)
        del self.ctx.diagnostics[n_diagnostics:]

    @staticmethod
    def _security_info_without_methods(info: dict) -> dict:
        """``info`` without the TA variants the prepasses found inside a
        typed method call, which an evaluator lowered as every earlier build
        did keeps on the chart."""
        variants = {
            idx: [
                variant for variant in found
                if not any(getattr(frame, "method", False)
                           for frame in variant.get("binding_stack", ()))
            ]
            for idx, found in (info.get("ta_variants") or {}).items()
        }
        return dict(
            info,
            ta_variants=variants,
            ta_indices=[idx for idx in info.get("ta_indices") or [] if variants.get(idx)],
        )

    def _emit_security_prologue_ta(
        self,
        sec_id: int,
        idx: int,
        variant: dict,
        ta_results: dict,
        security_mutable_names: set[str],
        lines: list[str],
    ) -> None:
        """One TA variant's committed value in an evaluator's prologue."""
        compute_args = self._security_ta_compute_args_for_site(
            sec_id,
            self.ctx.ta_call_sites[idx],
            ta_results,
            security_mutable_names,
            variant.get("binding_stack", ()),
            emitted_lines=lines,
        )
        var_name = variant["result_name"]
        sec_name = variant["member_name"]
        lines.append(f"        auto {var_name} = security_series_slot_is_new({sec_id}) "
                     f"? {sec_name}.compute({compute_args}) "
                     f": {sec_name}.recompute({compute_args});")
        ta_results[(idx, variant["signature"])] = var_name

    def _security_note_ta_read(self, key: tuple) -> None:
        """Record a read of a TA variant's committed value while
        ``_security_prologue_order`` builds a variant's arguments."""
        reads = getattr(self, "_security_ta_reads", None)
        if reads is not None:
            reads.add(key)

    def _security_ta_sites_reached(self, site: TACallSite, binding_stack) -> set[int]:
        """Every TA site index a site's compute arguments can reach -- through
        helper bindings, globals' values, mutable globals' statements and
        user calls' bodies -- with no side effect: a superset of the
        variants building those arguments reads."""
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        reached: set[int] = set()
        seen: set[int] = set()
        stack = [(arg, binding_stack or ()) for arg in site.compute_args or []]
        while stack:
            node, frames = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend((child, frames) for child in node)
                continue
            if not isinstance(node, ASTNode) or id(node) in seen:
                continue
            seen.add(id(node))
            ta_site = self._get_ta_site(node)
            if ta_site is not None:
                index = self._ta_index_by_site_id.get(id(ta_site))
                if index is not None:
                    reached.add(index)
            if isinstance(node, Identifier):
                for frame in frames:
                    bound = frame.get(node.name) if isinstance(frame, dict) else None
                    if isinstance(bound, ASTNode):
                        stack.append((bound, getattr(frame, "caller_stack", frames)))
                if node.name in global_expr_map:
                    stack.append((global_expr_map[node.name], ()))
                info = self._global_mutable_infos.get(node.name)
                if info is not None:
                    stack.extend((stmt, ()) for stmt in getattr(info, "source_stmts", []) or [])
            elif isinstance(node, FuncCall):
                key = self._security_user_call_key(node)
                info = self._func_info_map.get(key) if key is not None else None
                if info is not None and info.node is not None:
                    stack.append((info.node.body, frames))
            stack.extend((value, frames) for name, value in vars(node).items()
                         if name not in ("annotations", "loc"))
        return reached

    def _security_prologue_order(
        self,
        sec_id: int,
        entries: list[tuple[int, dict]],
        ta_results: dict,
        security_mutable_names: set[str],
    ) -> list[tuple[int, dict]]:
        """A prologue phase's variants, each after the variants of the phase
        its compute arguments read, in their order otherwise. The top-level
        statement of the evaluator computes each once, before any reader: a
        variant reached through a global declared after the helper whose TA
        reads it (``u(_x) => ta.sma(_x, 3)``, a later ``s5 = ta.sma(close,
        5)``, ``u(s5)``) was computed inline in its reader's arguments and
        again in the prologue, advancing twice a bar.

        The order follows ``_security_ta_sites_reached``, a superset of the
        reads with no side effect: a phase none of whose variants reaches a
        later one keeps its order, and any other is sorted by that graph,
        whose every order respects the reads. Only where it has a cycle are
        the reads themselves learnt, by building each variant's arguments
        once with every side effect undone (``_security_prologue_reads``)."""
        keys = [(idx, variant["signature"]) for idx, variant in entries]
        if len(keys) < 2:
            return entries
        by_index: dict[int, list[tuple]] = {}
        for key in keys:
            by_index.setdefault(key[0], []).append(key)
        reach = {
            key: {
                other
                for reached in self._security_ta_sites_reached(
                    self.ctx.ta_call_sites[idx], variant.get("binding_stack", ())
                )
                for other in by_index.get(reached, ())
                if other != key
            }
            for (idx, variant), key in zip(entries, keys)
        }
        position = {key: n for n, key in enumerate(keys)}
        if not any(position[other] > position[key]
                   for key, others in reach.items() for other in others):
            return entries
        order = self._security_topological_order(keys, reach)
        if order is None:
            order = self._security_topological_order(
                keys,
                self._security_prologue_reads(
                    sec_id, entries, keys, ta_results, security_mutable_names
                ),
                keep_cycles=True,
            )
        by_key = dict(zip(keys, entries))
        return [by_key[key] for key in order]

    @staticmethod
    def _security_topological_order(keys: list, edges: dict, keep_cycles: bool = False):
        """``keys``, each after the keys ``edges`` gives it, stably; None on a
        cycle unless ``keep_cycles`` (a cycle then keeps its order)."""
        order: list = []
        state: dict = {}

        def visit(key) -> bool:
            if state.get(key) == 2:
                return True
            if state.get(key) == 1:
                return keep_cycles
            state[key] = 1
            for dep in keys:
                if dep in edges.get(key, ()) and not visit(dep):
                    return False
            state[key] = 2
            order.append(key)
            return True

        for key in keys:
            if not visit(key):
                return None
        return order

    def _security_prologue_reads(
        self,
        sec_id: int,
        entries: list[tuple[int, dict]],
        keys: list[tuple],
        ta_results: dict,
        security_mutable_names: set[str],
    ) -> dict[tuple, set]:
        """The phase's variants each variant's compute arguments read: its
        arguments built once, with every side effect undone
        (``_security_state_snapshot``) and every other variant standing in
        as computed, so no read of one raises before the reads after it are
        seen (``s5[1] + r``). Arguments that raise keep what they read."""
        reads: dict[tuple, set] = {}
        snapshot = self._security_state_snapshot()
        try:
            for (idx, variant), key in zip(entries, keys):
                self._security_ta_reads = set()
                computed = dict(ta_results)
                computed.update(
                    (other, f"_pf_prologue_{n}")
                    for n, other in enumerate(keys) if other != key
                )
                try:
                    self._security_ta_compute_args_for_site(
                        sec_id,
                        self.ctx.ta_call_sites[idx],
                        computed,
                        security_mutable_names,
                        variant.get("binding_stack", ()),
                        emitted_lines=[],
                    )
                except Exception as exc:  # noqa: BLE001 -- the real emission reports it
                    if getattr(exc, "limit", False):
                        raise
                reads[key] = self._security_ta_reads - {key}
        finally:
            self._security_state_restore(snapshot)
        return reads

    def _emit_security_evaluator(self, item: dict, lines: list[str]) -> None:
        """Emit one ``_eval_security_N`` method
        (``_emit_security_evaluator_body``), with the values of its long pure
        calls (``_security_share_pure_call``) computed where it opens."""
        start = len(lines)
        outer = (getattr(self, "_security_shared_calls", None),
                 getattr(self, "_security_shared_definitions", None),
                 getattr(self, "_security_shared_globals", None),
                 getattr(self, "_security_global_hist_pushes", None))
        self._security_shared_calls = {}
        self._security_shared_definitions = definitions = []
        self._security_shared_globals = {}
        self._security_global_hist_pushes = pushes = []
        try:
            self._emit_security_evaluator_body(item, lines)
        finally:
            (self._security_shared_calls, self._security_shared_definitions,
             self._security_shared_globals, self._security_global_hist_pushes) = outer
        if pushes:
            # The history of a global the payload reads at an offset
            # (``_security_global_history_value``): the value its reads keep,
            # pushed once on a completed requested bar.
            close = max(i for i in range(start, len(lines)) if lines[i] == "    }")
            lines[close:close] = (
                ["        if (is_complete) {"]
                + [f"            if ({hist}_set) {hist}.push({hist}_next);"
                   for hist, _cpp_t in pushes]
                + ["        }"])
            definitions = [
                f"        {cpp_t} {hist}_next = na<{cpp_t}>(); bool {hist}_set = false;"
                for hist, cpp_t in pushes] + definitions
        lines[start + 1:start + 1] = definitions

    def _security_share_pure_call(self, sec_id: int, text: str) -> str:
        """What a pure call (``_security_shared_call_key``) reads, at this
        reach and at every later one with the same arguments: the text it
        inlined, or, once that text is ``_SECURITY_SHARED_CALL_MIN_CHARS``
        long, ``_pf_shared_<N>_<k>``, its value computed where the evaluator
        opens. The text reads only the requested bar, literals and earlier
        such values, and evaluates nothing else: computed once, eagerly, it
        is the value every reach would compute. A diamond of helpers
        (``f1(x) => f0(x) + f0(x)``, ``f2(x) => f1(x) + f1(x)``, ...) inlined
        its leaf once per path, doubling the payload per level."""
        if len(text) < _SECURITY_SHARED_CALL_MIN_CHARS:
            return text
        definitions = self._security_shared_definitions
        name = f"_pf_shared_{sec_id}_{len(definitions)}"
        definitions.append(f"        const auto {name} = {text};")
        return name

    def _emit_security_evaluator_body(self, item: dict, lines: list[str]) -> None:
        """Emit one ``_eval_security_N`` method."""
        sec_id = item["sec_id"]
        expr_node = item["expr_node"]
        info = self._security_eval_info[sec_id]
        if not self._security_requested_calls:
            info = self._security_info_without_methods(info)
        ta_indices = info.get("ta_indices") or []
        security_mutable_names = set(info.get("mutable_globals", []))
        # A variant a multi-statement helper computes where it is inlined.
        inline_helper_ta_keys = set(info.get("inline_helper_ta_indices", []))
        lazy_ta_keys = self._security_lazy_ta_keys(sec_id, expr_node, info)

        def prologue_variants(idx: int) -> list[dict]:
            return [
                variant for variant in (info.get("ta_variants") or {}).get(idx, [])
                if (idx, variant["signature"]) not in inline_helper_ta_keys
            ]

        lines.append(f"    void _eval_security_{sec_id}(const Bar& bar, bool is_complete) {{")
        if sec_id in self._security_bar_index_secs:
            member = self._security_bar_index_member(sec_id)
            if self._security_foreign(sec_id):
                # Another symbol's context answers its own bar_index
                # (XSYM-D: the feed's bars handed over so far).
                lines.append("#ifdef PINEFORGE_HAS_SYMBOL_SECURITY_EVAL_V1")
                lines.append(f"        {member} = pine_bar_index();")
                lines.append("#else")
                lines.append(f"        if (security_series_slot_is_new({sec_id})) ++{member};")
                lines.append("#endif")
            else:
                lines.append(f"        if (security_series_slot_is_new({sec_id})) ++{member};")
        if self._security_foreign(sec_id):
            lines.append("#ifdef PINEFORGE_HAS_SYMBOL_SECURITY_EVAL_V1")
            lines.append("        _PFForeignEmaSeeding _pf_ema_seeding;")
            lines.append("#endif")

        ta_results = {}
        pre_rebind_ta_indices: list[int] = []
        post_rebind_ta_indices: list[int] = []
        for idx in ta_indices:
            variants = prologue_variants(idx)
            if not variants:
                continue
            site = self.ctx.ta_call_sites[idx]
            depends_on_mutables = False
            for variant in variants:
                helper_binding_stack = variant.get("binding_stack", ())
                if self._security_ta_ctor_depends_on_mutables(
                    site,
                    security_mutable_names,
                    helper_binding_stack,
                ) and self._ta_security_plan(
                    sec_id, site, helper_binding_stack
                ) is not None:
                    # The lowered length is read per call (a series one
                    # through the rebound value): run after the rebinds.
                    depends_on_mutables = True
                elif self._security_ta_ctor_depends_on_mutables(
                    site,
                    security_mutable_names,
                    helper_binding_stack,
                ):
                    self._codegen_error(
                        site.node or expr_node,
                        "request.security does not support TA constructor args that depend on rebound mutable globals",
                        hint="Keep TA constructor arguments immutable/simple inside request.security(), or hoist the TA call outside the security expression.",
                    )
                if self._security_ta_depends_on_mutables(
                    site,
                    security_mutable_names,
                    helper_binding_stack,
                ):
                    depends_on_mutables = True
            if depends_on_mutables:
                post_rebind_ta_indices.append(idx)
            else:
                pre_rebind_ta_indices.append(idx)

        def emit_security_ta(indices: list[int]) -> None:
            # Pine only reaches a lazy site through a short-circuited operand
            # or an untaken ternary branch. Leave it out of the eager
            # prologue: _build_security_expr emits its compute()/recompute()
            # inline in expression position, where C++'s &&/||/?:
            # short-circuit advances the series exactly on the bars Pine does.
            entries = [
                (idx, variant)
                for idx in indices
                for variant in prologue_variants(idx)
                if (idx, variant["signature"]) not in lazy_ta_keys
            ]
            for idx, variant in self._security_prologue_order(
                sec_id, entries, ta_results, security_mutable_names,
            ):
                self._emit_security_prologue_ta(
                    sec_id, idx, variant, ta_results, security_mutable_names, lines,
                )

        emit_security_ta(pre_rebind_ta_indices)

        self._emit_security_rebinds(sec_id, info, lines, ta_results, indent=2, emitted_lines=lines)
        emit_security_ta(post_rebind_ta_indices)
        returns_tuple = item.get("returns_tuple", False)
        tuple_size = item.get("tuple_size", 0)
        if (
            returns_tuple
            and tuple_size
            and tuple_size > 0
            and isinstance(expr_node, TupleLiteral)
        ):
            # A tuple body destructures into per-element scalar members
            # ``_req_sec_{sec_id}_{i}`` (declared in ``base.py`` and reset in
            # ``clear_security``). Assign each element individually rather
            # than building the whole ``TupleLiteral`` (which lowers to an
            # ``std::make_tuple(...)`` against the non-existent aggregate
            # member ``_req_sec_{sec_id}``).
            for i, el in enumerate(expr_node.elements):
                el_cpp = self._build_security_expr(
                    sec_id,
                    el,
                    None,
                    ta_results,
                    security_mutable_names=security_mutable_names,
                    emitted_lines=lines,
                )
                if (self._security_foreign(sec_id)
                        and self._infer_cpp_type_for_security_elem(el) == "int"):
                    # Held as a double (base.py): the integer's na stays na.
                    el_cpp = ("[](auto _pf_v) { return is_na(_pf_v) ? na<double>() "
                              f": static_cast<double>(_pf_v); }}({el_cpp})")
                lines.append(f"        _req_sec_{sec_id}_{i} = {el_cpp};")
            self._emit_security_ohlc_hist_pushes(sec_id, lines)
            self._emit_security_ta_hist_pushes(sec_id, info, ta_results, lines)
            lines.append("    }")
            lines.append("")
            return
        expr_cpp = self._build_security_expr(
            sec_id,
            expr_node,
            None,
            ta_results,
            security_mutable_names=security_mutable_names,
            emitted_lines=lines,
        )
        if item.get("is_lower_tf_array"):
            # ``request.security_lower_tf`` accumulates one element per
            # synthesised sub-bar of the current chart bar. The runtime's
            # ``feed_security_eval_state`` resets ``lower_tf_sub_bar_index``
            # to 0 at the start of every chart bar's synthesis loop, so
            # we clear the vector on index 0 and push for every sub-bar
            # (including index 0).
            lines.append(
                f"        if (security_lower_tf_sub_bar_index({sec_id}) == 0)"
                f" _req_sec_lower_tf_{sec_id}.clear();"
            )
            lines.append(
                f"        _req_sec_lower_tf_{sec_id}.push_back({expr_cpp});"
            )
        else:
            lines.append(f"        _req_sec_{sec_id} = {expr_cpp};")
        self._emit_security_ohlc_hist_pushes(sec_id, lines)
        self._emit_security_ta_hist_pushes(sec_id, info, ta_results, lines)
        lines.append("    }")
        lines.append("")

    def _emit_security_evaluators(self, lines: list[str]) -> None:
        """Emit _eval_security_N() methods and evaluate_security() dispatch."""
        if not self._security_calls:
            return

        for item in self._security_calls:
            self._emit_security_evaluator_requested(item, lines)

        # Dispatch method. Security evaluators fire BEFORE on_bar, so we also
        # gate a TA reset here: whichever path fires first (evaluate_security
        # on the bar the HTF aggregator first completes, or on_bar on bar 0)
        # will run the reset and set _ta_initialized_. This makes sure security
        # TA objects use runtime-resolved ctor args on their very first compute.
        lines.append("    void evaluate_security(int sec_id, const Bar& bar, bool is_complete) override {")
        self._emit_ta_runtime_reset(lines, indent=2)
        lines.append("        switch (sec_id) {")
        for item in self._security_calls:
            sec_id = item["sec_id"]
            lines.append(f"            case {sec_id}: _eval_security_{sec_id}(bar, is_complete); break;")
        lines.append("        }")
        lines.append("    }")

        lines.append("    void clear_security(int sec_id) override {")
        lines.append("        switch (sec_id) {")
        for item in self._security_calls:
            sec_id = item["sec_id"]
            expr_node = item["expr_node"]
            returns_tuple = item.get("returns_tuple", False)
            tuple_size = item.get("tuple_size", 0)
            if self._security_foreign(sec_id):
                self._emit_foreign_security_clear(item, lines)
                continue
            if item.get("is_lower_tf_array"):
                # The accumulator is reset on each sub-bar 0 inside the
                # eval method itself, so ``clear_security`` only needs to
                # forget the previous chart bar's contents (e.g. when
                # gaps mode flushes between completions). Clearing the
                # vector is the right fallback.
                lines.append(f"            case {sec_id}:")
                lines.append(f"                _req_sec_lower_tf_{sec_id}.clear();")
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
                continue
            if returns_tuple and tuple_size and tuple_size > 0 and isinstance(expr_node, TupleLiteral):
                lines.append(f"            case {sec_id}:")
                for i, el in enumerate(expr_node.elements):
                    ctype = self._infer_cpp_type_for_security_elem(el)
                    if ctype == "double":
                        lines.append(f"                _req_sec_{sec_id}_{i} = na<double>();")
                    elif ctype == "bool":
                        lines.append(f"                _req_sec_{sec_id}_{i} = false;")
                    elif ctype == "int":
                        lines.append(f"                _req_sec_{sec_id}_{i} = 0;")
                    elif ctype == "std::string":
                        lines.append(f'                _req_sec_{sec_id}_{i} = std::string("");')
                    elif ctype == "std::vector<double>":
                        lines.append(f"                _req_sec_{sec_id}_{i}.clear();")
                    else:
                        lines.append(f"                _req_sec_{sec_id}_{i} = 0;")
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
            elif returns_tuple and tuple_size and tuple_size > 0:
                site = self._get_ta_site(expr_node)
                ta_name = self._ta_name_from_site(site) if site is not None else ""
                ctype = {
                    "macd": "ta::MACDResult",
                    "supertrend": "ta::SupertrendResult",
                    "dmi": "ta::DMIResult",
                    "bb": "ta::BBResult",
                    "kc": "ta::KCResult",
                    "vwap_bands": "ta::VWAPBandsResult",
                }.get(
                    ta_name,
                    self._security_helper_tuple_cpp_type(
                        tuple_size,
                        item.get("tuple_element_types", ()),
                    ),
                )
                lines.append(f"            case {sec_id}:")
                lines.append(
                    f"                _req_sec_{sec_id} = "
                    f"{self._security_tuple_result_default(ctype, tuple_size, item.get('tuple_element_types', ()))};"
                )
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
            else:
                hist = self._security_ohlc_hist_fields_by_sec.get(sec_id, ())
                ta_hist_names = self._security_ta_hist_series_names(sec_id)
                expr_hist_names = self._security_expr_hist_series_names(sec_id)
                na_cpp = (
                    "na<std::string>()" if item.get("string_result") else "na<double>()"
                )
                if hist or ta_hist_names or expr_hist_names:
                    lines.append(f"            case {sec_id}:")
                    lines.append(f"                _req_sec_{sec_id} = {na_cpp};")
                    for field in sorted(hist):
                        lines.append(
                            f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                        )
                    for name in ta_hist_names:
                        lines.append(f"                {name}.clear();")
                    for name in expr_hist_names:
                        lines.append(f"                {name}.clear();")
                    lines.append("                break;")
                else:
                    lines.append(f"            case {sec_id}: _req_sec_{sec_id} = {na_cpp}; break;")
        lines.append("        }")
        lines.append("    }")

    def _emit_foreign_security_clear(self, item: dict, lines: list[str]) -> None:
        """``clear_security`` of another symbol's site: the engine calls it
        under gaps_on on a chart bar the feed handed nothing, so the value
        reads na there. The requested context's own history (``close[1]``,
        ``ta.*``) is that symbol's and carries on at its next bar."""
        sec_id = item["sec_id"]
        expr_node = item["expr_node"]
        returns_tuple = item.get("returns_tuple", False)
        tuple_size = item.get("tuple_size", 0)
        lines.append(f"            case {sec_id}:")
        if returns_tuple and tuple_size and tuple_size > 0 and isinstance(expr_node, TupleLiteral):
            for i, el in enumerate(expr_node.elements):
                ctype = self._infer_cpp_type_for_security_elem(el)
                value = {
                    "double": "na<double>()",
                    "bool": "false",
                    "int": "na<double>()",
                    "std::string": 'std::string("")',
                }.get(ctype)
                if ctype == "std::vector<double>":
                    lines.append(f"                _req_sec_{sec_id}_{i}.clear();")
                else:
                    lines.append(f"                _req_sec_{sec_id}_{i} = "
                                 f"{value or self._default_for_type(ctype)};")
        elif returns_tuple and tuple_size and tuple_size > 0:
            site = self._get_ta_site(expr_node)
            ta_name = self._ta_name_from_site(site) if site is not None else ""
            ctype = {
                "macd": "ta::MACDResult",
                "supertrend": "ta::SupertrendResult",
                "dmi": "ta::DMIResult",
                "bb": "ta::BBResult",
                "kc": "ta::KCResult",
                "vwap_bands": "ta::VWAPBandsResult",
            }.get(
                ta_name,
                self._security_helper_tuple_cpp_type(
                    tuple_size, item.get("tuple_element_types", ())),
            )
            default = self._security_tuple_result_default(
                ctype, tuple_size, item.get("tuple_element_types", ()))
            lines.append(f"                _req_sec_{sec_id} = {default};")
        else:
            na_cpp = "na<std::string>()" if item.get("string_result") else "na<double>()"
            lines.append(f"                _req_sec_{sec_id} = {na_cpp};")
        lines.append("                break;")

    def _build_security_expr(
        self,
        sec_id: int,
        expr_node,
        ta_range,
        ta_results: dict,
        resolving: set[str] | None = None,
        security_mutable_names: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        emitted_lines: list[str] | None = None,
    ) -> str:
        """Build C++ expression for a security evaluator."""
        if expr_node is None:
            return "na<double>()"
        column = (getattr(expr_node, "annotations", None) or {}).get(FOOTPRINT_COLUMN_ANNOTATION)
        if column is not None and self._security_foreign(sec_id):
            # request.footprint(...) of another symbol: the delta its feed
            # records for the requested bar (the value its delta() reads).
            return f'_pf_symbol_column({sec_id}, "{column}")'

        if resolving is None:
            resolving = set()
        if security_mutable_names is None:
            security_mutable_names = set()
        if helper_binding_stack is None:
            helper_binding_stack = ()

        if isinstance(expr_node, Identifier):
            raw_cpp = self._security_raw_cpp.get(expr_node.name)
            if raw_cpp is not None:
                return raw_cpp
            binding = None
            if not self._security_identifier_is_global_binding(expr_node):
                binding = self._security_lookup_helper_binding_context(
                    expr_node.name, helper_binding_stack
                )
            if binding is not None:
                bound, bound_stack = binding
                if isinstance(bound, str):
                    series_name = self._security_series_binding_target(bound)
                    if series_name is not None:
                        return f"{self._security_helper_series_ref(series_name)}[0]"
                    return bound
                return self._build_security_expr(
                    sec_id,
                    bound,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    bound_stack,
                    emitted_lines,
                )
            bar_fields = {
                **SECURITY_BAR_FIELD_EXPRS,
                "hl2": "((bar.high + bar.low) / 2.0)",
                "hlc3": "((bar.high + bar.low + bar.close) / 3.0)",
                "ohlc4": "((bar.open + bar.high + bar.low + bar.close) / 4.0)",
            }
            if expr_node.name in bar_fields:
                return bar_fields[expr_node.name]
            if expr_node.name == "time_close":
                return self._security_bar_field_expr("time_close", sec_id)

            if expr_node.name in security_mutable_names:
                info = self._global_mutable_infos.get(expr_node.name)
                state_name = self._security_state_name(sec_id, expr_node.name)
                if info is not None and getattr(info, "is_series", False):
                    return f"{state_name}[0]"
                return state_name

            if self._security_is_bar_index(expr_node) and sec_id in self._security_bar_index_secs:
                return self._security_bar_index_member(sec_id)

            var_input = self._security_var_input_call(expr_node)
            if var_input is not None:
                return self._build_security_expr(
                    sec_id, var_input, ta_range, ta_results, resolving,
                    security_mutable_names, (), emitted_lines,
                )

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if (
                self._security_identifier_is_global_binding(expr_node)
                and expr_node.name in global_expr_map
                and expr_node.name not in resolving
            ):
                value = global_expr_map[expr_node.name]
                # A global bound to a user call is one value per requested
                # bar, however many reads the payload makes (``g - g[1]``):
                # every read inlined the call again, which advanced its TA
                # state once per read.
                shared_globals = getattr(self, "_security_shared_globals", None)
                shares = (shared_globals is not None
                          and isinstance(value, FuncCall)
                          and isinstance(value.callee, Identifier)
                          and self._security_user_call_key(value) is not None)
                if shares and (sec_id, expr_node.name) in shared_globals:
                    return shared_globals[(sec_id, expr_node.name)]
                resolving.add(expr_node.name)
                resolved = self._build_security_expr(
                    sec_id,
                    value,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    (),
                    emitted_lines,
                )
                resolving.remove(expr_node.name)
                if shares:
                    shared_globals[(sec_id, expr_node.name)] = resolved
                field = self._security_ta_tuple_element_field(expr_node.name)
                if field is None and expr_node.name in self._direct_program_tuple_binding_names:
                    # The whole tuple value, not the element (a user
                    # function's tuple would be inlined per element read).
                    self._codegen_error(
                        expr_node,
                        f"request.security payload reads '{expr_node.name}', an element of a "
                        "tuple declaration it cannot re-evaluate",
                    )
                if field is not None:
                    # ``[m, s, h] = ta.macd(...)``: the name's value is the
                    # whole TA call; read its element of the committed result.
                    if not resolved.isidentifier():
                        self._codegen_error(
                            expr_node,
                            f"request.security payload reads '{expr_node.name}', an element "
                            "of a TA tuple it does not compute once per requested bar",
                        )
                    return f"{resolved}.{field}"
                return resolved

        if (
            isinstance(expr_node, MemberAccess)
            and isinstance(expr_node.object, Identifier)
            and expr_node.object.name == "timeframe"
        ):
            resolved = self._build_security_timeframe_member(sec_id, expr_node.member)
            if resolved is not None:
                return resolved

        if (isinstance(expr_node, Subscript)
                and self._security_is_bar_index(expr_node.object)
                and sec_id in self._security_bar_index_secs):
            # ``bar_index[k]``: k requested bars back, na before the first.
            member = self._security_bar_index_member(sec_id)
            offset = self._build_security_expr(
                sec_id, expr_node.index, ta_range, ta_results, resolving,
                security_mutable_names, helper_binding_stack, emitted_lines,
            )
            return (f"([&]() -> double {{ auto _pf_bar_back = ({offset}); "
                    f"if (is_na(_pf_bar_back) || {member} - _pf_bar_back < 0) "
                    f"return na<double>(); return (double)({member} - _pf_bar_back); }}())")

        if isinstance(expr_node, Subscript):
            if isinstance(expr_node.object, Identifier):
                binding = None
                if not self._security_identifier_is_global_binding(expr_node.object):
                    binding = self._security_lookup_helper_binding_context(
                        expr_node.object.name, helper_binding_stack
                    )
                if binding is None:
                    self._security_warn_unbound_param(expr_node.object)
                if binding is not None:
                    bound, bound_stack = binding
                    if isinstance(bound, str):
                        series_name = self._security_series_binding_target(bound)
                        if series_name is not None:
                            index_cpp = self._build_security_expr(
                                sec_id,
                                expr_node.index,
                                ta_range,
                                ta_results,
                                resolving,
                                security_mutable_names,
                                helper_binding_stack,
                                emitted_lines,
                            )
                            return (
                                f"{self._security_helper_series_ref(series_name)}"
                                f"[{index_cpp}]"
                            )
                        return bound
                    # Function parameters retain Pine's series identity. Apply
                    # history to the supported bound bar series and compose
                    # nested offsets before lowering. Unsupported scalar/
                    # expression bindings fail closed in the shared helper.
                    local_index = expr_node.index
                    resolved_local_index = self._resolve_security_index_literal(
                        expr_node.index, helper_binding_stack
                    )
                    if resolved_local_index is not None:
                        local_index = NumberLiteral(value=resolved_local_index)
                    else:
                        # ``src[k]`` with ``k`` a helper local (``src[mHiAgo]``):
                        # the index belongs to this helper's scope, not to the
                        # caller scope the bound series is lowered in. Lower it
                        # here and hand the C++ over by name, outside the
                        # binding stack (whose shape keys the TA variants).
                        index_name = f"__pf_security_index_{id(expr_node)}"
                        self._security_raw_cpp[index_name] = self._build_security_expr(
                            sec_id,
                            expr_node.index,
                            ta_range,
                            ta_results,
                            resolving,
                            security_mutable_names,
                            helper_binding_stack,
                            emitted_lines,
                        )
                        local_index = Identifier(name=index_name)
                    composed = self._compose_security_helper_history_subscript(
                        bound,
                        local_index,
                        expr_node,
                    )
                    return self._build_security_expr(
                        sec_id,
                        composed,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        bound_stack,
                        emitted_lines,
                    )
                field = self._security_bar_history_field(expr_node.object)
                if field is not None:
                    idx_lit = self._resolve_security_index_literal(
                        expr_node.index,
                        helper_binding_stack,
                    )
                    if idx_lit is not None:
                        if idx_lit == 0:
                            return self._security_bar_field_expr(field, sec_id)
                        if idx_lit >= 1:
                            # lookahead_off: we evaluate when an HTF bar completes; `bar` is that
                            # bar. On the HTF series, high[0]/time[0] is the current
                            # (just-finished) bar; high[1]/time[1] is one HTF bar back
                            # = hist[field][0] *before* we push `bar` (Series [0] =
                            # most recent prior push). field[k] -> hist[k-1].
                            hist = self._security_ohlc_hist_series_cpp(sec_id, field)
                            return f"{hist}[{idx_lit - 1}]"
                    if idx_lit is not None:
                        self._codegen_error(
                            expr_node,
                            "request.security() bar-field history index must be non-negative",
                        )
                    hist = self._security_ohlc_hist_series_cpp(sec_id, field)
                    cpp_t = self._security_bar_hist_type(field)
                    current = self._security_bar_field_expr(field, sec_id)
                    index_cpp = self._build_security_expr(
                        sec_id,
                        expr_node.index,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        helper_binding_stack,
                        emitted_lines,
                    )
                    index_cpp = self._coerce_int_slot_with_cast(
                        index_cpp, expr_node.index, "int"
                    )
                    return (
                        f"([&]() -> {cpp_t} {{ "
                        f"int _hidx = {index_cpp}; "
                        f"if (is_na(_hidx)) return na<{cpp_t}>(); "
                        f"return (_hidx <= 0) ? {current} : {hist}[_hidx - 1]; "
                        f"}}())"
                    )

                # Indirect TA binding: ``v = ta.ema(close, 55)`` then
                # ``request.security(..., v[1], ...)``. _get_ta_site below only
                # matches the literal ta.* FuncCall node by identity, so a bare
                # Identifier subscript target silently misses it and falls
                # through to a chart-resolution read of the wrong (non-HTF)
                # series. Resolve through the same global_expr_map the
                # non-subscript Identifier path above already uses, and
                # recurse on a synthetic Subscript over the resolved value so
                # it re-enters this whole branch (TA site, OHLC field, or
                # helper binding, whichever the resolved expression turns out
                # to be) instead of duplicating that dispatch here.
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (
                    self._security_identifier_is_global_binding(expr_node.object)
                    and expr_node.object.name in self._direct_program_tuple_binding_names
                    and expr_node.object.name not in self._global_mutable_infos
                ):
                    # Its value is the whole tuple: every earlier build
                    # emitted the tuple's history, which did not compile.
                    self._codegen_error(
                        expr_node,
                        f"request.security payload reads the history of "
                        f"'{expr_node.object.name}', an element of a tuple declaration",
                    )
                if (
                    self._security_identifier_is_global_binding(expr_node.object)
                    and expr_node.object.name in global_expr_map
                    and expr_node.object.name not in resolving
                    and not self._security_reads_global_history(sec_id, expr_node)
                ):
                    resolving.add(expr_node.object.name)
                    resolved = self._build_security_expr(
                        sec_id,
                        Subscript(object=global_expr_map[expr_node.object.name], index=expr_node.index),
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        helper_binding_stack,
                        emitted_lines,
                    )
                    resolving.remove(expr_node.object.name)
                    return resolved
            if (
                (isinstance(expr_node.object, FuncCall)
                 and self._get_ta_site(expr_node.object) is None)
                # ``(close > ta.ema(close, n))[1]``: history of an operator
                # expression on the requested clock, one value per completed
                # requested bar, like a helper call result.
                or (self._is_compound_history_object(expr_node.object)
                    and (sec_id, id(expr_node)) in self._security_expr_hist_by_node)
                # ``g[1]`` of a global bound to a user call or an operator
                # expression: the history of the value the payload reads as
                # ``g`` (``_security_global_history_value``).
                or self._security_reads_global_history(sec_id, expr_node)
            ):
                meta = self._security_expr_hist_by_node.get((sec_id, id(expr_node)))
                if meta is None:
                    # Not registered (the prepasses walk the payload and the
                    # globals it reads, not every helper body): no history.
                    self._codegen_error(
                        expr_node,
                        "request.security helper call history is only supported in the payload itself",
                    )
                hist = meta["name"]
                cpp_t = meta["type"]
                index_cpp = self._build_security_expr(
                    sec_id,
                    expr_node.index,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
                index_cpp = self._coerce_int_slot_with_cast(
                    index_cpp, expr_node.index, "int"
                )
                inner = self._build_security_expr(
                    sec_id,
                    expr_node.object,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
                pushes = getattr(self, "_security_global_hist_pushes", None)
                if pushes is not None and self._security_reads_global_history(sec_id, expr_node):
                    # A helper inlines its argument at every read of its
                    # parameter, so this read can be emitted more than once:
                    # each keeps the value, and the evaluator pushes it once
                    # where it closes (``_emit_security_evaluator``), after
                    # every read has seen the history before this bar.
                    if (hist, cpp_t) not in pushes:
                        pushes.append((hist, cpp_t))
                    return (
                        f"([&]() -> {cpp_t} {{ "
                        f"{cpp_t} _hv = ({inner}); "
                        f"if (is_complete) {{ {hist}_next = _hv; {hist}_set = true; }} "
                        f"int _hidx = {index_cpp}; "
                        f"if (is_na(_hidx)) return na<{cpp_t}>(); "
                        f"return (_hidx <= 0) ? _hv : {hist}[_hidx - 1]; }}())"
                    )
                return (
                    f"([&]() -> {cpp_t} {{ "
                    f"{cpp_t} _hv = ({inner}); "
                    f"int _hidx = {index_cpp}; "
                    f"if (is_na(_hidx)) return na<{cpp_t}>(); "
                    f"{cpp_t} _out = (_hidx <= 0) ? _hv : {hist}[_hidx - 1]; "
                    f"if (is_complete) {hist}.push(_hv); "
                    f"return _out; }}())"
                )
            ta_site = self._get_ta_site(expr_node.object)
            if ta_site is not None:
                # ``ta.<fn>(...)[k]`` inside request.security(): the inner TA call
                # runs in the HTF (security) context and commits one value per
                # COMPLETED HTF bar. Read the already-emitted security TA result —
                # offset 0 reuses the current committed value (``_secval_*``),
                # offset k>=1 reads a per-site Series that advances on
                # ``is_complete`` (HTF-bar boundary) in ``_eval_security_N``. The
                # buggy generic path re-lowered the inner TA to the CHART member
                # and gated a ``_hist_call`` buffer on ``is_first_tick_`` (chart
                # tick), so without a magnifier it advanced every chart bar and
                # produced the chart-tf TA instead of the confirmed HTF value.
                idx = self._ta_index_by_site_id.get(id(ta_site))
                # A top-level TA site reached through a global alias owns the
                # global variant even when the subscript index was authored
                # in a helper.  Keep the helper stack for resolving that
                # index, but do not use it to select the TA object's state.
                ta_binding_stack = (
                    ()
                    if getattr(ta_site, "owner_func", None) is None
                    else helper_binding_stack
                )
                sig = self._security_binding_stack_signature(ta_binding_stack)
                idx_lit = self._resolve_security_index_literal(
                    expr_node.index, helper_binding_stack
                )
                if idx_lit is None:
                    resolved_input = self._resolve_security_immutable_input_int(
                        expr_node.index, helper_binding_stack
                    )
                    if (
                        resolved_input is None
                        and self._security_stable_value_type(
                            expr_node.index, helper_binding_stack
                        ) == "int"
                    ):
                        # A bar-invariant int over inputs (``n = math.round(
                        # nMin / 15)``) reads the same history offset on every
                        # requested bar; lowered in the requested context.
                        resolved_input = (expr_node.index, helper_binding_stack)
                    if resolved_input is None:
                        self._codegen_error(
                            expr_node,
                            "request.security() TA history index must be a literal integer (e.g. ta.ema(close, 55)[1])",
                            hint=(
                                "An int computed from inputs and literals is admitted; "
                                "a series index is not."
                            ),
                        )
                    input_node, input_stack = resolved_input
                    # An evaluator can run before on_bar sets the input
                    # members: read each input through its getter.
                    saved_index_inputs = self._security_index_inputs
                    self._security_index_inputs = True
                    try:
                        index_cpp = self._build_security_expr(
                            sec_id,
                            input_node,
                            ta_range,
                            ta_results,
                            resolving,
                            security_mutable_names,
                            input_stack,
                            emitted_lines,
                        )
                    finally:
                        self._security_index_inputs = saved_index_inputs
                    index_cpp = self._coerce_int_slot_with_cast(
                        index_cpp, expr_node.index, "int"
                    )
                    result_key = (idx, sig)
                    self._security_note_ta_read(result_key)
                    if result_key not in ta_results:
                        self._codegen_error(
                            expr_node,
                            "request.security multi-statement helper TA history is not supported",
                            hint="Use a single-expression helper for TA history offsets.",
                        )
                    current = self._build_security_expr(
                        sec_id,
                        expr_node.object,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        ta_binding_stack,
                        emitted_lines,
                    )
                    member_name = self._security_ta_variant_names.get(
                        (sec_id, idx, sig),
                        f"_sec{sec_id}_{ta_site.member_name}",
                    )
                    hist = self._security_ta_hist_series_cpp(member_name)
                    return (
                        "([&]() -> double { "
                        f"int _hidx = {index_cpp}; "
                        "if (is_na(_hidx) || _hidx < 0) return na<double>(); "
                        f"return (_hidx == 0) ? {current} : {hist}[_hidx - 1]; "
                        "}())"
                    )
                if idx_lit < 0:
                    self._codegen_error(
                        expr_node,
                        "request.security() TA history index must be non-negative",
                    )
                if idx_lit == 0:
                    # Current completed-HTF-bar value: reuse the bare-TA emission.
                    return self._build_security_expr(
                        sec_id,
                        expr_node.object,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        ta_binding_stack,
                        emitted_lines,
                    )
                self._security_note_ta_read((idx, sig))
                if (idx, sig) not in ta_results:
                    self._codegen_error(
                        expr_node,
                        "request.security multi-statement helper TA history is not supported",
                        hint="Use a single-expression helper for TA history offsets.",
                    )
                member_name = self._security_ta_variant_names.get(
                    (sec_id, idx, sig),
                    f"_sec{sec_id}_{ta_site.member_name}",
                )
                hist = self._security_ta_hist_series_cpp(member_name)
                # ta(...)[k] -> hist[k-1]: hist[0] is the prior completed HTF bar
                # (current value not yet pushed — push happens after this assign).
                return f"{hist}[{idx_lit - 1}]"

        if isinstance(expr_node, BinOp):
            left = self._build_security_expr(
                sec_id, expr_node.left, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            right = self._build_security_expr(
                sec_id, expr_node.right, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            wide_na = False
            if (self._int_arith_leaves_int32(expr_node)
                    # Operands spelled as int literals keep their fold
                    # (``_fold_int32_overflow_cpp`` in ``lower`` below).
                    and self._fold_int32_overflow_cpp(expr_node.op, left, right) is None
                    and not any(
                        self._security_emits_double(side, helper_binding_stack)
                        for side in (expr_node.left, expr_node.right))
                    and not any(
                        isinstance(sub, Identifier) and any(
                            sub.name in frame
                            for frame in helper_binding_stack or ())
                        for sub, _depth in iter_ast_nodes(expr_node))):
                # 32-bit int operands whose value can leave int32 (``bar_index
                # * 7200000``): Pine's int is 64-bit, as on the chart; an
                # operand that can be na makes the value na (the double form
                # of ``_wide_int_arith_cpp``: the requested value is a double).
                # A side the builder re-evaluates as a double (``int n =
                # math.round(x)``) already computes in double and keeps its
                # NaN, and a name a helper binds here is its argument, which
                # the chart's rule does not see.
                if self._int_operand_may_be_na(expr_node):
                    wide_na = True
                else:
                    left = f"static_cast<int64_t>({left})"

            def lower(left: str, right: str) -> str:
                cpp_ops = {"and": "&&", "or": "||"}
                op = cpp_ops.get(expr_node.op, expr_node.op)
                if expr_node.op in ("and", "or"):
                    left = self._coerce_bool_expr(left, expr_node.left)
                    right = self._coerce_bool_expr(right, expr_node.right)
                if expr_node.op == "%":
                    return f"std::fmod((double)({left}), (double)({right}))"
                # Pine v6 ``/`` yields a float on int operands too (the chart's
                # ``_visit_binop``); C++ divides two ints as integers. A double
                # operand already divides in floating point and keeps its spelling.
                if expr_node.op == "/" and not any(
                    self._security_emits_double(side, helper_binding_stack)
                    for side in (expr_node.left, expr_node.right)
                ):
                    return f"((double)({left}) / (double)({right}))"
                # A global is expanded into its declaration and a helper
                # parameter into its argument here, so ``400 * step`` over
                # ``step = 2 * 60 * 60 * 1000`` is C++ ``int`` literal
                # arithmetic: a result beyond int32 is a 64-bit Pine int.
                folded_cpp = self._fold_int32_overflow_cpp(expr_node.op, left, right)
                if folded_cpp is not None:
                    return folded_cpp
                # KI-71: honour Pine's falsy-on-na relational rule inside
                # request.security expressions too (this builder is a second
                # relational emission site independent of _visit_binop).
                return self._lower_relational(op, expr_node.left, expr_node.right, left, right)

            # The left operand first, as on the chart (``_left_operand_first``).
            if wide_na:
                return self._left_operand_first(
                    expr_node, left, right,
                    lambda left, right: self._wide_int_arith_cpp(
                        expr_node, left, right, lower))
            return self._left_operand_first(expr_node, left, right, lower)

        if isinstance(expr_node, UnaryOp):
            operand = self._build_security_expr(
                sec_id, expr_node.operand, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            if expr_node.op == "not":
                return f"!({self._coerce_bool_expr(operand, expr_node.operand)})"
            return unary_sign_cpp(expr_node.op, operand)

        if isinstance(expr_node, Ternary):
            cond = self._build_security_expr(
                sec_id, expr_node.condition, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            tv = self._build_security_expr(
                sec_id, expr_node.true_val, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            fv = self._build_security_expr(
                sec_id, expr_node.false_val, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            cond = self._coerce_bool_expr(cond, expr_node.condition)
            return f"(({cond}) ? ({tv}) : ({fv}))"

        if isinstance(expr_node, SwitchStmt):
            # A helper local holding a switch (``ma = switch maType``): the
            # arm its selector takes, as a ternary chain, which evaluates no
            # other arm (TradingView's switch does not either). It used to
            # render as ``/* unknown */``, which did not compile.
            return self._build_security_expr(
                sec_id, self._security_switch_as_ternary(expr_node), ta_range, ta_results,
                resolving, security_mutable_names, helper_binding_stack, emitted_lines,
            )

        if isinstance(expr_node, TupleLiteral):
            # A tuple returned by a user helper must lower every element in the
            # requested context.  Falling through to the ordinary expression
            # visitor loses AST-valued helper parameter bindings (for example
            # ``f(src) => [src, src + 1]``) and can emit an undeclared ``src``.
            # Recursive lowering also composes helper locals, HTF history, and
            # TA results for arbitrary supported tuple arity.
            elements = [
                self._build_security_expr(
                    sec_id,
                    element,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
                for element in expr_node.elements
            ]
            return f"std::make_tuple({', '.join(elements)})"

        if (
            isinstance(expr_node, FuncCall)
            and self._security_source_input_call(expr_node) is not None
        ):
            # ``src`` of ``src = input.source(ohlc4, ...)``: the requested bar's.
            return self._security_source_input_expr(expr_node)

        if isinstance(expr_node, FuncCall):
            func_name = self._security_user_call_key(expr_node)
            if func_name is not None and isinstance(expr_node.callee, MemberAccess):
                # Every earlier build called the chart method.
                self._security_requested_used = True
            if func_name is not None:
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    self._codegen_error(
                        expr_node,
                        "request.security helper functions must not recurse while building a security context",
                    )
                shared = getattr(self, "_security_shared_calls", None)
                share_key = None
                if shared is not None:
                    # A pure call reached again with the same arguments reads
                    # what its first reach spelled (``_security_share_pure_call``).
                    share_key = self._security_shared_call_key(expr_node, helper_binding_stack)
                    if share_key is not None and share_key in shared:
                        return shared[share_key]
                resolving.add(call_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    resolved = self._build_security_expr(
                        sec_id,
                        plan["expr"],
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        plan["binding_stack"],
                        emitted_lines,
                    )
                else:
                    if emitted_lines is None:
                        self._codegen_error(
                            expr_node,
                            "request.security multi-statement helpers require statement-capable security evaluation context",
                        )
                    resolved = self._emit_security_linear_helper_call(
                        sec_id,
                        plan,
                        ta_results,
                        security_mutable_names,
                        emitted_lines,
                        resolving,
                    )
                resolving.remove(call_key)
                if share_key is not None:
                    resolved = shared[share_key] = self._security_share_pure_call(
                        sec_id, resolved
                    )
                return resolved

        if (
            isinstance(expr_node, FuncCall)
            and isinstance(expr_node.callee, MemberAccess)
            and isinstance(expr_node.callee.object, Identifier)
            and expr_node.callee.object.name == "math"
        ):
            # A rolling/stateful math reducer (e.g. math.sum -> math::Sum) is
            # precomputed into a committed _secval_* by the security TA
            # machinery, exactly like a ta.* call. Return that committed value
            # instead of falling through to _build_security_math_call, whose
            # inline lowering only covers scalar math and emits a broken
            # "unsupported: math.<f>" 0.0 for a reducer. Scalar math
            # (abs/round/min/max/...) is not a TA site, so this is a no-op.
            math_site = self._get_ta_site(expr_node)
            if math_site is not None:
                math_idx = self._ta_index_by_site_id.get(id(math_site))
                math_sig = self._security_binding_stack_signature(helper_binding_stack)
                if math_idx is not None:
                    self._security_note_ta_read((math_idx, math_sig))
                if math_idx is not None and (math_idx, math_sig) in ta_results:
                    return ta_results[(math_idx, math_sig)]
            if math_site is None:
                return self._build_security_math_call(
                    sec_id,
                    expr_node.callee.member,
                    expr_node,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
            # A reducer the prologue left to a multi-statement helper (math.sum)
            # is computed where the helper is inlined, as any TA site below.

        if (isinstance(expr_node, FuncCall) and not expr_node.kwargs
                and len(expr_node.args) == 1
                and self._resolve_callee(expr_node.callee) in (("int", None), ("float", None))
                and expr_node.callee.name not in self._func_names
                and not self._security_requested_calls
                and self._security_reads_helper_binding(expr_node.args[0], helper_binding_stack)):
            # ``int(math.round(_len / 2.0))``, a helper local a TA length reads:
            # its argument names a helper's parameter or local, which the
            # expression visitor below renders as an unknown variable where it
            # hands no name back to this builder (``_security_fallback_owns``).
            x = self._build_security_expr(
                sec_id, expr_node.args[0], ta_range, ta_results, resolving,
                security_mutable_names, helper_binding_stack, emitted_lines,
            )
            if expr_node.callee.name == "float":
                return f"(double)({x})"
            return na_preserving_int_cast(x)

        if self._security_nested_heikinashi_request(expr_node):
            # TradingView reads a request inside another request's payload in
            # the requested context: Heikin-Ashi bars of the requested
            # timeframe, which this evaluator does not build. Rendering the
            # call read the chart's own request instead. Evaluating it stops
            # the run, so a selection that never takes it
            # (``useHA ? request.security(ticker.heikinashi(...), ...) :
            # close`` at its default) runs on the requested bars.
            cpp_t = self._infer_type(expr_node)
            if cpp_t not in ("double", "int", "bool"):
                cpp_t = "double"
            na_value = "false" if cpp_t == "bool" else f"na<{cpp_t}>()"
            message = self._cpp_string_escape(
                "request.security: a Heikin-Ashi request inside another request's "
                "expression reads that request's Heikin-Ashi bars, which PineForge "
                "does not build")
            return (f'([&]() -> {cpp_t} {{ pine_runtime_error(std::string("{message}")); '
                    f"return {na_value}; }}())")

        site = self._get_ta_site(expr_node)
        if site:
            idx = self._ta_index_by_site_id.get(id(site))
            sig = self._security_binding_stack_signature(helper_binding_stack)
            if idx is not None:
                result_key = (idx, sig)
                self._security_note_ta_read(result_key)
                if result_key in ta_results:
                    return ta_results[result_key]
            sec_name = self._security_ta_variant_names.get(
                (sec_id, idx, sig),
                f"_sec{sec_id}_{site.member_name}",
            )
            compute_args = self._security_ta_compute_args_for_site(
                sec_id,
                site,
                ta_results,
                security_mutable_names,
                helper_binding_stack,
                emitted_lines,
            )
            return f"(security_series_slot_is_new({sec_id}) ? {sec_name}.compute({compute_args}) : {sec_name}.recompute({compute_args}))"

        # The expression visitor renders this node on the chart's terms. Every
        # node below it that this builder lowers itself -- a user function or
        # method call, a TA site, a helper-bound name, requested-bar history --
        # is handed back through the frame (``_security_fallback_delegate``),
        # so ``nz(f())`` inlines ``f`` on the requested bar instead of calling
        # the chart-bar method. A node that also reads something else on the
        # chart's terms (``nz(f() - g)`` with a global ``g``) keeps the
        # rendering every earlier build gave it; the evaluator then keeps the
        # requested lowering only if it used none
        # (``_emit_security_evaluator_requested``).
        args = (
            sec_id,
            ta_range,
            ta_results,
            resolving,
            security_mutable_names,
            helper_binding_stack,
            emitted_lines,
        )
        self._security_warn_chart_call(expr_node)
        self._security_check_tuple_element_history(expr_node, sec_id, helper_binding_stack)
        chart: set[int] = set()
        if self._security_requested_calls:
            self._security_warn_global_history(expr_node, helper_binding_stack, sec_id)
            chart_read = self._security_root_chart_read(
                expr_node, helper_binding_stack, sec_id, security_mutable_names
            )
            if chart_read is not None:
                if self._security_requested_used:
                    # Beside, or inside, what the evaluator already inlined.
                    self._codegen_error(
                        expr_node, f"the payload reads {chart_read} on the chart's bar"
                    )
                # Rendered as every earlier build did, its calls on the chart;
                # the evaluator keeps the requested lowering only if it
                # inlines nothing else.
                chart = self._security_subtree_ids(expr_node)
                for call in self._security_user_call_sites(expr_node):
                    self._security_warn_chart_call(
                        call,
                        f"the expression around it reads {chart_read} on the chart's bar"
                        if self._security_call_inlinable(call) else None,
                    )
                self._security_note_chart_read(
                    expr_node, f"the payload reads {chart_read} on the chart's bar"
                )
        return self._security_render_fallback(expr_node, args, chart)

    def _security_check_tuple_element_history(
        self, root, sec_id: int, helper_binding_stack=None
    ) -> None:
        """History of a tuple declaration's element read under a node the
        expression visitor renders, directly or through a global's value,
        reads the chart's series: warned. Refused, as the builder refuses the
        bare form, where every earlier build failed to compile it: a TA
        tuple's element outside a user call's arguments, its site in the
        requested history and reached by no multi-statement helper, whose
        struct result the evaluator then pushed into a ``Series<double>``
        (``_emit_security_ta_hist_pushes`` skips it)."""
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        info = self._security_info_without_methods(self._security_eval_info[sec_id])
        hist = self._security_ta_hist_idx_by_sec.get(sec_id, ())
        helper_reached = {key[0] for key in info.get("inline_helper_ta_indices", [])}
        followed: set[tuple[str, bool]] = set()
        stack = [(root, False, helper_binding_stack)]
        while stack:
            n, in_call, bindings = stack.pop()
            if isinstance(n, (list, tuple, dict)):
                stack.extend(
                    (item, in_call, bindings)
                    for item in (n.values() if isinstance(n, dict) else n)
                )
                continue
            if not isinstance(n, ASTNode):
                continue
            if (isinstance(n, Identifier)
                    and self._security_identifier_is_global_binding(n)
                    and n.name in global_expr_map
                    and n.name not in self._global_mutable_infos
                    and (n.name, in_call) not in followed):
                # The visitor reads the global's chart value, its history too.
                followed.add((n.name, in_call))
                stack.append((global_expr_map[n.name], in_call, ()))
                continue
            if (isinstance(n, Subscript) and isinstance(n.object, Identifier)
                    and self._security_identifier_is_global_binding(n.object)
                    and n.object.name in self._direct_program_tuple_binding_names
                    and n.object.name not in self._global_mutable_infos
                    and self._resolve_security_index_literal(n.index, bindings) != 0):
                name = n.object.name
                site = self._get_ta_site(global_expr_map.get(name))
                idx = self._ta_index_by_site_id.get(id(site)) if site is not None else None
                if (not in_call and idx in hist and idx not in helper_reached
                        and info["ta_variants"].get(idx)):
                    self._codegen_error(
                        n,
                        f"request.security payload reads the history of "
                        f"'{name}', an element of a tuple declaration",
                    )
                warned = getattr(self, "_security_warned_tuple_history", None)
                if warned is None:
                    warned = self._security_warned_tuple_history = set()
                if id(n) not in warned:
                    warned.add(id(n))
                    self._codegen_warning(
                        n,
                        f"request.security payload reads the history of "
                        f"'{name}', an element of a tuple declaration, on "
                        "the chart's bar; TradingView reads it on the requested bar.",
                    )
            if (isinstance(n, FuncCall) and isinstance(n.callee, Identifier)
                    and n.callee.name in self._func_names):
                in_call = True
            stack.extend(
                (v, in_call, bindings) for k, v in vars(n).items() if k != "annotations"
            )

    def _security_note_chart_read(self, node, reason: str) -> None:
        """Record the first thing the evaluator being emitted reads on the
        chart's terms (``_emit_security_evaluator_requested``)."""
        if self._security_requested_calls and self._security_chart_read is None:
            self._security_chart_read = (getattr(node, "loc", None), reason)

    def _security_switch_as_ternary(self, node: SwitchStmt):
        """``node``'s value as nested ternaries: each case's arm when its
        value equals the selector (or its condition holds, with no
        selector), else the default arm, else ``na``. Every arm must be one
        expression."""
        def arm(body: list):
            if len(body) != 1 or not isinstance(body[0], ExprStmt):
                self._codegen_error(
                    node,
                    "request.security helper switch arms must each be one expression",
                    hint="Compute a multi-statement arm in its own helper, or on the chart.",
                )
            return body[0].expr

        value = arm(node.default_body) if node.default_body else NaLiteral(loc=node.loc)
        for case_expr, body in reversed(node.cases):
            condition = (case_expr if node.expr is None else
                         BinOp(left=node.expr, op="==", right=case_expr, loc=case_expr.loc))
            value = Ternary(condition=condition, true_val=arm(body), false_val=value,
                            loc=node.loc)
        return value

    def _security_render_fallback(self, expr_node, args: tuple, chart: set[int]) -> str:
        """``expr_node`` rendered by the expression visitor, handing back to
        the builder every node it owns outside ``chart``."""
        sec_id, _ta_range, _ta_results, _resolving, mutable_names, stack, _lines = args
        self._security_payload_depth += 1
        saved_frame = self._security_fallback_frame
        self._security_fallback_frame = {
            "root": expr_node,
            "memo": {},
            # Nodes the visitor keeps on the chart's terms.
            "chart": chart,
            "args": args,
        }
        try:
            result = self._visit_expr(expr_node)
        finally:
            self._security_fallback_frame = saved_frame
            self._security_payload_depth -= 1
        return self._rewrite_security_cpp(result, sec_id, mutable_names, stack)

    def _security_root_chart_read(
        self,
        root,
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
        sec_id: int,
        mutable_names: set[str],
    ) -> str | None:
        """What a node the builder renders through the expression visitor
        reads on the chart's terms besides the nodes it hands back to the
        builder, or None: only pure calls (``nz``, ``math.*``, ``str.*``),
        literals, namespace constants, per-run values, mutable globals the
        evaluator replays, and names the evaluator spells from the requested
        bar may surround a call inlined on the requested bar. Anything else --
        a global, history the builder does not own, a call kept on the chart,
        ``bar_index``, ``timeframe.*`` -- would put two bars side by side."""
        stack = [root]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if isinstance(n, dict):
                stack.extend(n.values())
                continue
            if not isinstance(n, ASTNode):
                continue
            if n is not root and self._security_fallback_owns(n, helper_binding_stack, sec_id):
                continue
            if isinstance(n, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral)):
                continue
            if isinstance(n, BinOp):
                stack.extend((n.left, n.right))
                continue
            if isinstance(n, UnaryOp):
                stack.append(n.operand)
                continue
            if isinstance(n, Ternary):
                stack.extend((n.condition, n.true_val, n.false_val))
                continue
            if isinstance(n, Identifier):
                # A name no helper binds is the visitor's global (a method
                # default carries no binding scope).
                bound = (
                    None
                    if self._security_identifier_is_global_binding(n)
                    else self._security_lookup_helper_binding_context(
                        n.name, helper_binding_stack
                    )
                )
                if bound is None and (
                    n.name in _SECURITY_REQUESTED_NAMES
                    or n.name in mutable_names
                    or (self._expr_is_stable(n)
                        and not self._security_global_reads_timeframe(n.name))
                ):
                    continue
                return f"'{n.name}'"
            if isinstance(n, MemberAccess) and isinstance(n.object, Identifier) and (
                n.object.name in _SECURITY_PURE_MEMBER_NAMESPACES
                or (n.object.name == "session"
                    and n.member in _SECURITY_PURE_SESSION_MEMBERS)
            ):
                continue
            if isinstance(n, FuncCall) and not self._security_user_call_site(n):
                func_name, namespace = self._resolve_callee(n.callee)
                if (namespace, func_name) not in _SECURITY_IMPURE_CALLS and (
                        (namespace is None and func_name in _SECURITY_PURE_CALLS)
                        or namespace in _SECURITY_PURE_CALL_NAMESPACES):
                    stack.extend(n.args)
                    stack.extend(n.kwargs.values())
                    continue
                return f"'{namespace + '.' if namespace else ''}{func_name}'"
            if isinstance(n, FuncCall):
                return "a user call it cannot inline"
            if isinstance(n, Subscript):
                return "history it does not keep on the requested clock"
            return f"a {type(n).__name__}"
        return None

    def _security_user_call_sites(self, node) -> list:
        """Every user call under ``node``, itself included."""
        found = []
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
            elif isinstance(n, dict):
                stack.extend(n.values())
            elif isinstance(n, ASTNode):
                if self._security_user_call_site(n):
                    found.append(n)
                stack.extend(v for k, v in vars(n).items() if k != "annotations")
        return found

    def _security_global_reads_timeframe(self, name: str) -> bool:
        """Whether a global's value reads ``timeframe.*``, through the globals
        it reads: a per-run value that still differs between the chart and a
        requested context (``tfm = timeframe.multiplier``)."""
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        seen: set[str] = set()
        stack = [global_expr_map.get(name)]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if isinstance(n, dict):
                stack.extend(n.values())
                continue
            if not isinstance(n, ASTNode):
                continue
            if (isinstance(n, MemberAccess) and isinstance(n.object, Identifier)
                    and n.object.name == "timeframe"):
                return True
            if (isinstance(n, Identifier) and n.name in global_expr_map
                    and n.name not in seen):
                seen.add(n.name)
                stack.append(global_expr_map[n.name])
            stack.extend(v for k, v in vars(n).items() if k != "annotations")
        return False

    def _security_emits_double(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
        depth: int = 0,
    ) -> bool:
        """``_emitted_value_is_double`` for a requested-context operand, read
        the way the builder lowers it: a helper-bound name reads its
        argument's (or evaluator local's) C++ type, an immutable global the
        builder re-evaluates reads its value, and a user call it inlines reads
        its final expression -- the chart's inference knows none of them (a
        ``float g = 1`` global is re-emitted as ``1``)."""
        if node is None or depth > 64:
            return False
        if isinstance(node, Identifier):
            if not self._security_identifier_is_global_binding(node):
                binding = self._security_lookup_helper_binding_context(
                    node.name, helper_binding_stack
                )
                if binding is not None:
                    bound, bound_stack = binding
                    if isinstance(bound, str):
                        series_name = self._security_series_binding_target(bound)
                        if series_name is not None:
                            return series_name not in self._security_string_series
                        return self._security_local_cpp_types.get(bound) == "double"
                    return self._security_emits_double(bound, bound_stack, depth + 1)
            else:
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (node.name in global_expr_map
                        and node.name not in self._global_mutable_infos):
                    return self._security_emits_double(
                        global_expr_map[node.name], (), depth + 1
                    )
        if isinstance(node, FuncCall) and self._security_user_call_key(node) is not None:
            share_key = self._security_shared_call_key(node, helper_binding_stack)
            if share_key is not None:
                # A pure call's answer follows from its arguments' values.
                memo = getattr(self, "_security_emits_double_memo", None)
                if memo is None:
                    memo = self._security_emits_double_memo = {}
                if (share_key, depth) not in memo:
                    plan = self._security_helper_call_plan(node, helper_binding_stack)
                    memo[(share_key, depth)] = self._security_emits_double(
                        plan["expr"], plan["binding_stack"], depth + 1
                    )
                return memo[(share_key, depth)]
            try:
                plan = self._security_helper_call_plan(node, helper_binding_stack)
            except CompileError:
                return self._emitted_value_is_double(node)
            final, stack = plan["expr"], plan["binding_stack"]
            if plan["mode"] == "linear":
                # The emitter declares each local with ``_type_for_decl``.
                decls = {
                    stmt.name: stmt for stmt in plan["body"] if isinstance(stmt, VarDecl)
                }
                if isinstance(final, Identifier):
                    # A tuple element's local is ``auto``: not known double.
                    return (final.name in decls
                            and self._type_for_decl(decls[final.name]) == "double")
                stack = stack + ({
                    name: decl.value for name, decl in decls.items()
                    if decl.value is not None
                },)
            return self._security_emits_double(final, stack, depth + 1)
        if isinstance(node, BinOp):
            if node.op in ("/", "%"):
                return True
            if node.op in ("+", "-", "*"):
                return (self._security_emits_double(node.left, helper_binding_stack, depth + 1)
                        or self._security_emits_double(node.right, helper_binding_stack, depth + 1))
        if isinstance(node, UnaryOp) and node.op in ("-", "+"):
            return self._security_emits_double(node.operand, helper_binding_stack, depth + 1)
        if isinstance(node, Ternary):
            return (self._security_emits_double(node.true_val, helper_binding_stack, depth + 1)
                    or self._security_emits_double(node.false_val, helper_binding_stack, depth + 1))
        return self._emitted_value_is_double(node)

    def _security_fallback_owns(
        self,
        node,
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
        sec_id: int,
    ) -> bool:
        """Whether ``_build_security_expr`` lowers ``node`` itself rather than
        through its expression-visitor fallback: a user call it inlines, a TA
        site, a scalar helper binding, and history it keeps on the requested
        clock (a bar field's, a TA site's, a helper call's or an operator
        expression's the prepasses registered). Anything else keeps the
        visitor's rendering, as before this path existed: a global and its
        history (re-evaluating ``g`` on the requested bar would pair it with
        the chart's ``g[1]``), and ``timeframe.*`` (beside the chart's
        ``timeframe.in_seconds()``)."""
        if not self._security_requested_calls:
            # Every earlier build's lowering, but an input read while a TA
            # history index or constructor argument is lowered keeps its
            # getter: the evaluator can run before on_bar sets the members.
            # A source input reads the requested bar, as ``close`` does.
            return isinstance(node, Identifier) and (
                (self._security_index_inputs
                 and self._security_identifier_is_global_binding(node)
                 and node.name in self._input_backed_vars)
                or self._security_source_input_call(node) is not None
            )
        if self._get_ta_site(node) is not None:
            return True
        if isinstance(node, FuncCall):
            return self._security_call_inlinable(node)
        if isinstance(node, Identifier):
            return (self._security_fallback_owns_name(node, helper_binding_stack)
                    or self._security_fallback_owns_global(node)
                    or (self._security_is_bar_index(node)
                        and sec_id in self._security_bar_index_secs))
        if (isinstance(node, Subscript) and self._security_is_bar_index(node.object)
                and sec_id in self._security_bar_index_secs):
            return True
        if isinstance(node, Subscript):
            obj = node.object
            if isinstance(obj, Identifier):
                if not self._security_identifier_is_global_binding(obj):
                    binding = self._security_lookup_helper_binding_context(
                        obj.name, helper_binding_stack
                    )
                    if binding is not None:
                        return self._security_bound_is_scalar(*binding)
                # ``close[1]`` (``src[1]``): the requested bar's history.
                return self._security_bar_history_field(obj) is not None
            if self._get_ta_site(obj) is not None:
                # ``ta.sma(close, 5)[1]``: the requested TA's history series.
                return True
            if isinstance(obj, FuncCall):
                return (sec_id, id(node)) in self._security_expr_hist_by_node
            return (
                self._is_compound_history_object(obj)
                and (sec_id, id(node)) in self._security_expr_hist_by_node
            )
        return False

    def _security_bound_is_scalar(self, bound, bound_stack) -> bool:
        """Whether a helper binding holds a scalar: an evaluator local or
        helper series, or an argument whose value is an int, float, bool or
        string (a collection, UDT or drawing argument keeps the visitor's
        rendering)."""
        if isinstance(bound, str):
            return True
        for _ in range(64):
            if not (isinstance(bound, Identifier)
                    and not self._security_identifier_is_global_binding(bound)):
                break
            binding = self._security_lookup_helper_binding_context(bound.name, bound_stack)
            if binding is None:
                break
            bound, bound_stack = binding
            if isinstance(bound, str):
                return True
        return self._infer_type(bound) in _SECURITY_SCALAR_CPP

    def _security_fallback_owns_name(
        self,
        node: Identifier,
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
    ) -> bool:
        """A name the builder resolves itself: a scalar helper binding, the
        requested ``time_close``, a source input, and -- while a TA history
        index is lowered -- an input, read through its getter."""
        if not self._security_identifier_is_global_binding(node):
            binding = self._security_lookup_helper_binding_context(
                node.name, helper_binding_stack
            )
            if binding is not None:
                return self._security_bound_is_scalar(*binding)
        if node.name == "time_close" or self._security_source_input_call(node) is not None:
            return True
        return (
            self._security_index_inputs
            and self._security_identifier_is_global_binding(node)
            and node.name in self._input_backed_vars
        )

    def _security_fallback_delegate(self, node) -> str | None:
        """The builder's C++ for a node the expression visitor reached while
        lowering a request.security payload node it does not handle itself
        (``_build_security_expr``'s fallback); None to render it as usual.
        A node is lowered once per fallback: a builtin whose visitor renders
        an argument twice (``str.format``) must not emit a helper's
        statements, and advance its TA state, twice."""
        frame = self._security_fallback_frame
        if frame is None or node is frame["root"]:
            return None
        memo = frame["memo"]
        if id(node) in memo:
            return memo[id(node)]
        args = frame["args"]
        # A source input reads the requested bar wherever it sits, as a bar
        # field does (the visitor spells ``close`` from ``bar``); it inlines
        # no call beside a chart read.
        source = (isinstance(node, Identifier)
                  and self._security_source_input_call(node) is not None)
        if (id(node) in frame["chart"] and not source) or not self._security_fallback_owns(
            node, args[5], args[0]
        ):
            self._security_warn_chart_call(node)
            return None
        if not source:
            self._security_requested_used = True
        self._security_fallback_frame = None
        try:
            memo[id(node)] = self._build_security_expr(args[0], node, *args[1:])
        finally:
            self._security_fallback_frame = frame
        return memo[id(node)]

    @staticmethod
    def _security_subtree_ids(node) -> set[int]:
        """``id`` of every AST node under ``node``, itself included."""
        out: set[int] = set()
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
            elif isinstance(n, dict):
                stack.extend(n.values())
            elif isinstance(n, ASTNode) and id(n) not in out:
                out.add(id(n))
                stack.extend(v for k, v in vars(n).items() if k != "annotations")
        return out

    def _security_fallback_owns_global(self, node: Identifier) -> bool:
        """A global the builder spells on the requested bar under a builtin
        call (``nz(s)``) as it spells the bare payload global: one the
        expression map holds (declared once, not ``var``), not replayed
        state, not a per-run value (an input or constant reads the same on
        either bar). The visitor used to render it from the chart's member,
        silently (P6 of lane CG-SECURITY-2). Its history (``nz(s[1])``) stays
        the visitor's, and a payload that reads it beside a name the builder
        owns falls back whole (``_emit_security_evaluator_requested``)."""
        if not self._security_identifier_is_global_binding(node):
            return False
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        return (
            node.name in global_expr_map
            and node.name not in self._global_mutable_infos
            and node.name not in _SECURITY_REQUESTED_NAMES
            and not self._expr_is_stable(node)
        )

    def _security_warn_global_history(
        self,
        root,
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
        sec_id: int,
    ) -> None:
        """Warn where an argument of a builtin call in a payload reads a
        global's history (``nz(s[1])``): the visitor renders it from the
        chart's series, while TradingView evaluates it on the requested bar,
        and the builder keeps no requested history of a global's expression.
        The global's value is the builder's (``_security_fallback_owns_global``)."""
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        warned = getattr(self, "_security_warned_global_history", None)
        if warned is None:
            warned = self._security_warned_global_history = set()
        for n in self._walk_ast(root):
            if (n is not root
                    and isinstance(n, Subscript) and isinstance(n.object, Identifier)
                    and self._security_identifier_is_global_binding(n.object)
                    and n.object.name in global_expr_map
                    and n.object.name not in self._global_mutable_infos
                    and not self._security_fallback_owns(n, helper_binding_stack, sec_id)
                    and id(n) not in warned):
                warned.add(id(n))
                self._codegen_warning(
                    n,
                    f"request.security payload reads the history of '{n.object.name}' "
                    "under a builtin call on the chart's bar; TradingView evaluates "
                    "it on the requested bar.",
                )

    def _security_var_input_call(self, node: Identifier):
        """The input call of a never-reassigned ``var v = input.*()`` read
        while a TA constructor argument or history index is lowered, or None.

        Such a ``v`` holds the input's value on every bar, and there it is
        read through its getter, as a plain input is (the plain one inlines
        through the expression map, which holds no ``var``):
        ``evaluate_security`` resets the TA object before ``on_bar`` has
        initialized the member, so ``g(close, int(vf))`` built
        ``ta::SMA((int)vf)`` from an ``na`` member, a length of INT_MIN, and
        the run crashed (P1 of lane CG-SECURITY-2's review)."""
        if not (self._security_index_inputs
                and self._security_identifier_is_global_binding(node)):
            return None
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        if node.name in global_expr_map:
            return None
        return self._input_var_to_call.get(node.name)

    @staticmethod
    def _security_bar_index_member(sec_id: int) -> str:
        return f"_sec{sec_id}_bar_index_"

    def _security_is_bar_index(self, node) -> bool:
        """The built-in ``bar_index``, where no declaration binds the name."""
        return (isinstance(node, Identifier) and node.name == "bar_index"
                and "bar_index" not in getattr(self, "_safe_name_bound", ()))

    def _security_reads_bar_index(self, expr_node) -> bool:
        """Whether a payload reads ``bar_index``: in itself, the globals it
        reads or the user functions it calls. TradingView evaluates it on
        the requested bar -- the count of requested bars before it -- where
        the evaluator used to read the chart's (``pine_bar_index()``)."""
        from ..limits import iter_ast_nodes

        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
        seen: set[tuple[str, str]] = set()
        pending = [expr_node]
        while pending:
            root = pending.pop()
            if not isinstance(root, ASTNode):
                continue
            for n, _depth in iter_ast_nodes(root):
                if self._security_is_bar_index(n):
                    return True
                if (isinstance(n, Identifier) and n.name in global_expr_map
                        and ("global", n.name) not in seen
                        and self._security_identifier_is_global_binding(n)):
                    seen.add(("global", n.name))
                    pending.append(global_expr_map[n.name])
                if (isinstance(n, FuncCall) and isinstance(n.callee, Identifier)
                        and n.callee.name in self._func_names
                        and ("func", n.callee.name) not in seen):
                    seen.add(("func", n.callee.name))
                    info = self._func_info_map.get(n.callee.name)
                    if info is not None and getattr(info, "node", None) is not None:
                        pending.extend(info.node.body)
        return False

    def _security_variant_order_key(self, signature: tuple, binding_stack) -> tuple:
        """The order of a TA site's requested-context variants (``_v0``,
        ``_v1``, ...): where each argument binding's value is written in the
        source, then the signature. The signature names a bound node by its
        ``id()``, so ordering by its ``repr`` followed memory addresses, and
        the same script could number its variants differently from run to
        run or between CPython and Pyodide."""
        frames = []
        for idx, frame in enumerate(binding_stack or ()):
            if idx == 0 or isinstance(frame, _SecurityHelperArgumentFrame):
                items = []
                for name, node in sorted(frame.items(), key=lambda item: item[0]):
                    if isinstance(node, str):
                        items.append((name, 0, 0, 0, node))
                    else:
                        loc = getattr(node, "loc", None)
                        items.append((
                            name,
                            getattr(loc, "line", 0) or 0,
                            getattr(loc, "col", 0) or 0,
                            getattr(loc, "end_col", 0) or 0,
                            type(node).__name__,
                        ))
                frames.append(("arguments", tuple(items)))
            else:
                frames.append(("locals", tuple(sorted(frame.keys()))))
        return tuple(frames), repr(signature)
