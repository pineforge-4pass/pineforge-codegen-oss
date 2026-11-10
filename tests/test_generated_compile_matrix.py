"""Generator properties only; compile acceptance belongs to the existing CI rule.

Small independent inputs check selection and result handling. Source
discrimination also reads the declared SPEC inventory and compares every
eligible one-axis sibling group. No translator, compiler, engine or
TradingView result is manufactured by these tests. Scratch files use pytest's
temporary directory, including the deliberate-crash row.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).absolute().parent.parent


def safe_test_path(raw):
    """Guard the loader and fixture reads before importing the generator."""
    original = Path(raw)
    path = Path(os.path.abspath(original))
    for candidate in (original, path):
        parts = tuple(part.casefold() for part in candidate.parts)
        if any(
            part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
            for part in parts
        ):
            raise ValueError(f"refused path by name: {original}")
        for index in range(len(parts)):
            if parts[index:index + 3] == (".config", "zsh", "keys"):
                raise ValueError(f"refused path by name: {original}")
            if parts[index:index + 2] == (".config", "pineforge"):
                raise ValueError(f"refused path by name: {original}")
    for prefix in (*reversed(path.parents), path):
        if prefix.is_symlink():
            raise ValueError(f"refused symlink path: {prefix}")
    return path


SCRIPT = safe_test_path(ROOT / "scripts/generate_compile_matrix.py")
print(f"READ {SCRIPT}")
MODULE_SPEC = importlib.util.spec_from_file_location("generated_compile_matrix", SCRIPT)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
matrix = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(matrix)


def read_bytes(path):
    path = safe_test_path(path)
    print(f"READ {path}")
    return path.read_bytes()


def read_json(path):
    return json.loads(read_bytes(path))


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def scratch(tmp_path):
    return safe_test_path(tmp_path)


def independent_product(axes):
    names = list(axes)
    return [
        dict(zip(names, values))
        for values in itertools.product(*(axes[name] for name in names))
    ]


def independent_pairs(rows, axis_order=None):
    result = set()
    for row in rows:
        names = list(row) if axis_order is None else list(axis_order)
        for left_index, left in enumerate(names):
            for right in names[left_index + 1:]:
                result.add((left, row[left], right, row[right]))
    return result


def unpack_pairs(records):
    return {
        (item["left"]["axis"], item["left"]["level"],
         item["right"]["axis"], item["right"]["level"])
        for item in records
    }


def language_rule(name, classification, match, **evidence):
    return {
        "id": name, "fact": name, "classification": classification, "matches": match,
        "reason": "Independent finite language control.",
        "languageBasis": "Independent test language evidence, not compiler behavior.",
        **evidence,
    }


def projected(rows, names):
    return {tuple((name, row[name]) for name in names) for row in rows}


def independent_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def independent_id(row):
    return "case-" + digest(independent_json({"renderFamily": "ordinary", "axes": row}))


def independent_groups(axes, rules, core_axes, seed):
    """An exhaustive small-input oracle, separate from completion search."""
    def matched(row, rule):
        return all(row[name] in values for name, values in rule["matches"].items())

    legal = [
        row for row in independent_product(axes)
        if not any(
            rule["classification"] in {"inapplicable", "invalid", "unresolved"} and matched(row, rule)
            for rule in rules
        )
    ]

    def completion(fixed):
        return next(
            (row for row in legal if all(row[name] == value for name, value in fixed.items())),
            None,
        )

    core = []
    for values in itertools.product(*(axes[name] for name in core_axes)):
        row = completion(dict(zip(core_axes, values)))
        if row is not None:
            core.append(row)
    pool = {independent_id(row): row for row in core}
    for left, right in itertools.combinations(axes, 2):
        for a, b in itertools.product(axes[left], axes[right]):
            row = completion({left: a, right: b})
            if row is not None:
                pool[independent_id(row)] = row
    for name, levels in axes.items():
        for value in levels:
            row = completion({name: value})
            if row is not None:
                pool[independent_id(row)] = row
    for rule in rules:
        if rule["classification"] == "valid":
            row = next((row for row in legal if matched(row, rule)), None)
            if row is not None:
                pool[independent_id(row)] = row
    target_pairs = independent_pairs(legal)

    def facts(row):
        return {
            *(f"axis:{name}={value}" for name, value in row.items()),
            *(("form:" + row["statement_kind"],) if "statement_kind" in row else ()),
            *(f"role:{rule['id']}" for rule in rules
              if rule["classification"] == "valid" and matched(row, rule)),
        }

    target_facts = set().union(*(facts(row) for row in legal)) if legal else set()
    target_facts.update("form:" + value for value in axes.get("statement_kind", ()))
    remaining_pairs = set(target_pairs)
    remaining_facts = set(target_facts)
    available = dict(pool)
    pairwise = []
    while remaining_pairs or remaining_facts:
        if not available:
            break
        best = min(available, key=lambda name: (
            -len(independent_pairs([available[name]]) & remaining_pairs),
            -len(facts(available[name]) & remaining_facts),
            digest(seed.encode("utf-8") + b"\n" + independent_json(available[name])),
            tuple(axes[axis].index(available[name][axis]) for axis in axes),
        ))
        row = available[best]
        pairs = independent_pairs([row])
        if not (pairs & remaining_pairs or facts(row) & remaining_facts):
            break
        pairwise.append(best)
        remaining_pairs -= pairs
        remaining_facts -= facts(row)
        del available[best]
    return {
        "legal": legal, "pool": pool, "coreIds": [independent_id(row) for row in core],
        "pairwiseIds": pairwise, "targetPairs": target_pairs, "targetFacts": target_facts,
        "uncoveredFacts": remaining_facts,
    }


def test_both_groups_recompute_against_a_small_exhaustive_oracle():
    axes = {
        "form": ["function", "if"], "layout": ["inline", "block"],
        "type": ["float", "string", "color"], "payload": ["scalar", "array"],
        "position": ["none", "missing"],
    }
    rules = [language_rule(
        "inline_if", "inapplicable", {"form": ["if"], "layout": ["inline"]},
    )]
    legal = [
        row for row in independent_product(axes)
        if not (row["form"] == "if" and row["layout"] == "inline")
    ]
    core_names = ["form", "layout", "type"]
    plan = matrix.coverage_plan(axes, rules, core_axes=core_names, seed="independent-7301")
    by_id = {matrix.case_id(row): row for row in plan["selected"]}
    core = [by_id[name] for name in plan["coreIds"]]
    pairwise = [by_id[name] for name in plan["pairwiseIds"]]
    target_core = projected(legal, core_names)
    assert projected(plan["targetCore"], core_names) == target_core
    assert projected(core, core_names) == target_core
    assert not plan["uncoveredCore"]
    target_pairs = independent_pairs(legal)
    assert plan["targetPairs"] == target_pairs
    assert plan["achievedPairs"] == independent_pairs(pairwise)
    assert plan["uncoveredPairs"] == target_pairs - independent_pairs(pairwise)
    assert not plan["uncoveredPairs"]
    assert set(plan["overlapIds"]) == set(plan["coreIds"]) & set(plan["pairwiseIds"])
    assert len(plan["selected"]) == len(set(plan["coreIds"]) | set(plan["pairwiseIds"]))
    assert all(row in legal for row in plan["selected"])
    assert plan["complete"]
    excluded = projected([row["axes"] for row in plan["excludedCore"]], core_names)
    assert excluded == projected(independent_product(axes), core_names) - target_core
    assert all(row["blockingConstraints"] == ["inline_if"] for row in plan["excludedCore"])


def test_changes_to_actual_levels_and_constraints_change_targets():
    axes = {
        "form": ["f", "g"], "layout": ["line", "block"],
        "type": ["number", "text"], "other": ["a", "b"],
    }
    base = matrix.coverage_plan(axes, [], core_axes=["form", "layout", "type"], seed="independent-181")
    changed = copy.deepcopy(axes)
    changed["other"].append("new")
    expanded = matrix.coverage_plan(changed, [], core_axes=["form", "layout", "type"], seed="independent-181")
    assert base["targetPairs"] < expanded["targetPairs"]
    assert expanded["targetPairs"] == independent_pairs(independent_product(changed))
    rules = [language_rule("one_invalid_tuple", "invalid", {
        "form": ["f"], "layout": ["line"], "type": ["text"],
    })]
    constrained = matrix.coverage_plan(changed, rules, core_axes=["form", "layout", "type"], seed="independent-181")
    legal = [
        row for row in independent_product(changed)
        if (row["form"], row["layout"], row["type"]) != ("f", "line", "text")
    ]
    assert projected(constrained["targetCore"], ["form", "layout", "type"]) == projected(
        legal, ["form", "layout", "type"],
    )
    assert constrained["targetPairs"] == independent_pairs(legal)
    assert constrained["targetCore"] != expanded["targetCore"]


def test_completion_does_not_require_the_full_cartesian_table(monkeypatch):
    axes = {
        "form": ["f", "g", "h", "i"], "layout": ["a", "b", "c", "d"],
        "type": ["number", "text", "color", "flag"],
        **{f"extra_{index}": [str(value) for value in range(4)] for index in range(7)},
    }
    assert math.prod(len(levels) for levels in axes.values()) > 100000
    real_product = itertools.product

    def bounded_product(*iterables, **kwargs):
        assert len(iterables) <= 3, "full Cartesian materialization is not a prerequisite"
        return real_product(*iterables, **kwargs)

    monkeypatch.setattr(matrix.itertools, "product", bounded_product)
    plan = matrix.coverage_plan(axes, [], core_axes=["form", "layout", "type"], seed="independent-55")
    assert plan["complete"]
    assert plan["search"]["partialAssignmentsVisited"] < 100000
    assert len(plan["candidateRows"]) < 100000
    assert len(plan["selected"]) <= 512
    assert projected(plan["targetCore"], ["form", "layout", "type"]) == {
        (("form", a), ("layout", b), ("type", c))
        for a, b, c in real_product(axes["form"], axes["layout"], axes["type"])
    }
    expected = {
        (a, x, b, y)
        for a, b in itertools.combinations(axes, 2)
        for x, y in real_product(axes[a], axes[b])
    }
    assert plan["targetPairs"] == plan["achievedPairs"] == expected


def test_fixed_seed_and_semantic_order_are_reproducible():
    axes = {"c": ["z", "a"], "a": ["2", "1"], "b": ["y", "x"], "d": ["u", "v"]}
    first = matrix.coverage_plan(axes, [], core_axes=["a", "b", "c"], seed="independent-13")
    second = matrix.coverage_plan(copy.deepcopy(axes), [], core_axes=["a", "b", "c"], seed="independent-13")
    assert first == second
    other_seed = matrix.coverage_plan(axes, [], core_axes=["a", "b", "c"], seed="independent-29")
    assert first["targetPairs"] == other_seed["targetPairs"]
    assert first["targetCore"] == other_seed["targetCore"]
    assert [
        matrix.seeded_key(row, "independent-13") for row in first["candidateRows"]
    ] != [
        matrix.seeded_key(row, "independent-29") for row in first["candidateRows"]
    ]
    row = first["selected"][0]
    assert matrix.case_id(row) == matrix.case_id(dict(reversed(list(row.items()))))
    assert matrix.case_id(row) != matrix.case_id(dict(row, a="new"))


def test_union_cap_shortfall_reports_the_actual_uncovered_groups():
    axes = {
        "form": ["f", "g"], "layout": ["line", "block"],
        "type": ["number", "text"], "other": ["a", "b", "c"],
    }
    plan = matrix.coverage_plan(axes, [], core_axes=["form", "layout", "type"], seed="independent-7", ordinary_limit=3)
    assert not plan["complete"]
    assert not plan["capSatisfied"]
    assert len(plan["selected"]) > 3
    selected = {matrix.case_id(row): row for row in plan["selected"]}
    achieved_core = [selected[name] for name in plan["coreIds"]]
    expected_missing = projected(independent_product(axes), ["form", "layout", "type"]) - projected(
        achieved_core, ["form", "layout", "type"],
    )
    assert projected(plan["uncoveredCore"], ["form", "layout", "type"]) == expected_missing
    pairwise = [selected[name] for name in plan["pairwiseIds"]]
    assert plan["uncoveredPairs"] == independent_pairs(independent_product(axes)) - independent_pairs(pairwise)
    assert not expected_missing
    assert projected(achieved_core, ["form", "layout", "type"]) == projected(
        independent_product(axes), ["form", "layout", "type"],
    )
    with pytest.raises(matrix.MatrixInputError, match="ordinary limit"):
        matrix.coverage_plan(axes, [], core_axes=["form", "layout", "type"], seed="independent-7", ordinary_limit=513)


def test_candidate_cap_counts_complete_witnesses_and_is_not_an_inapplicability_verdict():
    axes = {"a": ["0", "1"], "b": ["x", "y"], "c": ["p", "q"], "d": ["u", "v"]}
    search = matrix.CompletionSearch(axes, [], limit=1)
    row, blockers = search.complete({"a": "0"})
    assert row is not None and not blockers
    assert search.stats["partialAssignmentsVisited"] > search.stats["candidateLimit"]
    assert search.stats["candidateCount"] == len(search.witnesses) == 1
    with pytest.raises(matrix.MatrixInputError, match="actual complete candidate pool"):
        search.complete({"a": "1"})
    assert search.stats["candidateLimitExceeded"]
    assert search.stats["candidateCount"] == len(search.witnesses) == 1
    assert search.stats["attemptedCandidateCountAtFailure"] == len(search.witnesses) + 1
    assert search.stats["rejectedCandidateId"] is not None


@pytest.mark.parametrize("bad", [
    {"unknown_axis": ["x"]},
    {"a": ["unknown_level"]},
    {"a": []},
    {"a": ["x", "x"]},
    "a == 'x'",
])
def test_invalid_constraint_matches_are_not_interpreted(bad):
    with pytest.raises(matrix.MatrixInputError):
        matrix.validate_matches(bad, {"a": ["x", "y"], "b": ["m"]}, label="independent")


def test_constraint_membership_is_conjunctive_and_unmentioned_axes_are_free():
    match = matrix.validate_matches(
        {"a": ["x", "y"], "b": ["m"]},
        {"a": ["x", "y", "z"], "b": ["m", "n"], "c": ["one", "two"]},
        label="independent",
    )
    assert matrix.matches({"a": "x", "b": "m", "c": "two"}, match)
    assert matrix.matches({"a": "y", "b": "m", "c": "one"}, match)
    assert not matrix.matches({"a": "x", "b": "n", "c": "one"}, match)
    assert not matrix.matches({"a": "z", "b": "m", "c": "one"}, match)


def test_unclassified_and_current_implementation_failures_stay_positive():
    row = {"a": "x", "b": "y"}
    assert matrix.classify_candidate(row, [])["legality"] == "valid"
    rules = [
        language_rule("current_refusal", "valid_unsupported", {}),
    ]
    record = matrix.classify_candidate(row, rules)
    assert record["legality"] == "valid"
    assert set(record["constraints"]) == {"current_refusal"}
    assert set(record["constraintClassifications"]) == {"valid_unsupported"}
    rules.append(language_rule("unresolved_role", "unresolved", {"a": ["x"]}))
    assert matrix.classify_candidate(row, rules)["legality"] == "unresolved"


def test_evidence_precedence_is_per_fact_and_requires_a_tv_receipt():
    # Hypothetical unit inputs only; these are not observed TV evidence.
    implementation = {
        "tier": "implementation_observation", "classification": "invalid",
        "source": "independent hypothetical current rejection",
    }
    documentation = {
        "tier": "pine_documentation", "classification": "valid",
        "source": "independent hypothetical documented fact",
    }
    tv_fact = {
        "tier": "tv_observed", "classification": "invalid",
        "source": "independent hypothetical source", "receipt": "independent hypothetical receipt",
    }
    assert matrix.resolve_fact_evidence([implementation])["classification"] == "valid"
    assert matrix.resolve_fact_evidence([implementation, documentation])["classification"] == "valid"
    assert matrix.resolve_fact_evidence([documentation, implementation, tv_fact])["classification"] == "invalid"
    missing_receipt = {key: value for key, value in tv_fact.items() if key != "receipt"}
    assert matrix.resolve_fact_evidence([documentation, missing_receipt])["classification"] == "valid"
    assert matrix.resolve_fact_evidence([missing_receipt])["classification"] == "unresolved"
    conflict = matrix.resolve_fact_evidence([
        documentation, dict(documentation, classification="invalid"),
    ])
    assert conflict["classification"] == "unresolved" and conflict["conflict"]
    row = {"role": "x", "other": "y"}
    first_fact = language_rule("documented", "valid", {"role": ["x"]})
    second_fact = language_rule("independent_restriction", "inapplicable", {"other": ["y"]})
    assert matrix.classify_candidate(row, [first_fact, second_fact])["legality"] == "inapplicable"
    assert matrix.fresh_results()["tv"]["status"] == "NOT_MEASURED"


def test_completion_does_not_prune_a_higher_tier_resolution_of_the_same_fact():
    # Hypothetical unit records; no real TV receipt or result is created.
    axes = {"a": ["0", "1"], "b": ["x", "y"], "c": ["m", "n"]}
    rules = [
        language_rule("documented_restriction", "invalid", {"a": ["0"]}, fact="one_fact"),
        language_rule(
            "higher_tier", "valid", {"a": ["0"], "b": ["x"]},
            fact="one_fact", evidenceTier="tv_observed", receipt="hypothetical unit receipt",
        ),
    ]
    search = matrix.CompletionSearch(axes, rules, limit=100)
    row, blockers = search.complete({"a": "0"})
    assert row == {"a": "0", "b": "x", "c": "m"} and not blockers
    assert matrix.classify_candidate(row, rules)["legality"] == "valid"
    assert matrix.classify_candidate(dict(row, b="y"), rules)["legality"] == "invalid"
    conflict = [
        language_rule("yes", "valid", {}, fact="same"),
        language_rule("no", "invalid", {}, fact="same"),
    ]
    assert matrix.classify_candidate(row, conflict)["legality"] == "unresolved"
    assert matrix.CompletionSearch(axes, conflict, limit=100).complete({})[0] is None


def test_positive_fact_completion_adds_a_feasible_role_to_the_actual_pool():
    axes = {
        "form": ["f", "g"], "layout": ["line", "block"],
        "type": ["number", "text"], "d": ["0", "1"], "e": ["0", "1"], "f": ["0", "1"],
    }
    rules = [language_rule("rare_three_way_role", "valid", {"d": ["1"], "e": ["1"], "f": ["1"]})]
    plan = matrix.coverage_plan(axes, rules, core_axes=["form", "layout", "type"], seed="rare-role")
    assert not plan["uncoveredPairs"]
    assert not plan["uncoveredFacts"]
    assert plan["complete"]
    assert any(row["d"] == row["e"] == row["f"] == "1" for row in plan["candidateRows"])


def test_fact_completion_orders_unrestricted_axes_before_later_selector_choices():
    axes = {"a": ["0", "1"], "b": ["x", "y"], "c": ["first", "second"]}
    rules = [language_rule("blocked", "invalid", {"b": ["x"], "c": ["first"]})]
    search = matrix.CompletionSearch(axes, rules, limit=100)
    row, blockers = search.region({"a": ["0"], "c": ["first", "second"]})
    assert row == {"a": "0", "b": "x", "c": "second"} and not blockers
    assert search.stats["candidateCount"] == 1


def tiny_inputs(scratch):
    """A small independent language inventory, with three synthetic witnesses."""
    axes = {
        "statement_kind": ["function_definition", "method"],
        "body_layout": ["single_line", "indented"],
        "primitive_type": ["int", "string"],
        "value_shape": ["scalar", "map"],
        "missing_value_position": ["none"],
        "missing_predicate_target": ["none"],
        "map_key_type": ["none", "string"],
    }
    positive = {
        "classification": "valid_intended_supported", "translation": "MUST_SUCCEED",
        "compile": "MUST_SUCCEED", "tv": "NOT_MEASURED",
    }
    outcomes = [
        positive,
        {"classification": "valid_unsupported", "translation": "MUST_SUCCEED", "compile": "MUST_SUCCEED", "tv": "NOT_MEASURED"},
        {"classification": "language_invalid", "translation": "MUST_REJECT_WITH_EXPECTED_PF_E", "compile": "NOT_APPLICABLE", "tv": "NOT_MEASURED"},
        {"classification": "unresolved", "translation": "UNKNOWN", "compile": "UNKNOWN", "tv": "NOT_MEASURED"},
    ]
    header = '//@version=6\nstrategy("independent witness")\n'
    tail = 'if not na(result)\n    strategy.entry("L", strategy.long)\n'
    provenance = {
        "sourceUrl": "https://example.invalid/independent-input",
        "baselineCodegen": "unmeasured-independent-input",
        "reportedRelease": "unmeasured", "reportedEngine": "unmeasured",
    }
    seed_axes = {name: levels[0] for name, levels in axes.items()}
    seeds = []
    declarations = []
    for index, name in enumerate(("alpha", "beta", "gamma")):
        body = f"probe_{index}(int supplied) => supplied\nint result = probe_{index}(bar_index)\n"
        source = (header + body + tail).encode("utf-8")
        safe_test_path(scratch / (name + ".pine")).write_bytes(source)
        seeds.append({
            "id": name, "file": name + ".pine", "body": body,
            "sourceSha256": digest(source), "axes": dict(seed_axes),
            "reportedRedPhase": "independent unexecuted control",
            "baselineFailureStage": "front_end" if index == 0 else "cpp_compile",
        })
        declarations.append({
            "id": name, "header": header, "body": body, "tail": tail,
            "reportedRedPhase": seeds[-1]["baselineFailureStage"],
            "expectedOutcome": dict(positive), "provenance": dict(provenance),
            "sourceBytes": len(source), "sourceSha256": digest(source),
        })
    index_data = {
        "schemaVersion": 1, **provenance, "header": header, "tail": tail,
        "inputSha256": digest(json.dumps(declarations, sort_keys=True).encode("utf-8")),
        "expected": dict(positive), "seeds": seeds,
        "validity": {"classification": "independent_test_input"},
    }
    control_axes = dict(
        seed_axes, body_layout="indented", value_shape="map", map_key_type="string",
    )
    controls = []
    for name, call, code, message in (
        ("unknown", 'target.get(foo="x")', "PF-E5016", "map.get: unknown keyword argument 'foo'"),
        ("missing", "target.get()", "PF-E5020", "map.get: missing required argument 'key'"),
        ("duplicate", 'target.get("x", key="y")', "PF-E5019",
         "map.get: argument 'key' passed both positionally and by keyword"),
    ):
        controls.append({
            "id": name, "renderFamily": "invalid_control",
            "source": (
                '//@version=6\nstrategy("independent invalid input")\n'
                "probe(map<string, int> target) =>\n"
                f"    {call}\nobserved = probe(map.new<string, int>())\n"
            ),
            "expectedOutcome": "language_invalid", "expectedDiagnosticCode": code,
            "expectedDiagnosticMessage": message,
            "expectedDiagnosticPhase": "codegen",
            "expectedDiagnosticHint": (
                "Expected map.get(key); bind each argument once."
                if name == "duplicate" else "Expected map.get(key)."
            ),
            "languageBasis": "Pine map.get argument-binding language contract.",
            "languageReference": "https://www.tradingview.com/pine-script-reference/v6/#fun_map.get",
            "emitterSource": "pineforge_codegen/codegen/visit_call.py::_map_call_arg_nodes",
            "invalidInputFact": {
                "unknown": "unknown_keyword", "missing": "missing_required_argument",
                "duplicate": "duplicate_parameter_binding",
            }[name],
            "catalogSource": "pineforge_codegen/diagnostics_catalog.json#/codes/" + code,
            "testSource": "tests/test_map_call_diagnostics.py::_typed_method_source",
            "axes": dict(control_axes),
        })
    spec = {
        "schemaVersion": 1, "draftRevision": "independent-test",
        "bounds": {"candidate100000": 100000, "ordinary512": 512, "seeds3": 3, "total515": 515},
        "axes": [{"name": name, "levels": levels} for name, levels in axes.items()],
        "classificationPolicy": {
            "evidencePrecedence": ["tv_observed", "pine_documentation", "implementation_observation"],
            "defaultClassification": "valid", "defaultExpectedOutcome": "valid_intended_supported",
            "constraintEvidenceDefault": "pine_documentation",
            "constraintFactField": "fact",
        },
        "enumerationPolicy": {
            "mode": "core_full_cross_union_all_axes_pairwise",
            "coreAxes": ["statement_kind", "body_layout", "primitive_type"],
            "pairwiseAxes": list(axes), "seed": "independent-test-input",
        },
        "inputFactPolicy": {
            "positiveFactKinds": ["axis_level", "statement_form", "valid_constraint"],
            "sourceObligations": ["selected_axis_roles_present_in_source", "exact_source_hash"],
            **{
            name: "Independent test fact group: " + name
            for name in (
                "positive", "explicitInvalid", "inapplicableInventory",
                "unresolvedEvidence", "invalidInventory",
            )}
        },
        "layoutApplicability": [{
            "statementKinds": list(axes["statement_kind"]), "applicable": list(axes["body_layout"]),
            "languageBasis": "Pine function/method inline and indented bodies.",
        }],
        "constraints": [
            language_rule("independent_valid_inventory", "valid", {}),
            language_rule("nonmap_key", "inapplicable", {
                "value_shape": ["scalar"], "map_key_type": ["string"],
            }),
            language_rule("map_without_key", "inapplicable", {
                "value_shape": ["map"], "map_key_type": ["none"],
            }),
        ],
        "invalidControls": controls, "unresolvedEvidence": [],
        "seedInputProvenance": {
            key: index_data[key] for key in ("inputSha256", "sourceUrl", "baselineCodegen", "reportedEngine")
        },
        "requiredSeeds": [seed["id"] for seed in seeds], "seedFixtures": declarations,
        "expectedOutcomes": outcomes,
        "renderFamilies": [
            {"id": "ordinary", "kind": "generated", "maxCases": 512},
            {"id": "invalid_control", "kind": "explicit_expected_refusal", "maxCases": 3},
            {"id": "community_seed", "kind": "required_seed", "maxCases": 3},
        ],
    }
    spec_path = scratch / "coverage.json"
    index_path = scratch / "seed-index.json"
    write_json(spec_path, spec)
    write_json(index_path, index_data)
    return spec, index_data, spec_path, index_path


def write_json(path, value):
    safe_test_path(path).write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )


def invoke(spec_path, index_path, output):
    return matrix.main([
        "--spec", str(spec_path), "--seed-index", str(index_path),
        "--output-dir", str(output),
    ])


def test_manifest_and_sources_recompute_from_independent_data(scratch):
    spec, index, spec_path, index_path = tiny_inputs(scratch)
    first = scratch / "first"
    second = scratch / "second"
    assert invoke(spec_path, index_path, first) == 0
    manifest = read_json(first / "manifest.json")
    assert manifest["generation"]["status"] == "COMPLETE"
    assert manifest["results"]["status"] == "INCOMPLETE"
    axes = {axis["name"]: axis["levels"] for axis in spec["axes"]}
    core_axes = spec["enumerationPolicy"]["coreAxes"]
    reference = independent_groups(axes, spec["constraints"], core_axes, spec["enumerationPolicy"]["seed"])
    assert manifest["coverage"]["candidateCount"] == len(reference["pool"])
    assert {row["id"]: row["axes"] for row in manifest["candidates"]} == reference["pool"]
    ordinary = [row for row in manifest["cases"] if row["renderFamily"] == "ordinary"]
    controls = [row for row in manifest["cases"] if row["renderFamily"] == "invalid_control"]
    expected_target = independent_pairs(reference["legal"])
    achieved = independent_pairs([row["axes"] for row in ordinary if row["groups"]["pairwise"]], axes)
    coverage = manifest["coverage"]
    assert unpack_pairs(coverage["targetPairs"]) == expected_target
    assert unpack_pairs(coverage["achievedPairs"]) == achieved
    assert unpack_pairs(coverage["uncoveredPairs"]) == expected_target - achieved
    assert not coverage["uncoveredInputFacts"]
    assert coverage["ordinarySelectedCount"] == len(ordinary) + len(controls) <= 512
    assert coverage["seedCount"] == len(index["seeds"])
    assert len(manifest["cases"]) <= 515
    core_ids = {row["id"] for row in ordinary if row["groups"]["core"]}
    pair_ids = {row["id"] for row in ordinary if row["groups"]["pairwise"]}
    assert core_ids == set(reference["coreIds"])
    assert pair_ids == set(reference["pairwiseIds"])
    assert coverage["coreTupleCount"] == len(projected(reference["legal"], core_axes))
    assert coverage["coreCaseCount"] == len(core_ids)
    assert coverage["pairwiseCaseCount"] == len(pair_ids)
    assert coverage["overlapCaseCount"] == len(core_ids & pair_ids)
    assert coverage["positiveUnionCount"] == len(core_ids | pair_ids) == len(ordinary)
    assert coverage["positiveUnionCount"] == (
        coverage["coreCaseCount"] + coverage["pairwiseCaseCount"] - coverage["overlapCaseCount"]
    )
    assert coverage["invalidControlCount"] == len(spec["invalidControls"]) == len(controls)
    assert coverage["totalEmittedCount"] == len(manifest["cases"])
    assert coverage["enumeratedCaseCount"] == (
        coverage["candidateCount"] + coverage["invalidControlCount"] + coverage["seedCount"]
    ) <= 100000
    assert coverage["structuralEnumerationUpperBound"] == (
        math.prod(len(axes[name]) for name in core_axes)
        + sum(len(axes[a]) * len(axes[b]) for a, b in itertools.combinations(axes, 2))
        + sum(len(values) for values in axes.values()) + len(axes["statement_kind"])
        + sum(rule["classification"] == "valid" for rule in spec["constraints"])
        + len(spec["invalidControls"]) + len(index["seeds"])
    )
    assert coverage["core"] == coverage["groups"]["core"]
    assert coverage["pairwise"] == coverage["groups"]["pairwise"]
    assert set(coverage["overlapCaseIds"]) == core_ids & pair_ids
    assert set(coverage["positiveUnionCaseIds"]) == core_ids | pair_ids
    assert len(coverage["pairInventory"]) == sum(
        len(axes[a]) * len(axes[b]) for a, b in itertools.combinations(axes, 2)
    )
    assert coverage["candidateProduct"] == math.prod(len(values) for values in axes.values())
    assert coverage["groups"]["core"]["selectionOrder"] == reference["coreIds"]
    assert coverage["groups"]["pairwise"]["selectionOrder"] == reference["pairwiseIds"]
    assert {fact["id"] for fact in coverage["groups"]["pairwise"]["targetInputFacts"]} == reference["targetFacts"]
    assert not coverage["groups"]["core"]["uncoveredTuples"]
    assert not coverage["groups"]["pairwise"]["uncoveredPairs"]
    assert not coverage["groups"]["pairwise"]["uncoveredInputFacts"]
    assert coverage["positiveDomainStatus"] == coverage["status"] == "COMPLETE"
    assert coverage["inventoryStatus"] == "ACKNOWLEDGED"
    assert coverage["invalidInputFacts"]["refusalVerification"] == "UNEXECUTED"
    assert all(count > 0 for count in coverage["formCounts"].values())
    assert manifest["specSourceHash"] == digest(read_bytes(spec_path))
    assert manifest["inputIdentities"]["seedIndex"] == digest(read_bytes(index_path))
    assert manifest["generatorSourceHash"] == digest(read_bytes(SCRIPT))
    for row in manifest["cases"]:
        assert set(row["axes"]) == set(axes)
        data = read_bytes(first / row["sourcePath"])
        assert digest(data) == row["sourceSha256"]
        assert len(data) == row["sourceBytes"]
        assert "validityProvenance" in row
        assert set(row["coverageMembership"]) == {"core", "pairwise"}
        result = manifest["results"]["cases"][row["id"]]
        assert result["translation"]["status"] == "UNEXECUTED"
        assert result["compile"]["status"] == "UNEXECUTED"
        assert result["tv"]["status"] == "NOT_MEASURED"
        assert result["codegenIdentity"] is result["engineIdentity"] is result["compilerIdentity"] is None
        assert result["translation"]["exitCode"] is result["compile"]["exitCode"] is None
        assert result["translation"]["diagnostics"] is result["compile"]["diagnostics"] is None
        if row["renderFamily"] == "invalid_control":
            control = next(item for item in spec["invalidControls"] if "control-" + item["id"] == row["id"])
            assert data == control["source"].encode("utf-8")
            assert row["expected"]["translation"] == "MUST_REJECT_WITH_EXPECTED_PF_E"
            assert row["expected"]["compile"] == "NOT_APPLICABLE"
            assert row["expectedDiagnosticCode"] == row["expected"]["diagnosticCode"] == control["expectedDiagnosticCode"]
            assert row["expectedDiagnosticPhase"] == row["expected"]["diagnosticPhase"] == control["expectedDiagnosticPhase"]
            assert row["expectedDiagnosticHint"] == row["expected"]["diagnosticHint"] == control["expectedDiagnosticHint"]
            assert row["invalidInputFact"] == control["invalidInputFact"]
            assert row["controlProvenance"]["catalogSource"] == control["catalogSource"]
            assert row["controlProvenance"]["testSource"] == control["testSource"]
        else:
            assert row["expected"]["translation"] == row["expected"]["compile"] == "MUST_SUCCEED"
    for seed in index["seeds"]:
        assert read_bytes(first / "cases" / (seed["id"] + ".pine")) == (
            index["header"] + seed["body"] + index["tail"]
        ).encode("utf-8")
    assert invoke(spec_path, index_path, second) == 0
    assert read_bytes(first / "manifest.json") == read_bytes(second / "manifest.json")
    for row in manifest["cases"]:
        assert read_bytes(first / row["sourcePath"]) == read_bytes(second / row["sourcePath"])


def test_reused_output_is_refused_without_changing_it(scratch):
    _, _, spec_path, index_path = tiny_inputs(scratch)
    output = scratch / "retained"
    output.mkdir()
    sentinel = output / "retained.txt"
    sentinel.write_text("retain these bytes\n", encoding="utf-8")
    assert invoke(spec_path, index_path, output) == 2
    assert read_bytes(sentinel) == b"retain these bytes\n"
    assert sorted(path.name for path in output.iterdir()) == ["retained.txt"]


def test_constraint_error_is_an_incomplete_manifest(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["constraints"][0]["matches"] = {"misspelled_axis": ["x"]}
    write_json(spec_path, spec)
    output = scratch / "invalid"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    assert manifest["generation"]["status"] == "INCOMPLETE"
    assert manifest["generation"]["exitCode"] != 0
    assert manifest["coverage"]["targetPairs"] is None
    assert manifest["results"]["status"] == "INCOMPLETE"


def test_schema_expressions_and_duplicate_levels_are_refused(scratch):
    spec, _, _, _ = tiny_inputs(scratch)
    expression = copy.deepcopy(spec)
    expression["constraints"][0]["expression"] = "arbitrary_code()"
    with pytest.raises(matrix.MatrixInputError, match="unknown constraint fields"):
        matrix.validate_spec(expression)
    duplicate = copy.deepcopy(spec)
    duplicate["axes"][0]["levels"].append(duplicate["axes"][0]["levels"][0])
    with pytest.raises(matrix.MatrixInputError, match="repeats a level"):
        matrix.validate_spec(duplicate)
    unknown = copy.deepcopy(spec)
    unknown["schemaVersion"] = 2
    with pytest.raises(matrix.MatrixInputError, match="unknown spec schemaVersion"):
        matrix.validate_spec(unknown)


@pytest.mark.parametrize("field,value", [
    ("expectedDiagnosticCode", ""),
    ("expectedDiagnosticCode", "PF-W5016"),
    ("catalogSource", "pineforge_codegen/diagnostics_catalog.json#PF-E5020"),
    ("testSource", ""),
    ("expectedOutcome", "valid_intended_supported"),
])
def test_missing_or_conflicting_negative_evidence_is_incomplete(scratch, field, value):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["invalidControls"][0][field] = value
    write_json(spec_path, spec)
    output = scratch / "negative-evidence"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    assert actual_exit != 0
    assert manifest["generation"]["exitCode"] == actual_exit
    assert manifest["generation"]["status"] == manifest["results"]["status"] == "INCOMPLETE"
    assert any(issue["kind"] == "input_contract_error" for issue in manifest["generation"]["issues"])
    assert not manifest["cases"]


def test_empty_negative_set_cannot_be_called_complete(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["invalidControls"] = []
    write_json(spec_path, spec)
    output = scratch / "missing-controls"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    assert manifest["generation"]["status"] == "INCOMPLETE"
    assert any(
        issue["kind"] == "input_contract_error" and "three explicit invalid controls" in issue["detail"]
        for issue in manifest["generation"]["issues"]
    )


def test_changed_control_source_is_consumed_without_a_measured_refusal(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    original = spec["invalidControls"][0]["source"].encode("utf-8")
    spec["invalidControls"][0]["source"] = spec["invalidControls"][0]["source"].replace('foo="x"', 'foo="changed"')
    write_json(spec_path, spec)
    output = scratch / "changed-control"
    assert invoke(spec_path, index_path, output) == 0
    manifest = read_json(output / "manifest.json")
    control = next(
        row for row in manifest["cases"] if row["id"] == "control-" + spec["invalidControls"][0]["id"]
    )
    actual = read_bytes(output / control["sourcePath"])
    assert actual == spec["invalidControls"][0]["source"].encode("utf-8") != original
    assert control["sourceSha256"] == digest(actual) != digest(original)
    assert manifest["results"]["cases"][control["id"]]["translation"] == {
        "status": "UNEXECUTED", "exitCode": None, "diagnostics": None,
    }
    assert manifest["coverage"]["invalidInputFacts"]["refusalVerification"] == "UNEXECUTED"


def test_smaller_real_output_bound_withholds_the_entire_plan_and_exposes_uncovered_sources(scratch, monkeypatch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    real_build = matrix.build_matrix
    bound = 6

    def limited_build(parsed, output, manifest):
        return real_build(parsed, output, manifest, ordinary_limit=bound)

    monkeypatch.setattr(matrix, "build_matrix", limited_build)
    output = scratch / "output-bound"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    coverage = manifest["coverage"]
    assert actual_exit != 0
    assert manifest["generation"]["exitCode"] == actual_exit
    axes = {axis["name"]: axis["levels"] for axis in spec["axes"]}
    reference = independent_groups(axes, spec["constraints"], spec["enumerationPolicy"]["coreAxes"],
                                   spec["enumerationPolicy"]["seed"])
    full_union = set(reference["coreIds"]) | set(reference["pairwiseIds"])
    ordinary = [row for row in manifest["cases"] if row["renderFamily"] != "community_seed"]
    assert coverage["ordinarySelectedCount"] == len(full_union) + len(spec["invalidControls"]) > bound
    assert len(ordinary) == coverage["ordinarySelectedCount"]
    assert not coverage["capSatisfied"]
    assert all(row["renderStatus"] == "WITHHELD_CAP" and row["sourcePath"] is None for row in ordinary)
    assert coverage["ordinaryEmittedCount"] == coverage["positiveEmittedCount"] == 0
    assert unpack_pairs(coverage["uncoveredPairs"]) == reference["targetPairs"]
    assert coverage["groups"]["core"]["uncoveredTuples"]
    assert coverage["invalidInputFacts"]["uncovered"]
    assert any(issue["kind"] == "ordinary_case_cap_exceeded" and issue["limit"] == bound
               for issue in manifest["generation"]["issues"])
    assert manifest["results"]["status"] == "INCOMPLETE"


def test_seed_digest_drift_cannot_complete_generation(scratch):
    _, index, spec_path, index_path = tiny_inputs(scratch)
    source_path = scratch / index["seeds"][0]["file"]
    source_path.write_bytes(read_bytes(source_path) + b"// changed source\n")
    output = scratch / "seed-drift"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    assert manifest["generation"]["status"] == "INCOMPLETE"
    assert manifest["results"]["status"] == "INCOMPLETE"
    assert any(
        issue["kind"] == "input_contract_error" and "sourceSha256" in issue["detail"]
        for issue in manifest["generation"]["issues"]
    )


def test_seed_provenance_conflict_keeps_exact_positive_witnesses(scratch):
    spec, index, spec_path, index_path = tiny_inputs(scratch)
    spec["seedFixtures"][0]["provenance"]["baselineCodegen"] = "different-unmeasured-input"
    write_json(spec_path, spec)
    output = scratch / "provenance"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    assert any(
        issue["kind"] == "seed_spec_provenance_mismatch"
        for issue in manifest["generation"]["issues"]
    )
    witnesses = [row for row in manifest["cases"] if row["renderFamily"] == "community_seed"]
    assert {row["id"] for row in witnesses} == {seed["id"] for seed in index["seeds"]}
    for row in witnesses:
        assert row["expected"]["translation"] == row["expected"]["compile"] == "MUST_SUCCEED"
        assert read_bytes(output / row["sourcePath"]) == read_bytes(scratch / (row["id"] + ".pine"))


def test_resolved_positive_coverage_and_unresolved_inventory_are_distinct(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["constraints"].append({
        "id": "independent_unresolved",
        "fact": "independent_unresolved",
        "matches": {"statement_kind": ["method"], "value_shape": ["map"]},
        "classification": "unresolved", "reason": "Independent unknown-language control.",
        "languageBasis": "No language evidence is supplied for this control.",
    })
    write_json(spec_path, spec)
    output = scratch / "unresolved-inventory"
    assert invoke(spec_path, index_path, output) == 0
    manifest = read_json(output / "manifest.json")
    coverage = manifest["coverage"]
    assert coverage["positiveDomainStatus"] == "COMPLETE"
    assert coverage["status"] == "INCOMPLETE"
    assert coverage["inventoryStatus"] == "UNRESOLVED"
    assert coverage["unresolvedEvidence"]["selectors"][0]["matches"] == {
        "statement_kind": ["method"], "value_shape": ["map"],
    }
    assert not coverage["unresolvedEvidence"]["coreTuples"]
    assert coverage["unresolvedEvidence"]["pairs"]
    assert not coverage["uncoveredPairs"] and not coverage["uncoveredInputFacts"]
    assert all(
        (row["axes"]["statement_kind"], row["axes"]["value_shape"]) != ("method", "map")
        for row in manifest["candidates"]
    )
    assert manifest["generation"]["sourceStatus"] == "COMPLETE"
    assert manifest["generation"]["status"] == "COMPLETE"
    assert manifest["results"]["status"] == "INCOMPLETE"


def test_unknown_renderer_input_is_positive_and_stops_later_emission(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    for axis in spec["axes"]:
        if axis["name"] == "value_shape":
            axis["levels"].append("unknown_shape")
    write_json(spec_path, spec)
    output = scratch / "unknown-renderer"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    unknown = [row for row in manifest["candidates"] if row["axes"]["value_shape"] == "unknown_shape"]
    assert unknown and all(row["legality"] == "valid" for row in unknown)
    assert all(row["expected"]["compile"] == "MUST_SUCCEED" for row in unknown)
    issues = manifest["generation"]["issues"]
    assert sum(issue["kind"] == "renderer_mismatch" for issue in issues) == 1
    failed = next(issue["caseId"] for issue in issues if issue["kind"] == "renderer_mismatch")
    ordinary = [row for row in manifest["cases"] if row["renderFamily"] == "ordinary"]
    failed_index = next(index for index, row in enumerate(ordinary) if row["id"] == failed)
    assert all(row["sourcePath"] is None for row in ordinary[failed_index:])
    assert manifest["coverage"]["uncoveredPairs"]
    assert manifest["coverage"]["status"] == manifest["coverage"]["positiveDomainStatus"] == "INCOMPLETE"


def test_zero_ordinary_rows_for_a_requested_form_cannot_complete(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["constraints"].append(language_rule(
        "missing_method_reference", "unresolved", {"statement_kind": ["method"]},
    ))
    write_json(spec_path, spec)
    output = scratch / "zero-form"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    coverage = manifest["coverage"]
    assert coverage["formCounts"]["method"] == 0
    assert "form:method" in {fact["id"] for fact in coverage["uncoveredInputFacts"]}
    assert any(issue["kind"] == "zero_positive_form_rows" and issue["forms"] == ["method"]
               for issue in manifest["generation"]["issues"])
    assert coverage["positiveDomainStatus"] == "INCOMPLETE"


def test_a_constraint_cannot_erase_a_declared_applicable_core_layout(scratch):
    spec, _, spec_path, index_path = tiny_inputs(scratch)
    spec["constraints"].append(language_rule(
        "contradictory_layout", "inapplicable",
        {"statement_kind": ["method"], "body_layout": ["single_line"]},
    ))
    write_json(spec_path, spec)
    output = scratch / "layout-conflict"
    assert invoke(spec_path, index_path, output) != 0
    manifest = read_json(output / "manifest.json")
    assert any(issue["kind"] == "core_layout_contract_conflict"
               and issue["missingApplicableCompletions"]
               for issue in manifest["generation"]["issues"])
    missing = manifest["coverage"]["groups"]["core"]["uncoveredTuples"]
    assert any(row["statement_kind"] == "method" and row["body_layout"] == "single_line" for row in missing)
    assert all(row["sourcePath"] is None for row in manifest["cases"] if row["renderFamily"] == "ordinary")
    assert manifest["coverage"]["positiveDomainStatus"] == "INCOMPLETE"


def test_deliberate_generation_crash_has_nonzero_own_result(scratch, monkeypatch):
    _, _, spec_path, index_path = tiny_inputs(scratch)

    def deliberate_crash(_):
        raise RuntimeError("deliberate generation crash")

    monkeypatch.setattr(matrix, "render_case", deliberate_crash)
    output = scratch / "crash"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    assert actual_exit != 0
    assert manifest["generation"]["exitCode"] == actual_exit
    assert manifest["generation"]["status"] == "INCOMPLETE"
    assert any(
        issue["kind"] == "generation_crash" and issue["exception"] == "RuntimeError"
        and issue["detail"] == "deliberate generation crash"
        for issue in manifest["generation"]["issues"]
    )
    assert manifest["coverage"]["status"] == manifest["results"]["status"] == "INCOMPLETE"
    assert all(
        result["compile"]["status"] == "UNEXECUTED"
        for result in manifest["results"]["cases"].values()
    )


@pytest.mark.parametrize("raw", [
    "/unused/.config/zsh/keys/key", "/unused/.ssh/key",
    "/unused/.aws/credentials", "/unused/.codex/config",
    "/unused/.config/pineforge/config", "/unused/example.env", "/unused/.env",
    "/unused/.SSH/key", "/unused/example.ENV",
])
def test_named_sensitive_paths_are_refused_before_file_access(raw, monkeypatch):
    def unexpected_read(_):
        raise AssertionError("a refused path reached a file read")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    with pytest.raises(matrix.MatrixInputError, match="refused path by name"):
        matrix.InputFiles().read(raw, identity="refusal-control")


def test_community_fixtures_keep_exact_fragments_and_distinct_reported_phases():
    root = ROOT / "tests/fixtures/generated_compile_matrix"
    index = read_json(root / "seed-index.json")
    assert len(index["seeds"]) == 3
    assert index["expected"]["translation"] == index["expected"]["compile"] == "MUST_SUCCEED"
    assert index["observed"] == {
        "translation": "UNEXECUTED", "compile": "UNEXECUTED", "tv": "NOT_MEASURED",
    }
    stages = [seed["baselineFailureStage"] for seed in index["seeds"]]
    assert stages.count("front_end") == 1
    assert stages.count("cpp_compile") == 2
    for seed in index["seeds"]:
        assert seed["file"] == seed["id"] + ".pine"
        source = read_bytes(root / seed["file"])
        assert source == (index["header"] + seed["body"] + index["tail"]).encode("utf-8")
        assert digest(source) == seed["sourceSha256"]


def test_raw_missing_branches_map_roles_and_udt_predicates_reach_source():
    axes = {
        "statement_kind": "function_definition", "body_layout": "single_line",
        "primitive_type": "string", "value_shape": "udt_field_array",
        "missing_value_position": "branch_order",
        "missing_predicate_target": "udt_field_container", "map_key_type": "none",
    }
    source, _reserved_issues, details = matrix.render_case(axes)
    assert b"array<string> selected_value = holder.value" in source
    assert b"array<string> result = probe(selected_value, choose)" in source
    assert b"choose ? na : present" in source
    assert b"choose ? present : na" in source
    assert b"na(holder.value)" in source
    assert b"array<string> value" in source
    assert details["branchOrders"] == ["missing_first", "missing_last"]
    variants = []
    for key in ("int", "float", "bool", "string", "color"):
        mapped = dict(
            axes, value_shape="udt_field_map", map_key_type=key,
            missing_value_position="collection_element",
        )
        data, _reserved_issues, rendered = matrix.render_case(mapped)
        assert b"Payload holder = Payload.new(missing_element)" in data
        assert b"map<" in data and b"selected_value = holder.value" in data
        assert f"map<{key}, string>".encode("utf-8") in data
        assert b"missing_element.put(selected_key, na)" in data
        assert rendered["mapKeyType"] == key
        variants.append(data)
    assert len(set(variants)) == len(variants)


@pytest.mark.parametrize("statement,layouts", [
    ("if", ("single_line", "indented")), ("if_else", ("single_line", "indented")),
    ("ternary", ("single_line", "indented")), ("switch", ("single_line", "indented")),
    ("for", ("indented",)), ("while", ("indented",)),
    ("function_definition", ("single_line", "indented")),
    ("method", ("single_line", "indented")), ("var", ("single_line", "indented")),
    ("varip", ("single_line", "indented")), ("tuple_return", ("single_line", "indented")),
])
@pytest.mark.parametrize("primitive", ["int", "float", "bool", "string", "color"])
def test_each_form_keeps_every_applicable_layout_as_real_source(statement, layouts, primitive):
    sources = []
    for layout in layouts:
        row = {
            "statement_kind": statement, "body_layout": layout, "primitive_type": primitive,
            "value_shape": "scalar", "missing_value_position": "none",
            "missing_predicate_target": "none", "map_key_type": "none",
        }
        source, _reserved_issues, details = matrix.render_case(row)
        assert f"{primitive} selected_value = present".encode("utf-8") in source
        assert b"bool observed = " in source
        assert f"{primitive} present = ".encode("utf-8") in source
        assert b'strategy.entry("L", strategy.long)' in source
        if statement in {"if", "if_else"} and layout == "single_line":
            assert f"if choose\n    {primitive} branch_value = selected_value, result := branch_value".encode("utf-8") in source
        if statement in {"ternary", "var", "varip"} and layout == "indented":
            assert details["layoutRole"] == "enclosing_local_statement"
            assert b"if bar_index >= 0\n    " in source
        if statement in {"var", "varip"}:
            declaration = (
                f"{statement} {primitive} result = selected_value"
                if layout == "single_line"
                else f"    {statement} {primitive} stored_value = selected_value"
            )
            assert declaration.encode("utf-8") in source
        sources.append(source)
    assert len(set(sources)) == len(layouts)


SPEC_PATH = ROOT / "tests/fixtures/generated_compile_matrix/coverage_spec.json"
SOURCE_AXES = (
    "statement_kind", "body_layout", "primitive_type", "value_shape",
    "missing_value_position", "missing_predicate_target", "map_key_type",
)


@pytest.fixture(scope="module")
def source_inventory():
    raw = read_json(ROOT / "tests/fixtures/generated_compile_matrix/coverage_spec.json")
    domains = {axis["name"]: axis["levels"] for axis in raw["axes"]}
    assert tuple(domains) == SOURCE_AXES
    return domains, raw["constraints"], raw["classificationPolicy"]


def independently_renderable(row, rules, policy):
    """Language eligibility only; do not ask the renderer to define its tests.

    This oracle groups input evidence by fact. It does not call the
    generator's classifier, search, selection or source renderer.
    """
    tiers = policy["evidencePrecedence"]
    assert tiers == ["tv_observed", "pine_documentation", "implementation_observation"]
    assert policy["defaultClassification"] == "valid"
    facts = {}
    for rule in rules:
        if not all(row[name] in levels for name, levels in rule["matches"].items()):
            continue
        tier = rule.get("evidenceTier", policy["constraintEvidenceDefault"])
        assert tier is None or tier in tiers
        if tier == "tv_observed" and not (rule.get("languageBasis") and rule.get("receipt")):
            tier = None
        rank = len(tiers) if tier is None else tiers.index(tier)
        label = rule["classification"]
        if tier is None:
            label = "unresolved"
        elif tier == "implementation_observation" or label == "valid_unsupported":
            label = "valid"
        facts.setdefault(rule["fact"], []).append((rank, label))
    for records in facts.values():
        strongest = min(rank for rank, _ in records)
        labels = {label for rank, label in records if rank == strongest}
        if labels != {"valid"}:
            return False
    return True


def eligible_axis_groups(inventory, axis):
    """Hold every other axis fixed; compare all legal levels in that context.

    A group needs two eligible levels. Coupled roles with no common legal
    context (such as map_key_type=none versus a primitive key) are not
    declared aliases and receive no fictional one-axis comparison.
    """
    domains, _, _ = inventory
    yield from axis_groups(domains, oracle_eligible_set(inventory), axis)


_ELIGIBLE_CACHE = {}


def oracle_eligible_set(inventory):
    """The independent oracle's eligible rows: one cached pass over the SPEC product."""
    key = ("oracle", id(inventory))
    if key not in _ELIGIBLE_CACHE:
        domains, rules, policy = inventory
        names = list(domains)
        _ELIGIBLE_CACHE[key] = {
            values for values in itertools.product(*(domains[name] for name in names))
            if independently_renderable(dict(zip(names, values)), rules, policy)
        }
    return _ELIGIBLE_CACHE[key]


def production_positive_set(inventory):
    """The generator's own classifier's positive rows over the same real SPEC product."""
    key = ("production", id(inventory))
    if key not in _ELIGIBLE_CACHE:
        domains, _, _ = inventory
        spec = matrix.validate_spec(read_json(SPEC_PATH))
        names = list(domains)
        _ELIGIBLE_CACHE[key] = {
            values for values in itertools.product(*(domains[name] for name in names))
            if matrix.classify_candidate(dict(zip(names, values)), spec["rules"])["legality"] == "valid"
        }
    return _ELIGIBLE_CACHE[key]


def axis_groups(domains, eligible, axis):
    """Hold every other axis fixed; yield each context with two or more eligible levels."""
    names = list(domains)
    other_axes = [name for name in names if name != axis]
    for values in itertools.product(*(domains[name] for name in other_axes)):
        context = dict(zip(other_axes, values))
        rows = []
        for level in domains[axis]:
            row = {name: level if name == axis else context[name] for name in names}
            if tuple(row[name] for name in names) in eligible:
                rows.append(row)
        if len(rows) > 1:
            yield rows


def assert_axis_source_distinction(rows, axis, render):
    assert len(rows) > 1
    fixed = {name: value for name, value in rows[0].items() if name != axis}
    assert all({name: value for name, value in row.items() if name != axis} == fixed for row in rows)
    assert len({row[axis] for row in rows}) == len(rows)
    sources = [render(row)[0] for row in rows]
    assert all(isinstance(source, bytes) and source for source in sources)
    # The generator's own comparison normal form decides: a label comment or a
    # consistent rename must not rescue an ignored input, while indentation,
    # literals and statements keep body_layout and every construct observable.
    constructs = [matrix.comparison_normal_form(source) for source in sources]
    assert len(set(constructs)) == len(rows), (
        f"source collision for {axis}: {[row[axis] for row in rows]!r}; context={fixed!r}"
    )


@pytest.mark.parametrize("axis", SOURCE_AXES)
def test_all_eligible_axis_levels_change_source_constructs(source_inventory, axis):
    compared = False
    for rows in eligible_axis_groups(source_inventory, axis):
        compared = True
        assert_axis_source_distinction(rows, axis, matrix.render_case)
    assert compared, f"no eligible source comparison exercised {axis}"


@pytest.mark.parametrize("axis", SOURCE_AXES)
def test_source_distinction_oracle_detects_ignored_input_and_comment_credit(source_inventory, axis):
    rows = next(eligible_axis_groups(source_inventory, axis))
    # Positive control uses the same input rows as both negative controls.
    assert_axis_source_distinction(rows, axis, matrix.render_case)

    def ignore_axis(row):
        return matrix.render_case(dict(row, **{axis: rows[0][axis]}))

    with pytest.raises(AssertionError, match="source collision"):
        assert_axis_source_distinction(rows, axis, ignore_axis)

    def comment_only(row):
        source, reserved, details = ignore_axis(row)
        return source + f"// {axis}={row[axis]}\n".encode("utf-8"), reserved, details

    with pytest.raises(AssertionError, match="source collision"):
        assert_axis_source_distinction(rows, axis, comment_only)

    def rename_only(row):
        source, reserved, details = ignore_axis(row)
        return rename_user_names(source, row[axis]), reserved, details

    with pytest.raises(AssertionError, match="source collision"):
        assert_axis_source_distinction(rows, axis, rename_only)


def assert_initializer_and_handle_constructs(row, initializer, handle):
    primitive = row["primitive_type"]
    shape = row["value_shape"].removeprefix("udt_field_")
    pine_type = {
        "array": f"array<{primitive}>",
        "map": f"map<{row['map_key_type']}, {primitive}>",
        "matrix": f"matrix<{primitive}>",
    }[shape]
    assert f"{pine_type} initialized_missing = na\n".encode("utf-8") in initializer
    assert b"initialized_missing" not in handle
    if row["value_shape"].startswith("udt_field_"):
        assert (
            "Payload holder = Payload.new(initialized_missing)\n"
            f"{pine_type} selected_value = holder.value\n"
        ).encode("utf-8") in initializer
        assert (
            "Payload holder = Payload.new(present)\n"
            "holder.value := na\n"
            f"{pine_type} selected_value = holder.value\n"
        ).encode("utf-8") in handle
    else:
        assert f"{pine_type} selected_value = initialized_missing\n".encode("utf-8") in initializer
        assert (
            f"{pine_type} handle_value = present\n"
            "handle_value := na\n"
            f"{pine_type} selected_value = handle_value\n"
        ).encode("utf-8") in handle
    assert b"bool observed = " in initializer and b"bool observed = " in handle
    assert initializer != handle


def test_missing_handle_is_a_later_assignment_for_every_eligible_collection(source_inventory):
    domains, _, _ = source_inventory
    exercised_shapes = set()
    for rows in eligible_axis_groups(source_inventory, "missing_value_position"):
        by_position = {row["missing_value_position"]: row for row in rows}
        if not {"initializer", "collection_handle"} <= set(by_position):
            continue
        row = by_position["collection_handle"]
        initializer = matrix.render_case(by_position["initializer"])[0]
        handle = matrix.render_case(row)[0]
        assert_initializer_and_handle_constructs(row, initializer, handle)
        exercised_shapes.add(row["value_shape"])
    assert exercised_shapes == {
        shape for shape in domains["value_shape"]
        if shape.removeprefix("udt_field_") in {"array", "map", "matrix"}
    }


def test_reader_cited_array_case_keeps_two_missing_positions(source_inventory):
    row = {
        "statement_kind": "if", "body_layout": "indented",
        "primitive_type": "int", "value_shape": "array",
        "missing_value_position": "initializer",
        "missing_predicate_target": "none", "map_key_type": "none",
    }
    domains, rules, policy = source_inventory
    assert all(value in domains[name] for name, value in row.items())
    changed = dict(row, missing_value_position="collection_handle")
    assert independently_renderable(row, rules, policy)
    assert independently_renderable(changed, rules, policy)
    assert_axis_source_distinction([row, changed], "missing_value_position", matrix.render_case)
    assert_initializer_and_handle_constructs(
        changed, matrix.render_case(row)[0], matrix.render_case(changed)[0],
    )


def test_identical_ordinary_source_cannot_earn_two_coverage_credits(scratch, monkeypatch):
    _, _, spec_path, index_path = tiny_inputs(scratch)
    # Positive control first: this same independent input is generatable.
    baseline = scratch / "collision-positive"
    assert invoke(spec_path, index_path, baseline) == 0
    real_render = matrix.render_case
    first_source = None

    def input_ignoring_renderer(row):
        nonlocal first_source
        source, reserved, details = real_render(row)
        if first_source is None:
            first_source = source
        return first_source, reserved, details

    monkeypatch.setattr(matrix, "render_case", input_ignoring_renderer)
    output = scratch / "collision-negative"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    assert actual_exit != 0
    assert manifest["generation"]["exitCode"] == actual_exit
    collision = next(
        issue for issue in manifest["generation"]["issues"]
        if issue["kind"] == "renderer_source_collision"
    )
    by_id = {row["id"]: row for row in manifest["cases"]}
    first = by_id[collision["firstCaseId"]]
    duplicate = by_id[collision["caseId"]]
    assert first["axes"] != duplicate["axes"]
    assert first["sourceSha256"] == collision["sourceSha256"] == digest(first_source)
    assert first["renderStatus"] == "GENERATED"
    assert duplicate["renderStatus"] == "INCOMPLETE"
    assert duplicate["sourcePath"] is duplicate["sourceSha256"] is None
    ordinary = [
        row for row in manifest["cases"]
        if row["renderFamily"] == "ordinary" and row["sourcePath"]
    ]
    sources = [read_bytes(output / row["sourcePath"]) for row in ordinary]
    assert len(set(sources)) == len(sources)
    coverage = manifest["coverage"]
    assert coverage["positiveEmittedCount"] == len(ordinary)
    axes = list(first["axes"])
    assert unpack_pairs(coverage["achievedPairs"]) == independent_pairs(
        [row["axes"] for row in ordinary if row["groups"]["pairwise"]], axes,
    )
    core_axes = coverage["core"]["axes"]
    assert projected(coverage["core"]["achievedTuples"], core_axes) == projected(
        [row["axes"] for row in ordinary], core_axes,
    )
    assert coverage["positiveDomainStatus"] == coverage["status"] == "INCOMPLETE"
    assert manifest["generation"]["status"] == manifest["results"]["status"] == "INCOMPLETE"
    assert all(
        result["translation"]["status"] == result["compile"]["status"] == "UNEXECUTED"
        for result in manifest["results"]["cases"].values()
    )


# The vocabulary below is Pine language knowledge (keywords, types, built-in and
# member names the renderer uses). Every other word is a user identifier. It is
# the test's own derivation, not the generator's declaration scan.
PINE_VOCABULARY = frozenset({
    "if", "else", "for", "to", "while", "switch", "var", "varip", "type", "method",
    "and", "or", "not", "true", "false", "int", "float", "bool", "string", "color",
    "array", "map", "matrix", "na", "bar_index", "close", "open", "strategy", "entry",
    "long", "str", "tostring", "new", "put", "get", "size", "rows", "green", "red", "blue",
})


def rename_user_names(source, suffix):
    """Consistently rename every non-vocabulary word outside quotes and comment lines."""
    lines = []
    for line in source.decode("utf-8").split("\n"):
        if line.lstrip().startswith("//"):
            lines.append(line)
            continue
        parts = re.split(r"(\"[^\"\n]*\"|'[^'\n]*')", line)
        lines.append("".join(
            part if number % 2 else re.sub(
                r"[A-Za-z_][A-Za-z0-9_]*",
                lambda word: word.group(0) if word.group(0) in PINE_VOCABULARY
                else f"{word.group(0)}_{suffix}",
                part,
            )
            for number, part in enumerate(parts)
        ))
    return "\n".join(lines).encode("utf-8")


def with_labels(source):
    """Comment-only variation: label lines and trailing comments, never the directive line."""
    labeled = ["// label: not a construct // still a comment"]
    for number, line in enumerate(source.decode("utf-8").split("\n")):
        if not line.strip() or line.lstrip().startswith("//"):
            labeled.append(line)
        else:
            indent = " " * (len(line) - len(line.lstrip(" ")))
            labeled += [f"{indent}// note {number}", f"{line} // trailing {number}"]
    return "\n".join(labeled).encode("utf-8")


def mutated(source, kind):
    """A real literal, operator or construct change of one rendered source."""
    lines = source.decode("utf-8").split("\n")
    for position, line in enumerate(lines):
        if line.lstrip().startswith("//") or not line.strip():
            continue
        if kind == "literal":
            found = re.search(r"(?<![\w.\"'])\d+(?![\w.\"'])", line)
            if found:
                lines[position] = line[:found.start()] + str(int(found.group(0)) + 7) + line[found.end():]
                return "\n".join(lines).encode("utf-8")
        elif kind == "operator":
            for old, new in ((" + ", " - "), (" > ", " < "), (" == ", " != "), (" % ", " * "), (" >= ", " <= ")):
                if old in line:
                    lines[position] = line.replace(old, new, 1)
                    return "\n".join(lines).encode("utf-8")
    return source + b"int extra_probe = 1\n" if kind == "construct" else None


def sample_sources(source_inventory):
    sources = []
    for axis in SOURCE_AXES:
        for row in next(eligible_axis_groups(source_inventory, axis)):
            sources.append(matrix.render_case(row)[0])
    assert sources
    return sources


def test_comment_only_and_consistent_rename_only_sources_share_one_normal_form(source_inventory):
    for number, source in enumerate(sample_sources(source_inventory)):
        form = matrix.comparison_normal_form(source)
        labeled = with_labels(source)
        renamed = rename_user_names(source, f"r{number}")
        assert labeled != source and renamed != source
        assert matrix.comparison_normal_form(labeled) == form
        assert matrix.comparison_normal_form(renamed) == form
        assert matrix.comparison_normal_form(with_labels(renamed)) == form
        assert matrix.comparison_fingerprint(renamed) == matrix.comparison_fingerprint(source)
        assert digest(renamed) != digest(source)


def test_literal_operator_and_construct_changes_stay_distinct(source_inventory):
    applied = {"literal": 0, "operator": 0, "construct": 0}
    for source in sample_sources(source_inventory):
        form = matrix.comparison_normal_form(source)
        for kind in applied:
            changed = mutated(source, kind)
            if changed is None:
                continue
            applied[kind] += 1
            assert changed != source
            assert matrix.comparison_normal_form(changed) != form, kind
    assert all(applied.values()), applied


def test_normal_form_unit_controls_for_names_strings_layout_and_unreadable_forms():
    form = matrix.comparison_normal_form
    head = b"//@version=6\n"
    assert form(head + b"float a = 1\nfloat b = 2\nfloat c = a + a\n") == form(
        head + b"float p = 1\nfloat q = 2\nfloat r = p + p\n")
    assert form(head + b"float a = 1\nfloat b = 2\nfloat c = a + a\n") != form(
        head + b"float a = 1\nfloat b = 2\nfloat c = a + b\n")
    assert form(head + b'string s = "// kept"\n') != form(head + b'string s = ""\n')
    assert form(head + b'string s = "x" // dropped\n') == form(head + b'string s = "x"\n')
    assert form(head + b"if true\n    float x = 1\n") != form(head + b"if true\nfloat x = 1\n")
    assert form(head + b'string s = "1"\n') != form(head + b"float s = 1\n")
    for bad, reason in (
        (b'string s = "open\n', "unterminated_string"),
        (b"if true\n\tfloat x = 1\n", "unsupported_whitespace"),
        (b"for [i, v] in values\n    float x = 1\n", "unsupported_binding_form"),
        (b"float x = 1 @ 2\n", "unsupported_character"),
        (b"float close = 1\n", "declared_name_shadows_builtin"),
    ):
        with pytest.raises(matrix.NormalFormError) as info:
            form(bad)
        assert info.value.reason == reason


@pytest.mark.parametrize("variant", ["comment_only", "rename_only"])
def test_comment_or_rename_only_ordinary_sources_cannot_earn_two_coverage_credits(
    scratch, monkeypatch, variant,
):
    _, _, spec_path, index_path = tiny_inputs(scratch)
    assert invoke(spec_path, index_path, scratch / "variant-positive") == 0
    real_render = matrix.render_case
    rendered = []

    def input_ignoring_renderer(row):
        source, reserved, details = real_render(row)
        if not rendered:
            rendered.append(source)
            return source, reserved, details
        number = len(rendered)
        if variant == "comment_only":
            changed = (
                f"// label {number}\n".encode("utf-8") + rendered[0] + f"// tail {number}\n".encode("utf-8")
            )
        else:
            changed = rename_user_names(rendered[0], f"r{number}")
        rendered.append(changed)
        return changed, reserved, details

    monkeypatch.setattr(matrix, "render_case", input_ignoring_renderer)
    output = scratch / "variant-negative"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    assert actual_exit != 0 and manifest["generation"]["exitCode"] == actual_exit
    collision = next(
        issue for issue in manifest["generation"]["issues"]
        if issue["kind"] == "renderer_source_collision"
    )
    assert len(rendered) == 2 and rendered[0] != rendered[1]
    assert collision["firstSourceSha256"] == digest(rendered[0])
    assert collision["sourceSha256"] == digest(rendered[1]) != collision["firstSourceSha256"]
    assert collision["comparisonSha256"] == matrix.comparison_fingerprint(rendered[0]) == (
        matrix.comparison_fingerprint(rendered[1])
    )
    by_id = {row["id"]: row for row in manifest["cases"]}
    first, duplicate = by_id[collision["firstCaseId"]], by_id[collision["caseId"]]
    assert first["sourceSha256"] == collision["firstSourceSha256"]
    assert first["comparisonSha256"] == duplicate["comparisonSha256"] == collision["comparisonSha256"]
    assert duplicate["renderStatus"] == "INCOMPLETE"
    assert duplicate["sourcePath"] is duplicate["sourceSha256"] is None
    coverage = manifest["coverage"]
    assert coverage["positiveEmittedCount"] == sum(
        1 for row in manifest["cases"] if row["renderFamily"] == "ordinary" and row["sourcePath"]
    )
    assert coverage["positiveDomainStatus"] == coverage["status"] == "INCOMPLETE"


def test_unreadable_source_form_is_incomplete_with_a_named_reason(scratch, monkeypatch):
    _, _, spec_path, index_path = tiny_inputs(scratch)
    monkeypatch.setattr(
        matrix, "render_case", lambda row: (b'//@version=6\nstring s = "open\n', [], {}),
    )
    output = scratch / "unreadable"
    actual_exit = invoke(spec_path, index_path, output)
    manifest = read_json(output / "manifest.json")
    assert actual_exit != 0 and manifest["generation"]["exitCode"] == actual_exit
    issue = next(
        item for item in manifest["generation"]["issues"]
        if item["kind"] == "renderer_source_normal_form_unsupported"
    )
    assert issue["reason"] == "unterminated_string"
    row = next(item for item in manifest["cases"] if item["id"] == issue["caseId"])
    assert row["renderStatus"] == "INCOMPLETE" and row["sourcePath"] is None
    assert manifest["coverage"]["positiveDomainStatus"] == "INCOMPLETE"


def assert_same_eligible_set(oracle, production):
    assert oracle == production, (
        f"eligible set differs: only oracle {sorted(oracle - production)[:3]!r}, "
        f"only generator {sorted(production - oracle)[:3]!r}"
    )


def test_oracle_eligible_set_equals_the_generators_positive_set(source_inventory):
    oracle = oracle_eligible_set(source_inventory)
    production = production_positive_set(source_inventory)
    assert oracle and production
    assert_same_eligible_set(oracle, production)


def test_eligible_set_cross_check_fails_for_a_dropped_or_added_row(source_inventory):
    domains, _, _ = source_inventory
    oracle = oracle_eligible_set(source_inventory)
    production = production_positive_set(source_inventory)
    assert_same_eligible_set(oracle, production)
    with pytest.raises(AssertionError, match="eligible set differs"):
        assert_same_eligible_set(oracle - {min(oracle)}, production)
    names = list(domains)
    unsupported = next(
        values for values in itertools.product(*(domains[name] for name in names))
        if values not in production
    )
    with pytest.raises(AssertionError, match="eligible set differs"):
        assert_same_eligible_set(oracle, production | {unsupported})


@pytest.mark.parametrize("axis", SOURCE_AXES)
def test_each_axis_group_matches_the_generators_positive_groups(source_inventory, axis):
    domains, _, _ = source_inventory
    names = list(domains)
    production = production_positive_set(source_inventory)
    oracle_groups = list(axis_groups(domains, oracle_eligible_set(source_inventory), axis))
    assert oracle_groups, f"no eligible group for {axis}"
    assert oracle_groups == list(axis_groups(domains, production, axis))
    first = {tuple(row[name] for name in names) for row in oracle_groups[0]}
    assert len(list(axis_groups(domains, production - first, axis))) == len(oracle_groups) - 1


def test_every_row_the_generator_selects_is_oracle_eligible_and_renders(source_inventory):
    domains, _, _ = source_inventory
    names = list(domains)
    spec = matrix.validate_spec(read_json(SPEC_PATH))
    controls = len(spec["controls"])
    plan = matrix.coverage_plan(
        spec["levelOrder"], spec["rules"], core_axes=spec["coreAxes"], seed=spec["seed"],
        ordinary_limit=matrix.MAX_ORDINARY - controls,
        enumeration_limit=matrix.MAX_CANDIDATES - controls - len(spec["raw"]["requiredSeeds"]),
    )
    eligible = oracle_eligible_set(source_inventory)
    assert plan["selected"]
    for row in (*plan["candidateRows"], *plan["selected"]):
        assert tuple(row[name] for name in names) in eligible, row
    for row in plan["selected"]:
        matrix.render_case(row)  # a renderer hole raises; the oracle is not loosened


def test_runtime_engine_pin_is_absent_and_the_short_hash_is_unverified_input_provenance():
    raw = read_json(SPEC_PATH)
    identity = raw["provenance"]["runtimeEngineIdentity"]
    assert identity["runtimeEnginePin"] is None
    assert identity["runtimeEnginePinStatus"] == "ABSENT"
    assert identity["reportStatus"] == "UNVERIFIED_COMMUNITY_REPORT"
    assert identity["reportedShortHash"] == raw["seedInputProvenance"]["reportedEngine"]
    assert "runtimeEnginePin" not in raw["seedInputProvenance"]
