"""Creation-bar snapshots for direct retained bindings of stable var arrays.

This is deliberately separate from the bar-local field representation. The
selection proves the lifetime and input identity before changing any storage;
unproved escapes keep their previous lowering, including copy refusals.
"""

from ..ast_nodes import (
    Assignment, ForInStmt, ForStmt, FuncCall, FuncDef, Identifier,
    MemberAccess, MethodDef, NaLiteral, Subscript, TupleAssign, VarDecl,
)
from ..errors import Phase
from ..limits import iter_ast_nodes


class RetainedAliasPlan:
    def __init__(self):
        self.types = set()
        self.aliases = {}
        self.sources = {}
        self.cpp_name = "_PFRetainedArrayField"
        self.manager_name = "_PFRetainedArrayManager"

    def __bool__(self):
        return bool(self.types or self.aliases)

    def field_cpp(self, cg, spec):
        return f"{self.cpp_name}<{cg._type_spec_to_cpp(spec.element)}>"

    def manager_cpp(self, cg, spec):
        return f"{self.manager_name}<{cg._type_spec_to_cpp(spec.element)}>"

    def binding(self, cg, source):
        return f"{self.sources[source]}.pending({cg._safe_name(source)})"


def prepare(cg):
    plan = cg._retained_alias_plan = RetainedAliasPlan()
    # Requested clocks, collection history and intrabar rollback are outside
    # the covered shapes. Do not change their representation or generated text.
    if (cg._security_calls or cg.ctx.collection_history
            or cg.ctx.strategy_params.get("calc_on_order_fills") is True
            or cg.ctx.strategy_params.get("calc_on_every_tick") is True):
        return
    nodes = [node for node, _ in iter_ast_nodes(cg.ctx.ast)]
    top = {id(node) for node in cg.ctx.ast.body}
    stable = cg._stable_var_array_names()
    shadowed = set()
    for node in nodes:
        if isinstance(node, (FuncDef, MethodDef)):
            shadowed.update(node.params)
        elif isinstance(node, ForStmt):
            shadowed.add(node.var)
        elif isinstance(node, ForInStmt):
            shadowed.update(node.vars or [node.var])
        elif isinstance(node, TupleAssign):
            shadowed.update(node.names)
    primitive_sources = {
        name for name in stable - shadowed
        if (spec := cg._array_spec_for_name(name)) is not None
        and spec.element is not None and spec.element.kind == "primitive"
    }
    if not primitive_sources:
        return
    root_sources = {
        node.name for node in cg.ctx.ast.body
        if isinstance(node, VarDecl) and node.name in primitive_sources
        and not isinstance(node.value, Identifier)
    }
    callable_nodes = {
        id(child)
        for node in nodes if isinstance(node, (FuncDef, MethodDef))
        for child, _ in iter_ast_nodes(node)
    }
    specs = {}
    for node in nodes:
        if cg._budget is not None:
            cg._budget.check(phase=Phase.CODEGEN)
        if isinstance(node, VarDecl):
            spec = (cg._type_spec_from_hint_name(node.type_hint)
                    if node.type_hint else cg._type_spec_from_expr(node.value))
            specs[id(node)] = spec

    def udts(spec):
        if spec is None:
            return set()
        if spec.kind == "udt":
            return {spec.name}
        return udts(spec.element) | udts(spec.key) | udts(spec.value)

    def direct_binding(value, type_name):
        if isinstance(value, (Identifier, NaLiteral)):
            return True
        if not isinstance(value, FuncCall):
            return False
        method, namespace = cg._resolve_callee(value.callee)
        return (namespace == type_name and method in {"new", "copy"}
                or method == "copy" and isinstance(value.callee, MemberAccess))

    # Only a direct, script-level var owns the retained object in this
    # lowering. Nested objects, collection elements and histories keep theirs.
    candidates, excluded = set(), set()
    for node in nodes:
        if isinstance(node, VarDecl):
            spec = specs[id(node)]
            held = udts(spec)
            if spec is not None and spec.kind != "udt":
                excluded |= held
            if node.is_varip or ((node.is_var or node.is_varip) and id(node) not in top):
                excluded |= held
            elif node.is_var and id(node) in top:
                candidates |= held
        elif isinstance(node, Subscript):
            spec = cg._type_spec_from_expr(node.object)
            if spec is not None and spec.kind in {"array", "udt"}:
                return
        elif isinstance(node, FuncCall):
            method, namespace = cg._resolve_callee(node.callee)
            # Preserve array.copy/slice, including their existing ownership.
            if namespace == "array" and method in {"copy", "slice"}:
                return
            if isinstance(node.callee, MemberAccess) and method in {"copy", "slice"}:
                receiver = node.callee.object
                spec = cg._type_spec_from_expr(receiver)
                # Parameter and loop-variable types are not known at plan time:
                # a receiver bound by one of them may be an array.
                if (spec is not None and spec.kind == "array"
                        or spec is None and isinstance(receiver, Identifier)
                        and receiver.name in shadowed):
                    return
            if namespace in {"array", "map", "matrix"} or method in {
                    "push", "unshift", "insert", "set", "fill", "put"}:
                for arg in [*node.args, *node.kwargs.values()]:
                    excluded |= udts(cg._type_spec_from_expr(arg))
                for hint in cg._template_args_from_call(node) or ():
                    excluded |= udts(cg._type_spec_from_hint_name(hint))
    for fields in cg._udt_field_type_specs.values():
        for spec in fields.values():
            excluded |= udts(spec)
    candidates -= excluded

    used_sources = set()
    for type_name in cg._udt_defs:
        if type_name not in candidates:
            continue
        fields = cg._udt_defs[type_name]
        array_fields = {
            field.name: index for index, field in enumerate(fields)
            if (spec := cg._udt_field_type_specs.get(type_name, {}).get(field.name)) is not None
            and spec.kind == "array"
        }
        if not array_fields:
            continue
        if any(cg._udt_field_type_specs[type_name][name].element is None
               or cg._udt_field_type_specs[type_name][name].element.kind != "primitive"
               for name in array_fields):
            continue
        valid, constructed, sources = True, False, set()
        for node in nodes:
            if cg._budget is not None:
                cg._budget.check(phase=Phase.CODEGEN)
            if isinstance(node, VarDecl) and type_name in udts(specs[id(node)]):
                # Unknown helper returns keep the old lifetime decision.
                valid &= direct_binding(node.value, type_name)
            if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                target = cg._type_spec_from_expr(node.target)
                if type_name in udts(target):
                    valid &= direct_binding(node.value, type_name)
            if isinstance(node, Assignment) and isinstance(node.target, MemberAccess):
                holder = cg._type_spec_from_expr(node.target.object)
                if (node.target.member in array_fields
                        and (holder is None
                             or holder.kind == "udt" and holder.name == type_name)):
                    # Without a holder type, a matching array field may be
                    # rebound through a parameter. Selection needs a proof.
                    valid = False
            if not isinstance(node, FuncCall):
                continue
            method, namespace = cg._resolve_callee(node.callee)
            if namespace != type_name or method != "new":
                continue
            constructed = True
            if id(node) in callable_nodes:
                valid = False
            for field_name, index in array_fields.items():
                value = (node.args[index] if index < len(node.args)
                         else node.kwargs.get(field_name))
                if not isinstance(value, Identifier) or value.name not in root_sources:
                    valid = False
                else:
                    sources.add(value.name)
        if valid and constructed:
            plan.types.add(type_name)
            used_sources |= sources

    # Plain var-to-var arrays take a binding of their own, as retained fields do.
    # Only uniquely bound, never-reassigned top-level names are eligible.
    for node in cg.ctx.ast.body:
        if (not isinstance(node, VarDecl) or not node.is_var or node.is_varip
                or node.name not in primitive_sources
                or not isinstance(node.value, Identifier)
                or node.value.name not in root_sources):
            continue
        # A chain of retained array aliases has not been measured.
        plan.aliases[node.name] = node.value.name
        used_sources.add(node.value.name)

    if any(isinstance(node, VarDecl) and isinstance(node.value, Identifier)
           and node.value.name in plan.aliases for node in nodes):
        plan.types.clear()
        plan.aliases.clear()
        return
    if not plan:
        return
    used = set(cg._all_member_names)
    used.update(cg._safe_name(name) for name in cg._all_bound_names)
    used.update(getattr(cg, "_runtime_var_init_flag_used_names", ()))
    used.update(cg._func_safe_name(info.name) for info in cg.ctx.func_infos)
    used |= set(cg._udt_defs) | set(cg._udt_record_cpp_names.values())
    used.add(cg._checkpoint_traits_cpp_type())
    used.add(cg._udt_arena_template_cpp_name)
    plan.cpp_name = cg._allocate_generated_cpp_name(plan.cpp_name, used)
    plan.manager_name = cg._allocate_generated_cpp_name(plan.manager_name, used)
    for source in sorted(used_sources):
        name = cg._allocate_generated_cpp_name(
            f"_pf_retained_{cg._safe_name(source)}", used)
        plan.sources[source] = name
        cg._all_member_names.add(name)


RUNTIME = r"""
template <typename T>
class @FIELD@ {
public:
    struct Binding {
        std::vector<T>* pending = nullptr;
        std::shared_ptr<std::vector<T>> owned;
    };
    @FIELD@() = default;
    bool is_na() const { return !binding_; }
    std::vector<T>& operator*() const {
        if (is_na())
            _PF_NA_STOP("array", "Cannot call array methods when ID of array is 'na'.");
        return binding_->pending ? *binding_->pending : *binding_->owned;
    }
    const std::shared_ptr<Binding>& binding() const { return binding_; }
    void rebind(std::shared_ptr<Binding> binding) { binding_ = std::move(binding); }
private:
    std::shared_ptr<Binding> binding_;
};

template <typename T>
class @MANAGER@ {
public:
    // Every holder gets its own binding: it reads the live source until the
    // bar ends and then keeps its own copy of it.
    @FIELD@<T> pending(std::vector<T>& source) {
        auto binding = std::make_shared<typename @FIELD@<T>::Binding>();
        binding->pending = &source;
        open_.push_back(binding);
        @FIELD@<T> handle;
        handle.rebind(std::move(binding));
        return handle;
    }
    void close() {
        for (const auto& weak : open_) {
            if (auto binding = weak.lock()) {
                if (binding->pending) {
                    binding->owned = std::make_shared<std::vector<T>>(*binding->pending);
                    binding->pending = nullptr;
                }
            }
        }
        open_.clear();
    }
private:
    std::vector<std::weak_ptr<typename @FIELD@<T>::Binding>> open_;
};
"""


def checkpoint_traits(cg):
    """Keep the binding, its pending or owned state and the owned contents."""
    field = cg._retained_alias_plan.cpp_name
    traits = cg._checkpoint_traits_cpp_type()
    return f"""
template <typename T>
struct {traits}<{field}<T>> {{
    using field_type = {field}<T>;
    using binding_type = typename field_type::Binding;
    using vector_traits = {traits}<std::vector<T>>;
    struct snapshot_type {{
        std::shared_ptr<binding_type> binding;
        binding_type state;
        typename vector_traits::snapshot_type contents;
    }};
    static snapshot_type take(const field_type& value) {{
        snapshot_type snapshot{{value.binding(), {{}}, {{}}}};
        if (snapshot.binding) {{
            snapshot.state = *snapshot.binding;
            if (snapshot.state.owned)
                snapshot.contents = vector_traits::take(*snapshot.state.owned);
        }}
        return snapshot;
    }}
    static void restore(field_type& value, const snapshot_type& snapshot) {{
        value.rebind(snapshot.binding);
        if (snapshot.binding) {{
            *snapshot.binding = snapshot.state;
            if (snapshot.state.owned)
                vector_traits::restore(*snapshot.state.owned, snapshot.contents);
        }}
    }}
}};
"""
