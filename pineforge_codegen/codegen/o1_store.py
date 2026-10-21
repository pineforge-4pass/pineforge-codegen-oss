"""Route compiler-owned, bar-count-sized script state into the engine's store.

The engine's ``pineforge/detail/o1_store.hpp`` (CR-150 A5) keeps a fixed
resident working set over a spilling backing and addresses every object by a
stable id. Its contract is byte-addressed, so the codegen needs a typed view
over it. This module emits that view for the one category whose access the
codegen owns completely and whose payload is trivially copyable: the
precalculated TA cache. Every other audited category (arrays, matrices, maps,
strings, UDT records, collection history, snapshots, drawing arenas) still
reaches a reference-bearing or wide STL/engine API and keeps its resident
lowering until the typed engine adapters land.

The binding is feature-detected. An engine without the store header, or a
translation unit built with ``PF_O1_STORE_DISABLED``, gets the identical
resident fallback: a ``std::vector`` with the same operations. That fallback
is also the reference lowering the store path is checked against, so the
emitter cannot change observable strategy behaviour silently.

Capacity: nothing here refuses. ``resize`` grows the logical object and spills
through the backing; ``_PF_O1_RESIDENT_PAGES * _PF_O1_PAGE_BYTES`` is the only
resident ceiling, fixed at open as the contract requires.
"""

from __future__ import annotations

# Every generated identifier below is reserved against authored names
# (``helpers.CPP_EMITTER_NAMES``).
STORE_GUARD_CPP = r"""\
// >>> pf-o1-store
// A5: script state that the compiler cannot bound lives in the engine's O(1)
// store (fixed resident working set over a spilling backing) instead of a
// growing heap container. An engine without the store, or a TU built with
// -DPF_O1_STORE_DISABLED, keeps the identical resident lowering below.
#if !defined(PF_O1_STORE_DISABLED) && defined(__has_include)
#  if __has_include(<pineforge/detail/o1_store.hpp>)
#    include <pineforge/detail/o1_store.hpp>
#    define PF_O1_STORE_AVAILABLE 1
#  endif
#endif
#ifndef PF_O1_STORE_AVAILABLE
#  define PF_O1_STORE_AVAILABLE 0
#endif
"""

STORE_CORE_CPP = r"""\
#include <stdexcept>

#if PF_O1_STORE_AVAILABLE
// Fixed at open, per the store contract: the resident working set is a
// constant number of pages. Growth spills to the backing and never refuses.
constexpr std::uint32_t _PF_O1_PAGE_BYTES = 4096;
constexpr std::uint32_t _PF_O1_RESIDENT_PAGES = 64;
#define _PF_O1_CURRENT_STORAGE "external"

class _PFO1StoreOwner {
    std::unique_ptr<pineforge::detail::o1::Backing> _pf_backing_;
    std::unique_ptr<pineforge::detail::o1::Store> _pf_store_;
public:
    _PFO1StoreOwner() = default;
    _PFO1StoreOwner(const _PFO1StoreOwner&) = delete;
    _PFO1StoreOwner& operator=(const _PFO1StoreOwner&) = delete;

    pineforge::detail::o1::Store* store() {
        if (!_pf_store_) {
            _pf_backing_ = pineforge::detail::o1::make_memfd_backing();
            pineforge::detail::o1::Shape _pf_shape;
            _pf_shape.page_bytes = _PF_O1_PAGE_BYTES;
            _pf_shape.resident_pages = _PF_O1_RESIDENT_PAGES;
            _pf_store_ = pineforge::detail::o1::Store::open(_pf_shape, *_pf_backing_);
            if (!_pf_store_)
                throw std::runtime_error("script-state store failed to open");
        }
        return _pf_store_.get();
    }
    bool opened() const { return static_cast<bool>(_pf_store_); }

    // Same fields the engine reports, in a fixed order: live, resident,
    // spilled pages, backing bytes. Zero before the store is opened.
    void stats(std::uint64_t out[4]) const {
        if (!_pf_store_) {
            out[0] = out[1] = out[2] = out[3] = 0;
            return;
        }
        const auto _pf_s = _pf_store_->stats();
        out[0] = _pf_s.bytes_live;
        out[1] = _pf_s.bytes_resident;
        out[2] = _pf_s.pages_spilled;
        out[3] = _pf_s.bytes_backing;
    }
};

// A typed view of one store object: `n` logical elements of a trivially
// copyable `T`. Indexing copies the element in or out, which is exact; there
// is no reference to hand out across a page boundary.
template <typename T>
class _PFO1Buffer {
    static_assert(std::is_trivially_copyable<T>::value,
                  "script-state store buffers hold trivially copyable values");
    _PFO1StoreOwner* _pf_owner_ = nullptr;
    pineforge::detail::o1::Id _pf_id_ = pineforge::detail::o1::kNullId;
    std::size_t _pf_size_ = 0;

    pineforge::detail::o1::Store* _pf_store_() const {
        return const_cast<_PFO1Buffer*>(this)->_pf_owner_->store();
    }
public:
    _PFO1Buffer() = default;
    explicit _PFO1Buffer(_PFO1StoreOwner* _pf_owner) : _pf_owner_(_pf_owner) {}

    std::size_t size() const { return _pf_size_; }

    void resize(std::size_t _pf_n) {
        _pf_size_ = _pf_n;
        if (_pf_n == 0) return;
        auto* _pf_s = _pf_store_();
        const std::uint64_t _pf_bytes =
            static_cast<std::uint64_t>(_pf_n) * sizeof(T);
        if (_pf_id_ == pineforge::detail::o1::kNullId) {
            _pf_id_ = _pf_s->create(_pf_bytes, alignof(T));
            if (_pf_id_ == pineforge::detail::o1::kNullId)
                throw std::runtime_error("script-state store create failed");
        } else if (!_pf_s->resize(_pf_id_, _pf_bytes)) {
            throw std::runtime_error("script-state store resize failed");
        }
    }

    T load(std::size_t _pf_index) const {
        T _pf_value{};
        const std::size_t _pf_moved = _pf_store_()->read(
            _pf_id_, static_cast<std::uint64_t>(_pf_index) * sizeof(T),
            &_pf_value, sizeof(T));
        if (_pf_moved != sizeof(T))
            throw std::runtime_error(
                "script-state store read crossed the logical end");
        return _pf_value;
    }

    void assign(std::size_t _pf_index, T _pf_value) {
        const std::size_t _pf_moved = _pf_store_()->write(
            _pf_id_, static_cast<std::uint64_t>(_pf_index) * sizeof(T),
            &_pf_value, sizeof(T));
        if (_pf_moved != sizeof(T))
            throw std::runtime_error(
                "script-state store write crossed the logical end");
    }

    class reference {
        _PFO1Buffer* _pf_buffer_;
        std::size_t _pf_index_;
    public:
        reference(_PFO1Buffer* _pf_buffer, std::size_t _pf_index)
            : _pf_buffer_(_pf_buffer), _pf_index_(_pf_index) {}
        operator T() const { return _pf_buffer_->load(_pf_index_); }
        reference& operator=(T _pf_value) {
            _pf_buffer_->assign(_pf_index_, _pf_value);
            return *this;
        }
    };

    reference operator[](std::size_t _pf_index) {
        return reference(this, _pf_index);
    }
    T operator[](std::size_t _pf_index) const { return load(_pf_index); }
};
#else
#define _PF_O1_CURRENT_STORAGE "resident"

class _PFO1StoreOwner {
public:
    void stats(std::uint64_t out[4]) const {
        out[0] = out[1] = out[2] = out[3] = 0;
    }
    bool opened() const { return false; }
};

// The resident lowering every earlier build emitted, behind the same
// interface: identical values and identical strategy results by construction.
template <typename T>
class _PFO1Buffer {
    std::vector<T> _pf_values_;
public:
    _PFO1Buffer() = default;
    explicit _PFO1Buffer(const _PFO1StoreOwner*) {}
    std::size_t size() const { return _pf_values_.size(); }
    void resize(std::size_t _pf_n) { _pf_values_.resize(_pf_n); }
    // ``decltype(auto)`` keeps ``std::vector<bool>``'s proxy element, which a
    // ``bool&`` return could not bind.
    decltype(auto) operator[](std::size_t _pf_index) {
        return _pf_values_[_pf_index];
    }
    decltype(auto) operator[](std::size_t _pf_index) const {
        return _pf_values_[_pf_index];
    }
};
#endif
// <<< pf-o1-store
"""


def precalc_members(emitter) -> list[str]:
    """Members of the precalculated TA caches this script emits."""
    dead = getattr(emitter, "_dead_ta_indices", set())
    return [
        site.member_name
        for index, site in enumerate(emitter.ctx.ta_call_sites)
        if index not in dead and emitter._ta_site_uses_precalc(site)
    ]


def has_precalc_storage(emitter) -> bool:
    """Whether the script has a compiler-owned, bar-count-sized cache."""
    return bool(precalc_members(emitter))


def emit_o1_store_helper(emitter, lines: list[str]) -> None:
    """Emit the guard and the typed store view before the strategy class.

    Only a script with a precalculated TA cache needs it; every other TU keeps
    its emitted bytes unchanged.
    """
    if not has_precalc_storage(emitter):
        return
    lines.append(STORE_GUARD_CPP.strip("\n"))
    lines.append(STORE_CORE_CPP.strip("\n"))
    lines.append("")


def emit_o1_store_stats(emitter, lines: list[str]) -> None:
    """The read-only stats export, after the complete strategy class.

    ``strategy_script_state_store_stats_v1(handle, out[4])`` returns 0 when
    the store was never opened (a script with no precalculated cache, or a TU
    without the store header) and 1 with the engine's four counters in
    otherwise. It allocates nothing and changes no state.
    """
    if not has_precalc_storage(emitter):
        return
    lines.extend([
        'extern "C" int strategy_script_state_store_stats_v1('
        'void* s, std::uint64_t out[4]) {',
        "    if (!s || !out) return 0;",
        "    static_cast<GeneratedStrategy*>(s)->_pf_o1_store_.stats(out);",
        "    return static_cast<GeneratedStrategy*>(s)->_pf_o1_store_.opened()"
        " ? 1 : 0;",
        "}",
        "",
    ])
