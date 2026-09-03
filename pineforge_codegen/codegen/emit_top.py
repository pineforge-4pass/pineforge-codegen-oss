"""Top-level C++ section emitters for the codegen.

``TopLevelEmitter`` holds the methods that emit the four top-level
sections of the generated C++ translation unit -- the includes block,
the ``GeneratedStrategy`` constructor (plus ``set_strategy_override``
and the optional ``configure_security_evaluators`` override), the
``on_bar()`` body, and the ``extern "C"`` shim that exposes the
strategy to the loader -- together with the per-function emission
helpers (``_emit_func_def`` and ``_emit_udt_method_cpp_name``) used by
both regular Pine functions and UDT instance methods.

These emitters were extracted from ``base.py``'s ``CodeGen`` class as
step 7 of the codegen package refactor; behaviour is preserved
verbatim. The mixin owns no state of its own — it reads/writes only
attributes already established on the host class (``CodeGen``).

Mixin contract — host class must provide the following attributes
(all set by ``CodeGen.__init__``):

- ``self.ctx`` (``AnalyzerContext``): symbol-table source. Reads
  ``ctx.ast.body``, ``ctx.ta_call_sites``, ``ctx.var_members``,
  ``ctx.series_vars``, ``ctx.series_bar_fields``,
  ``ctx.func_series_vars``, ``ctx.func_var_members``,
  ``ctx.strategy_params``, and ``ctx.pf_trace_pragmas``
  (the ``// @pf-trace`` instrumentation list consumed by
  ``_emit_pf_trace_block``).
- ``self._uses_matrix`` (``bool``): gates the ``runtime/matrix.hpp``
  include in ``_emit_includes``.
- ``self._security_calls`` (``list[dict]``): non-empty when the
  strategy uses ``request.security``; controls the ``run_backtest``
  / ``run_backtest_full`` dispatch in ``_emit_extern_c``.
- ``self._security_eval_info`` (``list[dict]``): per-eval metadata
  (``tf``/``tf_node``/``lookahead_on``/``gaps_on``/``sec_id``/
  ``ta_variants``) consumed by ``_emit_constructor`` to build the
  TA-variant ctor list and the ``configure_security_evaluators``
  override.
- ``self._matrix_specs`` (``dict[str, TypeSpec]``): matrix-typed var
  members whose first-bar init is emitted in ``_emit_on_bar``.
- ``self._array_vars`` (``set[str]``): array-typed var members.
- ``self._map_vars`` (``set[str]``): map-typed var members.
- ``self._strategy_series_vars`` (``set[str]``): series mirrors of
  ``strategy.*`` member values pushed every bar.
- ``self._timeframe_period_vars`` (``set[str]``): identifiers that
  resolve to ``script_tf_`` in ``configure_security_evaluators``.
- ``self._udt_defs`` (``dict[str, list]``): UDT name -> field info,
  used to detect UDT-typed var initialisers in ``_emit_on_bar``.
- ``self._func_cs_var_remap``
  (``dict[tuple[str, int], dict[str, str]]``): per-function
  per-call-site rename table for cloned series/var members.
- ``self._func_cs_ta_remap``
  (``dict[tuple[str, int], dict[str, str]]``): per-function
  per-call-site rename table for cloned TA member names.

State written by ``_emit_func_def`` (the per-function emit context):

- ``self._current_func_param_types`` (``dict[str, str]``).
- ``self._current_func_series_params`` (``set[str]``).
- ``self._udt_param_udt`` (``dict[str, str]``).
- ``self._current_func_locals`` (``set[str]``).
- ``self._active_ta_remap`` (``dict[str, str]``).
- ``self._active_var_remap`` (``dict[str, str]``).
- ``self._active_call_site_idx`` (``int | None``).

Sibling-mixin methods consumed via ``self``:

- ``self._safe_name`` / ``self._func_safe_name`` (``NamingHelper``).
- ``self._default_for_type`` / ``self._infer_tuple_types``
  (``TypeInferer``).
- ``self._is_compile_time_value`` (``TaSiteHelper``).
- ``self._resolve_known`` / ``self._runtime_ctor_arg_for_reset``
  (``CodeGen.base``): compile-time argument resolver and the
  runtime-reset rewriter for ctor args sourced from inputs.
- ``self._emit_ta_runtime_reset`` (``CodeGen.base``): emits the
  first-bar TA rebuild block inside ``on_bar``.
- ``self._visit_stmt`` / ``self._visit_expr`` /
  ``self._visit_if_switch_expr`` (``CodeGen.base``).

The mixin avoids importing from ``base.py`` to stay free of cycles;
all tables and types come from ``codegen/tables.py``, ``..ast_nodes``,
and ``..analyzer``.
"""

from __future__ import annotations

from ..ast_nodes import (
    ExprStmt, FuncCall, Identifier, IfStmt, SwitchStmt, VarDecl,
)
from ..analyzer import FuncInfo
from ..symbols import PineType, method_receiver_cpp_token
from .tables import (
    BAR_SERIES_PUSH,
    DRAWING_TYPE_TO_CPP,
    PINE_TYPE_TO_CPP,
    RUNTIME_REGISTER_SECURITY_EVAL_FN,
    RUNTIME_REGISTER_SECURITY_LOWER_TF_EVAL_FN,
)


class TopLevelEmitter:
    """Mixin owning the top-level C++ section emitters and the
    per-function emitters used by regular Pine functions and UDT
    instance methods.

    Mixed into ``CodeGen``; not intended to be instantiated standalone."""

    def _emit_includes(self, lines: list[str]) -> None:
        lines.append('#include <pineforge/engine.hpp>')
        lines.append('#include <pineforge/ta.hpp>')
        lines.append('#include <pineforge/math.hpp>')
        lines.append('#include <pineforge/series.hpp>')
        lines.append('#include <pineforge/na.hpp>')
        if getattr(self, "_uses_map", False):
            lines.append('#include <pineforge/map.hpp>')
        lines.append("#include <cstdint>")
        lines.append("#include <cmath>")
        lines.append("#include <algorithm>")
        lines.append("#include <cstdlib>")
        lines.append("#include <numeric>")
        lines.append("#include <string>")
        lines.append("#include <vector>")
        if getattr(self, "_udt_defs", {}):
            lines.append("#include <deque>")
            lines.append("#include <functional>")
            lines.append("#include <limits>")
        lines.append("#include <tuple>")
        lines.append("#include <optional>")
        lines.append("#include <type_traits>")
        if getattr(self, "_udt_defs", {}):
            lines.append("#include <stdexcept>")
            lines.append("#include <utility>")
        lines.append("#include <memory>")
        lines.append("#include <mutex>")
        lines.append("#include <unordered_map>")
        lines.append("#include <unordered_map>")
        lines.append('#include <pineforge/color.hpp>')
        lines.append('#include <pineforge/log.hpp>')
        lines.append('#include <pineforge/str_utils.hpp>')
        lines.append('#include <pineforge/session_time.hpp>')
        # Feature-guarded argument tails (lab experiment cells build the
        # candidate codegen against the BASE engine too): the symbol clock
        # (syminfo tz + session) reaches time()/time_close() and ta.vwap only
        # when the engine header advertises the overloads; otherwise the
        # macros expand to nothing and the calls collapse to the legacy
        # 5-arg pine_time / 3-arg VWAP::compute shapes (session filter in
        # UTC, UTC-day VWAP anchor — the honest "factor absent" behaviour).
        # Gated on the pre-scans so scripts without such calls stay
        # byte-identical. The expansion carries no extra parentheses so the
        # preprocessed call is byte-identical to the plain 7-arg form.
        if getattr(self, "_uses_pine_time", False):
            lines.append("#ifdef PF_PINE_TIME_HAS_SESSION_DAY")
            lines.append("#define PF_PINE_TIME_SESSION_DAY_ARGS(tz, sess) , tz, sess")
            lines.append("#else")
            lines.append("#define PF_PINE_TIME_SESSION_DAY_ARGS(tz, sess)")
            lines.append("#endif")
        if getattr(self, "_uses_vwap_anchor", False):
            lines.append("#ifdef PF_VWAP_HAS_SESSION_ANCHOR")
            lines.append("#define PF_VWAP_SESSION_ANCHOR_ARGS(tz, sess) , tz, sess")
            lines.append("#else")
            lines.append("#define PF_VWAP_SESSION_ANCHOR_ARGS(tz, sess)")
            lines.append("#endif")
        if self._uses_matrix:
            # Unconditional include when matrix API is used — do not gate on
            # __has_include(<Eigen/Dense>) (can differ between runtime build and this TU).
            lines.append('#include <pineforge/matrix.hpp>')
            # generic_matrix.hpp only needed when at least one non-float matrix
            # is present in the TU. Float matrices route through PineMatrix in
            # matrix.hpp; pulling in the generic header otherwise is a wasted
            # include.
            if self._detect_generic_matrix_usage():
                lines.append('#include <pineforge/generic_matrix.hpp>')
        # Drawing-objects-as-data runtime (line/box/label/linefill arenas +
        # ChartPoint). Gated on _uses_drawing so non-drawing strategies stay
        # byte-identical — mirrors the matrix.hpp gating above.
        if getattr(self, "_uses_drawing", False):
            lines.append('#include <pineforge/drawing.hpp>')
        lines.append("")
        # Compatibility shim for the namespace-wrap refactor: unqualified
        # references to BacktestEngine / Bar / na<T>() / ta::* / etc. resolve
        # via the runtime's pineforge namespace. Removed in phase 5 lock-down
        # in favour of fully qualified names emitted at each call site.
        lines.append("using namespace pineforge;")
        lines.append("")
        # Syminfo derivation helpers (_pf_derive_main_tickerid, _pf_derive_country)
        from .helpers_syminfo import emit_syminfo_helpers
        lines.extend(emit_syminfo_helpers())

    def _script_has_strategy_close(self) -> bool:
        """True if the script's AST contains a ``strategy.close*`` call.

        Scans the entire program body (including nested function /
        method bodies) for any ``FuncCall`` whose callee resolves to
        ``strategy.close`` or ``strategy.close_all``. Comments are
        not considered (parser strips them). Result is independent of
        whether the close call ever runs at backtest time — this is a
        purely static script-shape check used by the engine's flip path
        to choose between TV's empirical growth rule and the standard
        Pine semantic.

        Mixin contract: relies on ``self._walk_ast`` (NamingHelper) and
        ``self._resolve_callee`` (NamingHelper); host class must
        provide ``self.ctx.ast``."""
        from ..ast_nodes import FuncCall  # local to avoid circular import
        for node in self._walk_ast(self.ctx.ast):
            if not isinstance(node, FuncCall):
                continue
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "strategy" and func_name in ("close", "close_all"):
                return True
        return False

    def _script_has_input_source(self) -> bool:
        """True if the script's AST contains an ``input.source(...)`` call.

        Gates the engine's native source-series push: the runtime only
        advances ``_src_<field>_`` (paying the per-bar cost) when
        ``_src_series_active_`` is set, which the ctor does iff this returns
        True. Same static-shape scan style as ``_script_has_strategy_close``."""
        from ..ast_nodes import FuncCall  # local to avoid circular import
        for node in self._walk_ast(self.ctx.ast):
            if not isinstance(node, FuncCall):
                continue
            if self._is_source_input(node):
                return True
        return False

    @staticmethod
    def _script_state_member_name(decl_line: str) -> str | None:
        """Extract a generated class-member name from one declaration line.

        ``CodeGen.generate`` emits the complete persistent script state as a
        contiguous block of one-line declarations before the constructor.  The
        rollback checkpoint is derived from that block rather than from a
        second, hand-maintained inventory: adding a new TA/helper/collection
        member therefore automatically makes it part of Pine's historical
        execution rollback.

        The supported declaration shapes are the only shapes emitted in that
        block today::

            Type name;
            Type name = value;
            Type name(args);
            Type name{args};

        A declaration that does not match fails generation loudly.  Silently
        omitting an unfamiliar member would be materially worse: it would let
        state leak between ``calc_on_order_fills`` executions.
        """
        text = decl_line.strip()
        if not text:
            return None
        if not text.endswith(";"):
            raise AssertionError(
                f"unexpected generated script-state declaration: {decl_line!r}"
            )
        text = text[:-1].rstrip()
        if " = " in text:
            text = text.split(" = ", 1)[0].rstrip()
        else:
            # Series<T> members can carry a max_bars_back ctor suffix and
            # drawing arenas use brace initialization.  Both suffixes start in
            # the final declarator token; C++ types emitted here never contain
            # parentheses or braces.
            last_space = text.rfind(" ")
            if last_space < 0:
                raise AssertionError(
                    f"missing type in generated script-state declaration: {decl_line!r}"
                )
            declarator = text[last_space + 1:]
            cut = len(declarator)
            for marker in ("(", "{"):
                pos = declarator.find(marker)
                if pos >= 0:
                    cut = min(cut, pos)
            text = text[:last_space + 1] + declarator[:cut]

        name = text.rsplit(" ", 1)[-1].strip()
        if not name or not (name[0].isalpha() or name[0] == "_") \
                or not all(ch.isalnum() or ch == "_" for ch in name):
            raise AssertionError(
                f"cannot identify generated script-state member: {decl_line!r}"
            )
        return name

    def _collect_script_state_members(self, declaration_lines: list[str]) -> list[str]:
        """Return every rollback-relevant generated member in declaration order.

        Precalculated TA result vectors and their mode flag are immutable once
        the engine starts its broker walk.  Copying an O(number-of-bars) cache
        before every COOF execution would be both unnecessary and catastrophic,
        so those implementation caches are the sole exclusions.  The live TA
        objects themselves remain captured because dynamic/magnifier runs call
        ``compute`` and mutate them.
        """
        members: list[str] = []
        seen: set[str] = set()
        for line in declaration_lines:
            name = self._script_state_member_name(line)
            if name is None:
                continue
            if name == "_use_precalc" or name.startswith("_precalc_"):
                continue
            if name in seen:
                raise AssertionError(f"duplicate generated script-state member: {name}")
            seen.add(name)
            members.append(name)
        return members

    def _emit_handle_checkpoint_traits(self, lines: list[str]) -> None:
        """Emit recursive rollback adapters for shared-ID collection state.

        This support block is emitted for scripts using a shared-ID map/matrix
        or an authored UDT arena. The primary trait retains the historical
        value-copy checkpoint for ordinary runtime state; specialized map,
        matrix, vector, UDT-record, and arena traits snapshot their owned state
        without recursively following nested UDT handle graphs.
        """
        checkpoint_traits = self._checkpoint_traits_cpp_type()
        lines.extend([
            "template <typename _PFValue>",
            f"struct {checkpoint_traits} {{",
            "    using snapshot_type = _PFValue;",
            "    static snapshot_type take(const _PFValue& value) { return value; }",
            "    static void restore(_PFValue& value, const snapshot_type& snapshot) {",
            "        value = snapshot;",
            "    }",
            "};",
            "",
        ])

        if getattr(self, "_udt_defs", {}):
            coordinator_type = self._udt_undo_coordinator_cpp_name
            lines.extend([
                "template <>",
                f"struct {checkpoint_traits}<{coordinator_type}> {{",
                f"    using coordinator_type = {coordinator_type};",
                "    using snapshot_type = typename coordinator_type::Snapshot;",
                "    static snapshot_type take(coordinator_type& value) {",
                "        return value.snapshot();",
                "    }",
                "    static void restore(coordinator_type& value,",
                "                        const snapshot_type& snapshot) {",
                "        value.restore(snapshot);",
                "    }",
                "};",
                "",
            ])

        if getattr(self, "_uses_map", False):
            lines.extend([
            "template <typename _PFKey, typename _PFValue>",
            f"struct {checkpoint_traits}<PineMap<_PFKey, _PFValue>> {{",
            "    using map_type = PineMap<_PFKey, _PFValue>;",
            "    static_assert(map_type::snapshot_supported,",
            '                  "generated map checkpoints require primitive map values");',
            "    using snapshot_type = std::optional<typename map_type::Snapshot>;",
            "    static snapshot_type take(const map_type& value) {",
            "        if (value.is_na()) return std::nullopt;",
            "        return value.snapshot();",
            "    }",
            "    static void restore(map_type& value, const snapshot_type& snapshot) {",
            "        if (!snapshot) {",
            "            value = map_type{};",
            "            return;",
            "        }",
            "        value.restore(*snapshot);",
            "    }",
            "};",
            "",
            ])

        if getattr(self, "_uses_matrix", False):
            lines.extend([
                "template <>",
                f"struct {checkpoint_traits}<PineMatrix> {{",
                "    using matrix_type = PineMatrix;",
                "    using snapshot_type = std::optional<typename matrix_type::Snapshot>;",
                "    static snapshot_type take(const matrix_type& value) {",
                "        if (value.is_na()) return std::nullopt;",
                "        return value.snapshot();",
                "    }",
                "    static void restore(matrix_type& value, const snapshot_type& snapshot) {",
                "        if (!snapshot) {",
                "            value = matrix_type{};",
                "            return;",
                "        }",
                "        value.restore(*snapshot);",
                "    }",
                "};",
                "",
            ])

            if self._detect_generic_matrix_usage():
                lines.extend([
                    "template <typename _PFElement>",
                    f"struct {checkpoint_traits}<PineGenericMatrix<_PFElement>> {{",
                    "    using matrix_type = PineGenericMatrix<_PFElement>;",
                    f"    using element_traits = {checkpoint_traits}<_PFElement>;",
                    "    using element_snapshot = typename element_traits::snapshot_type;",
                    "    struct active_snapshot {",
                    "        typename matrix_type::Snapshot outer;",
                    "        int rows;",
                    "        int columns;",
                    "        std::vector<element_snapshot> elements;",
                    "    };",
                    "    using snapshot_type = std::optional<active_snapshot>;",
                    "    static snapshot_type take(const matrix_type& value) {",
                    "        if (value.is_na()) return std::nullopt;",
                    "        active_snapshot snapshot{value.snapshot(), value.rows(),",
                    "                                 value.columns(), {}};",
                    "        snapshot.elements.reserve(",
                    "            static_cast<std::size_t>(snapshot.rows) *",
                    "            static_cast<std::size_t>(snapshot.columns));",
                    "        for (int row = 0; row < snapshot.rows; ++row) {",
                    "            for (int column = 0; column < snapshot.columns; ++column) {",
                    "                snapshot.elements.push_back(",
                    "                    element_traits::take(value.get(row, column)));",
                    "            }",
                    "        }",
                    "        return snapshot;",
                    "    }",
                    "    static void restore(matrix_type& value, const snapshot_type& snapshot) {",
                    "        if (!snapshot) {",
                    "            value = matrix_type{};",
                    "            return;",
                    "        }",
                    "        value.restore(snapshot->outer);",
                    "        std::size_t index = 0;",
                    "        for (int row = 0; row < snapshot->rows; ++row) {",
                    "            for (int column = 0; column < snapshot->columns; ++column) {",
                    "                _PFElement element = value.get(row, column);",
                    "                element_traits::restore(element, snapshot->elements[index++]);",
                    "                value.set(row, column, element);",
                    "            }",
                    "        }",
                    "    }",
                    "};",
                    "",
                ])

        lines.extend([
            "template <typename _PFElement, typename _PFAllocator>",
            f"struct {checkpoint_traits}<std::vector<_PFElement, _PFAllocator>> {{",
            f"    using element_traits = {checkpoint_traits}<_PFElement>;",
            "    using element_snapshot = typename element_traits::snapshot_type;",
            "    using snapshot_type = std::vector<element_snapshot>;",
            "    static snapshot_type take(",
            "            const std::vector<_PFElement, _PFAllocator>& value) {",
            "        snapshot_type snapshot;",
            "        snapshot.reserve(value.size());",
            "        for (std::size_t index = 0; index < value.size(); ++index) {",
            "            const _PFElement element = value[index];",
            "            snapshot.push_back(element_traits::take(element));",
            "        }",
            "        return snapshot;",
            "    }",
            "    static void restore(",
            "            std::vector<_PFElement, _PFAllocator>& value,",
            "            const snapshot_type& snapshot) {",
            "        value.clear();",
            "        value.reserve(snapshot.size());",
            "        for (const auto& element_snapshot_value : snapshot) {",
            "            _PFElement element{};",
            "            element_traits::restore(element, element_snapshot_value);",
            "            value.push_back(element);",
            "        }",
            "    }",
            "};",
            "",
        ])

        # Snapshot backing records field-by-field.  A nested UDT field is only
        # a numeric handle, so the primary trait copies its ID without recursing
        # into another record.  This makes self/cyclic object graphs finite;
        # each arena snapshots every owned record exactly once. Shared-ID map
        # and matrix fields still enter their specialized recursive traits.
        for type_name, fields in self._udt_defs.items():
            record_type = self._udt_record_cpp_type(type_name)
            emitted_fields = [
                field
                for field in fields
                if field.name not in self._udt_omitted_fields.get(type_name, set())
            ]
            checkpoint_fields = [field.name for field in emitted_fields]
            lines.append("template <>")
            lines.append(f"struct {checkpoint_traits}<{record_type}> {{")
            lines.append("    struct snapshot_type {")
            for index, field_name in enumerate(checkpoint_fields):
                lines.append(
                    f"        {checkpoint_traits}<"
                    f"decltype({record_type}::{field_name})>::snapshot_type "
                    f"_pf_field_{index};"
                )
            lines.append("    };")
            lines.append(
                f"    static snapshot_type take(const {record_type}& value) {{"
            )
            lines.append("        return snapshot_type{")
            for field_name in checkpoint_fields:
                lines.append(
                    f"            {checkpoint_traits}<"
                    f"decltype({record_type}::{field_name})>::take(value.{field_name}),"
                )
            lines.append("        };")
            lines.append("    }")
            lines.append(
                f"    static void restore({record_type}& value, "
                "const snapshot_type& snapshot) {"
            )
            for index, field_name in enumerate(checkpoint_fields):
                lines.append(
                    f"        {checkpoint_traits}<"
                    f"decltype({record_type}::{field_name})>::restore("
                    f"value.{field_name}, snapshot._pf_field_{index});"
                )
            lines.append("    }")
            lines.append("};")
            lines.append("")

            arena_type = self._udt_arena_cpp_type(type_name)
            lines.append("template <>")
            lines.append(f"struct {checkpoint_traits}<{arena_type}> {{")
            lines.append(
                f"    using arena_type = {arena_type};"
            )
            lines.append(
                "    using snapshot_type = typename arena_type::Snapshot;"
            )
            lines.append("    static snapshot_type take(arena_type& value) {")
            lines.append("        return value.snapshot();")
            lines.append("    }")
            lines.append(
                "    static void restore(arena_type& value, const snapshot_type& snapshot) {"
            )
            lines.append("        value.restore(snapshot);")
            lines.append("    }")
            lines.append("};")
            lines.append("")

    def _emit_script_state_hooks(self, lines: list[str], members: list[str]) -> None:
        """Emit the engine's Pine rollback checkpoint hook implementation.

        The checkpoint owns detached snapshots of all generated mutable state.
        Shared-ID maps and matrices retain their original backing identity so
        rollback updates every alias. Generated UDT arenas snapshot each owned
        record once; nested UDT/map/matrix fields therefore restore through
        their numeric/shared IDs without recursively walking object graphs.
        Vectors snapshot their elements, including UDT handle values. Drawing
        handles themselves are stable ids and their arenas are captured in the
        same checkpoint.

        The static assertions deliberately turn any future non-copyable member
        into a compile failure instead of a nominal, shallow rollback.  Engine
        broker/order state lives in the base class and is intentionally absent:
        fills must survive while Pine script variables roll back.
        """
        checkpoint_traits = self._checkpoint_traits_cpp_type()
        handle_aware = bool(
            getattr(self, "_uses_map", False)
            or getattr(self, "_uses_matrix", False)
            or getattr(self, "_udt_defs", {})
        )
        lines.append("    struct _PFScriptState {")
        for idx, name in enumerate(members):
            if handle_aware:
                lines.append(
                    f"        {checkpoint_traits}<"
                    f"decltype(GeneratedStrategy::{name})>::snapshot_type "
                    f"_pf_value_{idx};"
                )
            else:
                lines.append(
                    f"        decltype(GeneratedStrategy::{name}) _pf_value_{idx};"
                )
        lines.append("    };")
        lines.append(
            "    static_assert(std::is_copy_constructible_v<_PFScriptState>, "
            '"generated Pine state must be deep-copy constructible");'
        )
        lines.append(
            "    static_assert(std::is_copy_assignable_v<_PFScriptState>, "
            '"generated Pine state must be deep-copy assignable");'
        )
        lines.append("    std::optional<_PFScriptState> _pf_script_state_checkpoint_;")
        lines.append("")
        lines.append("    void snapshot_script_state() override {")
        lines.append("        _pf_script_state_checkpoint_.emplace(_PFScriptState{")
        for name in members:
            if handle_aware:
                lines.append(
                    f"            {checkpoint_traits}<"
                    f"decltype(GeneratedStrategy::{name})>::take({name}),"
                )
            else:
                lines.append(f"            {name},")
        lines.append("        });")
        lines.append("    }")
        lines.append("")
        lines.append("    void restore_script_state() override {")
        lines.append("        if (!_pf_script_state_checkpoint_) return;")
        for idx, name in enumerate(members):
            if handle_aware:
                lines.append(
                    f"        {checkpoint_traits}<"
                    f"decltype(GeneratedStrategy::{name})>::restore("
                    f"this->{name}, _pf_script_state_checkpoint_->_pf_value_{idx});"
                )
            else:
                lines.append(
                    f"        this->{name} = _pf_script_state_checkpoint_->_pf_value_{idx};"
                )
        lines.append("    }")
        lines.append("")
        lines.append("    void commit_script_state() override {")
        lines.append("        snapshot_script_state();")
        lines.append("    }")

    def _typed_na_init(self, cpp_val: str, name: str, ptype) -> str:
        """Re-type a bare ``na<double>()`` initializer to match a non-double
        member's C++ type. A ``var int x = na`` resolves its RHS to
        ``na<double>()`` (a quiet NaN); constructing/pushing that into an int or
        bool member is a NaN->int conversion (UB) that yields garbage and defeats
        ``is_na<T>()`` (which checks the type sentinel, e.g. INT_MIN). Returns the
        value unchanged unless it is exactly ``na<double>()`` and the member type
        is non-double."""
        if cpp_val != "na<double>()":
            return cpp_val
        cpp_type = PINE_TYPE_TO_CPP.get(ptype, "double")
        if cpp_type == "int" and self._is_int64_builtin_init(name):
            cpp_type = "int64_t"
        if cpp_type == "double":
            return cpp_val
        return f"na<{cpp_type}>()"

    def _emit_constructor(self, lines: list[str]) -> None:
        init_parts: list[str] = []
        # TA members with ctor args
        for ta_idx, site in enumerate(self.ctx.ta_call_sites):
            # Skip dead-code function TA sites entirely — their buffers never
            # run and their ctor args (bare param names) can never be sized.
            if ta_idx in self._dead_ta_indices:
                continue
            if site.ctor_args:
                # If a ctor arg is neither a compile-time literal nor expandable
                # to an input-backed runtime expression, the old code silently
                # emitted period 1 with no overwriting reset — a wrong indicator
                # masquerading as a working one. Refuse loudly instead. Args that
                # DO expand to a runtime expr (input-backed / arithmetic-over-input,
                # incl. function-derived lengths) are safe: the `!_ta_initialized_`
                # reset overwrites the placeholder before the first compute.
                for a in site.ctor_args:
                    r = self._resolve_ta_ctor_arg(a)
                    if (not self._is_compile_time_value(r)
                            and self._runtime_ctor_arg_for_reset(a) is None):
                        # A TA source reached through request.security can have
                        # several helper-bound constructor variants. Validate
                        # only variants of this exact source node before the
                        # ordinary guard fires, so an unstable requested-context
                        # expression keeps its provenance-specific diagnostic
                        # without allowing a later, unrelated security error to
                        # reorder an earlier ordinary error.
                        if self._security_eval_info and site.node is not None:
                            self._collect_ta_runtime_resets(
                                security_source_node=site.node
                            )
                        self._codegen_error(
                            getattr(site, "node", None),
                            f"Unsupported TA constructor length '{a}' for "
                            f"{site.class_name}: it is neither a compile-time "
                            f"constant nor derived from an input, so PineForge "
                            f"cannot size the indicator buffer.",
                            hint=("Use a literal, an input.*() value, or "
                                  "arithmetic over those for TA lengths."),
                        )
                resolved = [self._resolve_ta_ctor_arg(a) for a in site.ctor_args]
                # Compile-time placeholder for the init list; the runtime reset
                # (when the arg is input-derived) overwrites it on the first bar.
                safe_resolved = []
                for r in resolved:
                    if self._is_compile_time_value(r):
                        safe_resolved.append(r)
                    else:
                        safe_resolved.append("1")
                init_parts.append(f"{site.member_name}({', '.join(safe_resolved)})")
        # Security evaluator TA ctor args (skip for user function call expressions)
        for info in self._security_eval_info:
            for idx, variants in (info.get("ta_variants") or {}).items():
                site = self.ctx.ta_call_sites[idx]
                if not site.ctor_args:
                    continue
                for variant in variants:
                    ctor_args, _ctor_arg_stability = self._security_ta_ctor_args_for_variant(
                        info["sec_id"],
                        site,
                        variant.get("binding_stack", ()),
                    )
                    resolved = [self._resolve_ta_ctor_arg(a) for a in ctor_args]
                    safe_resolved = []
                    for r in resolved:
                        safe_resolved.append(
                            r if self._is_compile_time_value(r) else "1"
                        )
                    init_parts.append(f"{variant['member_name']}({', '.join(safe_resolved)})")

        # Non-series var members with compile-time init (deduplicate by name)
        seen_ctor_vars: set[str] = set()
        for name, ptype, init_expr in self.ctx.var_members:
            if name in seen_ctor_vars:
                continue
            seen_ctor_vars.add(name)
            safe = self._safe_name(name)
            if (name in self._array_vars
                    or name in self._map_vars
                    or name in self._matrix_specs):
                # Collection members are initialized in the first-bar block.
                # A matrix declared as ``var matrix<T> x = na`` has no
                # constructor call to replay there and its default constructor
                # is already the correctly typed null ID.  Letting the generic
                # scalar path add ``x(na<double>())`` is ill-typed.
                continue
            # Callable-scoped ``var`` members initialize at their exact source
            # declarations; exclude them from the constructor so source order,
            # first reach, and the active clone remap remain authoritative.
            if name in getattr(self, "_func_local_var_names", ()):
                continue
            # Runtime primitive initializers execute at their Pine declaration
            # site under a per-member once flag.  This preserves source-order
            # dependencies and first-entry semantics for conditional blocks.
            if name in self._runtime_scalar_var_init_members:
                continue
            # UDT-typed var members (``var SDZone z = na``) default-construct to
            # na via the struct's in-class ``__pf_na = true``; a ctor init like
            # ``z(na<double>())`` would not type-match the struct member.
            member_udt_type = self._member_udt_type(name)
            if member_udt_type in self._udt_defs:
                continue
            # Drawing handle var member (L-N3): ``var line x`` / ``var box b``
            # default-construct to {-1} (na). A ``b(na<double>())`` ctor init
            # would not type-match the handle struct — skip it (the in-class
            # member default is the once-only persistent na init).
            if (name in self._drawing_var_member_cpp_types
                    or member_udt_type in DRAWING_TYPE_TO_CPP):
                continue
            if safe not in self._series_var_member_names:
                cpp_val = self._resolve_known(init_expr)
                cpp_val = self._typed_na_init(cpp_val, name, ptype)
                if self._is_compile_time_value(cpp_val):
                    init_parts.append(f"{safe}({cpp_val})")
        # Strategy params that map to engine members
        ctor_body: list[str] = []
        sp = self.ctx.strategy_params

        if sp.get("process_orders_on_close") is True:
            ctor_body.append("        process_orders_on_close_ = true;")

        if sp.get("calc_on_order_fills") is True:
            ctor_body.append("        calc_on_order_fills_ = true;")

        if "initial_capital" in sp and isinstance(sp["initial_capital"], (int, float)):
            ctor_body.append(f"        initial_capital_ = {float(sp['initial_capital'])};")

        # default_qty_type: strategy.fixed / strategy.percent_of_equity / strategy.cash
        qty_type_map = {
            "strategy.fixed": "QtyType::FIXED",
            "strategy.percent_of_equity": "QtyType::PERCENT_OF_EQUITY",
            "strategy.cash": "QtyType::CASH",
        }
        qty_type = sp.get("default_qty_type")
        if qty_type in qty_type_map:
            ctor_body.append(f"        default_qty_type_ = {qty_type_map[qty_type]};")

        if "default_qty_value" in sp and isinstance(sp["default_qty_value"], (int, float)):
            ctor_body.append(f"        default_qty_value_ = {float(sp['default_qty_value'])};")

        if "pyramiding" in sp and isinstance(sp["pyramiding"], int):
            ctor_body.append(f"        pyramiding_ = {sp['pyramiding']};")

        # commission_type: strategy.commission.percent / .cash_per_order / .cash_per_contract
        comm_type_map = {
            "strategy.commission.percent": "CommissionType::PERCENT",
            "strategy.commission.cash_per_order": "CommissionType::CASH_PER_ORDER",
            "strategy.commission.cash_per_contract": "CommissionType::CASH_PER_CONTRACT",
        }
        comm_type = sp.get("commission_type")
        if comm_type in comm_type_map:
            ctor_body.append(f"        commission_type_ = {comm_type_map[comm_type]};")

        if "commission_value" in sp and isinstance(sp["commission_value"], (int, float)):
            ctor_body.append(f"        commission_value_ = {float(sp['commission_value'])};")

        if "slippage" in sp and isinstance(sp["slippage"], (int, float)):
            ctor_body.append(f"        slippage_ = {int(sp['slippage'])};")

        # margin_long / margin_short: percent of position value required as
        # equity (default 100 = 1x leverage). When required_margin exceeds
        # available equity, TV silently rejects the fill — engine mirrors
        # this in execute_market_entry's FLAT branch.
        if "margin_long" in sp and isinstance(sp["margin_long"], (int, float)):
            ctor_body.append(f"        margin_long_ = {float(sp['margin_long'])};")
        if "margin_short" in sp and isinstance(sp["margin_short"], (int, float)):
            ctor_body.append(f"        margin_short_ = {float(sp['margin_short'])};")

        # close_entries_rule: "FIFO" (default) or "ANY"
        if sp.get("close_entries_rule") == "ANY":
            ctor_body.append("        close_entries_rule_any_ = true;")

        # Detect ``strategy.close`` / ``strategy.close_all`` calls anywhere in
        # the script body. The runtime uses this flag in its priced-entry flip
        # path to reproduce TradingView's empirical
        # ``new_size = |old| + qty`` rule (see
        # docs/codegen-gaps/validation-tv-pyramiding-override.md). The flag
        # is set once per compilation; it is independent of how many times
        # the close call actually fires at runtime.
        if self._script_has_strategy_close():
            ctor_body.append("        script_has_strategy_close_ = true;")

        # Turn on native source-series history only when the script uses
        # input.source — otherwise the engine pays nothing per bar.
        if self._script_has_input_source():
            ctor_body.append("        _src_series_active_ = true;")

        if init_parts and ctor_body:
            lines.append(f"    explicit GeneratedStrategy() : {', '.join(init_parts)} {{")
            lines.extend(ctor_body)
            lines.append("    }")
        elif init_parts:
            lines.append(f"    explicit GeneratedStrategy() : {', '.join(init_parts)} {{}}")
        elif ctor_body:
            lines.append("    explicit GeneratedStrategy() {")
            lines.extend(ctor_body)
            lines.append("    }")
        else:
            lines.append("    explicit GeneratedStrategy() {}")

        lines.append("")
        lines.append("    void set_strategy_override(const std::string& key, const std::string& value) {")
        lines.append('        if (key == "initial_capital") { initial_capital_ = std::stod(value); return; }')
        lines.append('        if (key == "commission_value") { commission_value_ = std::stod(value); return; }')
        lines.append('        if (key == "default_qty_value") { default_qty_value_ = std::stod(value); return; }')
        lines.append('        if (key == "pyramiding") { pyramiding_ = std::stoi(value); return; }')
        lines.append('        if (key == "slippage") { slippage_ = std::stoi(value); return; }')
        lines.append('        if (key == "process_orders_on_close") { process_orders_on_close_ = (value == "true" || value == "1"); return; }')
        lines.append('        if (key == "calc_on_order_fills") { calc_on_order_fills_ = (value == "true" || value == "1"); return; }')
        lines.append('        if (key == "close_entries_rule") { close_entries_rule_any_ = (value == "ANY" || value == "any" || value == "1"); return; }')
        lines.append('        if (key == "default_qty_type") {')
        lines.append('            if (value == "fixed" || value == "strategy.fixed" || value == "0") default_qty_type_ = QtyType::FIXED;')
        lines.append('            else if (value == "percent_of_equity" || value == "strategy.percent_of_equity" || value == "1") default_qty_type_ = QtyType::PERCENT_OF_EQUITY;')
        lines.append('            else if (value == "cash" || value == "strategy.cash" || value == "2") default_qty_type_ = QtyType::CASH;')
        lines.append("            return;")
        lines.append("        }")
        lines.append('        if (key == "commission_type") {')
        lines.append('            if (value == "percent" || value == "strategy.commission.percent" || value == "0") commission_type_ = CommissionType::PERCENT;')
        lines.append('            else if (value == "cash_per_order" || value == "strategy.commission.cash_per_order" || value == "1") commission_type_ = CommissionType::CASH_PER_ORDER;')
        lines.append('            else if (value == "cash_per_contract" || value == "strategy.commission.cash_per_contract" || value == "2") commission_type_ = CommissionType::CASH_PER_CONTRACT;')
        lines.append("            return;")
        lines.append("        }")
        lines.append("    }")

        if self._security_eval_info:
            lines.append("")
            lines.append("    void configure_security_evaluators() override {")
            lines.append("        security_eval_states_.clear();")
            for info in self._security_eval_info:
                tf = info.get("tf")
                tf_expr = info.get("tf_expr")
                if tf:
                    tf_expr = f'"{tf}"'
                elif not tf_expr:
                    # No static tf and no resolvable runtime expression — fall
                    # back to the chart timeframe so registration still compiles
                    # (e.g. a request.security inside a dead-code UDF, or one
                    # whose tf is a function param called with mixed timeframes).
                    tf_expr = "input_tf_"
                if tf_expr:
                    la = "true" if info["lookahead_on"] else "false"
                    go = "true" if info.get("gaps_on") else "false"
                    # Heikin-Ashi same-symbol read: emit the 6th arg only when set
                    # so every non-HA strategy's generated code stays byte-identical
                    # (the engine param defaults to false).
                    ha_arg = ", true" if info.get("heikinashi") else ""
                    sec_id = info["sec_id"]
                    # The runtime registration function is named in tables.py
                    # to keep a single source of truth and to avoid embedding
                    # the bare identifier in an f-string here (the editor's
                    # built-in security scanner blocks file moves whose
                    # source text contains certain keywords as substrings).
                    if info.get("is_lower_tf_array"):
                        lines.append(
                            f"        {RUNTIME_REGISTER_SECURITY_LOWER_TF_EVAL_FN}"
                            f"({sec_id}, {tf_expr}, input_tf_);"
                        )
                    else:
                        lines.append(
                            f"        {RUNTIME_REGISTER_SECURITY_EVAL_FN}"
                            f"({sec_id}, {tf_expr}, "
                            f"input_tf_, {la}, {go}{ha_arg});")
            lines.append("    }")

    # Map strategy series member name to push expression
    _STRAT_SERIES_PUSH = {
        "position_size": "signed_position_size()",
        "closedtrades": "((int)trades_.size())",
        "opentrades": "((signed_position_size() != 0) ? 1 : 0)",
        "wintrades": "count_wintrades()",
        "losstrades": "count_losstrades()",
        "equity": "(current_equity() + open_profit(current_bar_.close))",
        "netprofit": "net_profit()",
        "openprofit": "open_profit(current_bar_.close)",
        "initial_capital": "initial_capital_",
    }

    @staticmethod
    def _emit_history_series_write(
            lines: list[str], pad: str, member: str, value: str) -> None:
        """Emit one Pine-series write without conflating history with isnew.

        Historical fill recalculations keep ``barstate.isnew`` true, but a
        post-close recalculation restored from the completed ordinary-close
        checkpoint must replace that bar's current history slot rather than
        append a duplicate slot.  The engine exposes those independent facts
        as ``is_first_tick_`` and ``history_advances_new_bar()`` respectively.
        """
        lines.append(
            f"{pad}if (history_advances_new_bar()) {member}.push({value});"
        )
        lines.append(f"{pad}else {member}.update({value});")

    def _emit_on_bar(self, lines: list[str]) -> None:
        self._lexical_drawing_types = {}
        self._lexical_udt_types = {}
        self._lexical_series_bindings = {}
        self._lexical_known_var_tombstones = set()
        lines.append("    void on_bar(const Bar& bar) override {")

        # A GeneratedStrategy handle may execute multiple batch runs or
        # streaming lifecycles. BacktestEngine resets broker/base state, but
        # generated members survive. Reset the hold-last source clocks
        # (change/mom/roc below a top-level lazy edge) and their held-source
        # Series at the first genuine slot of each lifecycle, then freeze this
        # bar's base and record it once per chart bar. This lives in on_bar
        # rather than a generated run wrapper because stream_begin() enters the
        # base run path directly. COOF post-close recalculations have
        # history_advances_new_bar()==false and therefore keep the same base.
        if self._lazy_source_clock_by_node:
            lines.append(
                "        if (history_advances_new_bar() && bar_index_ == 0) {"
            )
            for info in self._lazy_source_clock_by_node.values():
                lines.append(f"            {info['clock']}.reset();")
                lines.append(f"            {info['hist']}.clear();")
                if info["chart"] is not None:
                    lines.append(f"            {info['chart']}.clear();")
            lines.append("        }")
            for info in self._lazy_source_clock_by_node.values():
                lines.append(f"        {info['clock']}.begin_bar(bar_index_);")
                self._emit_history_series_write(
                    lines,
                    "        ",
                    info["hist"],
                    f"{info['clock']}.bar_base_source",
                )
                if info["chart"] is not None:
                    self._emit_history_series_write(
                        lines,
                        "        ",
                        info["chart"],
                        self._visit_expr(info["chart_source"]),
                    )

        # reset_run_state() owns engine/broker state, while these generated
        # Series members belong to the strategy object. Clear all of them on
        # the first genuine history slot of bar zero, unconditionally: a site
        # may live behind a branch that does not execute on bar zero. This is a
        # narrow synthetic-buffer reset, not a promise that every generated
        # member/init latch supports full same-handle reruns. The post-C rollback
        # execution has history_advances_new_bar()==false, so it preserves the
        # committed slot.
        for info in self._inline_history_members:
            lines.append(
                "        if (history_advances_new_bar() && bar_index_ == 0) "
                f"{info['member_name']}.clear();"
            )

        # A history-reading plain-UDF parameter advances on the chart clock,
        # even when lazy control flow skips its written call on this bar.  Seed
        # the new slot with the prior current value (``na`` before first reach);
        # an executed call later in the bar updates this same slot with its
        # scalar actual.  Typed-method ``series_arg`` bridges intentionally keep
        # their existing execution-clock behavior.
        for info in self._inline_history_members:
            if info["kind"] != "udf_series_arg":
                continue
            member = info["member_name"]
            lines.append(
                f"        if (history_advances_new_bar()) "
                f"{member}.push({member}.current());"
            )

        # a. Push bar field series (with bar magnifier support)
        for field_name in sorted(self.ctx.series_bar_fields):
            push_expr = BAR_SERIES_PUSH.get(field_name, f"current_bar_.{field_name}")
            self._emit_history_series_write(
                lines, "        ", f"_s_{field_name}", push_expr
            )

        # a1. Push history-referenced scalar bar builtins (time[n], bar_index[n],
        #     hl2[n], …). They land in ``series_vars`` and are declared as Series
        #     members (base.py section 6) but — unlike user series vars (pushed at
        #     their assignment) and bar fields (pushed above) — have no push site,
        #     so ``[n]`` would read an unfed buffer (the na sentinel) on every bar.
        #     Push each from its scalar lowering. A builtin whose lowering is a
        #     self-referential call (e.g. ``time_close`` -> ``time_close()``) is
        #     skipped — the call would resolve to the shadowing Series member.
        from .tables import BAR_BUILTINS
        for _bname in sorted(self.ctx.series_vars):
            if _bname in self._var_names:
                continue
            _bexpr = BAR_BUILTINS.get(_bname)
            if _bexpr is None or _bexpr.strip().startswith(f"{_bname}("):
                continue
            _bsafe = self._safe_name(_bname)
            self._emit_history_series_write(lines, "        ", _bsafe, _bexpr)

        # a2. Push strategy series
        for svar in sorted(self._strategy_series_vars):
            member = svar.replace("_strat_", "")
            push_expr = self._STRAT_SERIES_PUSH.get(member, "0")
            self._emit_history_series_write(lines, "        ", svar, push_expr)

        # b. Var init / carry-forward
        if self.ctx.var_members:
            lines.append("        if (!_var_initialized) {")
            for name, ptype, init_expr in self.ctx.var_members:
                # Callable-scoped ``var`` members initialize at their exact
                # declaration statements, not in the global on_bar preamble.
                if name in getattr(self, "_func_local_var_names", ()):
                    continue
                safe = self._safe_name(name)
                runtime_info = self._runtime_scalar_var_init_by_member.get(name)
                if (runtime_info is not None
                        and runtime_info.get("drawing_cpp") is not None):
                    if runtime_info.get("is_series"):
                        self._emit_history_series_write(
                            lines,
                            "            ",
                            safe,
                            f"{runtime_info['drawing_cpp']}{{}}",
                        )
                    continue
                # Declaration-site initialization owns nullable collection
                # members carrying runtime metadata. A persistent map/matrix
                # if/switch initializer must not also enter the legacy
                # first-bar aggregate preamble (which cannot lower a block
                # expression and formerly emitted ``/* unknown */`` for maps).
                # Keep primitive and ternary paths on their established order.
                runtime_spec = (
                    runtime_info.get("type_spec")
                    if runtime_info is not None
                    else None
                )
                if (runtime_spec is not None
                        and runtime_spec.kind in {"map", "matrix"}):
                    continue
                if name in self._array_vars:
                    for stmt in self.ctx.ast.body:
                        if isinstance(stmt, VarDecl) and stmt.name == name:
                            cpp_val = self._visit_expr(stmt.value)
                            lines.append(f"            {safe} = {cpp_val};")
                            break
                    continue
                if name in self._matrix_specs:
                    # Matrix vars are nullable ID handles. Initialize from the
                    # authored RHS in declaration order, not only from a
                    # matrix.new(...) call: ``var matrix<int> alias = source``
                    # must copy source's ID once, while a later local rebind
                    # remains independent.
                    for stmt in self.ctx.ast.body:
                        if isinstance(stmt, VarDecl) and stmt.name == name:
                            cpp_val = self._visit_rhs_value(
                                stmt.value,
                                name,
                                target_cpp_type=self._type_spec_to_cpp(
                                    self._matrix_specs[name]
                                ),
                            )
                            lines.append(f"            {safe} = {cpp_val};")
                            break
                    continue
                if name in self._map_vars:
                    for stmt in self.ctx.ast.body:
                        if isinstance(stmt, VarDecl) and stmt.name == name:
                            cpp_val = self._visit_rhs_value(
                                stmt.value,
                                name,
                                target_cpp_type=self._type_spec_to_cpp(
                                    self._map_spec_for_name(name)
                                ),
                            )
                            lines.append(f"            {safe} = {cpp_val};")
                            break
                    continue
                if name in self._runtime_scalar_var_init_members:
                    continue
                # Persistent authored UDT variables initialize from their full
                # source RHS, not only from ``Type.new``.  In particular,
                # ``var Item alias = original`` must copy the numeric handle
                # once and therefore alias the same arena record.
                udt_name = self._member_udt_type(name)
                if udt_name in self._udt_defs:
                    for stmt in self.ctx.ast.body:
                        if isinstance(stmt, VarDecl) and stmt.name == name:
                            cpp_val = self._visit_rhs_value(
                                stmt.value,
                                name,
                                target_cpp_type=udt_name,
                            )
                            lines.append(f"            {safe} = {cpp_val};")
                            break
                    continue
                if self._binding_is_series(name, safe):
                    cpp_val = self._resolve_known(init_expr)
                    cpp_val = self._typed_na_init(cpp_val, name, ptype)
                    lines.append(f"            {safe}.push({cpp_val});")
                    # Also init cloned copies for per-call-site function variants
                    init_emitted: set[str] = set()
                    for (fname, cs_idx), remap in self._func_cs_var_remap.items():
                        if cs_idx == 0:
                            continue
                        if safe in remap:
                            cloned = remap[safe]
                            if cloned not in init_emitted:
                                init_emitted.add(cloned)
                                lines.append(f"            {cloned}.push({cpp_val});")
            lines.append("            _var_initialized = true;")
            lines.append("        } else {")
            for name, _, _ in self.ctx.var_members:
                safe = self._safe_name(name)
                runtime_info = self._runtime_scalar_var_init_by_member.get(name)
                if (runtime_info is not None
                        and runtime_info.get("drawing_cpp") is not None):
                    if runtime_info.get("is_series"):
                        lines.append(f"            if ({runtime_info['flag']}) {{")
                        self._emit_history_series_write(
                            lines, "                ", safe, f"{safe}[0]"
                        )
                        lines.append("            } else {")
                        self._emit_history_series_write(
                            lines,
                            "                ",
                            safe,
                            f"{runtime_info['drawing_cpp']}{{}}",
                        )
                        lines.append("            }")
                    continue
                if name in self._array_vars:
                    continue
                if self._binding_is_series(name, safe):
                    self._emit_history_series_write(
                        lines, "            ", safe, f"{safe}[0]"
                    )
                    # Also carry-forward cloned copies for per-call-site function variants
                    carry_emitted: set[str] = set()
                    for (fname, cs_idx), remap in self._func_cs_var_remap.items():
                        if cs_idx == 0:
                            continue
                        if safe in remap:
                            cloned = remap[safe]
                            if cloned not in carry_emitted:
                                carry_emitted.add(cloned)
                                self._emit_history_series_write(
                                    lines, "            ", cloned, f"{cloned}[0]"
                                )
                    # Context-sensitive nested helper instances own fresh
                    # Series members outside the flat cs remap table.  Advance
                    # each exact fresh member once per bar as well; otherwise a
                    # valid ``fresh[1]`` history read never moves past slot 0.
                    for _owner, orig_safe, fresh_safe in self._fresh_var_members:
                        if orig_safe != safe or fresh_safe in carry_emitted:
                            continue
                        carry_emitted.add(fresh_safe)
                        self._emit_history_series_write(
                            lines,
                            "            ",
                            fresh_safe,
                            f"{fresh_safe}[0]",
                        )
            lines.append("        }")

        # c. Push non-var series (they start fresh each bar with a push)
        # (actual push happens in visit_VarDecl when the decl is visited)

        # c3. Evaluate static global inputs and variables once
        static_vars = []
        for stmt in self.ctx.ast.body:
            if isinstance(stmt, VarDecl):
                is_input = isinstance(stmt.value, FuncCall) and self._is_input_call(stmt.value)
                if is_input:
                    func_name_i, namespace_i = self._resolve_callee(stmt.value.callee)
                    is_static_global_input = (
                        stmt.name in self._global_member_vars
                        and not self._is_source_input(stmt.value)
                        and stmt.name not in self._array_vars
                        and stmt.name not in getattr(self, "_matrix_specs", {})
                        and stmt.name not in getattr(self, "_map_vars", {})
                        and not stmt.is_var
                        and not stmt.is_varip
                    )
                    if is_static_global_input:
                        safe = self._safe_name(stmt.name)
                        default = self._get_input_default(stmt.value)
                        default_cpp = self._visit_expr(default) if default is not None else "0"
                        title = self._get_input_title(stmt.value, var_name=stmt.name)
                        getter = self._input_type_to_getter(func_name_i, namespace_i)
                        default_cpp = self._coerce_string_input_default(getter, default_cpp)
                        cpp_val = f'{getter}("{title}", {default_cpp})'
                        static_vars.append(f"{safe} = {cpp_val};")

        if static_vars:
            lines.append("        if (!_inputs_initialized_) {")
            for var_expr in static_vars:
                lines.append(f"            {var_expr}")
            lines.append("            _inputs_initialized_ = true;")
            lines.append("        }")

        # c2. First-bar TA resize: rebuild any TA object whose ctor args come
        # from input-backed variables so strategy_set_input() actually changes
        # the circular-buffer sizes. Emits nothing when no TA site depends on
        # an input (the default-sized construction already matches Pine).
        self._emit_ta_runtime_reset(lines, indent=2)

        # d. Visit each statement. A stateful ``ta.*`` site below a lazy
        #    ``and``/``or`` RHS or ternary arm of a top-level statement is
        #    evaluated every bar BEFORE the statement (TV rule, see ``ta.py``).
        for stmt in self.ctx.ast.body:
            self._emit_lazy_edge_ta_hoists(stmt, lines, indent=2)
            try:
                self._visit_stmt(stmt, lines, indent=2)
            finally:
                self._clear_lazy_edge_ta_hoists()

        # e. ``// @pf-trace`` pragma block — emitted last so trace values
        #    reflect every assignment / strategy call made earlier in the
        #    bar. The block is wrapped in ``if (trace_enabled_)`` so cost
        #    is zero when tracing is off (the engine flips the flag on
        #    demand). The ``(double)`` cast covers bool/int/float without
        #    overload tax — the engine has overloads for the exact types
        #    but a single double form is the simplest emission. When no
        #    pragmas are present we emit nothing at all (zero overhead
        #    for legacy scripts).
        self._emit_pf_trace_block(lines, indent=2)

        lines.append("    }")

    def _emit_pf_trace_block(self, lines: list[str], indent: int = 2) -> None:
        """Emit the ``// @pf-trace`` instrumentation block.

        Reads ``self.ctx.pf_trace_pragmas`` (populated by
        :func:`pineforge_codegen.pragmas.extract_pf_trace_pragmas` and
        attached in :func:`pineforge_codegen.transpile`). For each
        pragma ``// @pf-trace name=expr`` we emit:

            trace(std::string("name"), (double)(<expr_cpp>));

        wrapped in a single ``if (trace_enabled_)`` guard so the entire
        block compiles to a predictable-branch zero-overhead read of one
        bool when tracing is off. ``trace_enabled_`` and the
        ``trace(name, value)`` overloads live on the engine base class
        (parallel agent owns that wiring).

        The expression is lowered through the standard expression
        visitor (``self._visit_expr``) — exactly the same machinery
        used for any other Pine expression, so logical operators
        ``and`` / ``or`` lower to ``&&`` / ``||``, member access /
        function calls / ternaries all work, and per-call-site
        rewrites are inactive (we run after the on_bar statement
        loop, where ``_active_var_remap`` and friends are at their
        default empty state).

        Pragma names match ``[A-Za-z_][A-Za-z0-9_]*`` (enforced by
        the regex in :mod:`pineforge_codegen.pragmas`) so they need
        no escaping inside the C++ string literal.

        Empty pragma list -> nothing is emitted (zero overhead).
        """
        pragmas = getattr(self.ctx, "pf_trace_pragmas", None) or []
        if not pragmas:
            return
        pad = "    " * indent
        inner = pad + "    "
        lines.append(f"{pad}if (trace_enabled_) {{")
        for pragma in pragmas:
            cpp_expr = self._visit_expr(pragma.expr_node)
            lines.append(
                f'{inner}trace(std::string("{pragma.name}"), '
                f"(double)({cpp_expr}));"
            )
        lines.append(f"{pad}}}")

    def _emit_extern_c(self, lines: list[str]) -> None:
        lines.append('extern "C" {')
        lines.append("    void* strategy_create(const char* params_json) {")
        lines.append("        return new GeneratedStrategy();")
        lines.append("    }")
        lines.append("    void run_backtest(void* s, Bar* bars, int n, ReportC* out) {")
        lines.append("        auto* strat = static_cast<GeneratedStrategy*>(s);")
        if self._security_calls:
            # If there are security calls, use the full run path. Pass empty strings
            # so the C++ runtime auto-detects input_tf from bar timestamps.
            lines.append('        strat->run(bars, n, "", "", false, 4, MagnifierDistribution::ENDPOINTS);')
        else:
            lines.append("        strat->run(bars, n);")
        lines.append("        strat->fill_report(out);")
        lines.append("    }")
        lines.append("    void run_backtest_full(void* s, Bar* bars, int n,")
        lines.append('                           const char* input_tf, const char* script_tf,')
        lines.append("                           int bar_magnifier, int magnifier_samples,")
        lines.append("                           int magnifier_dist,")
        lines.append("                           ReportC* out) {")
        lines.append('        auto* strat = static_cast<GeneratedStrategy*>(s);')
        lines.append('        std::string itf = input_tf ? input_tf : "";')
        lines.append('        std::string stf = script_tf ? script_tf : "";')
        if self._security_calls:
            # Pass empty strings through — the C++ runtime auto-detects input_tf from bar timestamps.
            # script_tf defaults to input_tf in the runtime if empty.
            lines.append("        strat->run(bars, n, itf, stf, bar_magnifier != 0, magnifier_samples,")
            lines.append("                   static_cast<MagnifierDistribution>(magnifier_dist));")
        else:
            # The magnifier-aware run() overload handles ratio=1 (no
            # aggregation), empty itf (auto-detect from bar timestamps),
            # and empty stf (default-to-input) entirely on its own, so we
            # route through it whenever ANY TF/magnifier knob is set. We
            # only fall back to the simple ``run(bars, n)`` path when the
            # caller passed NO magnifier AND NO timeframe at all.
            #
            # The previous guard ``(!itf.empty() && !stf.empty() && itf != stf)``
            # required BOTH timeframes to be present before aggregating.
            # The cloud caller passes ``input_tf=""`` (auto-detect) with a
            # concrete ``script_tf="240"``; that made the guard false and
            # the chosen ``script_tf`` was silently ignored — the strategy
            # ran on raw 1m bars with no aggregation. It also dropped the
            # bar_magnifier flag whenever the host passed empty TFs,
            # producing 0.21% exit-price drift on the magnifier probes.
            #
            # Over-approximating to ``!itf.empty() || !stf.empty()`` is
            # always correct: the TF-aware overload is a no-op when the
            # ratio resolves to 1, so the only thing lost in that case is
            # the precalc optimization — never correctness.
            lines.append("        bool needs_full_run = (bar_magnifier != 0)")
            lines.append("            || !itf.empty() || !stf.empty();")
            lines.append("        if (!needs_full_run) {")
            lines.append("            strat->run(bars, n);")
            lines.append("        } else {")
            lines.append("            strat->run(bars, n, itf, stf, bar_magnifier != 0, magnifier_samples,")
            lines.append("                       static_cast<MagnifierDistribution>(magnifier_dist));")
            lines.append("        }")
        lines.append("        strat->fill_report(out);")
        lines.append("    }")
        lines.append("    void strategy_free(void* s) {")
        lines.append("        delete static_cast<GeneratedStrategy*>(s);")
        lines.append("    }")
        lines.append("    void report_free(ReportC* report) {")
        lines.append("        BacktestEngine::free_report(report);")
        lines.append("    }")
        lines.append("    void strategy_set_input(void* s, const char* key, const char* value) {")
        lines.append("        if (!s || !key || !value) return;")
        lines.append("        static_cast<GeneratedStrategy*>(s)->set_input(key, value);")
        lines.append("    }")
        lines.append("    void strategy_set_override(void* s, const char* key, const char* value) {")
        lines.append("        if (!s || !key || !value) return;")
        lines.append("        static_cast<GeneratedStrategy*>(s)->set_strategy_override(key, value);")
        lines.append("    }")
        lines.append("    void strategy_set_magnifier_volume_weighted(void* s, int on) {")
        lines.append("        if (!s) return;")
        lines.append("        static_cast<GeneratedStrategy*>(s)->set_magnifier_volume_weighted(on != 0);")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    def _emit_udt_method_cpp_name(self, fi: FuncInfo) -> str:
        """Stable C++ identifier for a typed instance method."""
        receiver_spec = (
            fi.param_type_specs[0]
            if getattr(fi, "param_type_specs", None)
            else None
        )
        receiver = method_receiver_cpp_token(
            receiver_spec,
            fi.udt_type_name,
        )
        base = fi.node.name if fi.node else ""
        return self._func_safe_name(f"_udt_{receiver}_{base}")

    def _emit_func_def(self, fi: FuncInfo, lines: list[str], call_site_idx: int | None = None,
                       instance: dict | None = None) -> None:
        """Emit a user-defined function as a class method.

        If call_site_idx is not None, emit a per-call-site variant with
        TA member names remapped to call-site-specific copies.

        If ``instance`` is provided (a fresh context-sensitive instance minted by
        ``_build_func_instances``), emit a uniquely-named clone whose TA/var
        members come from the instance's composed remaps instead of the flat
        ``_func_cs_*_remap`` tables.
        """
        node = fi.node
        if node is None:
            return

        # Collection registries historically used raw variable names for the
        # whole translation unit.  Emit each callable against copy-on-write
        # registries overlaid with its analyzer-captured lexical TypeSpecs, then
        # restore the top-level state.  This keeps legacy readers working while
        # preventing local declarations/method dispatch from contaminating a
        # sibling UDF, UDT method, clone, or on_bar.
        prev_func_collection_specs = self._current_func_collection_specs
        prev_func_collection_shadows = self._current_func_collection_shadows
        prev_collection_types = self._collection_types
        prev_array_vars = self._array_vars
        prev_map_vars = self._map_vars
        prev_matrix_specs = self._matrix_specs
        # Start from top-level state only. Callable locals become visible at
        # their declaration statement (after its RHS), not at function entry:
        # preloading the analyzer's final inventory would make a later local
        # shadow an earlier read of a same-named global collection.
        self._current_func_collection_specs = {}
        self._current_func_collection_shadows = set()
        self._collection_types = dict(prev_collection_types)
        self._array_vars = set(prev_array_vars)
        self._map_vars = set(prev_map_vars)
        self._matrix_specs = dict(prev_matrix_specs)

        is_method = (
            bool(getattr(fi, "is_udt_method", False))
            and fi.udt_type_name
        )

        # Determine param types and set context for type inference inside body
        param_strs = []
        self._current_func_param_types = {}
        self._current_func_param_specs = {}
        param_hints = (getattr(node, "annotations", None) or {}).get(
            "param_type_hints", []
        )
        self._current_func_declared_param_names = {
            alias
            for i, param in enumerate(node.params)
            if i < len(param_hints) and param_hints[i]
            for alias in (param, self._safe_name(param))
        }
        self._current_func_series_params = set()
        self._current_func_series_param_types = {}
        self._udt_param_udt = {}
        func_sv = self.ctx.func_series_vars.get(fi.name, set())
        declared_param_specs = list(
            getattr(self.ctx, "func_declared_param_type_specs", {}).get(
                fi.name, ()
            )
        )
        variant_param_types = (
            getattr(self.ctx, "func_callsite_param_types", {}).get(
                (fi.name, call_site_idx), ()
            )
            if call_site_idx is not None
            else ()
        )
        for i, p in enumerate(node.params):
            spec = None
            receiver_spec = None
            if is_method and i == 0:
                recv_spec = (
                    fi.param_type_specs[i]
                    if i < len(fi.param_type_specs)
                    else None
                )
                receiver_spec = recv_spec

            if (
                receiver_spec is not None
                and receiver_spec.kind == "primitive"
                and p in func_sv
            ):
                # A primitive receiver used with history is a Series boundary,
                # just like an ordinary history-bearing UDF parameter.
                elem_cpp_t = self._series_param_element_cpp_type(
                    fi, i, call_site_idx
                )
                cpp_t = f"const Series<{elem_cpp_t}>&"
                spec = receiver_spec
                self._current_func_series_params.add(p)
                self._current_func_series_param_types[p] = elem_cpp_t
                self._current_func_series_param_types[
                    self._safe_name(p)
                ] = elem_cpp_t
            elif is_method and i == 0 and fi.udt_type_name:
                # Receiver pass modes follow Pine's value/ID families. A user
                # UDT is itself a numeric object-ID handle, so pass it by value:
                # field mutation dereferences the same arena record while
                # ``self := other`` remains a local handle rebind.
                recv_spec = receiver_spec
                if recv_spec is None:
                    recv_spec = self._type_spec_from_hint_name(
                        fi.udt_type_name
                    )
                spec = recv_spec
                if recv_spec is not None:
                    cpp_t = self._type_spec_to_cpp(recv_spec)
                    if (
                        recv_spec.kind in {"array", "matrix"}
                        or (
                            recv_spec.kind == "udt"
                            and recv_spec.name not in self._udt_defs
                        )
                    ):
                        cpp_t = f"{cpp_t}&"
                    if recv_spec.kind == "udt" and recv_spec.name:
                        safe_p = self._safe_name(p)
                        self._udt_param_udt[safe_p] = recv_spec.name
                        self._udt_param_udt[p] = recv_spec.name
                else:
                    # Compatibility for synthetic/legacy method records that
                    # carry only the old receiver-name field.
                    recv_cpp = DRAWING_TYPE_TO_CPP.get(
                        fi.udt_type_name, fi.udt_type_name
                    )
                    cpp_t = (
                        recv_cpp
                        if fi.udt_type_name in self._udt_defs
                        else f"{recv_cpp}&"
                    )
                    safe_p = self._safe_name(p)
                    self._udt_param_udt[safe_p] = fi.udt_type_name
                    self._udt_param_udt[p] = fi.udt_type_name
            elif fi.name == "isInSession" and i < 2:
                cpp_t = "std::string"
            elif p in func_sv:
                # This param uses history access (e.g. src[1]) — preserve its
                # Pine scalar family instead of hard-coding Series<double>.
                elem_cpp_t = self._series_param_element_cpp_type(
                    fi, i, call_site_idx
                )
                cpp_t = f"const Series<{elem_cpp_t}>&"
                self._current_func_series_params.add(p)
                self._current_func_series_param_types[p] = elem_cpp_t
                self._current_func_series_param_types[
                    self._safe_name(p)
                ] = elem_cpp_t
                if (
                    i < len(getattr(fi, "param_type_specs", []))
                    and fi.param_type_specs[i] is not None
                ):
                    spec = fi.param_type_specs[i]
            elif (
                i >= len(declared_param_specs)
                or declared_param_specs[i] is None
            ) and i < len(variant_param_types) and variant_param_types[i] in {
                PineType.INT,
                PineType.FLOAT,
                PineType.BOOL,
                PineType.STRING,
                PineType.COLOR,
            }:
                # Non-history untyped parameters are polymorphic too. A pure
                # wrapper around a stateful helper is cloned per written call,
                # so its scalar boundary must use that clone's actual family
                # instead of the first call's shared FuncInfo inference.
                variant_pt = variant_param_types[i]
                cpp_t = {
                    PineType.INT: "int64_t",
                    PineType.FLOAT: "double",
                    PineType.BOOL: "bool",
                    PineType.STRING: "std::string",
                    PineType.COLOR: "int",
                }[variant_pt]
            elif i < len(getattr(fi, "param_type_specs", [])) and fi.param_type_specs[i] is not None:
                # Precise per-param TypeSpec (declared hint or call-site inference):
                # ``pivot hi`` -> ``pivot`` numeric handle, ``line ln`` keeps its
                # established drawing-handle route, and an untyped ``s`` used as
                # a string -> ``std::string``. User UDT field mutation propagates
                # through the arena even though the handle parameter is by value.
                spec = fi.param_type_specs[i]
                cpp_t = self._type_spec_to_cpp(spec)
                if spec.kind == "udt":
                    self._udt_param_udt[p] = spec.name
                    self._udt_param_udt[self._safe_name(p)] = spec.name
                    if spec.name not in self._udt_defs:
                        cpp_t = f"{cpp_t}&"
                elif spec.kind in ("array", "map"):
                    elem = spec.element if spec.kind == "array" else spec.value
                    if elem is not None and elem.kind == "udt":
                        self._udt_param_udt[p] = elem.name
                        self._udt_param_udt[self._safe_name(p)] = elem.name
                    if spec.kind == "array":
                        cpp_t = f"{cpp_t}&"
            elif i < len(fi.param_types):
                pt = fi.param_types[i]
                cpp_t = PINE_TYPE_TO_CPP.get(pt, "double")
            else:
                cpp_t = "double"
            param_strs.append(f"{cpp_t} {self._safe_name(p)}")
            self._current_func_param_types[p] = cpp_t
            if spec is not None:
                self._current_func_param_specs[p] = spec
                self._current_func_param_specs[self._safe_name(p)] = spec

        # Determine return type: tuple, UDT, or scalar.
        # The UDT branch handles user functions whose body is ``T.new(...)``;
        # without it the function would be emitted as returning ``double`` and
        # clang errors with "no viable conversion from T to double". Probe:
        # data/validation/udt-method-probe-20-udt-return-from-func.
        if fi.returns_tuple:
            # Infer actual tuple element types from function body's last expression
            tuple_types_list = self._infer_tuple_types(node, fi.tuple_element_count)
            ret_type = f"std::tuple<{', '.join(tuple_types_list)}>"
        elif getattr(fi, "udt_return_type", None):
            # A function returning a drawing handle must emit the C++ handle
            # struct (Line/Box/Label/Linefill), not the unknown lowercase name.
            ret_type = DRAWING_TYPE_TO_CPP.get(fi.udt_return_type, fi.udt_return_type)
        elif self._func_int_return_uses_wide_history(
            fi, call_site_idx=call_site_idx
        ):
            # The history parameter is represented as int64_t so timestamp
            # values and their na sentinel cannot narrow at the return edge.
            ret_type = "int64_t"
        elif (
            call_site_idx is not None
            and self._callsite_callable_return_pine_type(
                fi, call_site_idx
            ) != PineType.UNKNOWN
        ):
            ret_type = PINE_TYPE_TO_CPP.get(
                self._callsite_callable_return_pine_type(
                    fi, call_site_idx
                ),
                "double",
            )
        elif getattr(fi, "return_type_spec", None) is not None:
            # Exact return TypeSpec (collections plus narrowly cached primitive
            # terminal reads) takes precedence over the coarse PineType slot.
            ret_type = self._type_spec_to_cpp(fi.return_type_spec)
        else:
            ret_type = PINE_TYPE_TO_CPP.get(fi.return_type, "double")
        rhs_return_cpp_type = (
            ret_type
            if (self._is_nullable_collection_cpp_type(ret_type)
                or ret_type in DRAWING_TYPE_TO_CPP.values()
                or ret_type in self._udt_defs)
            else None
        )

        # For per-call-site variants, suffix the function name and activate TA + var remapping
        func_name = (
            self._emit_udt_method_cpp_name(fi)
            if is_method
            else self._func_safe_name(fi.name)
        )
        if instance is not None:
            # Fresh context-sensitive instance: name + composed remaps come from
            # the instance record. No textual cs index (dispatch is via the
            # instance map), but it IS a state-isolated variant.
            func_name = instance["name"]
            self._active_ta_remap = instance["ta_remap"]
            self._active_var_remap = instance["var_remap"]
            self._active_fixnan_remap = instance.get("fixnan_remap", {})
            self._active_call_site_idx = None
            self._current_instance_name = instance["name"]
        elif call_site_idx is not None:
            func_name = f"{func_name}_cs{call_site_idx}"
            remap = self._func_cs_ta_remap.get((fi.name, call_site_idx), {})
            self._active_ta_remap = remap
            var_remap = self._func_cs_var_remap.get((fi.name, call_site_idx), {})
            self._active_var_remap = var_remap
            self._active_fixnan_remap = self._func_cs_fixnan_remap.get((fi.name, call_site_idx), {})
            self._active_call_site_idx = call_site_idx
            # Use the actual emitted name. Plain UDFs are unchanged; UDT
            # methods carry their `_udt_Type_method` prefix. This identity is
            # shared with _build_func_instances and synthetic-history member
            # registration, so method call paths can dispatch independently.
            self._current_instance_name = func_name
        else:
            self._active_ta_remap = {}
            self._active_var_remap = {}
            self._active_fixnan_remap = {}
            self._active_call_site_idx = None
            self._current_instance_name = None

        prev_func_locals = self._current_func_locals
        prev_func_local_types = self._current_func_local_types
        prev_lexical_drawing_types = self._lexical_drawing_types
        prev_lexical_udt_types = self._lexical_udt_types
        prev_lexical_series_bindings = self._lexical_series_bindings
        prev_known_var_tombstones = self._lexical_known_var_tombstones
        prev_func_body = getattr(self, "_current_func_body", None)
        prev_func_name = getattr(self, "_active_func_name", None)
        self._current_func_body = node.body
        self._active_func_name = fi.name
        self._current_func_locals = {n for n, _, _ in self.ctx.func_var_members.get(fi.name, [])}
        self._current_func_local_types = {}
        self._lexical_drawing_types = {
            param: DRAWING_TYPE_TO_CPP[drawing_name]
            for param in node.params
            for drawing_name in (
                (
                    self._current_func_param_specs[param].name
                    if (param in self._current_func_param_specs
                        and self._current_func_param_specs[param].kind == "udt")
                    else self._udt_param_udt.get(param)
                ),
            )
            if (drawing_name in DRAWING_TYPE_TO_CPP
                and self._current_func_param_types.get(
                    param, ""
                ).removesuffix("&").removesuffix("*")
                == DRAWING_TYPE_TO_CPP[drawing_name])
        }
        self._lexical_udt_types = {
            param: (
                spec.name
                if spec is not None
                and spec.kind == "udt"
                and spec.name in self._udt_defs
                else (
                    self._udt_param_udt.get(param)
                    if (self._udt_param_udt.get(param) in self._udt_defs
                        and self._current_func_param_types.get(
                            param, ""
                        ).removesuffix("&").removesuffix("*")
                        == self._udt_param_udt.get(param))
                    else None
                )
            )
            for param in node.params
            for spec in (self._current_func_param_specs.get(param),)
        }
        self._lexical_series_bindings = {
            param: param in self._current_func_series_params
            for param in node.params
        }
        self._lexical_known_var_tombstones = set(node.params)
        # Plain (non-persistent) scalar locals are emitted inline and live in
        # no other set; collect them so the unknown-identifier guard in
        # _visit_ident does not mistake them for undeclared symbols.
        self._current_func_locals |= self._collect_binding_names(node.body)

        lines.append(f"    {ret_type} {func_name}({', '.join(param_strs)}) {{")

        emitted_return = False
        if node.is_single_expr and node.body:
            expr = node.body[0].expr if isinstance(node.body[0], ExprStmt) else None
            if expr and self._call_is_void(expr):
                # void setter as the sole body expr — emit as statement, fall
                # through to the default return.
                self._visit_stmt(node.body[0], lines, indent=2)
            elif expr:
                lines.append(
                    "        return "
                    f"{self._visit_rhs_value(expr, target_cpp_type=rhs_return_cpp_type)};"
                )
                emitted_return = True
        else:
            for i, s in enumerate(node.body):
                if i == len(node.body) - 1 and isinstance(s, ExprStmt):
                    # A void drawing setter / delete / visual-noop, or a dropped
                    # table/polyline method call (``panel.cell(...)``), used as
                    # the last statement cannot be the return value (it lowers to
                    # a void / no-op C++ call). Emit it as a plain statement
                    # (which ``_is_skip_expr`` drops) and let the default-return
                    # path below supply the function's result.
                    if self._call_is_void(s.expr) or self._is_skip_expr(s.expr):
                        self._visit_stmt(s, lines, indent=2)
                    else:
                        lines.append(
                            "        return "
                            f"{self._visit_rhs_value(s.expr, target_cpp_type=rhs_return_cpp_type)};"
                        )
                        emitted_return = True
                elif i == len(node.body) - 1 and isinstance(s, (SwitchStmt, IfStmt)):
                    # Switch/if as last statement = return expression in PineScript
                    # Emit as: double _ret = 0; if/switch assigns _ret; return _ret;
                    # A drawing-handle / UDT return type must brace-init its
                    # default (``Label _func_ret = Label{};``) — falling through
                    # to ``_default_for_type`` would emit ``0.0`` and clang would
                    # reject ``Label _func_ret = 0.0;``.
                    if ret_type in self._udt_defs or ret_type in DRAWING_TYPE_TO_CPP.values():
                        default_ret = f"{ret_type}{{}}"
                    else:
                        default_ret = self._default_for_type(ret_type)
                    lines.append(f"        {ret_type} _func_ret = {default_ret};")
                    self._visit_if_switch_expr(
                        s,
                        "_func_ret",
                        lines,
                        indent=2,
                        target_cpp_type=rhs_return_cpp_type,
                    )
                    lines.append(f"        return _func_ret;")
                    emitted_return = True
                else:
                    self._visit_stmt(s, lines, indent=2)

        # Always emit a default return if no explicit return was emitted,
        # to avoid non-void function without return value.
        if not emitted_return:
            if fi.returns_tuple:
                default_vals = ", ".join(["0.0"] * fi.tuple_element_count)
                lines.append(f"        return std::make_tuple({default_vals});")
            else:
                if ret_type in self._udt_defs or ret_type in DRAWING_TYPE_TO_CPP.values():
                    default_ret = f"{ret_type}{{}}"
                else:
                    default_ret = self._default_for_type(ret_type)
                lines.append(f"        return {default_ret};")

        lines.append("    }")
        self._current_func_param_types = {}
        self._current_func_param_specs = {}
        self._current_func_declared_param_names = set()
        self._current_func_series_params = set()
        self._current_func_series_param_types = {}
        self._udt_param_udt = {}
        self._current_func_locals = prev_func_locals
        self._current_func_local_types = prev_func_local_types
        self._lexical_drawing_types = prev_lexical_drawing_types
        self._lexical_udt_types = prev_lexical_udt_types
        self._lexical_series_bindings = prev_lexical_series_bindings
        self._lexical_known_var_tombstones = prev_known_var_tombstones
        self._current_func_body = prev_func_body
        self._active_func_name = prev_func_name
        self._current_func_collection_specs = prev_func_collection_specs
        self._current_func_collection_shadows = prev_func_collection_shadows
        self._collection_types = prev_collection_types
        self._array_vars = prev_array_vars
        self._map_vars = prev_map_vars
        self._matrix_specs = prev_matrix_specs
        self._active_ta_remap = {}
        self._active_var_remap = {}
        self._active_fixnan_remap = {}
        self._active_call_site_idx = None
        self._current_instance_name = None

    def _emit_precalculate_and_run(self, lines: list[str]) -> None:
        has_static_ta = any(
            self._ta_site_uses_precalc(site)
            for _ti, site in enumerate(self.ctx.ta_call_sites)
            if _ti not in self._dead_ta_indices
        )
        if not has_static_ta:
            return

        replayed_source_series: list[str] = []
        for stmt in self.ctx.ast.body:
            if not isinstance(stmt, VarDecl):
                continue
            if stmt.name not in self._global_member_vars:
                continue
            if not (isinstance(stmt.value, FuncCall) and self._is_source_input(stmt.value)):
                continue
            if self._decl_binding_is_series(id(stmt), stmt.name):
                replayed_source_series.append(self._safe_name(stmt.name))
        replayed_source_series = sorted(set(replayed_source_series))

        lines.append("    void precalculate(const Bar* bars, int n) {")
        lines.append("        _use_precalc = false;")
        lines.append("        if (n <= 0 || bars == nullptr) return;")
        lines.append("")

        # Resize precalculated vectors
        for _ti, site in enumerate(self.ctx.ta_call_sites):
            if _ti in self._dead_ta_indices:
                continue
            if self._ta_site_uses_precalc(site):
                lines.append(f"        _precalc_{site.member_name}.resize(n);")

        # Reset indicators to clean slate
        lines.append("")
        for _ti, site in enumerate(self.ctx.ta_call_sites):
            if _ti in self._dead_ta_indices:
                continue
            if self._ta_site_uses_precalc(site):
                resolved = [self._resolve_ta_ctor_arg(a) for a in site.ctor_args]
                safe_resolved = []
                for r in resolved:
                    safe_resolved.append(r if self._is_compile_time_value(r) else "1")
                lines.append(f"        {site.member_name} = {site.class_name}({', '.join(safe_resolved)});")

        # Clear series
        lines.append("")
        for field_name in sorted(self.ctx.series_bar_fields):
            lines.append(f"        _s_{field_name}.clear();")
        for safe in replayed_source_series:
            lines.append(f"        {safe}.clear();")
        if self._script_has_input_source():
            lines.append("        _src_open_.clear(); _src_high_.clear(); _src_low_.clear();")
            lines.append("        _src_close_.clear(); _src_volume_.clear();")
            lines.append("        _src_hl2_.clear(); _src_hlc3_.clear();")
            lines.append("        _src_ohlc4_.clear(); _src_hlcc4_.clear();")

        # Start precalculation loop
        lines.append("")
        lines.append("        for (int i = 0; i < n; ++i) {")

        # Push OHLCV into series
        for field_name in sorted(self.ctx.series_bar_fields):
            push_expr = BAR_SERIES_PUSH.get(field_name, f"bars[i].{field_name}")
            push_expr_bars = push_expr.replace("current_bar_.", "bars[i].")
            lines.append(f"            _s_{field_name}.push({push_expr_bars});")

        # Advance the native input.source() backing series (_src_open_ etc.)
        # from bars[i] too. A static TA site's compute args can reference an
        # input.source()-derived member (e.g. ``ta.stdev(bbSourceInput, 20)``)
        # which resolves at runtime to ``get_input_source(...)`` reading one
        # of these series — normally advanced once per real bar by
        # ``_push_source_series()`` inside ``dispatch_bar()``, which this
        # standalone precalc loop never calls. Without this, every static TA
        # site fed by an input.source() reads an empty series (0.0) for the
        # entire precalculation, silently corrupting its precalculated
        # values (e.g. a Bollinger Band's stdev collapsing to 0). Gated on
        # ``_src_series_active_`` to stay a no-op for scripts with no
        # input.source() usage; cleared before and after the precalc pass so
        # replayed source history cannot leak into the real run.
        lines.append("            if (_src_series_active_) {")
        lines.append("                const double _pc_o = bars[i].open;")
        lines.append("                const double _pc_h = bars[i].high;")
        lines.append("                const double _pc_l = bars[i].low;")
        lines.append("                const double _pc_c = bars[i].close;")
        lines.append("                const double _pc_v = bars[i].volume;")
        lines.append("                _src_open_.push(_pc_o);   _src_high_.push(_pc_h);   _src_low_.push(_pc_l);")
        lines.append("                _src_close_.push(_pc_c);  _src_volume_.push(_pc_v);")
        lines.append("                _src_hl2_.push((_pc_h + _pc_l) / 2.0);")
        lines.append("                _src_hlc3_.push((_pc_h + _pc_l + _pc_c) / 3.0);")
        lines.append("                _src_ohlc4_.push((_pc_o + _pc_h + _pc_l + _pc_c) / 4.0);")
        lines.append("                _src_hlcc4_.push((_pc_h + _pc_l + _pc_c + _pc_c) / 4.0);")
        lines.append("            }")

        # Replay every top-level ``X = input.source(...)`` (or bare
        # ``X = input(close)``) assignment. A static TA site's compute args
        # often don't reference ``get_input_source(...)`` inline — they
        # reference the top-level variable the script bound it to (e.g.
        # ``bbSourceInput = input.source(close, "BB Source")`` then
        # ``ta.stdev(bbSourceInput, 20)``). That variable is deliberately
        # NOT covered by the ``_inputs_initialized_`` once-only static-input
        # block above (see ``is_static_global_input``'s ``_is_source_input``
        # exclusion) because it tracks a live per-bar series, not a frozen
        # config value — under normal per-bar dispatch it is reassigned every
        # real bar. This precalc loop has no other path that reassigns it, so
        # without this replay every is_static site downstream of it would
        # keep reading its ctor-initialized 0.0 for the whole precalculation
        # even with the ``_src_*_`` fix above.
        for stmt in self.ctx.ast.body:
            if not isinstance(stmt, VarDecl):
                continue
            if stmt.name not in self._global_member_vars:
                continue
            if not (isinstance(stmt.value, FuncCall) and self._is_source_input(stmt.value)):
                continue
            safe = self._safe_name(stmt.name)
            default = self._get_input_default(stmt.value)
            base = self._source_defval_to_base_series(default)
            title = self._get_input_title(stmt.value, var_name=stmt.name)
            cpp_val = f'get_input_source("{title}", {base})[0]'
            # A source var subscripted elsewhere in the script (e.g. ``src[1]``)
            # is declared ``Series<double>``, not a scalar double, mirroring
            # the normal per-bar path's ``{safe}.push({cpp_val})`` (see
            # ``_visit_var_decl``'s ``node.name in self.ctx.series_vars``
            # branch) — a plain ``=`` there is a compile error.
            if self._decl_binding_is_series(id(stmt), stmt.name):
                lines.append(f'            {safe}.push({cpp_val});')
            else:
                lines.append(f'            {safe} = {cpp_val};')

        # Set _precalc_loop_active = True
        self._precalc_loop_active = True
        try:
            for _ti, site in enumerate(self.ctx.ta_call_sites):
                if _ti in self._dead_ta_indices:
                    continue
                if self._ta_site_uses_precalc(site):
                    compute_args = self._ta_compute_args_for_site(site)
                    compute_args_bars = compute_args.replace("current_bar_.", "bars[i].")
                    lines.append(f"            _precalc_{site.member_name}[i] = {site.member_name}.compute({compute_args_bars});")
        finally:
            self._precalc_loop_active = False

        lines.append("        }")

        # Reset indicators and series for the real backtest run
        lines.append("")
        for _ti, site in enumerate(self.ctx.ta_call_sites):
            if _ti in self._dead_ta_indices:
                continue
            if self._ta_site_uses_precalc(site):
                resolved = [self._resolve_ta_ctor_arg(a) for a in site.ctor_args]
                safe_resolved = []
                for r in resolved:
                    safe_resolved.append(r if self._is_compile_time_value(r) else "1")
                lines.append(f"        {site.member_name} = {site.class_name}({', '.join(safe_resolved)});")

        for field_name in sorted(self.ctx.series_bar_fields):
            lines.append(f"        _s_{field_name}.clear();")
        for safe in replayed_source_series:
            lines.append(f"        {safe}.clear();")
        if self._script_has_input_source():
            lines.append("        _src_open_.clear(); _src_high_.clear(); _src_low_.clear();")
            lines.append("        _src_close_.clear(); _src_volume_.clear();")
            lines.append("        _src_hl2_.clear(); _src_hlc3_.clear();")
            lines.append("        _src_ohlc4_.clear(); _src_hlcc4_.clear();")

        lines.append("")
        lines.append("        _use_precalc = true;")
        lines.append("    }")
        lines.append("")

        # Overridden run methods
        lines.append("    void run(const Bar* bars, int n) {")
        lines.append("        precalculate(bars, n);")
        lines.append("        BacktestEngine::run(bars, n);")
        lines.append("    }")
        lines.append("")
        lines.append("    void run(const Bar* input_bars, int n_input,")
        lines.append("             const std::string& input_tf,")
        lines.append("             const std::string& script_tf,")
        lines.append("             bool bar_magnifier = false,")
        lines.append("             int magnifier_samples = 4,")
        lines.append("             MagnifierDistribution magnifier_dist = MagnifierDistribution::ENDPOINTS) {")
        lines.append("        bool needs_dynamic = bar_magnifier || !input_tf.empty() || !script_tf.empty();")
        lines.append("        if (needs_dynamic) {")
        lines.append("            _use_precalc = false;")
        lines.append("        } else {")
        lines.append("            precalculate(input_bars, n_input);")
        lines.append("        }")
        lines.append("        BacktestEngine::run(input_bars, n_input, input_tf, script_tf, bar_magnifier, magnifier_samples, magnifier_dist);")
        lines.append("    }")
