"""Frozen array versions selected by the shared array mutation proof."""

from __future__ import annotations

from ..ast_nodes import Assignment, MemberAccess, Subscript
from ..collection_history import history_annotation, literal_offset


class HistorySharingPlan:
    """History-specific policy; ownership and escape checks stay in the planner."""

    def __init__(self, emitter):
        self.variables = {
            variable.decl_node_id: variable
            for variable in emitter._collection_history_variables()
            if variable.kind == "array" and variable.is_var and not variable.closed_by
        }
        self.members = {}
        self.calls = {}
        self.history_members = {}

    def candidate(self, declaration):
        return id(declaration) in self.variables

    def use(self, node, parent, parents, mutable_targets):
        """Admit detached history reads and covered replacement targets.

        A changing history receiver rejoins the ordinary call-site proof:
        its dynamic zero offset can reach the live vector. Iteration and
        every unclassified use retain the old freezing path.
        """
        if (isinstance(parent, Assignment) and parent.target is node
                and parent.op == ":="):
            return True, node, parent
        if isinstance(parent, Subscript) and parent.object is node:
            annotation = history_annotation(parent)
            if annotation is not None:
                consumer = parents.get(id(parent))
                if isinstance(consumer, MemberAccess):
                    consumer = parents.get(id(consumer))
                if id(parent) in mutable_targets or id(consumer) in mutable_targets:
                    return False, node, parent
                # The literal-zero shortcut can pass the live vector to a
                # user callable; a dynamic/nonzero value is detached instead.
                if annotation["use"] == "copy" and literal_offset(parent) == 0:
                    return False, node, parent
                if annotation["use"] in {"read", "na", "copy", "reference"}:
                    return True, node, parent
                if annotation["use"] == "change":
                    return False, parent, parents.get(id(parent))
        return False, node, parent

    def admit(self, emitter, candidates, admitted, calls, bindings):
        self.members = {
            emitter._safe_name(name): self.variables[id(declaration)]
            for name, declaration in candidates.items() if name in admitted
        }
        self.calls = {key: emitter._safe_name(name) for key, name in calls.items()
                      if name in admitted}
        self.history_members = {
            member: emitter._collection_history_member(variable)
            for member, variable in self.members.items()
        }
        used = set(bindings) | emitter._all_member_names | set(emitter._enum_defs)
        used.update(getattr(emitter, "_runtime_var_init_flag_used_names", ()))
        used.update(instance["name"] for instance in emitter._fresh_instances)
        used.update(emitter._func_safe_name(fi.name) for fi in emitter.ctx.func_infos)
        used.update(emitter._session_history_member_names.values())
        used.update(emitter._collection_history_member(variable)
                    for variable in emitter._collection_history_variables())
        for attribute in ("_array_journal_cpp_name", "_array_journal_generation_name"):
            name = getattr(emitter, attribute, None)
            if name is not None:
                used.add(name)
        self.history_type = emitter._allocate_generated_cpp_name("_PFCachedCollectionHistory", used)
        self.clock_type = emitter._allocate_generated_cpp_name("_PFHistoryVersionClock", used)
        self.clock_member = emitter._allocate_generated_cpp_name("_pf_history_versions", used)
        emitter._all_member_names.add(self.clock_member)

    def writer(self, member):
        history = self.history_members[member]
        return f"this->{history}.writer(this->{self.clock_member})"


HISTORY_VERSION_CLOCK_CPP = r"""
class @CLOCK@ {
    uint64_t next_ = 1;
    uint64_t run_ = 1;
public:
    @CLOCK@() = default;
    @CLOCK@(const @CLOCK@&) = delete;
    @CLOCK@& operator=(const @CLOCK@&) = delete;
    uint64_t allocate() noexcept {
        // Zero disables reuse permanently after exhaustion, without wrapping.
        if (next_ == std::numeric_limits<uint64_t>::max()) return 0;
        return ++next_;
    }
    uint64_t run() const noexcept { return run_; }
    bool reusable() const noexcept { return next_ != std::numeric_limits<uint64_t>::max(); }
    void reset_for_run() noexcept { run_ = allocate(); }
};
"""


CACHED_COLLECTION_HISTORY_CPP = r"""
template <typename T>
class @HISTORY@ {
    Series<std::shared_ptr<const T>> slots_;
    bool open_ = false;
    uint64_t content_ = 0;
    std::shared_ptr<const T> cached_;
    const void* cached_owner_ = nullptr;
    uint64_t cached_run_ = 0;
    uint64_t cached_content_ = 0;
public:
    explicit @HISTORY@(int max_len = 500) : slots_(max_len) {}
    void open(const T& value, bool new_slot, const @CLOCK@& versions) {
        close(value, versions);
        if (new_slot) slots_.push(nullptr);
        else slots_.update(nullptr);
        open_ = true;
    }
    void close(const T& value, const @CLOCK@& versions) {
        if (!open_) return;
        if (!versions.reusable() || content_ == 0 || versions.run() == 0 || cached_owner_ != &value
                || cached_run_ != versions.run() || cached_content_ != content_) {
            auto frozen = _PFCollectionTraits<T>::freeze(value);
            cached_ = std::move(frozen);
            cached_owner_ = &value;
            cached_run_ = versions.run();
            cached_content_ = content_;
        }
        slots_.update(cached_);
        open_ = false;
    }
    bool is_na(int offset) const { return offset <= 0 || !slots_[offset]; }
    bool is_na(int offset, const T& current) const {
        return offset == 0 ? _PFCollectionTraits<T>::is_na(current) : is_na(offset);
    }
    const T& at(int offset) const {
        std::shared_ptr<const T> slot = offset > 0 ? slots_[offset] : nullptr;
        if (!slot) _PFCollectionTraits<T>::na_stop();
        return *slot;
    }
    const T& at(int offset, const T& current) const {
        return offset == 0 ? current : at(offset);
    }
    T value(int offset) const {
        std::shared_ptr<const T> slot = offset > 0 ? slots_[offset] : nullptr;
        return slot ? _PFCollectionTraits<T>::copy(*slot) : T{};
    }
    T value(int offset, const T& current) const {
        return offset == 0 ? current : value(offset);
    }
    auto reference(int offset) const {
        return _PFCollectionTraits<T>::reference(offset > 0 ? slots_[offset] : nullptr);
    }
    auto reference(int offset, const T& current) const {
        return offset == 0 ? _PFCollectionTraits<T>::reference(
            _PFCollectionTraits<T>::freeze(current)) : reference(offset);
    }
    T& changed(int offset, T& current) const {
        if (offset == 0) return current;
        (void)at(offset);
        _pf_collection_history_changed<T>();
    }
    uint64_t content_token() const noexcept { return content_; }

    // The same checked mutation sites serve journaling and version tracking.
    // This adapter never copies a preimage and runs without a checkpoint too.
    class Writer {
        @HISTORY@& history_;
        @CLOCK@& versions_;
        void dirty() noexcept { history_.content_ = versions_.allocate(); }
    public:
        Writer(@HISTORY@& history, @CLOCK@& versions)
            : history_(history), versions_(versions) {}
        void save_set(T&, size_t) noexcept { dirty(); }
        void save_remove(T&, size_t) noexcept { dirty(); }
        template <typename V>
        void push(T& array, V&& value) {
            dirty();
            array.push_back(std::forward<V>(value));
        }
        template <typename I, typename V>
        typename T::iterator insert(T& array, I position, V&& value) {
            dirty();
            return array.insert(position, std::forward<V>(value));
        }
        template <typename V>
        void replace(T& array, V&& value) {
            dirty();
            array = std::forward<V>(value);
        }
        void clear(T& array) {
            dirty();
            array.clear();
        }
        template <typename I, typename V>
        void fill(T&, I first, I last, const V& value) {
            dirty();
            std::fill(first, last, value);
        }
        template <typename I, typename... C>
        void sort(T&, I first, I last, C... compare) {
            dirty();
            std::sort(first, last, compare...);
        }
        template <typename I>
        void reverse(T&, I first, I last) {
            dirty();
            std::reverse(first, last);
        }
        template <typename I, typename R>
        typename T::iterator concat(T& array, I position, R first, R last) {
            dirty();
            return array.insert(position, first, last);
        }
    };
    Writer writer(@CLOCK@& versions) { return Writer(*this, versions); }
};
"""
