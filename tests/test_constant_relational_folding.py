"""Special numeric operands in folded Pine comparisons."""

from pineforge_codegen.ast_nodes import BinOp, FuncCall, Identifier, NumberLiteral
from pineforge_codegen.codegen.constant_fold import fold_constant_expression


def test_folded_relations_on_missing_and_infinite_operands():
    ops = ("==", "!=", "<", "<=", ">", ">=")
    equal = (True, False, False, True, False, True)
    less = (False, True, True, True, False, False)
    greater = (False, True, False, False, True, True)
    unordered = (False,) * len(ops)
    positive_inf = float("inf")
    negative_inf = -positive_inf
    # Missing is unordered even for !=. Infinities compare by value, so
    # equal infinities are equal and opposite infinities retain their order.
    for a, b, expected in (
        (float("nan"), 1.0, unordered),
        (1, float("nan"), unordered),
        (None, 1, unordered),
        (1.0, None, unordered),
        (positive_inf, positive_inf, equal),
        (negative_inf, negative_inf, equal),
        (negative_inf, positive_inf, less),
        (positive_inf, 1, greater),
        (0.0, negative_inf, greater),
    ):
        assert [fold_constant_expression(
            BinOp(op=op, left=Identifier(name="a"), right=Identifier(name="b")),
            {"a": a, "b": b}, {},
        ) for op in ops] == [(value, True) for value in expected], (a, b)


def test_nonfinite_arithmetic_and_missing_casts_at_a_comparison():
    def number(value):
        return NumberLiteral(value=value)

    infinity = BinOp(op="/", left=number(1.0), right=number(0.0))
    nan = BinOp(op="/", left=number(0.0), right=number(0.0))
    overflow = BinOp(op="*", left=number(-1e308), right=number(1e308))
    missing_int = FuncCall(callee=Identifier(name="int"), args=[infinity])
    minimum = number(float(-2**63))
    sentinel = FuncCall(callee=Identifier(name="int"), args=[minimum])
    # Division by zero or overflow yields IEEE values; int(infinity) yields
    # the engine's missing integer, not a finite integer or a true != result.
    for op, left, right, expected in (
        ("==", infinity, infinity, True),
        ("!=", nan, number(1), False),
        ("<", overflow, number(-1), True),
        ("!=", missing_int, number(1.0), False),
        ("==", sentinel, minimum, False),
    ):
        assert fold_constant_expression(
            BinOp(op=op, left=left, right=right), {}, {},
        ) == (expected, True)
