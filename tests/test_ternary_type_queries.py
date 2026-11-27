"""Type query traversal tests that need no C++ build."""

from __future__ import annotations

import pytest

from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import BoolLiteral, NumberLiteral, Program, Ternary
from pineforge_codegen.codegen.types import TypeInferer


@pytest.mark.parametrize(
    "helper_class", [Analyzer, TypeInferer], ids=["analyzer", "codegen"],
)
@pytest.mark.parametrize("nested_arm", ["true", "false"])
def test_ternary_type_queries_visit_each_arm_once(
    helper_class,
    nested_arm: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = NumberLiteral(value=65)
    for index in range(64):
        arm = NumberLiteral(value=index + 1)
        value = Ternary(
            condition=BoolLiteral(value=True),
            true_val=value if nested_arm == "true" else arm,
            false_val=arm if nested_arm == "true" else value,
        )

    nullability_checks = 0
    joins = 0
    original_contains_na = helper_class._selection_contains_na
    original_selection_spec = helper_class._selection_type_spec

    def contains_na(cls, node):
        nonlocal nullability_checks
        nullability_checks += 1
        return original_contains_na(node)

    def selection_spec(self, branches, **kwargs):
        nonlocal joins
        joins += 1
        return original_selection_spec(self, branches, **kwargs)

    monkeypatch.setattr(helper_class, "_selection_contains_na", classmethod(contains_na))
    monkeypatch.setattr(helper_class, "_selection_type_spec", selection_spec)
    if helper_class is TypeInferer:
        # Hand-derived: every value arm is an integer, so C++ inference returns int.
        assert TypeInferer()._infer_type(value) == "int"
    else:
        # A primitive selection without na has no spec in script scope.
        analyzer = Analyzer(Program(version=6))
        assert analyzer._type_spec_from_expr(value) is None
    # Hand-derived: 64 ternaries require 64 joins, independent of the nested arm.
    assert joins == 64
    # Hand-derived: 64 ternaries plus 65 terminal arms allow at most 129 structural checks.
    assert nullability_checks <= 129
