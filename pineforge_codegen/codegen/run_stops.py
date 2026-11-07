"""Optional coded run stops, with the original throws on older engines."""

from .helpers import NamingHelper


RUN_STOP_SHIMS_CPP = r"""
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
#define _PF_NO_DATA_STOP(function, call, line, english) ::pineforge::pine_no_data_stop(function, call, line, english)
#define _PF_OTHER_SYMBOL_STOP(function, symbol, call, line, english) ::pineforge::pine_other_symbol_stop(function, symbol, call, line, english)
#define _PF_ARRAY_STOP(reason, method, english) ::pineforge::pine_array_stop(reason, method, std::string(english).c_str())
#define _PF_COLLECTION_STOP(reason, object, english) ::pineforge::pine_collection_stop(object, reason, english)
#define _PF_NA_STOP(object, english) ::pineforge::pine_na_stop(object, english)
#define _PF_LIMIT_STOP(limit, max, english) ::pineforge::pine_limit_stop(limit, max, english)
#define _PF_UNSUPPORTED_STOP(reason, line, english) ::pineforge::pine_unsupported_stop(reason, line, english)
#define _PF_STRING_STOP(reason, english) ::pineforge::pine_string_stop(reason, english)
#define _PF_ENGINE_INVARIANT(english, legacy_type) ::pineforge::pine_engine_invariant(english)
#define _PF_INVARIANT_AT(container, index) _pf_invariant_at(container, index)
#define _PF_SETTING_FAILURE(strategy, entrypoint, message, reason) (strategy)->_pf_record_setting_failure(entrypoint, message, [] { return reason ? ::pineforge::RunFailureInfo(::pineforge::RunFailureCode::setting_rejected, {{"entrypoint", entrypoint}, {"reason", reason}}) : ::pineforge::RunFailureInfo(::pineforge::RunFailureCode::setting_rejected, {{"entrypoint", entrypoint}}); })
template <typename Container, typename Index>
decltype(auto) _pf_invariant_at(Container& container, Index index) {
    try { return container.at(index); }
    catch (const std::out_of_range& error) { ::pineforge::pine_engine_invariant(error.what()); }
}
#else
#define _PF_NO_DATA_STOP(function, call, line, english) pine_runtime_error(std::string(english))
#define _PF_OTHER_SYMBOL_STOP(function, symbol, call, line, english) pine_runtime_error(std::string(english))
#define _PF_ARRAY_STOP(reason, method, english) pine_runtime_error(english)
#define _PF_COLLECTION_STOP(reason, object, english) pine_runtime_error(english)
#define _PF_NA_STOP(object, english) throw std::runtime_error(english)
#define _PF_LIMIT_STOP(limit, max, english) throw std::length_error(english)
#define _PF_UNSUPPORTED_STOP(reason, line, english) pine_runtime_error(std::string(english))
#define _PF_STRING_STOP(reason, english) pine_runtime_error(std::string(english))
#define _PF_ENGINE_INVARIANT(english, legacy_type) throw legacy_type(english)
#define _PF_INVARIANT_AT(container, index) (container).at(index)
#define _PF_SETTING_FAILURE(strategy, entrypoint, message, reason) (strategy)->_pf_record_setting_failure(entrypoint, message)
#endif
"""


def history_offset_guard(offset: str, line: int) -> str:
    """Check an already evaluated numeric offset without narrowing it."""
    return (
        f"if (!is_na({offset}) && ({offset} < 0 || {offset} > 10000)) "
        f'pine_runtime_error(std::string("History reference at line {line}: offset ") '
        f'+ std::to_string({offset}) '
        '+ ". The history offset must be a value between 0 and 10000."); '
    )


def history_capacity_guard(offset: str, capacity: int) -> str:
    """Check one captured offset against an explicitly selected retention."""
    return (
        f"if (!is_na({offset}) && {offset} >= {capacity}LL) "
        f'_PF_LIMIT_STOP("max_bars_back", {capacity}LL, '
        '(std::string("The script attempts to reference historical data that is too far '
        'from the current bar (") '
        f'+ std::to_string({offset}) '
        f'+ " bars back). The historical buffer\'s limit is {capacity} bars.").c_str()); '
    )


def checked_history_offset(offset: str, line: int, capacity: int | None = None,
                           *, check_range: bool = True) -> str:
    """Check one evaluated offset before its original type is narrowed."""
    range_check = (
        "if constexpr (!std::is_same_v<std::decay_t<decltype(_pf_idx_v)>, bool>) { "
        f"{history_offset_guard('_pf_idx_v', line)}}} "
    ) if check_range else ""
    capacity_check = (
        history_capacity_guard("_pf_idx_v", capacity)
        if capacity is not None else ""
    )
    return (
        f"([&]() {{ auto _pf_idx_v = ({offset}); "
        f"{range_check}{capacity_check}"
        "return _pf_idx_v; }())"
    )


def request_stop(marker: dict) -> str:
    """Metadata comes only from the original request's compile-time spelling."""
    escape = NamingHelper._cpp_string_escape
    function = f'"{escape(marker["function"])}"'
    call = f'"{escape(marker["call"])}"'
    english = f'"{escape(marker["message"])}"'
    arguments = f'{function}, {call}, {marker["line"]}, {english}'
    if marker["kind"] == "other_symbol":
        literal = marker["symbol_literal"]
        symbol = "nullptr" if literal is None else f'"{escape(literal)}"'
        arguments = f'{function}, {symbol}, {call}, {marker["line"]}, {english}'
        return f"_PF_OTHER_SYMBOL_STOP({arguments})"
    return f"_PF_NO_DATA_STOP({arguments})"
