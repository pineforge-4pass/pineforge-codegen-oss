"""Array storage for translation units which use builtin slice windows.

The existing emitter uses vector spellings as its logical collection type in
many independent paths. Lower that spelling once, after emission, rather than
changing type inference or leaving a vector/handle seam in helper signatures.
Literals and comments are inert. Slice-free translation units bypass this pass.
The storage implementation itself keeps real STL vectors.
"""

from __future__ import annotations

import re

from ..ast_nodes import FuncCall, FuncDef, Identifier, MemberAccess, MethodDef
from ..limits import iter_ast_nodes
from ..symbols import method_receiver_type_name
from .helpers import _CPP_COMMENT_OR_LITERAL
from .tables import DRAWING_TYPE_TO_CPP


SUPPORT_MARKER = "// @pf-array-slice-storage@"
_VECTOR = re.compile(r"(?<![\w:])std::vector\b(?=\s*<)")
_ELEMENT = re.compile(r"\s*<\s*([^<>,]+)(?=[,>])")


def uses_slice(gen) -> bool:
    """Find builtin slices before ownership/checkpoint planning.

    Only call nodes count, never source text. Typed authored methods take
    precedence, just as they do in visit_call. Identifier receivers need exact
    array metadata: _infer_type's default double is not receiver evidence.
    """
    roots = [gen.ctx.ast, *(p.expr_node for p in gen.ctx.pf_trace_pragmas or [])]
    functions = {id(info.node): info for info in gen.ctx.func_infos if info.node is not None}
    for root in roots:
        callable_depth = None
        parameters = {}
        for node, depth in iter_ast_nodes(root):
            if callable_depth is not None and depth <= callable_depth:
                callable_depth = None
                parameters = {}
            if isinstance(node, (FuncDef, MethodDef)):
                callable_depth = depth
                info = (gen._func_info_map.get(f"{node.type_name}.{node.name}")
                        if isinstance(node, MethodDef) else functions.get(id(node)))
                parameters = dict(zip(node.params, getattr(info, "param_type_specs", ()) or ()))
            if not isinstance(node, FuncCall):
                continue
            callee = node.callee
            if not isinstance(callee, MemberAccess) or callee.member != "slice":
                continue
            if isinstance(callee.object, Identifier):
                binding = gen.ctx.identifier_decl_bindings.get(id(callee.object))
                if binding and binding[0] in gen._callable_collection_bindings:
                    # The analyzer retained this local's type before its scope
                    # was popped. A scalar tombstone must not borrow a global.
                    spec = gen._callable_collection_bindings[binding[0]]
                    if spec is None:
                        continue
                elif callee.object.name in parameters:
                    spec = parameters[callee.object.name]
                else:
                    spec = gen._type_spec_from_expr(callee.object)
                receiver = method_receiver_type_name(spec)
                authored = gen._func_info_map.get(f"{receiver}.slice") if receiver else None
            else:
                spec, authored = gen._typed_user_method_info(callee.object, "slice")
            if (authored is None and isinstance(callee.object, Identifier)
                    and callee.object.name == "array"):
                return True
            if spec is None and isinstance(callee.object, Identifier):
                # Unresolved identifiers (including retained read-only aliases)
                # keep the copy path and its warning, never a scalar guess.
                continue
            if authored is None and (spec is None or spec.kind == "array"):
                return True
    return False


def prepare_names(gen) -> None:
    used = set(gen._safe_name_occupied) | set(gen._all_member_names)
    used.update(gen._safe_name(name) for name in gen._all_bound_names)
    used.update(gen._safe_name(node.name)
                for node, _ in iter_ast_nodes(gen.ctx.ast)
                if isinstance(getattr(node, "name", None), str))
    gen._slice_array_cpp = gen._allocate_generated_cpp_name("_PFSliceArray", used)
    gen._slice_view_cpp = gen._allocate_generated_cpp_name("_pf_slice_view", used)
    gen._slice_copy_cpp = gen._allocate_generated_cpp_name("_pf_slice_copy", used)


def lower_storage(cpp: str, gen) -> str:
    """Lower collection type tokens, preserving all authored literal bytes."""
    # Leave backend-only vectors (settings, callbacks, formatting arguments,
    # checkpoint payloads) as STL containers. These are not Pine array IDs.
    elements = {
        "double", "int", "int64_t", "bool", "std::string",
        "T", "E", "_PFElement",
        *(gen._safe_name(name) for name in gen._udt_defs),
        *DRAWING_TYPE_TO_CPP.values(),
    }

    def storage_tokens(code: str) -> str:
        def replace(match):
            # All admitted element spellings are scalar names; a nested
            # backend container is not an authored array element.
            element = _ELEMENT.match(code, match.end())
            if element is not None and element.group(1).strip() in elements:
                return gen._slice_array_cpp
            return match.group()
        return _VECTOR.sub(replace, code)

    pieces = []
    previous = 0
    for literal in _CPP_COMMENT_OR_LITERAL.finditer(cpp):
        pieces.append(storage_tokens(cpp[previous:literal.start()]))
        pieces.append(literal.group())
        previous = literal.end()
    pieces.append(storage_tokens(cpp[previous:]))
    names = {
        "_PFSliceArray": gen._slice_array_cpp,
        "_pf_slice_view": gen._slice_view_cpp,
        "_pf_slice_copy": gen._slice_copy_cpp,
    }
    support = re.sub(r"\b(?:_PFSliceArray|_pf_slice_view|_pf_slice_copy)\b",
                     lambda match: names[match.group()], ARRAY_SLICE_CPP)
    return "".join(pieces).replace(SUPPORT_MARKER, support, 1)


ARRAY_SLICE_CPP = r"""
// Owned arrays retain value-copy behavior. A window carries its backing owner
// and offsets, never element pointers or iterators. Structural mutation
// materializes the affected array (PineForge compatibility; no TV resize tape).
template <typename T, typename Allocator = std::allocator<T>>
class _PFSliceArray {
public:
    using storage_type = std::vector<T, Allocator>;
    using value_type = T;
    using allocator_type = Allocator;
    using size_type = typename storage_type::size_type;
    using difference_type = typename storage_type::difference_type;
    using reference = typename storage_type::reference;
    using const_reference = typename storage_type::const_reference;
    using iterator = typename storage_type::iterator;
    using const_iterator = typename storage_type::const_iterator;
    using reverse_iterator = typename storage_type::reverse_iterator;
    using const_reverse_iterator = typename storage_type::const_reverse_iterator;

    struct Binding {
        std::shared_ptr<storage_type> owner;
        size_type offset;
        size_type length;
        bool window;
    };

private:
    std::shared_ptr<storage_type> owner_ = std::make_shared<storage_type>();
    size_type offset_ = 0;
    size_type length_ = 0;
    bool window_ = false;

    void ensure_owner() {
        if (!owner_) owner_ = std::make_shared<storage_type>();
    }
    void structural_write() {
        ensure_owner();
        if (window_ || !owner_.unique()) {
            auto detached = std::make_shared<storage_type>(cbegin(), cend());
            owner_ = std::move(detached);
        }
        offset_ = 0;
        length_ = 0;
        window_ = false;
    }
    explicit _PFSliceArray(Binding binding)
        : owner_(std::move(binding.owner)), offset_(binding.offset),
          length_(binding.length), window_(binding.window) {}

public:
    _PFSliceArray() = default;
    explicit _PFSliceArray(size_type count)
        : owner_(std::make_shared<storage_type>(count)) {}
    _PFSliceArray(size_type count, const T& value)
        : owner_(std::make_shared<storage_type>(count, value)) {}
    _PFSliceArray(std::initializer_list<T> values)
        : owner_(std::make_shared<storage_type>(values)) {}
    template <typename I, std::enable_if_t<!std::is_integral_v<I>, int> = 0>
    _PFSliceArray(I first, I last)
        : owner_(std::make_shared<storage_type>(first, last)) {}
    _PFSliceArray(const storage_type& value)
        : owner_(std::make_shared<storage_type>(value)) {}
    _PFSliceArray(storage_type&& value)
        : owner_(std::make_shared<storage_type>(std::move(value))) {}
    _PFSliceArray(const _PFSliceArray& value)
        : owner_(value.window_ ? value.owner_
                              : std::make_shared<storage_type>(value.cbegin(), value.cend())),
          offset_(value.window_ ? value.offset_ : 0),
          length_(value.length_), window_(value.window_) {}
    _PFSliceArray(_PFSliceArray&& value) noexcept
        : owner_(std::move(value.owner_)),
          offset_(std::exchange(value.offset_, 0)),
          length_(std::exchange(value.length_, 0)),
          window_(std::exchange(value.window_, false)) {}
    _PFSliceArray& operator=(const _PFSliceArray& value) {
        if (this != &value) {
            _PFSliceArray replacement(value);
            swap(replacement);
        }
        return *this;
    }
    _PFSliceArray& operator=(_PFSliceArray&& value) noexcept {
        if (this != &value) {
            owner_ = std::move(value.owner_);
            offset_ = std::exchange(value.offset_, 0);
            length_ = std::exchange(value.length_, 0);
            window_ = std::exchange(value.window_, false);
        }
        return *this;
    }
    _PFSliceArray& operator=(std::initializer_list<T> values) {
        _PFSliceArray replacement(values);
        swap(replacement);
        return *this;
    }
    // Runtime APIs still expose STL vectors. Materialize at that boundary.
    operator storage_type() const { return storage_type(cbegin(), cend()); }

    size_type size() const {
        return window_ ? length_ : (owner_ ? owner_->size() : 0);
    }
    bool empty() const { return size() == 0; }
    size_type capacity() const {
        return window_ ? length_ : (owner_ ? owner_->capacity() : 0);
    }
    size_type max_size() const { return storage_type{}.max_size(); }
    allocator_type get_allocator() const {
        return owner_ ? owner_->get_allocator() : allocator_type{};
    }
    reference operator[](size_type index) { return (*owner_)[offset_ + index]; }
    const_reference operator[](size_type index) const {
        return static_cast<const storage_type&>(*owner_)[offset_ + index];
    }
    reference at(size_type index) {
        if (index >= size()) throw std::out_of_range("array window index");
        return (*this)[index];
    }
    const_reference at(size_type index) const {
        if (index >= size()) throw std::out_of_range("array window index");
        return (*this)[index];
    }
    reference front() { return (*this)[0]; }
    const_reference front() const { return (*this)[0]; }
    reference back() { return (*this)[size() - 1]; }
    const_reference back() const { return (*this)[size() - 1]; }
    iterator begin() { ensure_owner(); return owner_->begin() + offset_; }
    iterator end() { return begin() + size(); }
    const_iterator begin() const { return cbegin(); }
    const_iterator end() const { return cend(); }
    const_iterator cbegin() const {
        // A moved-from array is an empty, usable value.
        static const storage_type empty;
        return owner_ ? owner_->cbegin() + offset_ : empty.cbegin();
    }
    const_iterator cend() const { return cbegin() + size(); }
    reverse_iterator rbegin() { return reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }
    template <typename U = T, std::enable_if_t<!std::is_same_v<U, bool>, int> = 0>
    U* data() {
        ensure_owner();
        auto pointer = owner_->data();
        return offset_ ? pointer + offset_ : pointer;
    }
    template <typename U = T, std::enable_if_t<!std::is_same_v<U, bool>, int> = 0>
    const U* data() const {
        if (!owner_) return nullptr;
        auto pointer = owner_->data();
        return offset_ ? pointer + offset_ : pointer;
    }

    _PFSliceArray window(size_type first, size_type last) const {
        return _PFSliceArray(Binding{owner_, offset_ + first, last - first, true});
    }
    _PFSliceArray detached_copy() const { return _PFSliceArray(cbegin(), cend()); }
    Binding binding() const { return Binding{owner_, offset_, length_, window_}; }
    void rebind(const Binding& binding) {
        owner_ = binding.owner;
        offset_ = binding.offset;
        length_ = binding.length;
        window_ = binding.window;
    }

    void reserve(size_type count) {
        ensure_owner();
        // Reserve changes no elements or size. Windows never retain iterators.
        if (!window_) owner_->reserve(count);
    }
    void shrink_to_fit() { if (owner_) owner_->shrink_to_fit(); }
    void clear() {
        if (empty()) return;
        structural_write();
        owner_->clear();
    }
    void resize(size_type count) {
        if (count == size()) return;
        structural_write();
        owner_->resize(count);
    }
    void resize(size_type count, const T& value) {
        if (count == size()) return;
        T saved = value;
        structural_write();
        owner_->resize(count, saved);
    }
    void push_back(const T& value) {
        T saved = value;
        structural_write();
        owner_->push_back(std::move(saved));
    }
    void push_back(T&& value) {
        T saved = std::move(value);
        structural_write();
        owner_->push_back(std::move(saved));
    }
    template <typename... Args>
    reference emplace_back(Args&&... args) {
        T saved(std::forward<Args>(args)...);
        structural_write();
        owner_->push_back(std::move(saved));
        return owner_->back();
    }
    void pop_back() {
        structural_write();
        owner_->pop_back();
    }
    iterator insert(const_iterator position, const T& value) {
        auto index = position - cbegin();
        T saved = value;
        structural_write();
        return owner_->insert(owner_->cbegin() + index, std::move(saved));
    }
    iterator insert(const_iterator position, T&& value) {
        auto index = position - cbegin();
        T saved = std::move(value);
        structural_write();
        return owner_->insert(owner_->cbegin() + index, std::move(saved));
    }
    template <typename I>
    iterator insert(const_iterator position, I first, I last) {
        auto index = position - cbegin();
        storage_type saved(first, last);
        if (saved.empty()) return begin() + index;
        structural_write();
        return owner_->insert(owner_->cbegin() + index, saved.begin(), saved.end());
    }
    iterator erase(const_iterator position) {
        auto index = position - cbegin();
        structural_write();
        return owner_->erase(owner_->cbegin() + index);
    }
    iterator erase(const_iterator first, const_iterator last) {
        auto from = first - cbegin();
        auto to = last - cbegin();
        if (from == to) return begin() + from;
        structural_write();
        return owner_->erase(owner_->cbegin() + from, owner_->cbegin() + to);
    }
    template <typename I>
    void assign(I first, I last) {
        _PFSliceArray replacement(first, last);
        swap(replacement);
    }
    void assign(size_type count, const T& value) {
        _PFSliceArray replacement(count, value);
        swap(replacement);
    }
    void swap(_PFSliceArray& other) noexcept {
        owner_.swap(other.owner_);
        std::swap(offset_, other.offset_);
        std::swap(length_, other.length_);
        std::swap(window_, other.window_);
    }
    friend bool operator==(const _PFSliceArray& a, const _PFSliceArray& b) {
        return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin());
    }
    friend bool operator!=(const _PFSliceArray& a, const _PFSliceArray& b) {
        return !(a == b);
    }
};

template <typename Result, typename T, typename A>
Result _pf_slice_view(const _PFSliceArray<T, A>& source, size_t first, size_t last) {
    return source.window(first, last);
}
template <typename Result, typename T, typename A>
Result _pf_slice_view(const std::vector<T, A>& source, size_t first, size_t last) {
    // Runtime-produced arrays (matrix rows, lower-TF results) are values.
    return Result(source).window(first, last);
}
template <typename T, typename A>
_PFSliceArray<T, A> _pf_slice_copy(const _PFSliceArray<T, A>& source) {
    return source.detached_copy();
}
template <typename T, typename A>
_PFSliceArray<T, A> _pf_slice_copy(const std::vector<T, A>& source) {
    return _PFSliceArray<T, A>(source);
}
"""


def checkpoint_traits(traits: str) -> str:
    """Freeze contents independently while retaining the live backing identity."""
    return r"""
template <typename E, typename A>
struct @TRAITS@<std::vector<E, A>> {
    using array_type = std::vector<E, A>;
    using element_traits = @TRAITS@<E>;
    struct snapshot_type {
        typename array_type::Binding binding;
        std::vector<typename element_traits::snapshot_type> contents;
    };
    static snapshot_type take(const array_type& value) {
        snapshot_type snapshot{value.binding(), {}};
        if (snapshot.binding.owner) {
            snapshot.contents.reserve(snapshot.binding.owner->size());
            for (const E element : *snapshot.binding.owner)
                snapshot.contents.push_back(element_traits::take(element));
        }
        return snapshot;
    }
    static void restore(array_type& value, const snapshot_type& snapshot) {
        value.rebind(snapshot.binding);
        if (!snapshot.binding.owner) return;
        auto& storage = *snapshot.binding.owner;
        storage.clear();
        storage.reserve(snapshot.contents.size());
        for (const auto& saved : snapshot.contents) {
            E element{};
            element_traits::restore(element, saved);
            storage.push_back(std::move(element));
        }
    }
};
""".replace("@TRAITS@", traits)
