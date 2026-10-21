"""Sound, deliberately incomplete proofs for generated persistent script state.

These describe logical payloads, never STL object sizes or reserved capacity.
An unknown proof requires the external store; it is not a compile/run refusal.
This module does not migrate a container to the engine's byte store. The emitted
metadata keeps that distinction explicit until typed runtime adapters exist.
"""

from __future__ import annotations

import re
from collections import Counter

from ..ast_nodes import (
    Assignment, BinOp, ForInStmt, ForStmt, FuncCall, FuncDef, Identifier,
    MemberAccess, MethodDef, NumberLiteral, StringLiteral, UnaryOp, VarDecl,
)
from ..limits import iter_ast_nodes
from .tables import _matrix_functional_receiver


_INLINE_CPP = frozenset({"bool", "int", "int32_t", "int64_t", "uint64_t", "double"})
# These operations cannot increase the receiver's cardinality. Unknown methods,
# user calls, aliases, fields and reassignments all invalidate the proof.
_ARRAY_PRESERVING = frozenset({
    "abs", "avg", "binary_search", "binary_search_leftmost", "binary_search_rightmost",
    "clear", "copy", "covariance", "every", "fill", "first", "get", "includes",
    "indexof", "join", "last", "lastindexof", "max", "median", "min", "mode",
    "percentile_linear_interpolation", "percentile_nearest_rank", "percentrank",
    "pop", "range", "remove", "reverse", "shift", "size", "slice", "some",
    "sort", "sort_indices", "standardize", "stdev", "sum", "variance", "set",
})
_MATRIX_PRESERVING = frozenset({
    "avg", "col", "columns", "copy", "det", "diff", "eigenvalues", "eigenvectors",
    "elements_count", "fill", "get", "inv", "is_antidiagonal", "is_antisymmetric",
    "is_binary", "is_diagonal", "is_identity", "is_square", "is_stochastic",
    "is_symmetric", "is_triangular", "is_zero", "kron", "max", "median", "min",
    "mode", "mult", "pinv", "pow", "rank", "remove_col", "remove_row", "reverse",
    "row", "rows", "set", "sort", "submatrix", "sum", "swap_columns", "swap_rows",
    "trace", "transpose",
})


class _Proofs:
    def __init__(self, emitter):
        self.emitter = emitter
        self.nodes = [node for node, _ in iter_ast_nodes(emitter.ctx.ast)]
        self.counts = Counter(node.name for node in self.nodes if isinstance(node, VarDecl))
        self.reassigned = {
            node.target.name for node in self.nodes
            if isinstance(node, Assignment) and isinstance(node.target, Identifier)
        }
        shadowed = set()
        for node in self.nodes:
            if isinstance(node, (FuncDef, MethodDef)):
                shadowed.update(node.params)
            elif isinstance(node, ForStmt):
                shadowed.add(node.var)
            elif isinstance(node, ForInStmt):
                shadowed.update(node.vars or ([node.var] if node.var else []))
        # Loops and parameters can shadow a constant without a VarDecl. A
        # constant resolves only at a top-level constructor, never in a helper.
        self.top = {
            node.name: node for node in emitter.ctx.ast.body
            if (isinstance(node, VarDecl) and self.counts[node.name] == 1
                and node.name not in shadowed)
        }
        # Source-order caching keeps a flat chain of aliases flat in Python
        # too. AST nesting is bounded, but the number of top-level aliases is
        # not; following the entire chain recursively could introduce a new
        # RecursionError in an otherwise accepted script.
        self.constants = {}
        for decl in self.top.values():
            if (decl.loc is not None and decl.name not in self.reassigned
                    and not decl.is_var and not decl.is_varip):
                self.constants[decl.name] = self.integer(decl.value, decl.loc.line)
        self.user_methods = {
            node.name for node in self.nodes if isinstance(node, MethodDef)
        }
        self.safe_uses = {"array": set(), "matrix": set()}
        for node in self.nodes:
            if not isinstance(node, FuncCall) or not isinstance(node.callee, MemberAccess):
                continue
            receiver = node.callee.object
            method = node.callee.member
            if method in self.user_methods:
                continue
            for kind, methods in (("array", _ARRAY_PRESERVING),
                                  ("matrix", _MATRIX_PRESERVING)):
                if method not in methods:
                    continue
                if isinstance(receiver, Identifier):
                    if receiver.name == kind:
                        # Only the first (id) argument is the receiver. An
                        # occurrence in another slot may escape or alias it.
                        arg = (_matrix_functional_receiver(method, node)
                               if kind == "matrix" else
                               node.args[0] if node.args else node.kwargs.get("id"))
                        if isinstance(arg, Identifier):
                            self.safe_uses[kind].add(id(arg))
                    elif receiver.name not in {"array", "matrix"}:
                        self.safe_uses[kind].add(id(receiver))
        self.uses = {}
        for node in self.nodes:
            if isinstance(node, Identifier):
                self.uses.setdefault(node.name, set()).add(id(node))

    def integer(self, node, before):
        """Exact integer arithmetic in a conservative, non-overflowing subset.

        Input defaults, mutable vars, function results, division and floating
        arithmetic are not proofs. All intermediates must fit the emitted int.
        """
        result = None
        if isinstance(node, NumberLiteral) and type(node.value) is int:
            result = node.value
        elif isinstance(node, Identifier):
            decl = self.top.get(node.name)
            if (decl is not None and decl.name not in self.reassigned
                    and not decl.is_var and not decl.is_varip
                    and decl.loc is not None and decl.loc.line < before):
                result = self.constants.get(node.name)
        elif isinstance(node, UnaryOp) and node.op in {"+", "-"}:
            value = self.integer(node.operand, before)
            if value is not None:
                result = value if node.op == "+" else -value
        elif isinstance(node, BinOp) and node.op in {"+", "-", "*"}:
            left = self.integer(node.left, before)
            right = self.integer(node.right, before)
            if left is not None and right is not None:
                if node.op == "+":
                    result = left + right
                elif node.op == "-":
                    result = left - right
                else:
                    result = left * right
        return result if result is not None and -(2**31) <= result < 2**31 else None

    def fixed_cardinality(self, name, kind):
        decl = self.top.get(name)
        if (decl is None or decl.loc is None or name in self.reassigned
                or self.uses.get(name, set()) - self.safe_uses[kind]):
            return None
        value = decl.value
        if not (isinstance(value, FuncCall) and isinstance(value.callee, MemberAccess)
                and isinstance(value.callee.object, Identifier)
                and value.callee.object.name == kind):
            return None
        method = value.callee.member
        if kind == "array" and method == "from":
            return len(value.args) if not value.kwargs else None
        if not (method == "new" or kind == "array" and method.startswith("new_")):
            return None
        if kind == "array":
            size = value.args[0] if value.args else value.kwargs.get("size")
            count = 0 if size is None else self.integer(size, decl.loc.line)
        else:
            rows = value.args[0] if value.args else value.kwargs.get("rows")
            cols = value.args[1] if len(value.args) > 1 else value.kwargs.get("columns")
            r = self.integer(rows, decl.loc.line) if rows is not None else 0
            c = self.integer(cols, decl.loc.line) if cols is not None else 0
            count = r * c if r is not None and c is not None and r >= 0 and c >= 0 else None
        return count if count is not None and 0 <= count < 2**63 else None


def _row(member, kind, count, element, reason, cpp_type):
    return {
        "member": member,
        "kind": kind,
        "cpp_type": cpp_type,
        "cardinality_bound": count,
        "element_bytes_cpp": element,
        "storage_requirement": "preallocate" if count is not None and element is not None else "external",
        # A storage requirement is not a claim that an adapter was installed.
        "current_storage": "resident",
        "reason": reason,
    }


def build_script_state_bounds(emitter, declarations, checkpoint_members):
    """Inventory the actual emitted members, including callable clones/caches.

    The safe default is unknown. Scalar sizeof proofs exclude every type that
    might contain an indirect payload; sizeof(vector/string/handle) never proves
    the size of its children. Order is declaration order, independent of id().
    """
    proofs = _Proofs(emitter)
    authored = {emitter._safe_name(name): name for name in proofs.top}
    histories = {
        emitter._collection_history_member(variable): variable
        for variable in emitter._collection_history_variables()
    }
    arenas = {emitter._udt_arena_member_name(name): name for name in emitter._udt_defs}
    coordinator = (emitter._udt_undo_coordinator_member_name if emitter._udt_defs else None)
    rows = []
    for declaration in declarations:
        member = emitter._script_state_member_name(declaration)
        if member is None:
            continue
        text = declaration.strip()
        decl_match = re.match(r"^(.+?)\s+" + re.escape(member) + r"(?=[\s;{(=])", text)
        assert decl_match is not None, declaration
        cpp_type = decl_match.group(1)
        kind, count, element = "opaque", None, None
        reason = "No complete payload bound is proven."
        if cpp_type in _INLINE_CPP:
            kind, count, element = "scalar", 1, f"sizeof({cpp_type})"
            reason = "One inline primitive."
        elif member in arenas:
            kind = "udt_arena"
            type_name = arenas[member]
            specs = emitter._udt_field_type_specs.get(type_name, {})
            omitted = emitter._udt_omitted_fields.get(type_name, set())
            if all(
                emitter._type_spec_to_cpp(
                    specs.get(field.name) or emitter._type_spec_from_hint_name(field.type_name)
                ) in _INLINE_CPP
                for field in emitter._udt_defs[type_name] if field.name not in omitted
            ):
                element = f"sizeof({emitter._udt_record_cpp_type(type_name)})"
            reason = "A fixed record layout does not bound the number of objects or their children."
        elif member == coordinator:
            kind = "undo_log"
            reason = "Touched records and captured payloads have no proven bound."
        elif member in histories:
            kind = "collection_history"
            variable = histories[member]
            count = (max(2, variable.capacity) if variable.capacity is not None
                     else emitter._max_bars_back_cap)
            reason = "The history horizon bounds slots, not each frozen collection payload."
        elif cpp_type.startswith("Series<"):
            kind = "series"
            match = re.fullmatch(r"Series<(.+)>", cpp_type)
            inner = match.group(1) if match else ""
            suffix = text[decl_match.end():]
            capacity = re.fullmatch(r"\{([0-9]+)\};", suffix)
            count = int(capacity.group(1)) if capacity else None
            element = f"sizeof({inner})" if inner in _INLINE_CPP else None
            reason = "Only an explicit emitted horizon is a compiler-known slot bound."
        elif cpp_type.startswith("std::vector<"):
            kind = "precalc" if member.startswith("_precalc_") else "array"
            inner = cpp_type[len("std::vector<"):-1]
            element = f"sizeof({inner})" if inner in _INLINE_CPP else None
            if kind == "array" and member in authored:
                count = proofs.fixed_cardinality(authored[member], "array")
            reason = "Constant constructor cardinality with no growing, rebinding or escaping use." if count is not None else "Initial cardinality is not a lifetime bound."
        elif cpp_type == "PineMatrix" or cpp_type.startswith("PineGenericMatrix<"):
            kind = "matrix"
            inner = "double" if cpp_type == "PineMatrix" else cpp_type[len("PineGenericMatrix<"):-1]
            element = f"sizeof({inner})" if inner in _INLINE_CPP else None
            if member in authored:
                count = proofs.fixed_cardinality(authored[member], "matrix")
            reason = "Constant dimensions with no growing, rebinding or escaping use." if count is not None else "Matrix dimensions or aliases are not bounded."
        elif cpp_type == "std::string":
            kind = "string"
            element = "sizeof(char)"
            decl = proofs.top.get(authored.get(member))
            if decl is not None and decl.name not in proofs.reassigned and isinstance(decl.value, StringLiteral):
                try:
                    count = len(decl.value.value.encode("utf-8"))
                except UnicodeEncodeError:
                    # Metadata is optional evidence, never a new refusal.
                    count = None
            reason = "Only an immutable literal bounds the string bytes."
        elif "Map<" in cpp_type or "unordered_map<" in cpp_type:
            kind = "map"
            reason = "Neither map cardinality nor reachable payload is proven."
        rows.append(_row(member, kind, count, element, reason, cpp_type))
    by_member = {row["member"]: row for row in rows}
    fixed_checkpoint = all(
        by_member[name]["kind"] == "scalar" for name in checkpoint_members
    )
    rows.append(_row(
        "_pf_script_state_checkpoint_", "checkpoint", 1,
        "sizeof(GeneratedStrategy::_PFScriptState)" if fixed_checkpoint else None,
        "The checkpoint's fixed tuple does not bound indirect snapshots.",
        "std::optional<GeneratedStrategy::_PFScriptState>",
    ))
    return rows


def emit_script_state_bounds(emitter, lines):
    """A read-only, allocation-free query available before strategy_create/bar 0.

    This is compiler evidence only. No typed Store binding or migration is
    implied by the external requirement, and no engine declaration is guessed.
    """
    lines.extend([
        "struct _PFScriptStateBoundV1 {",
        "    const char* member;",
        "    const char* kind;",
        "    bool cardinality_known;",
        "    uint64_t cardinality_bound;",
        "    bool element_bytes_known;",
        "    size_t element_bytes;",
        "    const char* storage_requirement;",
        "    const char* current_storage;",
        "};",
        'extern "C" const _PFScriptStateBoundV1* strategy_script_state_bounds_v1(size_t* count) {',
        "    static const _PFScriptStateBoundV1 bounds[] = {",
    ])
    for row in emitter.script_state_bounds:
        name = emitter._cpp_string_escape(row["member"])
        count = row["cardinality_bound"]
        size = row["element_bytes_cpp"]
        lines.append(
            f'        {{"{name}", "{row["kind"]}", {str(count is not None).lower()}, '
            f'{count if count is not None else 0}ULL, {str(size is not None).lower()}, '
            f'{size if size is not None else 0}, "{row["storage_requirement"]}", "resident"}},'
        )
    lines.extend([
        "    };",
        "    if (count) *count = sizeof(bounds) / sizeof(bounds[0]);",
        "    return bounds;",
        "}",
        "",
    ])
