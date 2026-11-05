"""Frozen sources for counter evidence and unchanged shared type inference."""

from pathlib import Path


MAIN_COMMIT = "4fd63ec8b94021a15945294c3648dc7b7f5480bb"
FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers" / "type_sources"
BASELINE = FIXTURES.parent.parent / "for_counter_type_source_baseline.json"

# Capture every source's main C++ before comparing unchanged output. Float
# counter rows deliberately change their loop storage and are checked below
# by family and by native execution, rather than by C++ equality.
SOURCE_NAMES = (
    "array_float_avg",
    "array_float_extrema",
    "array_method_float_extrema",
    "bool_comparison",
    "float_chain",
    "float_tuple_call",
    "float_tuple_literal",
    "for_local_float",
    "if_local",
    "int_chain",
    "int_tuple_call",
    "int_tuple_literal",
    "string_global",
    "string_input",
    "string_mixed",
    "string_symbol",
    "string_ticker",
    "string_var",
    "ta_int_max",
    "ta_int_median",
    "ta_int_min",
    "ta_int_mode",
    "unknown_parameter",
)
BASELINE_SOURCES = {
    name: (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    for name in SOURCE_NAMES
}

ARRAY_TYPES = {
    "bool_comparison": "bool",
    "string_global": "std::string",
    "string_input": "std::string",
    "string_mixed": "std::string",
    "string_symbol": "std::string",
    "string_ticker": "std::string",
    "string_var": "std::string",
}
FLOAT_COUNTER_SOURCES = {
    "array_float_avg",
    "array_float_extrema",
    "array_method_float_extrema",
    "float_chain",
    "float_tuple_call",
    "float_tuple_literal",
    "for_local_float",
    # TradingView types ta.max and ta.min as float even for an int source
    # (it accepts them as the start of a float-step loop: compile verdicts
    # for ta.max(bar_index) and ta.min(bar_index)), so the counter is a double.
    "ta_int_max",
    "ta_int_min",
    "unknown_parameter",
}
INT_COUNTER_SOURCES = {
    "int_chain",
    "int_tuple_call",
    "int_tuple_literal",
    "ta_int_median",
    "ta_int_mode",
}
