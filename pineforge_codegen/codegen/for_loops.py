"""The absolute continuation allowance for floating counted loops."""

# Absolute allowance in the counter's units, chosen inside the measured
# bracket about [5.6e-17, 9.98e-11]; it is not a TradingView constant.
FLOAT_STEP_TOLERANCE_CPP = "static constexpr double kFloatStepTolerance = 1e-12;\n"


def for_continuation(counter_cpp_type, var, end, step, down) -> str:
    if counter_cpp_type == "double":
        return (
            f"({down} ? ({var} >= {end} - ::kFloatStepTolerance)"
            f" : ({var} <= {end} + ::kFloatStepTolerance))"
        )
    return f"({down} ? ({var} >= {end}) : ({var} <= {end}))"
