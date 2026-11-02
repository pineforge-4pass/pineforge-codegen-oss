"""TradingView's measured continuation rule for floating counted loops."""

# TradingView's measured loop-end rule: a counter whose difference to the end is
# below 1e-10 is still within the loop. Measured on TradingView at the 0.3 scale
# (counted up to a 9.999e-11 shortfall, not counted from 1.0e-10) and at counter
# magnitudes of about 1, 1e3, 1e6 and 1e9; no magnitude-independent constant is
# claimed; not a constant TradingView publishes.
TV_FLOAT_STEP_END_CPP = "static constexpr double kTvFloatStepEnd = 1e-10;\n"


def for_continuation(counter_cpp_type, var, end, step, down) -> str:
    if counter_cpp_type == "double":
        return (
            f"({down} ? (({end} - {var}) < ::kTvFloatStepEnd)"
            f" : (({var} - {end}) < ::kTvFloatStepEnd))"
        )
    return f"({down} ? ({var} >= {end}) : ({var} <= {end}))"
