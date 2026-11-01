"""Checkpoint journals for proven, unaliased primitive map and matrix roots."""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode, Assignment, ForInStmt, ForStmt, FuncCall, FuncDef,
    Identifier, MemberAccess, MethodDef, NaLiteral, Program,
    Subscript, TupleAssign, VarDecl,
)
from ..errors import Phase
from ..limits import syntax_children
from .tables import MATRIX_RECEIVER_KEYWORD


MAP_DELTA = frozenset({"put"})
MAP_FULL = frozenset({"remove", "clear", "put_all"})
MAP_READ = frozenset({"get", "contains", "size", "keys", "values", "copy"})
MATRIX_DELTA = frozenset({"set"})
MATRIX_FULL = frozenset({"fill", "reshape", "reverse", "sort"})
MATRIX_MIXED = frozenset({
    "add_row", "add_col", "remove_row", "remove_col", "swap_rows", "swap_columns",
})
MATRIX_EAGER = frozenset({"concat"})
MATRIX_READ = frozenset({
    "get", "row", "col", "rows", "columns", "copy", "submatrix", "transpose",
    "avg", "min", "max", "mode", "sum", "diff", "mult", "pow", "det", "inv",
    "pinv", "rank", "trace", "eigenvalues", "eigenvectors", "kron",
    "elements_count", "is_square", "is_identity", "is_diagonal",
    "is_antidiagonal", "is_symmetric", "is_antisymmetric", "is_triangular",
    "is_stochastic", "is_binary", "is_zero",
})
_PRIMITIVES = frozenset({"int", "int64", "float", "bool", "string", "color"})


def _primitive(spec):
    return spec is not None and spec.kind == "primitive" and spec.name in _PRIMITIVES


def prepare(cg):
    """Prove all uses before adding roots to the existing coordinator plan.

    The syntax/dependency graph follows definitions and calls once, including
    trace expressions and callable defaults. Unknown binding, lifetime, write
    coverage or request reachability retains the original eager emission.
    """
    cg._graph_journal_members = {}
    cg._graph_journal_calls = {}
    if cg._udt_defs or not (cg._uses_map or cg._uses_matrix):
        return
    enabled = cg.ctx.strategy_params.get("calc_on_order_fills") is True
    if not enabled:
        return

    roots = [cg.ctx.ast]
    roots.extend(p.expr_node for p in (cg.ctx.pf_trace_pragmas or []))
    roots.extend(fi.node for fi in cg.ctx.func_infos if fi.node is not None)
    nodes, parents, owners, graph, bindings, calls_by_owner = {}, {}, {}, {}, {}, {}
    ambiguous, mutable_targets, methods, requests = set(), set(), set(), []
    function_keys = {id(fi.node): fi.name for fi in cg.ctx.func_infos if fi.node is not None}
    plain_functions = {fi.node.name: fi.name for fi in cg.ctx.func_infos
                       if fi.node is not None and not fi.is_udt_method}
    for function in cg.ctx.func_infos:
        if function.node is not None:
            for spelling in {function.name, function.name.rsplit(".", 1)[-1]}:
                graph.setdefault(("call", spelling), []).append(id(function.node))
    stack = [(root, None, root, None) for root in reversed(roots)]
    while stack:
        node, parent, top, owner = stack.pop()
        key = id(node)
        if key in nodes:
            if isinstance(node, Identifier) and parents[key] is not parent:
                ambiguous.add(node.name)
            continue
        if len(nodes) >= 100_000:
            return
        if cg._budget is not None:
            cg._budget.check(phase=Phase.CODEGEN)
        nodes[key], parents[key], owners[key] = node, parent, owner
        children = list(syntax_children(node))
        if isinstance(node, (FuncDef, MethodDef)):
            children.extend(default for default in (node.annotations or {}).get("param_defaults", ())
                            if isinstance(default, ASTNode))
        graph[key] = [id(child) for child in children]
        names = []
        if isinstance(node, VarDecl):
            names = [node.name]
        elif isinstance(node, TupleAssign):
            names = node.names
        elif isinstance(node, (FuncDef, MethodDef)):
            names = [node.name, *node.params]
            owner = function_keys.get(key, node.name)
            if isinstance(node, MethodDef):
                owner = f"{node.type_name}.{node.name}"
                methods.add(node.name)
        elif isinstance(node, ForStmt):
            names = [node.var]
        elif isinstance(node, ForInStmt):
            names = node.vars or [node.var]
        for name in names:
            if name:
                bindings.setdefault(cg._safe_name(name), []).append(node)
        if owner is None:
            writes = []
            if isinstance(node, VarDecl):
                writes = [node.name]
            elif isinstance(node, TupleAssign):
                writes = node.names
            elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
                writes = [node.target.name]
            for name in writes:
                graph.setdefault(("value", name), []).append(id(top))
        if isinstance(node, Identifier):
            graph[key].append(("value", node.name))
        if isinstance(node, Assignment):
            target = node.target
            while isinstance(target, (MemberAccess, Subscript)):
                mutable_targets.add(id(target))
                target = target.object
            mutable_targets.add(id(target))
        if isinstance(node, FuncCall):
            name, namespace = cg._resolve_callee(node.callee)
            graph[key].append(("call", name))
            if namespace == "request":
                requests.append(key)
            bound = cg.ctx.func_call_cs_map.get(key)
            function = cg._func_info_map.get(bound[0] if bound else name)
            if function is not None and function.node is not None:
                graph[key].append(id(function.node))
            target = (bound[0] if bound else
                      plain_functions.get(name) if isinstance(node.callee, Identifier) else None)
            if target is not None:
                calls_by_owner.setdefault(owner, set()).add(target)
        for child in reversed(children):
            stack.append((child, node, child if isinstance(node, Program) else top, owner))

    requested, pending = set(), list(requests)
    while pending:
        key = pending.pop()
        if key not in requested:
            requested.add(key)
            pending.extend(graph.get(key, ()))
    live, pending = set(), [None]
    while pending:
        owner = pending.pop()
        if owner not in live and owner not in cg._dead_func_names:
            live.add(owner)
            pending.extend(calls_by_owner.get(owner, ()))

    history_names = {value.name for value in cg.ctx.collection_history.values()}
    metadata = cg.ctx.var_member_metadata_by_node
    candidates = {}
    for declaration in cg.ctx.ast.body:
        if not isinstance(declaration, VarDecl) or not declaration.is_var or declaration.is_varip:
            continue
        name, safe = declaration.name, cg._safe_name(declaration.name)
        meta = metadata.get(id(declaration))
        spec = cg._global_collection_types.get(name)
        if (meta is None or meta[0] is not declaration or meta[1] != name or meta[4]
                or len(bindings.get(safe, ())) != 1 or bindings[safe][0] is not declaration
                or name in ambiguous or name in history_names or name in cg.ctx.series_vars
                or safe in cg._series_var_member_names or ("value", name) in requested
                or spec is None or spec.kind not in {"map", "matrix"}):
            continue
        if spec.kind == "map":
            if not (_primitive(spec.key) and _primitive(spec.value)):
                continue
        elif not _primitive(spec.element):
            continue
        # Only fresh constructors and typed na prove the initial owner cannot
        # share a backing with an eager, requested or varip root.
        value = declaration.value
        if not isinstance(value, NaLiteral):
            if not isinstance(value, FuncCall):
                continue
            constructor, namespace = cg._resolve_callee(value.callee)
            if (constructor != "new" or namespace != spec.kind
                    or cg._safe_name(namespace) in bindings):
                continue
        candidates[name] = (declaration, spec.kind)
    if not candidates:
        return

    invalid, mutated, calls = set(), set(), {}
    for key, node in nodes.items():
        if not isinstance(node, Identifier) or node.name not in candidates:
            continue
        name = node.name
        kind = candidates[name][1]
        scope = cg.ctx.identifier_binding_scopes.get(key)
        if scope is not None and scope != "global":
            invalid.add(name)
            continue
        parent, call, method = parents[key], None, None
        if (isinstance(parent, MemberAccess) and parent.object is node
                and isinstance(parents.get(id(parent)), FuncCall)):
            call = parents[id(parent)]
            if call.callee is parent and parent.member not in methods:
                method = parent.member
        elif isinstance(parent, FuncCall):
            function, namespace = cg._resolve_callee(parent.callee)
            keyword = MATRIX_RECEIVER_KEYWORD.get(function, "id") if kind == "matrix" else "id"
            receiver = parent.args[0] if parent.args else parent.kwargs.get(keyword)
            if (namespace == kind and cg._safe_name(kind) not in bindings and receiver is node):
                call, method = parent, function
        changing = MAP_DELTA | MAP_FULL if kind == "map" else MATRIX_DELTA | MATRIX_FULL | MATRIX_MIXED
        reads = MAP_READ if kind == "map" else MATRIX_READ
        if call is None or method not in changing | reads or id(call) in mutable_targets:
            invalid.add(name)
            continue
        calls[id(call)] = (name, kind, method)
        if method in changing and owners[key] in live:
            mutated.add(name)
    admitted = {name for name in candidates if name in mutated and name not in invalid}
    if not admitted:
        return
    cg._graph_journal_members = {
        cg._safe_name(name): kind for name, (_, kind) in candidates.items() if name in admitted
    }
    cg._graph_journal_calls = {key: value for key, value in calls.items() if value[0] in admitted}
    # Reuse one coordinator and the existing token/hook/reset path. Array-only
    # scripts never enter this branch, so their generated support is unchanged.
    if not cg._array_journal_members:
        used = set(bindings) | cg._all_member_names | set(cg._enum_defs)
        used.update(getattr(cg, "_runtime_var_init_flag_used_names", ()))
        used.update(instance["name"] for instance in cg._fresh_instances)
        used.update(cg._func_safe_name(fi.name) for fi in cg.ctx.func_infos)
        used.update(cg._session_history_member_names.values())
        cg._array_journal_cpp_name = cg._allocate_generated_cpp_name("_PFGraphJournal", used)
        cg._array_journal_member_name = cg._allocate_generated_cpp_name("_pf_graph_journal", used)
        cg._array_journal_generation_name = cg._allocate_generated_cpp_name("_pf_next_generation", used)
        cg._all_member_names.add(cg._array_journal_member_name)
    cg._array_journal_members.update({
        cg._safe_name(name): declaration for name, (declaration, _) in candidates.items()
        if name in admitted
    })


def receiver(cg, expression, kind, method, node):
    """Wrap only inventoried mutation call nodes; keep existing argument code."""
    selected = getattr(cg, "_graph_journal_calls", {}).get(id(node))
    changing = MAP_DELTA | MAP_FULL if kind == "map" else MATRIX_DELTA | MATRIX_FULL | MATRIX_MIXED
    if selected is not None and selected[1:] == (kind, method) and method in changing:
        return f"this->{cg._array_journal_member_name}.{kind}({expression})"
    return expression


def runtime(cg, array_runtime):
    """Add handle adapters inside the same ordered coordinator, only if used."""
    kinds = set(getattr(cg, "_graph_journal_members", {}).values())
    if not kinds:
        return array_runtime
    additions = _HANDLE_CPP
    if "map" in kinds:
        additions += _MAP_CPP
    if "matrix" in kinds:
        additions += _MATRIX_CPP
    return array_runtime.removesuffix("};\n") + additions + "};\n"


_HANDLE_CPP = r"""
private:
    struct HandleState {
        uint64_t generation = 0;
        uint64_t saved_generation = 0;
        size_t deltas = 0;
    };
    // Node addresses survive rehash; undo callbacks keep pointers to these states.
    std::unordered_map<const void*, HandleState> handle_states_;
    HandleState& handle_state(const void* root) {
        auto& state = handle_states_[root];
        if (state.generation != generation_) state = {generation_, 0, 0};
        return state;
    }
    bool handle_saved(const void* root) const {
        const auto found = handle_states_.find(root);
        return found != handle_states_.end()
            && found->second.saved_generation == generation_;
    }
public:
    size_t entry_count() const { return entries_.size(); }
    template <typename H>
    void validate(const Token& token, const H& handle) const {
        if (!active_ || token.owner != &handle || token.generation != generation_)
            throw std::logic_error("Invalid collection checkpoint owner or generation");
    }
    template <typename F>
    size_t append_handle(const void* root, F&& undo, bool committed = true) {
        entries_.push_back(Entry{root, generation_, committed, std::forward<F>(undo)});
        return entries_.size() - 1;
    }
    template <typename H>
    void save_handle(const H& handle, const void* root) {
        if (!recording() || handle.is_na() || handle_saved(root)) return;
        auto& state = handle_state(root);
        auto old = std::make_shared<typename H::Snapshot>(handle.snapshot());
        append_handle(root, [owner = handle, old = std::move(old), state = &state]() mutable {
            owner.restore(*old);
            // Keep the latch if restore throws and the record must be retried.
            state->saved_generation = 0;
        });
        // Publish only after both the preimage and its undo record exist.
        state.saved_generation = generation_;
    }
    template <typename H>
    bool record_handle_delta(const H& handle, const void* root, size_t elements) {
        if (!recording() || handle.is_na() || handle_saved(root)) return false;
        auto& state = handle_state(root);
        // The next delta would exceed the current payload's element count.
        if (state.deltas >= elements) {
            save_handle(handle, root);
            return false;
        }
        return true;
    }
    template <typename F>
    size_t append_handle_delta(const void* root, F&& undo, bool committed = true) {
        auto& state = handle_state(root);
        const size_t entry = append_handle(root,
            [state = &state, undo = std::forward<F>(undo)]() mutable {
                undo();
                --state->deltas;
            }, committed);
        if (committed) ++state.deltas;
        return entry;
    }
    void commit_handle_delta(size_t entry) {
        auto& state = handle_states_.at(entries_[entry].owner);
        entries_[entry].committed = true;
        ++state.deltas;
    }
    template <typename H, typename V>
    void replace(H& handle, V&& value) {
        if (recording()) {
            append_handle(&handle, [root = &handle, old = handle]() {
                *root = old;
            });
        }
        handle = std::forward<V>(value);
    }
"""


_MAP_CPP = r"""
    template <typename K, typename V>
    struct MapTarget {
        @JOURNAL@& journal;
        const void* root;
        pineforge::PineMap<K, V> owner;
        V put(K key, V value) {
            if (journal.recording() && !owner.is_na()
                    && journal.record_handle_delta(owner, root, owner.size())) {
                std::optional<V> old;
                if (owner.contains(key)) old.emplace(owner.get(key));
                journal.append_handle_delta(root, [handle = owner, key, old = std::move(old)]() mutable {
                    if (old) (void)handle.put(key, *old);
                    else (void)handle.remove(key);
                });
            }
            return owner.put(std::move(key), std::move(value));
        }
        V remove(const K& key) {
            if (journal.recording() && !owner.is_na() && owner.contains(key))
                journal.save_handle(owner, root);
            return owner.remove(key);
        }
        void clear() {
            if (!owner.is_na() && owner.size() != 0) journal.save_handle(owner, root);
            owner.clear();
        }
        void put_all(const pineforge::PineMap<K, V>& source) {
            if (!source.is_na()) journal.save_handle(owner, root);
            owner.put_all(source);
        }
    };
    template <typename K, typename V>
    MapTarget<K, V> map(pineforge::PineMap<K, V>& handle) {
        return {*this, &handle, handle};
    }
"""


_MATRIX_CPP = r"""
    template <typename H>
    struct MatrixTarget {
        using Value = decltype(std::declval<H>().get(0, 0));
        static constexpr bool generic = !std::is_same_v<H, pineforge::PineMatrix>;
        @JOURNAL@& journal;
        const void* root;
        H owner;
        int rows() const { return owner.rows(); }
        int columns() const { return owner.columns(); }
        void set(int row, int column, Value value) {
            // Invalid targets retain the setter's own validation and error.
            if (journal.recording() && !owner.is_na()
                    && row >= 0 && row < owner.rows()
                    && column >= 0 && column < owner.columns()
                    && journal.record_handle_delta(owner, root,
                        (size_t)owner.rows() * (size_t)owner.columns())) {
                Value old = owner.get(row, column);
                journal.append_handle_delta(root, [handle = owner, row, column, old = std::move(old)]() mutable {
                    handle.set(row, column, old);
                });
            }
            owner.set(row, column, std::move(value));
        }
        void fill(Value value) {
            journal.save_handle(owner, root);
            owner.fill(std::move(value));
        }
        void add_row(int index, const std::vector<Value>& values) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && index == owner.rows()
                        && (owner.rows() == 0 || values.size() == (size_t)owner.columns())
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    // End insertion has a strong guarantee; removing that
                    // row also recovers a formerly empty 0x0 matrix.
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, index]() mutable {
                        handle.remove_row(index);
                    }, false);
                    owner.add_row(index, values);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.add_row(index, values);
        }
        void add_col(int index, const std::vector<Value>& values) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && owner.rows() > 0
                        && index >= 0 && index <= owner.columns()
                        && values.size() == (size_t)owner.rows()
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, index]() mutable {
                        handle.remove_col(index);
                    }, false);
                    owner.add_col(index, values);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.add_col(index, values);
        }
        void remove_row(int index) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && index >= 0 && index < owner.rows()
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    auto old = owner.row(index);
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, index, old = std::move(old)]() mutable {
                        handle.add_row(index, old);
                    }, false);
                    owner.remove_row(index);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.remove_row(index);
        }
        void remove_col(int index) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && index >= 0 && index < owner.columns()
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    auto old = owner.col(index);
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, index, old = std::move(old)]() mutable {
                        handle.add_col(index, old);
                    }, false);
                    owner.remove_col(index);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.remove_col(index);
        }
        void swap_rows(int first, int second) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && first >= 0 && second >= 0
                        && first < owner.rows() && second < owner.rows()
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, first, second]() mutable {
                        handle.swap_rows(first, second);
                    }, false);
                    owner.swap_rows(first, second);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.swap_rows(first, second);
        }
        void swap_columns(int first, int second) {
            if constexpr (generic) {
                if (journal.recording() && !owner.is_na() && first >= 0 && second >= 0
                        && first < owner.columns() && second < owner.columns()
                        && journal.record_handle_delta(owner, root,
                            (size_t)owner.rows() * (size_t)owner.columns())) {
                    const size_t entry = journal.append_handle_delta(root, [handle = owner, first, second]() mutable {
                        handle.swap_columns(first, second);
                    }, false);
                    owner.swap_columns(first, second);
                    journal.commit_handle_delta(entry);
                    return;
                }
            }
            journal.save_handle(owner, root);
            owner.swap_columns(first, second);
        }
        void reshape(int rows, int columns) {
            journal.save_handle(owner, root);
            owner.reshape(rows, columns);
        }
        void reverse() {
            journal.save_handle(owner, root);
            owner.reverse();
        }
        void sort(int column, bool ascending = true) {
            journal.save_handle(owner, root);
            owner.sort(column, ascending);
        }
    };
    template <typename H>
    MatrixTarget<H> matrix(H& handle) {
        return {*this, &handle, handle};
    }
"""
