"""Exact numeric emission fragments for preservation assertions."""


def integer_to_double(value: str) -> str:
    return (
        f"[&](){{ auto _pf_w = ({value}); "
        "if constexpr (std::is_floating_point_v<decltype(_pf_w)> "
        "|| std::is_same_v<decltype(_pf_w), bool>) return (double)_pf_w; "
        "else return is_na(_pf_w) ? na<double>() : (double)_pf_w; }()"
    )


def integer_arithmetic(left: str, op: str, right: str, operation: str | None = None) -> str:
    """Pin original operand guards and the family used before the calculation."""
    if operation is None:
        operation = f"(_pna_l {op} _pna_r)"
        if op == "/":
            operation = "((double)(_pna_l) / (double)(_pna_r))"
        elif op == "%":
            operation = "std::fmod((double)(_pna_l), (double)(_pna_r))"
    value_type = f"decltype({operation})"
    if op in ("+", "-", "*"):
        # A finite integer result may equal the narrow input's na encoding.
        # Keep float operations floating, and widen both integer operands
        # before calculating, after checking their original missingness.
        value_type = (
            f"std::conditional_t<std::is_integral_v<{value_type}>, "
            f"int64_t, {value_type}>"
        )
        operation = operation.replace(
            "_pna_l", "static_cast<_pf_val_t>(_pna_l)",
        ).replace("_pna_r", "static_cast<_pf_val_t>(_pna_r)")
    predicate = (
        "[](auto _pf_v) { if constexpr (std::is_integral_v<decltype(_pf_v)> "
        "&& !std::is_same_v<decltype(_pf_v), bool>) return is_na(_pf_v); "
        "else return false; }"
    )
    return (
        f"([&]() {{ auto _pna_l = ({left}); auto _pna_r = ({right}); "
        f"using _pf_val_t = {value_type}; "
        f"return ({predicate}(_pna_l) || {predicate}(_pna_r)) "
        f"? na<_pf_val_t>() : {operation}; }}())"
    )


def integer_store(value: str, cpp_type: str = "int") -> str:
    if cpp_type != "int":
        return (
            f"[&](){{ auto _pf_v = ({value}); return is_na(_pf_v) "
            f"? na<{cpp_type}>() : ({cpp_type})_pf_v; }}()"
        )
    return (
        f"[&](){{ auto _pf_v = ({value}); "
        "if constexpr (std::is_floating_point_v<decltype(_pf_v)>) "
        "return (_pf_v >= -2147483648.0 && _pf_v < 2147483648.0) "
        "? (int)_pf_v : na<int>(); "
        "else return is_na(_pf_v) ? na<int>() : (int)_pf_v; }()"
    )


def require_numeric_hunk(cpp: str, previous: str, expected: str) -> str:
    """Require the exact replacement, then retain an existing whole-file pin."""
    assert previous != expected
    count = cpp.count(expected)
    if count != 1:
        anchor = expected[:40]
        start = cpp.find(anchor)
        actual = cpp[start:start + 600] if start >= 0 else f"anchor absent: {anchor!r}"
        assert count == 1, (
            f"Expected one numeric fragment, found {count}.\n"
            f"EXPECTED: {expected}\nACTUAL: {actual}"
        )
    return cpp.replace(expected, previous, 1)
