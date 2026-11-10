#!/usr/bin/env python3
"""Generate finite Pine inputs and evidence metadata; never invoke a compiler.

The ordinary corpus is the deduplicated union of a complete applicable
three-core-axis cross and a deterministic all-axis two-way covering set.
Constrained completion establishes targets without first materializing the
full Cartesian table. Coverage describes source metadata, never buildability
or TradingView values.

Consumer contract: read manifest.json, retain every positive case and verify
its sourceSha256. A positive row has expected.translation and expected.compile
both MUST_SUCCEED, including all mandatory witnesses. A missing sourcePath is
an incomplete input, never a skipped row. Feed exact UTF-8 source through the
public transpile() API and then tests._compile.compile_cpp(cpp, label=id,
extra_flags=STRATEGY_FP_FLAGS) in the existing CI rule. Pin the codegen,
paired engine, Eigen, generated headers and compiler identities. Missing
inputs, skipped compilation, zero cases or absent phase results are
INCOMPLETE. Preserve actual exits and diagnostics separately. No refusal of
a valid case is a successful compile. The generator supplies no such verdict.

CLI: python3 scripts/generate_compile_matrix.py --spec <spec.json>
--output-dir <new-directory> [--seed-index <index.json>]. The output parent
must already exist. An existing output directory is never reused.
manifest.candidates retains the bounded completion witnesses, not a purported
full Cartesian inventory. manifest.specification and coverage exclusions
retain the complete finite inventory, constraints and language evidence.
All results.cases records start
UNEXECUTED / NOT_MEASURED with null tool identities, exits and diagnostics.
Generation exit 0 means source generation completed, not compile acceptance;
2 means incomplete input or required source coverage,
and 1 means a generation or storage crash. Read generation.sourceStatus and
coverage.positiveDomainStatus separately from coverage.inventoryStatus; a
source-complete resolved positive domain can coexist with unresolved overall
inventory. Generation can finish for that resolved domain while coverage.status
and results.status remain INCOMPLETE. Every translation/compile result remains UNEXECUTED.

CGI interface: schemaVersion=1, generatorVersion=3. Ordinary positive rows
use renderFamily=ordinary, groups.core/groups.pairwise and zero-based
selectionOrder for each group. coverage.groups contains independently
recomputable targets, achieved/uncovered sets and counts. Top-level
coreTupleCount/coreCaseCount/pairwiseCaseCount/overlapCaseCount/
positiveUnionCount describe the planned sets; positiveEmittedCount,
ordinaryEmittedCount and totalEmittedCount describe sources actually written.
ordinarySelectedCount includes invalidControlCount; seeds are additional.
Read sourcePath and sourceSha256 on every row, including control-<id> rows
whose renderFamily=invalid_control. Their expectedDiagnosticCode and
expected.diagnosticCode name the required front-end PF-E, not an observation.
Preserve the control's exact source and catalog/test provenance. No source
generation field establishes a consumer verdict. Read real work result files.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
GENERATOR_VERSION = "3"
MAX_CANDIDATES = 100_000
MAX_ORDINARY = 512
MAX_SEEDS = 3
MAX_CONTROLS = 3
Pair = tuple[str, str, str, str]


class MatrixInputError(ValueError):
    """An input cannot be interpreted without changing its declared scope."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semantic_key(axes: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(axes.items()))


def case_id(axes: Mapping[str, str]) -> str:
    return "case-" + sha256(canonical_bytes({
        "renderFamily": "ordinary", "axes": dict(axes),
    }))


def pairs_for(axes: Mapping[str, str]) -> frozenset[Pair]:
    return frozenset(
        (left, a, right, b)
        for (left, a), (right, b) in itertools.combinations(axes.items(), 2)
    )


def pair_records(
    pairs: Iterable[Pair], domains: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, dict[str, str]]]:
    def order(pair):
        left, a, right, b = pair
        if domains is None:
            return pair
        names = list(domains)
        return names.index(left), names.index(right), domains[left].index(a), domains[right].index(b)
    return [
        {"left": {"axis": left, "level": a}, "right": {"axis": right, "level": b}}
        for left, a, right, b in sorted(pairs, key=order)
    ]


def finite_axes(raw: Any) -> dict[str, tuple[str, ...]]:
    if isinstance(raw, list):
        by_name = {}
        for axis in raw:
            if not isinstance(axis, dict) or set(axis) != {"name", "levels"}:
                raise MatrixInputError("each axes entry must contain name and levels")
            name = axis["name"]
            if not isinstance(name, str) or not name or name in by_name:
                raise MatrixInputError("axes contains an empty, non-string or repeated name")
            by_name[name] = axis["levels"]
        raw = by_name
    if not isinstance(raw, dict) or len(raw) < 2:
        raise MatrixInputError("axes must name at least two finite string level lists")
    if any(not isinstance(name, str) or not name for name in raw):
        raise MatrixInputError("an axis name must be a nonempty string")
    result = {}
    for name, levels in sorted(raw.items()):
        if not isinstance(name, str) or not name:
            raise MatrixInputError("an axis name must be a nonempty string")
        if not isinstance(levels, list) or not levels:
            raise MatrixInputError(f"axis {name!r} must have a nonempty level list")
        if any(not isinstance(level, str) or not level for level in levels):
            raise MatrixInputError(f"axis {name!r} has a non-string or empty level")
        if len(set(levels)) != len(levels):
            raise MatrixInputError(f"axis {name!r} repeats a level")
        result[name] = tuple(sorted(levels))
    return result


def validate_matches(
    raw: Any, axes: Mapping[str, Sequence[str]], *, label: str,
) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise MatrixInputError(f"{label}.matches must be an explicit object")
    if any(not isinstance(name, str) for name in raw):
        raise MatrixInputError(f"{label}.matches axis names must be strings")
    result = {}
    for name, levels in sorted(raw.items()):
        if name not in axes:
            raise MatrixInputError(f"{label}.matches names unknown axis {name!r}")
        if not isinstance(levels, list) or not levels:
            raise MatrixInputError(f"{label}.matches.{name} must be a nonempty list")
        if any(not isinstance(level, str) or level not in axes[name] for level in levels):
            raise MatrixInputError(f"{label}.matches.{name} names an unknown level")
        if len(set(levels)) != len(levels):
            raise MatrixInputError(f"{label}.matches.{name} repeats a level")
        result[name] = tuple(sorted(levels))
    return result


def matches(axes: Mapping[str, str], rule: Mapping[str, Sequence[str]]) -> bool:
    """Conjunction across axes; membership within each allowed-level list."""
    return all(axes[name] in levels for name, levels in rule.items())


def seeded_key(axes: Mapping[str, str], seed: str) -> str:
    """SPEC .3's exact UTF-8 seed, LF, canonical named-axis JSON rank."""
    return sha256(seed.encode("utf-8") + b"\n" + canonical_bytes(dict(axes)))


def declared_key(axes: Mapping[str, str], domains: Mapping[str, Sequence[str]]) -> tuple[int, ...]:
    return tuple(domains[name].index(axes[name]) for name in domains)


class CompletionSearch:
    """Memoized finite search, bounded by actual retained complete witnesses.

    Every target tuple/pair receives either a complete positive witness or an
    exhausted-search exclusion with its blocking language constraints. The
    Cartesian product and partial-node counts are reported separately. The
    candidate cap is not applied to either of those numbers. A cap violation
    raises; it is never interpreted as an infeasible pair.
    """

    def __init__(
        self, axes: Mapping[str, Sequence[str]], rules: Sequence[Mapping[str, Any]],
        *, limit: int,
    ) -> None:
        if type(limit) is not int or not 0 < limit <= MAX_CANDIDATES:
            raise MatrixInputError(f"enumeration bound must be in 1..{MAX_CANDIDATES}")
        self.axes = {name: tuple(levels) for name, levels in axes.items()}
        self.rules = rules
        self.cache: dict[tuple, tuple[dict[str, str] | None, frozenset[str]]] = {}
        self.inventory_cache: dict[tuple, dict[str, str] | None] = {}
        self.witnesses: dict[str, dict[str, str]] = {}
        self.stats = {
            "candidateLimit": limit, "candidateCount": 0, "partialAssignmentsVisited": 0,
            "completeAssignmentsClassified": 0, "completionQueries": 0,
            "cacheHits": 0, "candidateLimitExceeded": False,
            "inventoryAssignmentsVisited": 0, "inventoryQueries": 0,
            "attemptedCandidateCountAtFailure": None, "rejectedCandidateId": None,
            "regionSearches": 0,
        }

    def complete(
        self, fixed: Mapping[str, str], *, retain: bool = True,
    ) -> tuple[dict[str, str] | None, frozenset[str]]:
        if any(name not in self.axes or value not in self.axes[name] for name, value in fixed.items()):
            raise MatrixInputError("completion query names an unknown axis or level")
        self.stats["completionQueries"] += 1
        row, blockers = self._visit(dict(fixed))
        if row is not None and retain:
            self._retain(row)
        return row, blockers

    def _retain(self, row: dict[str, str]) -> None:
        name = case_id(row)
        if name not in self.witnesses and len(self.witnesses) >= self.stats["candidateLimit"]:
            self.stats["candidateLimitExceeded"] = True
            self.stats["attemptedCandidateCountAtFailure"] = len(self.witnesses) + 1
            self.stats["rejectedCandidateId"] = name
            raise MatrixInputError(
                f"actual complete candidate pool would contain {len(self.witnesses) + 1} "
                f"assignments, exceeding bound {self.stats['candidateLimit']}"
            )
        self.witnesses[name] = row
        self.stats["candidateCount"] = len(self.witnesses)

    def region(
        self, match: Mapping[str, Sequence[str]], *, retain: bool = True,
    ) -> tuple[dict[str, str] | None, frozenset[str]]:
        """Canonical completion of a positive input fact's selector."""
        if any(name not in self.axes for name in match):
            raise MatrixInputError("positive fact selector names an unknown axis")
        domains = {
            name: tuple(value for value in levels if name not in match or value in match[name])
            for name, levels in self.axes.items()
        }
        if any(not levels for levels in domains.values()):
            raise MatrixInputError("positive fact selector has an empty axis domain")
        # Restrict domains before searching, so a later selector axis cannot
        # take precedence over an earlier unrestricted axis in the ordering.
        scoped = CompletionSearch(domains, self.rules, limit=self.stats["candidateLimit"])
        row, blockers = scoped.complete({}, retain=False)
        self.stats["regionSearches"] += 1
        for field in ("partialAssignmentsVisited", "completeAssignmentsClassified", "completionQueries", "cacheHits"):
            self.stats[field] += scoped.stats[field]
        if row is not None and retain:
            self._retain(row)
        return row, blockers

    def explain(
        self, selectors: Mapping[str, Any], blockers: Iterable[str], **fields: Any,
    ) -> dict[str, Any]:
        """Classify a nonpositive selector by real complete language witnesses.

        Mixed invalid/inapplicable extensions remain inventory; an unresolved
        extension is called out separately. This search never adds positive
        candidates and never opens or executes a generated source.
        """
        names = [name for name in self.axes if name in selectors]
        domains = [
            (selectors[name],) if isinstance(selectors[name], str) else selectors[name]
            for name in names
        ]
        for classification in ("unresolved", "invalid", "inapplicable"):
            for values in itertools.product(*domains):
                self.stats["inventoryQueries"] += 1
                witness = self._inventory_visit(dict(zip(names, values)), classification)
                if witness is not None:
                    record = classify_candidate(witness, self.rules)
                    return excluded_record(
                        selectors, blockers, self.rules,
                        status="invalid_only" if classification == "invalid" else classification,
                        classificationWitness={
                            "axes": witness, "legality": record["legality"],
                            "constraints": record["constraints"],
                            "decisiveConstraints": record["decisiveConstraints"],
                        },
                        **fields,
                    )
        raise MatrixInputError("excluded selector has no complete classification witness")

    def _inventory_visit(self, fixed: dict[str, str], target: str) -> dict[str, str] | None:
        key = (target, semantic_key(fixed))
        if key in self.inventory_cache:
            return self.inventory_cache[key]
        self.stats["inventoryAssignmentsVisited"] += 1
        forced = {item["classification"] for item in stable_partial_facts(fixed, self.rules).values()}
        higher = {
            "unresolved": {"inapplicable", "invalid"},
            "invalid": {"inapplicable"}, "inapplicable": set(),
        }[target]
        possible = any(partial_matches(fixed, rule["matches"]) for rule in self.rules)
        result = None
        if not (forced & higher) and possible:
            if len(fixed) == len(self.axes):
                if classify_candidate(fixed, self.rules)["legality"] == target:
                    result = {name: fixed[name] for name in self.axes}
            else:
                name = next(name for name in self.axes if name not in fixed)
                for value in self.axes[name]:
                    result = self._inventory_visit({**fixed, name: value}, target)
                    if result is not None:
                        break
        self.inventory_cache[key] = result
        return result

    def _visit(self, fixed: dict[str, str]) -> tuple[dict[str, str] | None, frozenset[str]]:
        key = semantic_key(fixed)
        if key in self.cache:
            self.stats["cacheHits"] += 1
            return self.cache[key]
        self.stats["partialAssignmentsVisited"] += 1
        blockers = partial_exclusions(fixed, self.rules)
        if blockers:
            result = (None, frozenset(blockers))
        elif len(fixed) == len(self.axes):
            self.stats["completeAssignmentsClassified"] += 1
            record = classify_candidate(fixed, self.rules)
            result = (
                ({name: fixed[name] for name in self.axes}, frozenset())
                if record["legality"] == "valid"
                else (None, frozenset(record["decisiveConstraints"]))
            )
        else:
            name = next(name for name in self.axes if name not in fixed)
            levels = self.axes[name]
            rejected: set[str] = set()
            result = (None, frozenset())
            for value in levels:
                witness, reasons = self._visit({**fixed, name: value})
                if witness is not None:
                    result = (witness, frozenset())
                    break
                rejected.update(reasons)
            if result[0] is None:
                result = (None, frozenset(rejected))
        self.cache[key] = result
        return result


def partial_matches(fixed: Mapping[str, str], rule: Mapping[str, Sequence[str]]) -> bool:
    return all(name not in fixed or fixed[name] in levels for name, levels in rule.items())


def coverage_plan(
    axes: Mapping[str, Sequence[str]], rules: Sequence[Mapping[str, Any]],
    *, core_axes: Sequence[str], seed: str, ordinary_limit: int = MAX_ORDINARY,
    enumeration_limit: int = MAX_CANDIDATES,
    search: CompletionSearch | None = None,
) -> dict[str, Any]:
    """Complete the core cross, then greedily cover actual extendable pairs.

    Core and pairwise membership are independent, with their overlap retained.
    The union budget counts an overlapping row once. No seed supplies ordinary
    coverage. Infeasible tuples and pairs are inventories, never compile rows.
    """
    if (
        len(core_axes) != 3 or len(set(core_axes)) != 3
        or any(name not in axes for name in core_axes)
    ):
        raise MatrixInputError("the full core cross must name three distinct declared axes")
    if type(ordinary_limit) is not int or not 0 <= ordinary_limit <= MAX_ORDINARY:
        raise MatrixInputError(f"ordinary limit must be in 0..{MAX_ORDINARY}")
    if not isinstance(seed, str) or not seed:
        raise MatrixInputError("selection seed must be an explicit nonempty string")
    core_product = math.prod(len(axes[name]) for name in core_axes)
    pair_product = sum(
        len(axes[left]) * len(axes[right])
        for left, right in itertools.combinations(sorted(axes), 2)
    )
    solver = search or CompletionSearch(axes, rules, limit=enumeration_limit)
    core: dict[str, dict[str, str]] = {}
    target_core: list[dict[str, str]] = []
    excluded_core = []
    for values in itertools.product(*(axes[name] for name in core_axes)):
        fixed = dict(zip(core_axes, values))
        row, blockers = solver.complete(fixed)
        if row is None:
            excluded_core.append(solver.explain(fixed, blockers))
        else:
            target_core.append(fixed)
            core[case_id(row)] = row
    target_pairs: set[Pair] = set()
    excluded_pairs = []
    pair_inventory = []
    for left, right in itertools.combinations(axes, 2):
        for a, b in itertools.product(axes[left], axes[right]):
            row, blockers = solver.complete({left: a, right: b})
            pair = next(iter(pairs_for({left: a, right: b})))
            if row is None:
                exclusion = solver.explain(
                    {left: a, right: b}, blockers,
                    pair=pair_records([pair], axes)[0],
                )
                excluded_pairs.append(exclusion)
                witness = exclusion["classificationWitness"]
                pair_inventory.append({
                    "pair": exclusion["pair"], "classification": exclusion["status"],
                    "witnessAxes": witness["axes"], "constraintIds": witness["constraints"],
                    "evidence": exclusion["evidence"],
                })
            else:
                target_pairs.add(pair)
                record = classify_candidate(row, rules)
                pair_inventory.append({
                    "pair": pair_records([pair], axes)[0], "classification": "positive_target",
                    "witnessAxes": row, "constraintIds": record["constraints"], "evidence": record["evidence"],
                })
    target_facts = {
        *(f"form:{value}" for value in axes.get("statement_kind", ())),
    }
    for name, levels in axes.items():
        for value in levels:
            row, _ = solver.complete({name: value})
            if row is not None:
                target_facts.add(f"axis:{name}={value}")
    role_inventory = []
    for rule in rules:
        if rule["classification"] != "valid":
            continue
        row, blockers = solver.region(rule["matches"])
        if row is not None:
            target_facts.add(f"role:{rule['id']}")
        else:
            role_inventory.append(solver.explain(rule["matches"], blockers, role=rule["id"]))
    pool = dict(solver.witnesses)
    covered_by = {name: pairs_for(row) for name, row in pool.items()}
    selected_core = list(core)
    union = set(selected_core)
    pairwise: list[str] = []
    remaining = set(target_pairs)
    facts_by_id = {
        name: positive_row_facts(row, core_axes, rules) for name, row in pool.items()
    }
    remaining_facts = set(target_facts)
    available = set(pool)
    while remaining or remaining_facts:
        if not available:
            break
        best = min(
            available,
            key=lambda name: (
                -len(covered_by[name] & remaining),
                -len(facts_by_id[name] & remaining_facts),
                seeded_key(pool[name], seed), declared_key(pool[name], axes),
            ),
        )
        if not (covered_by[best] & remaining or facts_by_id[best] & remaining_facts):
            break
        pairwise.append(best)
        union.add(best)
        remaining.difference_update(covered_by[best])
        remaining_facts.difference_update(facts_by_id[best])
        available.remove(best)
    selected = sorted(union)
    achieved_core = [
        {name: pool[identifier][name] for name in core_axes} for identifier in selected_core
    ]
    return {
        "coreIds": selected_core, "pairwiseIds": pairwise,
        "overlapIds": sorted(set(selected_core) & set(pairwise)),
        "selected": [pool[name] for name in selected],
        "candidateRows": sorted(pool.values(), key=semantic_key),
        "targetCore": target_core, "achievedCore": achieved_core,
        "uncoveredCore": [
            row for row in target_core if row not in achieved_core
        ],
        "excludedCore": excluded_core,
        "targetPairs": target_pairs, "achievedPairs": target_pairs - remaining,
        "uncoveredPairs": remaining, "excludedPairs": excluded_pairs,
        "coreProduct": core_product, "pairQueryProduct": pair_product,
        "search": solver.stats,
        "pairInventory": pair_inventory,
        "targetFacts": target_facts, "achievedFacts": target_facts - remaining_facts,
        "uncoveredFacts": remaining_facts, "roleInventory": role_inventory,
        "capSatisfied": len(selected) <= ordinary_limit,
        "complete": (
            bool(selected) and not remaining and not remaining_facts
            and len(selected_core) == len(core) and len(selected) <= ordinary_limit
        ),
    }


def checked_path(raw: str | Path) -> Path:
    """Refuse sensitive names and symlinks before opening any input."""
    original = Path(raw)
    path = Path(os.path.abspath(original))
    for candidate in (original, path):
        parts = tuple(part.casefold() for part in candidate.parts)
        if any(
            part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
            for part in parts
        ):
            raise MatrixInputError(f"refused path by name: {original}")
        for index in range(len(parts)):
            if parts[index:index + 3] == (".config", "zsh", "keys"):
                raise MatrixInputError(f"refused path by name: {original}")
            if parts[index:index + 2] == (".config", "pineforge"):
                raise MatrixInputError(f"refused path by name: {original}")
    # Walk from the filesystem root, checking the link itself at each step.
    # A symlink cannot redirect the later file open into a refused tree.
    for prefix in (*reversed(path.parents), path):
        if prefix.is_symlink():
            raise MatrixInputError(f"refused symlink path: {prefix}")
    return path


class InputFiles:
    """Announce every explicit file before reading it; no recursive discovery."""

    def __init__(self) -> None:
        self.identities: dict[str, str] = {}

    def read(self, raw: str | Path, *, identity: str) -> bytes:
        path = checked_path(raw)
        print(f"READ {path}", file=sys.stderr, flush=True)
        data = path.read_bytes()
        self.identities[identity] = sha256(data)
        return data

    def json(self, raw: str | Path, *, identity: str) -> tuple[Any, bytes]:
        data = self.read(raw, identity=identity)
        try:
            value = json.loads(
                data.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_invalid_json_constant,
            )
        except (ValueError, UnicodeError) as exc:
            raise MatrixInputError(f"invalid JSON in {identity}: {exc}") from exc
        return value, data


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise MatrixInputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> Any:
    raise MatrixInputError(f"non-finite JSON constant {value!r}")


CLASSIFICATIONS = {"valid", "invalid", "inapplicable", "unresolved", "valid_unsupported"}
EVIDENCE_TIERS = ("tv_observed", "pine_documentation", "implementation_observation")
RENDER_LEVELS = {
    "statement_kind": {
        "if", "if_else", "ternary", "switch", "for", "while",
        "function_definition", "method", "var", "varip", "tuple_return",
    },
    "body_layout": {"single_line", "indented"},
    "primitive_type": {"int", "float", "bool", "string", "color"},
    "value_shape": {
        "scalar", "array", "map", "matrix", "udt_field_scalar",
        "udt_field_array", "udt_field_map", "udt_field_matrix",
    },
    "missing_value_position": {
        "none", "branch_order", "return", "initializer", "argument",
        "collection_handle", "collection_element", "udt_field",
    },
    "missing_predicate_target": {
        "none", "container_handle", "container_element", "udt_field_container",
    },
    "map_key_type": {
        "none", "int", "float", "bool", "string", "color",
        "array", "map", "matrix", "udt",
    },
}


def validate_spec(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or type(raw.get("schemaVersion")) is not int:
        raise MatrixInputError("spec requires an integer schemaVersion")
    if raw["schemaVersion"] != SCHEMA_VERSION:
        raise MatrixInputError(f"unknown spec schemaVersion {raw['schemaVersion']!r}")
    if not isinstance(raw.get("draftRevision"), str) or not raw["draftRevision"]:
        raise MatrixInputError("spec requires a nonempty draftRevision")
    axes = finite_axes(raw.get("axes"))
    declared_axes = (
        {axis["name"]: tuple(axis["levels"]) for axis in raw["axes"]}
        if isinstance(raw["axes"], list)
        else {name: tuple(raw["axes"][name]) for name in raw["axes"]}
    )
    policy = raw.get("classificationPolicy")
    if not isinstance(policy, dict) or (
        policy.get("evidencePrecedence") != list(EVIDENCE_TIERS)
        or policy.get("defaultClassification") != "valid"
        or policy.get("defaultExpectedOutcome") != "valid_intended_supported"
        or policy.get("constraintEvidenceDefault") != "pine_documentation"
        or policy.get("constraintFactField") != "fact"
    ):
        raise MatrixInputError("spec requires the .3 evidence precedence and default-positive policy")
    enumeration = raw.get("enumerationPolicy")
    if not isinstance(enumeration, dict) or (
        enumeration.get("mode") != "core_full_cross_union_all_axes_pairwise"
        or enumeration.get("coreAxes") != ["statement_kind", "body_layout", "primitive_type"]
        or enumeration.get("pairwiseAxes") != list(declared_axes)
        or not isinstance(enumeration.get("seed"), str) or not enumeration["seed"]
    ):
        raise MatrixInputError("spec requires the .3 core/all-axis policy and explicit string seed")
    if any(name not in axes for name in enumeration["coreAxes"]):
        raise MatrixInputError("core axes are absent from the finite inventory")
    fact_policy = raw.get("inputFactPolicy")
    fact_groups = {
        "positive", "explicitInvalid", "inapplicableInventory",
        "unresolvedEvidence", "invalidInventory",
    }
    if not isinstance(fact_policy, dict) or any(
        not isinstance(fact_policy.get(name), str) or not fact_policy[name] for name in fact_groups
    ):
        raise MatrixInputError("spec requires separate positive, invalid, inapplicable and unresolved fact policies")
    if fact_policy.get("positiveFactKinds") != ["axis_level", "statement_form", "valid_constraint"]:
        raise MatrixInputError("spec requires the .3 positive fact kinds")
    if (
        not isinstance(fact_policy.get("sourceObligations"), list)
        or not fact_policy["sourceObligations"]
        or any(not isinstance(value, str) or not value for value in fact_policy["sourceObligations"])
    ):
        raise MatrixInputError("spec requires explicit positive source obligations")
    bounds = raw.get("bounds")
    expected_bounds = {
        "candidate100000": MAX_CANDIDATES, "ordinary512": MAX_ORDINARY,
        "seeds3": MAX_SEEDS, "total515": MAX_ORDINARY + MAX_SEEDS,
    }
    if bounds != expected_bounds or any(type(value) is not int for value in bounds.values()):
        raise MatrixInputError(f"spec bounds must be {expected_bounds!r}")
    constraints = raw.get("constraints")
    if not isinstance(constraints, list):
        raise MatrixInputError("constraints must be an explicit list")
    rules = []
    ids: set[str] = set()
    for entry in constraints:
        if not isinstance(entry, dict):
            raise MatrixInputError("a constraint must be an object")
        unknown = set(entry) - {
            "id", "fact", "matches", "classification", "reason", "languageBasis",
            "evidenceTier", "receipt",
        }
        if unknown:
            raise MatrixInputError(f"unknown constraint fields: {sorted(unknown)!r}")
        name = entry.get("id")
        if not isinstance(name, str) or not name or name in ids:
            raise MatrixInputError("constraint ids must be nonempty and unique")
        ids.add(name)
        if not isinstance(entry.get("fact"), str) or not entry["fact"]:
            raise MatrixInputError(f"constraint {name!r} requires its semantic fact")
        if entry.get("evidenceTier", "pine_documentation") not in (*EVIDENCE_TIERS, None):
            raise MatrixInputError(f"constraint {name!r} has an unknown evidence tier")
        if entry.get("classification") not in CLASSIFICATIONS:
            raise MatrixInputError(f"constraint {name!r} has an unknown classification")
        for field in ("reason", "languageBasis"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise MatrixInputError(f"constraint {name!r} requires {field}")
        rule = {
            **entry,
            "matches": validate_matches(entry.get("matches"), axes, label=name),
        }
        # The .3 constraint wire declares documentation by default. It does
        # not provide measured TV receipts; issue-author claims stay separate.
        rule["_resolution"] = resolve_fact_evidence([{
            "tier": entry.get("evidenceTier", policy["constraintEvidenceDefault"]),
            "classification": entry["classification"], "source": entry["languageBasis"],
            "receipt": entry.get("receipt"),
        }])
        rules.append(rule)
    outcomes = raw.get("expectedOutcomes")
    if not isinstance(outcomes, list):
        raise MatrixInputError("expectedOutcomes must be an explicit list")
    by_class = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise MatrixInputError("an expected outcome must be an object")
        classification = outcome.get("classification")
        if not isinstance(classification, str) or classification in by_class:
            raise MatrixInputError("expected outcome classifications must be unique strings")
        for field in ("translation", "compile", "tv"):
            if not isinstance(outcome.get(field), str) or not outcome[field]:
                raise MatrixInputError(f"expected outcome {classification!r} requires {field}")
        if outcome["tv"] != "NOT_MEASURED":
            raise MatrixInputError("source generation cannot supply measured TV evidence")
        by_class[classification] = outcome
    for name in ("valid_intended_supported", "valid_unsupported", "language_invalid", "unresolved"):
        if name not in by_class:
            raise MatrixInputError(f"missing expected outcome {name!r}")
    positive = by_class["valid_intended_supported"]
    if (positive["translation"], positive["compile"]) != ("MUST_SUCCEED", "MUST_SUCCEED"):
        raise MatrixInputError("a positive case must require real translation and compilation")
    if (
        by_class["valid_unsupported"]["translation"],
        by_class["valid_unsupported"]["compile"],
    ) != ("MUST_SUCCEED", "MUST_SUCCEED"):
        raise MatrixInputError("implementation gaps cannot relax a valid case's desired outcome")
    if (
        by_class["language_invalid"]["translation"],
        by_class["language_invalid"]["compile"],
    ) != ("MUST_REJECT_WITH_EXPECTED_PF_E", "NOT_APPLICABLE"):
        raise MatrixInputError("invalid controls must require their explicit expected PF-E code")
    seeds = raw.get("requiredSeeds")
    if (
        not isinstance(seeds, list) or len(seeds) != MAX_SEEDS
        or any(not safe_id(seed) for seed in seeds) or len(set(seeds)) != MAX_SEEDS
    ):
        raise MatrixInputError("requiredSeeds must name exactly three distinct safe ids")
    families = raw.get("renderFamilies")
    if not isinstance(families, list) or any(not isinstance(item, dict) for item in families):
        raise MatrixInputError("renderFamilies must be a list of objects")
    names = [item.get("id") for item in families]
    if (
        any(not isinstance(name, str) for name in names)
        or len(set(names)) != len(names) or set(names) != {"ordinary", "invalid_control", "community_seed"}
    ):
        raise MatrixInputError(f"unknown or missing renderFamilies: {names!r}")
    for family in families:
        required = {
            "ordinary": ("generated", MAX_ORDINARY),
            "invalid_control": ("explicit_expected_refusal", MAX_CONTROLS),
            "community_seed": ("required_seed", MAX_SEEDS),
        }[family["id"]]
        if (family.get("kind"), family.get("maxCases")) != required:
            raise MatrixInputError(f"render family {family['id']!r} changes the decided bounds")
    controls = raw.get("invalidControls")
    if not isinstance(controls, list) or len(controls) != MAX_CONTROLS:
        raise MatrixInputError("spec must declare exactly three explicit invalid controls")
    control_ids = set()
    for control in controls:
        if not isinstance(control, dict) or not safe_id(control.get("id")):
            raise MatrixInputError("invalid control requires a safe id")
        if control["id"] in control_ids:
            raise MatrixInputError("invalid control ids must be unique")
        control_ids.add(control["id"])
        if control.get("renderFamily") != "invalid_control" or control.get("expectedOutcome") != "language_invalid":
            raise MatrixInputError("invalid control changes its render family or expected outcome")
        for field in (
            "source", "expectedDiagnosticCode", "expectedDiagnosticMessage",
            "languageBasis", "catalogSource", "testSource", "expectedDiagnosticPhase",
            "expectedDiagnosticHint", "emitterSource", "languageReference", "invalidInputFact",
        ):
            if not isinstance(control.get(field), str) or not control[field]:
                raise MatrixInputError(f"invalid control {control['id']} requires {field}")
        code = control["expectedDiagnosticCode"]
        if (
            not code.startswith("PF-E") or len(code) <= 4
            or any(character not in "0123456789" for character in code[4:])
        ):
            raise MatrixInputError(f"invalid control {control['id']} requires a literal PF-E code")
        if not control["catalogSource"].endswith("#/codes/" + code):
            raise MatrixInputError(f"invalid control {control['id']} catalog provenance disagrees with its code")
        values = control.get("axes")
        if not isinstance(values, dict) or set(values) != set(axes) or any(
            values[name] not in axes[name] for name in axes
        ):
            raise MatrixInputError(f"invalid control {control['id']} must bind every declared axis")
        if "control-" + control["id"] in seeds:
            raise MatrixInputError("invalid control and community seed ids collide")
    unresolved = raw.get("unresolvedEvidence")
    if not isinstance(unresolved, list) or any(not isinstance(item, dict) for item in unresolved):
        raise MatrixInputError("unresolvedEvidence must remain an explicit inventory list")
    for item in unresolved:
        if not safe_id(item.get("id")) or not isinstance(item.get("reason"), str) or not item["reason"]:
            raise MatrixInputError("unresolved evidence requires an id and reason")
        if not isinstance(item.get("constraintIds"), list) or any(
            name not in ids for name in item["constraintIds"]
        ):
            raise MatrixInputError("unresolved evidence names an unknown constraint")
    layouts = raw.get("layoutApplicability")
    if not isinstance(layouts, list) or any(not isinstance(item, dict) for item in layouts):
        raise MatrixInputError("layoutApplicability must remain an explicit language inventory")
    layout_forms = []
    for item in layouts:
        forms = item.get("statementKinds")
        applicable = item.get("applicable")
        if (
            not isinstance(forms, list) or not forms
            or any(form not in axes["statement_kind"] for form in forms)
            or not isinstance(applicable, list) or not applicable
            or any(layout not in axes["body_layout"] for layout in applicable)
            or not isinstance(item.get("languageBasis"), str) or not item["languageBasis"]
        ):
            raise MatrixInputError("layout applicability must name declared forms/layouts and a language basis")
        layout_forms.extend(forms)
    if len(layout_forms) != len(set(layout_forms)) or set(layout_forms) != set(axes["statement_kind"]):
        raise MatrixInputError("layout applicability must retain every requested form exactly once")
    return {
        "raw": raw, "axes": axes, "rules": rules,
        "outcomes": by_class, "levelOrder": declared_axes,
        "coreAxes": tuple(enumeration["coreAxes"]), "seed": enumeration["seed"],
        "controls": controls,
    }


def safe_id(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value)
        and value[0].isascii() and value[0].isalnum()
        and all(char.isascii() and (char.isalnum() or char in "_-") for char in value)
    )


def resolve_fact_evidence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve one semantic fact; independent facts never override each other.

    The current .3 wire supplies documentation constraints. This helper also
    makes precedence explicit for existing normalized TV/implementation facts;
    a TV claim without a source and receipt is unresolved, never a measurement.
    """
    if not records:
        return {"classification": "valid", "tier": None, "conflict": False, "records": []}
    if any(
        item.get("tier") not in (*EVIDENCE_TIERS, None) or item.get("classification") not in CLASSIFICATIONS
        for item in records
    ):
        raise MatrixInputError("unknown evidence tier or classification")
    def available_tier(item):
        if item["tier"] == "tv_observed" and (
            not isinstance(item.get("source"), str) or not item["source"]
            or not isinstance(item.get("receipt"), str) or not item["receipt"]
        ):
            return None
        return item["tier"]

    tier = min((available_tier(item) for item in records), key=evidence_rank)
    strongest = [item for item in records if available_tier(item) == tier]
    labels = set()
    for item in strongest:
        if tier is None:
            labels.add("unresolved")
        elif tier == "implementation_observation":
            labels.add("valid")
        elif tier == "tv_observed" and (
            not isinstance(item.get("source"), str) or not item["source"]
            or not isinstance(item.get("receipt"), str) or not item["receipt"]
        ):
            labels.add("unresolved")
        else:
            labels.add("valid" if item["classification"] == "valid_unsupported" else item["classification"])
    return {
        "classification": next(iter(labels)) if len(labels) == 1 else "unresolved",
        "tier": tier, "conflict": len(labels) != 1,
        "records": [dict(item) for item in records],
    }


def rule_resolution(rule: Mapping[str, Any]) -> dict[str, Any]:
    if "_resolution" in rule:
        return rule["_resolution"]
    return resolve_fact_evidence([{
        "tier": rule.get("evidenceTier", "pine_documentation"), "classification": rule["classification"],
        "source": rule["languageBasis"],
        "receipt": rule.get("receipt"),
    }])


def evidence_rank(tier: str | None) -> int:
    return EVIDENCE_TIERS.index(tier) if tier is not None else len(EVIDENCE_TIERS)


def rule_evidence(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tier": rule.get("evidenceTier", "pine_documentation"),
        "classification": rule["classification"], "source": rule["languageBasis"],
        "receipt": rule.get("receipt"), "constraintId": rule["id"],
    }


def stable_partial_facts(
    fixed: Mapping[str, str], rules: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for rule in rules:
        if partial_matches(fixed, rule["matches"]):
            grouped.setdefault(rule.get("fact", rule["id"]), []).append(rule)
    result = {}
    for fact, possible in grouped.items():
        forced = [rule for rule in possible if set(rule["matches"]) <= set(fixed)]
        if not forced:
            continue
        rank = min(evidence_rank(rule.get("evidenceTier", "pine_documentation")) for rule in forced)
        if any(
            set(rule["matches"]) - set(fixed)
            and evidence_rank(rule.get("evidenceTier", "pine_documentation")) <= rank
            for rule in possible
        ):
            continue
        result[fact] = resolve_fact_evidence([rule_evidence(rule) for rule in forced])
    return result


def partial_exclusions(
    fixed: Mapping[str, str], rules: Sequence[Mapping[str, Any]],
) -> list[str]:
    return sorted({
        item["constraintId"]
        for resolved in stable_partial_facts(fixed, rules).values()
        if resolved["classification"] != "valid"
        for item in resolved["records"]
    })


def classify_candidate(
    axes: Mapping[str, str], rules: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    applicable = [rule for rule in rules if matches(axes, rule["matches"])]
    labels = {rule["classification"] for rule in applicable}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in applicable:
        grouped.setdefault(rule.get("fact", rule["id"]), []).append(rule_evidence(rule))
    resolved = {fact: resolve_fact_evidence(items) for fact, items in grouped.items()}
    classes = {item["classification"] for item in resolved.values()}
    legality = next(
        (label for label in ("inapplicable", "invalid", "unresolved") if label in classes),
        "valid",
    )
    return {
        "id": case_id(axes), "axes": dict(axes), "legality": legality,
        "constraints": [rule["id"] for rule in applicable],
        "constraintClassifications": sorted(labels),
        "decisiveConstraints": sorted(
            record["constraintId"] for item in resolved.values()
            if item["classification"] == legality for record in item["records"]
        ),
        "evidence": resolved,
        "classificationBasis": (
            "highest_evidence_per_fact; independent_restrictions_retained"
            if labels else "unclassified_defaults_valid"
        ),
    }


def excluded_record(
    axes: Mapping[str, Any], blockers: Iterable[str], rules: Sequence[Mapping[str, Any]],
    **fields: Any,
) -> dict[str, Any]:
    names = set(blockers)
    evidence = [
        {
            "id": rule["id"], "matches": dict(rule["matches"]),
            "classification": rule_resolution(rule)["classification"],
            "reason": rule["reason"], "languageBasis": rule["languageBasis"],
        }
        for rule in rules if rule["id"] in names
    ]
    labels = {item["classification"] for item in evidence}
    # An excluded selector may have several kinds of extension. Retain every
    # blocking class rather than claiming a homogeneous Cartesian region.
    status = (
        "unresolved" if "unresolved" in labels else
        "invalid_only" if "invalid" in labels else "inapplicable"
    )
    return {
        "axes": dict(axes), "status": status, "blockingConstraints": sorted(names),
        "blockingClassifications": sorted(labels), "evidence": evidence, **fields,
    }


def core_fact(axes: Mapping[str, str], core_axes: Sequence[str]) -> str:
    return "core:" + canonical_bytes({name: axes[name] for name in core_axes}).decode("utf-8")


def positive_row_facts(
    axes: Mapping[str, str], core_axes: Sequence[str], rules: Sequence[Mapping[str, Any]],
) -> set[str]:
    return {
        *(f"axis:{name}={value}" for name, value in axes.items()),
        *(("form:" + axes["statement_kind"],) if "statement_kind" in axes else ()),
        *(f"role:{rule['id']}" for rule in rules
          if rule["classification"] == "valid"
          and matches(axes, rule["matches"])),
    }


def input_fact_records(
    facts: Iterable[str], axes: Mapping[str, Sequence[str]], rules: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    remaining = set(facts)
    records = []
    for name, levels in axes.items():
        for level in levels:
            identity = f"axis:{name}={level}"
            if identity in remaining:
                records.append({"id": identity, "kind": "axis_level", "axis": name, "level": level})
                remaining.remove(identity)
    for form in axes.get("statement_kind", ()):
        identity = f"form:{form}"
        if identity in remaining:
            records.append({"id": identity, "kind": "statement_form", "statement_kind": form})
            remaining.remove(identity)
    for rule in rules:
        identity = f"role:{rule['id']}"
        if identity in remaining:
            records.append({"id": identity, "kind": "valid_constraint", "constraintId": rule["id"]})
            remaining.remove(identity)
    if remaining:
        raise MatrixInputError(f"unrecognized positive input facts: {sorted(remaining)!r}")
    return records


def expected_for(legality: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    if legality == "inapplicable":
        return {
            "classification": "inapplicable", "translation": "NOT_APPLICABLE",
            "compile": "NOT_APPLICABLE", "tv": "NOT_MEASURED",
        }
    name = {
        "valid": "valid_intended_supported", "invalid": "language_invalid",
        "unresolved": "unresolved", "valid_unsupported": "valid_unsupported",
    }[legality]
    return dict(spec["outcomes"][name])


def render_case(axes: Mapping[str, str]) -> tuple[bytes, list[str], dict[str, Any]]:
    """Render axis-dependent values that flow into a strategy condition.

    Raw missing branches and arguments are intentional. Do not replace them
    with casts to accommodate a translator's present inference.

    The middle return slot is reserved for scope issues; this renderer has no
    defined producer for it. An empty slot is not evidence of source fidelity.
    Source distinctions are checked separately, before emitted coverage.
    """
    if set(axes) != set(RENDER_LEVELS):
        raise MatrixInputError(
            f"renderer axis mismatch: missing={sorted(set(RENDER_LEVELS) - set(axes))}, "
            f"unknown={sorted(set(axes) - set(RENDER_LEVELS))}"
        )
    for name, level in axes.items():
        if level not in RENDER_LEVELS[name]:
            raise MatrixInputError(f"renderer has no spelling for {name}={level}")
    primitive = axes["primitive_type"]
    shape = axes["value_shape"]
    field = shape.startswith("udt_field_")
    base_shape = shape.removeprefix("udt_field_")
    position = axes["missing_value_position"]
    predicate = axes["missing_predicate_target"]
    statement = axes["statement_kind"]
    layout = axes["body_layout"]
    key_type = axes["map_key_type"]
    collection = base_shape != "scalar"
    if base_shape == "map" and key_type not in RENDER_LEVELS["primitive_type"]:
        raise MatrixInputError(f"renderer requires a primitive map key, found {key_type}")
    if base_shape != "map" and key_type != "none":
        raise MatrixInputError("renderer cannot apply a map key to a non-map shape")
    if position in {"collection_handle", "collection_element"} and not collection:
        raise MatrixInputError(f"{position} has no collection in {shape}")
    if position == "udt_field" and not field:
        raise MatrixInputError("udt_field missing position has no UDT field")
    if predicate != "none" and not collection:
        raise MatrixInputError(f"{predicate} has no container in {shape}")
    if predicate == "udt_field_container" and not field:
        raise MatrixInputError("udt_field_container predicate has no UDT field")
    pine_type = {
        "scalar": primitive, "array": f"array<{primitive}>",
        "map": f"map<{key_type}, {primitive}>", "matrix": f"matrix<{primitive}>",
    }[base_shape]
    atom = {
        "int": "bar_index + 1", "float": "close", "bool": "close > open",
        "string": "str.tostring(bar_index)",
        "color": "close > open ? color.green : color.red",
    }[primitive]
    key = {
        "none": "0", "int": "bar_index", "float": "close", "bool": "choose",
        "string": 'str.tostring(bar_index)', "color": "color.blue",
    }.get(key_type, "0")
    issues = []
    if layout == "single_line" and statement in {"for", "while"}:
        raise MatrixInputError("single-line loop layout is inventory-only under SPEC .3")
    declarations = ["//@version=6", 'strategy("generated compile matrix")']
    if field:
        declarations += ["type Payload", f"    {pine_type} value"]
    helpers: list[str] = []
    setup = ["bool choose = bar_index % 2 == 0"]
    if base_shape == "scalar":
        setup.append(f"{pine_type} present = {atom}")
    elif base_shape == "array":
        setup.append(f"{pine_type} present = array.new<{primitive}>(1, {atom})")
    elif base_shape == "matrix":
        setup.append(f"{pine_type} present = matrix.new<{primitive}>(1, 1, {atom})")
    else:
        setup += [
            f"{key_type} selected_key = {key}",
            f"{pine_type} present = map.new<{key_type}, {primitive}>()",
            f"present.put(selected_key, {atom})",
        ]
    value = "present"
    branch_orders: list[str] = []
    if position == "branch_order":
        setup += [
            f"{pine_type} missing_first = choose ? na : present",
            f"{pine_type} missing_last = choose ? present : na",
            f"{pine_type} branch_value = bar_index % 3 == 0 ? missing_first : missing_last",
        ]
        value = "branch_value"
        branch_orders = ["missing_first", "missing_last"]
    elif position == "return":
        helpers += [
            f"missing_return({pine_type} supplied, bool condition) =>",
            "    condition ? supplied : na",
        ]
        value = "missing_return(present, choose)"
    elif position == "argument":
        helpers += [f"missing_argument({pine_type} supplied) => supplied"]
        value = "missing_argument(na)"
    elif position == "initializer":
        setup.append(f"{pine_type} initialized_missing = na")
        value = "initialized_missing"
    elif position == "collection_handle" and not field:
        # Missing at a later handle assignment, not at its declaration.
        setup += [f"{pine_type} handle_value = present", "handle_value := na"]
        value = "handle_value"
    elif position == "collection_element":
        if base_shape == "array":
            setup.append(f"{pine_type} missing_element = array.new<{primitive}>(1, na)")
        elif base_shape == "matrix":
            setup.append(f"{pine_type} missing_element = matrix.new<{primitive}>(1, 1, na)")
        else:
            setup += [
                f"{pine_type} missing_element = map.new<{key_type}, {primitive}>()",
                "missing_element.put(selected_key, na)",
            ]
        value = "missing_element"
    if field:
        setup.append(
            "Payload holder = Payload.new()" if position == "udt_field"
            else f"Payload holder = Payload.new({value})"
        )
        if position == "collection_handle":
            # Start with the present field, then make that field's handle
            # missing. initializer and udt_field keep their own constructs.
            setup.append("holder.value := na")
        value = "holder.value"
    setup.append(f"{pine_type} selected_value = {value}")
    body: list[str]

    def block(header: str, lines: Sequence[str]) -> list[str]:
        if layout == "single_line":
            if header.endswith("=>"):
                return [header + " " + ", ".join(lines)]
            return [header, "    " + ", ".join(lines)]
        return [header, *("    " + line for line in lines)]

    if statement in {"function_definition", "method", "tuple_return"}:
        is_method = statement == "method"
        receiver_type = "Payload" if is_method and field else pine_type
        receiver = "supplied.value" if is_method and field else "supplied"
        prefix = "method " if is_method else ""
        ending = (
            f"[condition ? {receiver} : local_value, local_value]"
            if statement == "tuple_return" else f"condition ? {receiver} : local_value"
        )
        helpers += block(
            f"{prefix}probe({receiver_type} supplied, bool condition) =>",
            [f"{pine_type} local_value = {receiver}", ending],
        )
        if is_method and field:
            setup.append("holder.value := selected_value")
        call = (
            "holder.probe(choose)" if is_method and field
            else "selected_value.probe(choose)" if is_method
            else "probe(selected_value, choose)"
        )
        body = (
            [f"[result, tuple_other] = {call}"] if statement == "tuple_return"
            else [f"{pine_type} result = {call}"]
        )
    elif statement in {"var", "varip"}:
        if layout == "single_line":
            body = [f"{statement} {pine_type} result = selected_value"]
        else:
            body = [
                f"{pine_type} result = present",
                "if bar_index >= 0",
                f"    {statement} {pine_type} stored_value = selected_value",
                "    result := stored_value",
            ]
    elif statement == "ternary":
        if layout == "single_line":
            body = [f"{pine_type} result = choose ? selected_value : present"]
        else:
            body = [
                f"{pine_type} result = present", "if bar_index >= 0",
                "    result := choose ? selected_value : present",
            ]
    elif statement == "switch":
        body = [f"{pine_type} result = switch"]
        if layout == "single_line":
            body += ["    choose => selected_value", "    => present"]
        else:
            body += ["    choose =>", "        selected_value", "    =>", "        present"]
    else:
        body = [f"{pine_type} result = present"]
        if statement in {"if", "if_else"}:
            body += block("if choose", [
                f"{pine_type} branch_value = selected_value", "result := branch_value",
            ])
            if statement == "if_else":
                body += block("else", [
                    f"{pine_type} alternate_value = present", "result := alternate_value",
                ])
        elif statement == "for":
            body += block("for index = 0 to 1", [
                f"{pine_type} loop_value = selected_value", "result := loop_value",
            ])
        elif statement == "while":
            body += ["int remaining = 2"]
            body += block("while remaining > 0", ["result := selected_value", "remaining -= 1"])
        else:
            raise MatrixInputError(f"unhandled statement {statement}")

    def observation(name: str) -> str:
        if predicate == "container_handle":
            return f"na({name})"
        if predicate == "container_element":
            element = {
                "array": f"{name}.get(0)", "map": f"{name}.get(selected_key)",
                "matrix": f"{name}.get(0, 0)",
            }[base_shape]
            return f"not na({name}) and na({element})"
        if not collection:
            return name if primitive == "bool" else f"not na({name})"
        size = f"{name}.rows()" if base_shape == "matrix" else f"{name}.size()"
        return f"not na({name}) and {size} > 0"

    if predicate == "udt_field_container":
        body += ["holder.value := result"]
        observed = "na(holder.value)"
        if statement == "tuple_return":
            observed += " or na(tuple_other)"
    else:
        observed = observation("result")
        if statement == "tuple_return":
            observed = f"({observed}) or ({observation('tuple_other')})"
    tail = [
        f"bool observed = {observed}",
        "if observed", '    strategy.entry("L", strategy.long)',
    ]
    source = "\n".join([*declarations, *helpers, *setup, *body, *tail]) + "\n"
    return source.encode("utf-8"), issues, {
        "branchOrders": branch_orders, "payloadType": primitive,
        "pineValueType": pine_type, "fieldAccess": "holder.value" if field else None,
        "mapKeyType": key_type, "predicateTarget": predicate,
        "layoutRole": (
            "global_statement" if layout == "single_line" else "enclosing_local_statement"
        ) if statement in {"ternary", "var", "varip"} else (
            "one_line_local_block" if statement in {"if", "if_else"} and layout == "single_line"
            else "inline_arrow_body" if layout == "single_line" else "indented_body"
        ),
    }


COMPARISON_BASIS = "lexical_normal_form_v1"
PINE_KEYWORDS = frozenset({
    "if", "else", "for", "to", "by", "while", "switch", "var", "varip", "type", "method",
    "and", "or", "not", "true", "false", "break", "continue",
})
# Binding forms the renderer never emits: refused (fail closed), never guessed.
PINE_UNSUPPORTED_KEYWORDS = frozenset({"in", "import", "export", "enum"})
PINE_VALUE_TYPES = frozenset({"int", "float", "bool", "string", "color"})
PINE_GENERIC_TYPES = frozenset({"array", "map", "matrix"})
PINE_RESERVED_NAMES = (
    PINE_KEYWORDS | PINE_UNSUPPORTED_KEYWORDS | PINE_VALUE_TYPES | PINE_GENERIC_TYPES
    | {"na", "bar_index", "close", "open", "high", "low", "volume", "time",
       "strategy", "str", "math", "ta"}
)
NF_OPERATORS = (":=", "==", "!=", "<=", ">=", "=>", "+=", "-=", "*=", "/=", "%=")
NF_PUNCTUATION = frozenset("+-*/%<>=?:,.()[]")


class NormalFormError(MatrixInputError):
    """A source uses a lexical or binding form the comparison cannot read.

    The comparison fails closed: the caller reports INCOMPLETE and names
    `reason`. It never omits the source or reclassifies a valid row.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"comparison normal form: {reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _nf_lex_line(text: str, number: int) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char == " ":
            index += 1
        elif text.startswith("//", index):
            break  # a comment outside quotes runs to the end of its line
        elif char in "\"'":
            if text.startswith(char * 3, index):
                raise NormalFormError("unsupported_multiline_string", f"line {number}")
            end = index + 1
            while end < size and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            if end >= size:
                raise NormalFormError("unterminated_string", f"line {number}")
            # Comment markers and identifiers inside quotes stay string data.
            tokens.append(("str", text[index + 1:end]))
            index = end + 1
        elif char.isascii() and (
            char.isdigit()
            or (char == "." and index + 1 < size and text[index + 1].isascii() and text[index + 1].isdigit())
        ):
            end = index
            while end < size and text[end].isascii() and text[end].isdigit():
                end += 1
            if end < size and text[end] == ".":
                end += 1
                while end < size and text[end].isascii() and text[end].isdigit():
                    end += 1
            if end < size and text[end] in "eE":
                probe = end + 1
                if probe < size and text[probe] in "+-":
                    probe += 1
                if probe < size and text[probe].isascii() and text[probe].isdigit():
                    end = probe
                    while end < size and text[end].isascii() and text[end].isdigit():
                        end += 1
            tokens.append(("num", text[index:end]))
            index = end
        elif char.isascii() and (char.isalpha() or char == "_"):
            end = index + 1
            while end < size and text[end].isascii() and (text[end].isalnum() or text[end] == "_"):
                end += 1
            tokens.append(("word", text[index:end]))
            index = end
        else:
            for operator in NF_OPERATORS:
                if text.startswith(operator, index):
                    tokens.append(("op", operator))
                    index += len(operator)
                    break
            else:
                if char not in NF_PUNCTUATION:
                    raise NormalFormError("unsupported_character", f"line {number}: {char!r}")
                tokens.append(("op", char))
                index += 1
    return tokens


def _nf_lex(text: str) -> list[tuple[int, list[tuple[str, str]]]]:
    """Logical lines as (indent, tokens): comments and blank lines vanish.

    Indentation stays, so body_layout remains observable. A comment-only
    `//@version=N` line is the language-version directive and stays a token.
    """
    lines: list[tuple[int, list[tuple[str, str]]]] = []
    for number, raw in enumerate(text.split("\n"), 1):
        if "\r" in raw:
            raise NormalFormError("carriage_return", f"line {number}")
        body = raw.lstrip(" ")
        if not body:
            continue
        if body[0] in "\t\x0b\x0c":
            raise NormalFormError("unsupported_whitespace", f"line {number}")
        indent = len(raw) - len(body)
        if body.startswith("//"):
            compact = body[2:].replace(" ", "")
            if compact.startswith("@version=") and compact[9:].isascii() and compact[9:].isdigit():
                lines.append((indent, [("directive", compact)]))
            continue
        tokens = _nf_lex_line(body, number)
        if tokens:
            if indent % 4:
                raise NormalFormError("unsupported_indentation", f"line {number}: {indent} spaces")
            lines.append((indent, tokens))
    return lines


def _nf_opens_generic(tokens: Sequence[tuple[str, str]], position: int) -> bool:
    """`<` opens a type-argument list after array/map/matrix or `.new`."""
    if not position or tokens[position - 1][0] != "word":
        return False
    previous = tokens[position - 1][1]
    return previous in PINE_GENERIC_TYPES or (
        previous == "new" and position > 1 and tokens[position - 2] == ("op", ".")
    )


def _nf_statements(tokens: Sequence[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split on commas outside brackets and type-argument lists."""
    statements: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    depth = angle = 0
    for position, token in enumerate(tokens):
        kind, text = token
        if kind == "op":
            if text in ("(", "["):
                depth += 1
            elif text in (")", "]"):
                depth -= 1
            elif text == "<" and _nf_opens_generic(tokens, position):
                angle += 1
            elif text == ">" and angle:
                angle -= 1
            elif text == "," and not depth and not angle:
                statements.append(current)
                current = []
                continue
        current.append(token)
    statements.append(current)
    return statements


def _nf_top_level_equals(statement: Sequence[tuple[str, str]]) -> int | None:
    depth = angle = 0
    for position, (kind, text) in enumerate(statement):
        if kind != "op":
            continue
        if text in ("(", "["):
            depth += 1
        elif text in (")", "]"):
            depth -= 1
        elif text == "<" and _nf_opens_generic(statement, position):
            angle += 1
        elif text == ">" and angle:
            angle -= 1
        elif text == "=" and not depth and not angle:
            return position
    return None


def _nf_name_before_equals(segment: Sequence[tuple[str, str]]) -> str:
    stop = _nf_top_level_equals(segment)
    if stop is None:
        stop = len(segment)
    if not stop or segment[stop - 1][0] != "word":
        raise NormalFormError("unsupported_binding_form", "declaration without a name")
    return segment[stop - 1][1]


def _nf_definition(tokens: Sequence[tuple[str, str]], indent: int):
    """(name, parameter tokens, body tokens) of `[method] name(params) => body`."""
    if indent:
        return None
    start = 1 if tokens[0] == ("word", "method") else 0
    if (
        len(tokens) >= start + 3 and tokens[start][0] == "word"
        and tokens[start + 1] == ("op", "(")
    ):
        depth = 0
        for position in range(start + 1, len(tokens)):
            if tokens[position] == ("op", "("):
                depth += 1
            elif tokens[position] == ("op", ")"):
                depth -= 1
                if depth == 0:
                    if position + 1 < len(tokens) and tokens[position + 1] == ("op", "=>"):
                        return (
                            tokens[start][1], list(tokens[start + 2:position]),
                            list(tokens[position + 2:]),
                        )
                    break
    if start:
        raise NormalFormError("unsupported_binding_form", "method declaration")
    return None


def _nf_declare_statement(statement: Sequence[tuple[str, str]], declared: set[str]) -> None:
    if not statement:
        raise NormalFormError("unsupported_statement_form", "empty statement")
    if statement[0] == ("op", "["):
        close = next((i for i, token in enumerate(statement) if token == ("op", "]")), None)
        if close is not None and close + 1 < len(statement) and statement[close + 1] == ("op", "="):
            for kind, text in statement[1:close]:
                if kind == "word":
                    declared.add(text)
                elif (kind, text) != ("op", ","):
                    raise NormalFormError("unsupported_binding_form", "tuple declaration element")
        return
    if statement[0] == ("word", "for"):
        if len(statement) > 2 and statement[1][0] == "word" and statement[2] == ("op", "="):
            declared.add(statement[1][1])
            return
        raise NormalFormError("unsupported_binding_form", "for header")
    position = _nf_top_level_equals(statement)
    if position is None:
        return
    if position == 0 or statement[position - 1][0] != "word":
        raise NormalFormError("unsupported_binding_form", "assignment target")
    declared.add(statement[position - 1][1])


def _nf_declared(lines: Sequence[tuple[int, list[tuple[str, str]]]]) -> set[str]:
    """Names the source declares: type, field, function, parameter, variable, loop."""
    declared: set[str] = set()
    type_indent: int | None = None
    for indent, tokens in lines:
        if tokens[0][0] == "directive":
            continue
        for kind, text in tokens:
            if kind == "word" and text in PINE_UNSUPPORTED_KEYWORDS:
                raise NormalFormError("unsupported_binding_form", f"keyword {text!r}")
        if type_indent is not None and indent > type_indent:
            declared.add(_nf_name_before_equals(tokens))
            continue
        type_indent = None
        if tokens[0] == ("word", "type"):
            if len(tokens) != 2 or tokens[1][0] != "word":
                raise NormalFormError("unsupported_binding_form", "type declaration")
            declared.add(tokens[1][1])
            type_indent = indent
            continue
        definition = _nf_definition(tokens, indent)
        if definition is not None:
            name, parameters, body = definition
            declared.add(name)
            for segment in _nf_statements(parameters):
                if segment:
                    declared.add(_nf_name_before_equals(segment))
            statements = _nf_statements(body) if body else []
        else:
            statements = _nf_statements(tokens)
        for statement in statements:
            _nf_declare_statement(statement, declared)
    shadowed = sorted(declared & PINE_RESERVED_NAMES)
    if shadowed:
        raise NormalFormError("declared_name_shadows_builtin", ", ".join(shadowed))
    return declared


def comparison_normal_form(source: bytes) -> tuple[tuple[Any, ...], ...]:
    """Typed token form that the collision guard and the tests both compare.

    Dropped: comments outside quotes, blank and comment-only lines, and
    whitespace between tokens. Kept: each line's indentation, string and
    number literals, operators, keywords, built-in and member names. A name
    the source declares (type, field, function, parameter, variable, loop
    counter) is replaced everywhere by its order of first occurrence, so a
    consistent rename is no distinction while `x + x` and `x + y` differ.
    Tokens are typed tuples, so a string `"0"`, a number `0` and a renamed
    identifier never collide. Scope is not tracked: this reads spelling-level
    bijective renames only. It is no Pine compiler and no equivalence proof.
    A form it cannot read raises NormalFormError with a named reason.
    """
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NormalFormError("invalid_utf8", str(exc)) from exc
    lines = _nf_lex(text)
    declared = _nf_declared(lines)
    order: dict[str, int] = {}
    form: list[tuple[Any, ...]] = []
    for indent, tokens in lines:
        form.append(("line", indent))
        for kind, lexeme in tokens:
            if kind == "word":
                if lexeme in declared:
                    form.append(("id", order.setdefault(lexeme, len(order))))
                elif lexeme in PINE_KEYWORDS:
                    form.append(("kw", lexeme))
                else:
                    form.append(("name", lexeme))
            else:
                form.append((kind, lexeme))
    return tuple(form)


def comparison_fingerprint(source: bytes) -> str:
    """Comparison hash; the raw source SHA-256 stays the content identity."""
    form = comparison_normal_form(source)
    return sha256(
        COMPARISON_BASIS.encode("utf-8") + b"\n"
        + canonical_bytes([list(token) for token in form])
    )


def fresh_results() -> dict[str, Any]:
    return {
        "translation": {"status": "UNEXECUTED", "exitCode": None, "diagnostics": None},
        "compile": {"status": "UNEXECUTED", "exitCode": None, "diagnostics": None},
        "tv": {"status": "NOT_MEASURED", "exitCode": None, "diagnostics": None},
        "codegenIdentity": None, "engineIdentity": None, "compilerIdentity": None,
        "headersIdentity": None, "eigenIdentity": None, "generatedHeadersIdentity": None,
        "headerIdentity": None, "generatedHeaderIdentity": None,
    }


def empty_manifest() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION, "generatorVersion": GENERATOR_VERSION,
        "draftRevision": None, "specSourceHash": None, "generatorSourceHash": None,
        "generation": {"status": "INCOMPLETE", "exitCode": None, "issues": []},
        "sourceComparison": {
            "basis": COMPARISON_BASIS,
            "rule": (
                "Ordinary sources are compared by comparisonSha256, a lexical normal form "
                "that drops comments, blank lines and token whitespace, keeps indentation, "
                "literals, operators and built-ins, and renames declared names by first "
                "occurrence. sourceSha256 stays the exact-bytes content identity."
            ),
            "limits": (
                "Spelling-level bijective renames only; scope is not tracked; no Pine "
                "compiler and no semantic-equivalence claim. An unreadable form is INCOMPLETE."
            ),
        },
        "cases": [], "candidates": [],
        "coverage": {
            "status": "INCOMPLETE", "basis": "generation_metadata_only",
            "positiveDomainStatus": "INCOMPLETE", "inventoryStatus": "UNKNOWN",
            "positiveCompileCoverage": "UNEXECUTED",
            "candidateCount": None, "candidateProduct": None,
            "enumeratedCandidateCount": None, "ordinarySelectedCount": None, "seedCount": None,
            "coreTupleCount": None, "coreCaseCount": None, "pairwiseCaseCount": None,
            "overlapCaseCount": None, "positiveUnionCount": None,
            "invalidControlCount": None, "totalEmittedCount": None,
            "enumeratedCaseCount": None, "structuralEnumerationUpperBound": None,
            "targetPairs": None, "achievedPairs": None, "uncoveredPairs": None,
            "targetInputFacts": None, "achievedInputFacts": None, "uncoveredInputFacts": None,
        },
        "consumerContract": {
            "status": "UNEXECUTED",
            "positive": (
                "Verify the exact source digest, call the public transpile API, then "
                "tests._compile.compile_cpp(cpp, label=id, extra_flags=STRATEGY_FP_FLAGS). "
                "Record actual phase results and paired tool/input identities. "
                "Missing input, source, result, or skipped compile is INCOMPLETE."
            ),
            "invalidControl": (
                "Require a real public-translator CompileError carrying expectedDiagnosticCode, "
                "expectedDiagnosticPhase, message and hint. Accepted translation, another code/phase, compiler "
                "failure, crash, missing receipt, or a marker alone is not the expected refusal."
            ),
            "failureRule": "Stop the consumer sequence on a failed control; retain its own results.",
            "verdictRule": "Read actual work result files and judge output; never infer a verdict from a marker.",
        },
        "results": {"status": "INCOMPLETE", "cases": {}},
    }


def record_issue(manifest: dict[str, Any], kind: str, **facts: Any) -> None:
    manifest["generation"]["issues"].append({"kind": kind, **facts})


def write_new(path: Path, data: bytes) -> None:
    path = checked_path(path)
    with path.open("xb") as target:
        target.write(data)


def emit_case(
    output: Path, manifest: dict[str, Any], record: dict[str, Any], source: bytes,
) -> None:
    filename = f"cases/{record['id']}.pine"
    write_new(output / filename, source)
    record.update(sourcePath=filename, sourceSha256=sha256(source), sourceBytes=len(source))
    existing = next((row for row in manifest["cases"] if row["id"] == record["id"]), None)
    if existing is not None and existing is not record:
        raise MatrixInputError(f"duplicate emitted case id {record['id']}")
    if existing is None:
        manifest["cases"].append(record)
        manifest["results"]["cases"][record["id"]] = fresh_results()


def load_seeds(
    spec: Mapping[str, Any], index_path: Path, inputs: InputFiles,
    manifest: dict[str, Any],
) -> list[tuple[dict[str, Any], bytes]]:
    index, _ = inputs.json(index_path, identity="seedIndex")
    if (
        not isinstance(index, dict) or type(index.get("schemaVersion")) is not int
        or index["schemaVersion"] != SCHEMA_VERSION
    ):
        raise MatrixInputError("seed index has an unknown schema")
    for key in ("header", "tail", "sourceUrl", "baselineCodegen", "inputSha256"):
        if not isinstance(index.get(key), str) or not index[key]:
            raise MatrixInputError(f"seed index requires {key}")
    seeds = index.get("seeds")
    if not isinstance(seeds, list) or any(not isinstance(seed, dict) for seed in seeds):
        raise MatrixInputError("seed index requires a seeds list")
    names = [seed.get("id") for seed in seeds]
    if any(not safe_id(name) for name in names) or len(names) != len(set(names)):
        raise MatrixInputError("seed index ids must be safe and unique")
    if set(names) != set(spec["raw"]["requiredSeeds"]):
        raise MatrixInputError("seed index does not match all requiredSeeds")
    stages = [seed.get("baselineFailureStage") for seed in seeds]
    if stages.count("front_end") != 1 or stages.count("cpp_compile") != 2:
        raise MatrixInputError("seed index must retain one front-end and two C++ baseline phases")
    declarations = spec["raw"].get("seedFixtures")
    if not isinstance(declarations, list) or any(not isinstance(seed, dict) for seed in declarations):
        raise MatrixInputError("spec seedFixtures must be a list of objects")
    declared = {seed.get("id"): seed for seed in declarations}
    if len(declared) != len(declarations) or set(declared) != set(names):
        raise MatrixInputError("spec seedFixtures does not match requiredSeeds")
    expected = spec["outcomes"]["valid_intended_supported"]
    declared_input = spec["raw"].get("seedInputProvenance")
    if not isinstance(declared_input, dict):
        raise MatrixInputError("spec requires seedInputProvenance")
    for key in ("inputSha256", "sourceUrl", "baselineCodegen", "reportedEngine"):
        if declared_input.get(key) != index.get(key):
            raise MatrixInputError(f"spec seedInputProvenance disagrees with the seed index: {key}")
    if not isinstance(index.get("expected"), dict) or any(
        index["expected"].get(key) != expected[key] for key in ("translation", "compile", "tv")
    ):
        raise MatrixInputError("seed index changes the positive expected outcome")
    rows = []
    paths = []
    for seed in sorted(seeds, key=lambda row: row["id"]):
        filename = seed.get("file")
        if not isinstance(filename, str) or filename != seed["id"] + ".pine":
            raise MatrixInputError(f"seed {seed['id']} must name its own .pine basename")
        paths.append(checked_path(index_path.parent / filename))
    print("Seed input files:", *(str(path) for path in paths), sep="\n", file=sys.stderr, flush=True)
    for seed, path in zip(sorted(seeds, key=lambda row: row["id"]), paths):
        name = seed["id"]
        if not isinstance(seed.get("body"), str):
            raise MatrixInputError(f"seed {name} requires its exact body string")
        source = inputs.read(path, identity=f"seed:{name}")
        assembled = (index["header"] + seed["body"] + index["tail"]).encode("utf-8")
        if source != assembled or sha256(source) != seed.get("sourceSha256"):
            raise MatrixInputError(f"seed {name} bytes or sourceSha256 differ from its index")
        axes = seed.get("axes")
        if not isinstance(axes, dict) or set(axes) != set(spec["axes"]):
            raise MatrixInputError(f"seed {name} must record every axis")
        if any(not isinstance(value, str) for value in axes.values()):
            raise MatrixInputError(f"seed {name} axis values must be strings")
        if any(value not in spec["axes"][axis] for axis, value in axes.items()):
            raise MatrixInputError(f"seed {name} names an undeclared axis level")
        declaration = declared[name]
        if declaration.get("sourceBytes") != len(source) or declaration.get("sourceSha256") != sha256(source):
            raise MatrixInputError(f"seed {name} differs from the spec's byte/hash receipt")
        for key, actual in (("header", index["header"]), ("body", seed["body"]), ("tail", index["tail"])):
            if declaration.get(key) != actual:
                record_issue(manifest, "seed_spec_fragment_mismatch", seed=name, field=key)
        provenance = declaration.get("provenance", {})
        if not isinstance(provenance, dict):
            raise MatrixInputError(f"seed {name} provenance must be an object")
        for key in ("sourceUrl", "baselineCodegen", "reportedRelease", "reportedEngine"):
            if provenance.get(key) != index.get(key):
                record_issue(
                    manifest, "seed_spec_provenance_mismatch", seed=name, field=key,
                    specValue=provenance.get(key), indexValue=index.get(key),
                )
        if declaration.get("reportedRedPhase") != seed.get("baselineFailureStage"):
            record_issue(manifest, "seed_spec_phase_mismatch", seed=name)
        declared_outcome = declaration.get("expectedOutcome")
        if not isinstance(declared_outcome, dict) or any(
            declared_outcome.get(key) != expected[key] for key in ("translation", "compile", "tv")
        ):
            record_issue(manifest, "seed_spec_expected_outcome_mismatch", seed=name)
        baseline = declaration.get("baseline")
        if isinstance(baseline, dict) and baseline.get("measured") is not False:
            record_issue(manifest, "seed_spec_unmeasured_baseline_conflict", seed=name)
        ordinary_record = classify_candidate(axes, spec["rules"])
        rows.append(({
            "id": name, "renderFamily": "community_seed", "axes": dict(axes),
            "constraints": ordinary_record["constraints"], "legality": "valid",
            "validityProvenance": {
                "basis": "mandatory_positive_source_contract", "issueClaim": index.get("validity"),
                "declaration": declaration.get("provenance"), "tvResult": "NOT_MEASURED",
            },
            "coverageMembership": {"core": False, "pairwise": False},
            "groups": {"core": False, "pairwise": False},
            "expectedDiagnosticCode": None, "expectedDiagnosticPhase": None,
            "expectedDiagnosticHint": None, "invalidInputFact": None,
            "sourceObligations": list(spec["raw"]["inputFactPolicy"]["sourceObligations"]),
            "ordinaryClassification": ordinary_record["legality"],
            "expected": dict(expected),
            "seedProvenance": {
                "sourceUrl": index["sourceUrl"], "inputSha256": index["inputSha256"],
                "communityInputIdentityBasis": "seed_index_provenance; original_input_not_opened_by_generator",
                "baselineCodegen": index["baselineCodegen"],
                "reportedRelease": index.get("reportedRelease"),
                "reportedEngine": index.get("reportedEngine"),
                "reportedRedPhase": seed.get("reportedRedPhase"),
                "baselineFailureStage": seed.get("baselineFailureStage"),
                "baselineStatus": "UNEXECUTED", "tv": "NOT_MEASURED",
                "validity": index.get("validity"),
                "specDeclaration": declaration,
            },
            "pairCoverageRole": "mandatory_witness_only",
        }, source))
    return rows


def seed_fact_coverage(spec: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    required = spec["raw"]["requiredSeeds"]
    target = {
        "render:community_seed",
        *(f"seed:{name}:{fact}" for name in required for fact in ("present", "exact_bytes", "provenance")),
    }
    achieved: set[str] = set()
    for row in manifest["cases"]:
        if row["renderFamily"] != "community_seed" or row["id"] not in required:
            continue
        name = row["id"]
        if row.get("sourcePath") and row.get("sourceSha256"):
            achieved.update({"render:community_seed", f"seed:{name}:present"})
            problems = {
                issue["kind"] for issue in manifest["generation"]["issues"]
                if issue.get("seed") == name
            }
            if "seed_spec_fragment_mismatch" not in problems:
                achieved.add(f"seed:{name}:exact_bytes")
            if not problems:
                achieved.add(f"seed:{name}:provenance")
    return {
        "target": sorted(target), "achieved": sorted(achieved),
        "uncovered": sorted(target - achieved),
    }


def build_matrix(
    spec: Mapping[str, Any], output: Path, manifest: dict[str, Any],
    *, ordinary_limit: int = MAX_ORDINARY, candidate_limit: int = MAX_CANDIDATES,
) -> None:
    if type(ordinary_limit) is not int or not len(spec["controls"]) <= ordinary_limit <= MAX_ORDINARY:
        raise MatrixInputError("ordinary bound cannot omit the explicit controls or exceed 512")
    axes = spec["levelOrder"]
    coverage = manifest["coverage"]
    coverage["candidateProduct"] = math.prod(len(levels) for levels in axes.values())
    extra_cases = len(spec["controls"]) + len(spec["raw"]["requiredSeeds"])
    if type(candidate_limit) is not int or not extra_cases < candidate_limit <= MAX_CANDIDATES:
        raise MatrixInputError("enumeration bound must include candidate pool, controls and seeds")
    search = CompletionSearch(axes, spec["rules"], limit=candidate_limit - extra_cases)
    coverage["search"] = search.stats
    coverage["structuralEnumerationUpperBound"] = (
        math.prod(len(axes[name]) for name in spec["coreAxes"])
        + sum(len(axes[a]) * len(axes[b]) for a, b in itertools.combinations(axes, 2))
        + sum(len(levels) for levels in axes.values()) + len(axes["statement_kind"])
        + sum(rule["classification"] == "valid" for rule in spec["rules"]) + extra_cases
    )
    plan = coverage_plan(
        axes, spec["rules"], core_axes=spec["coreAxes"], seed=spec["seed"],
        ordinary_limit=ordinary_limit - len(spec["controls"]),
        enumeration_limit=candidate_limit - extra_cases, search=search,
    )
    applicable_layouts = {
        form: set(item["applicable"])
        for item in spec["raw"]["layoutApplicability"] for form in item["statementKinds"]
    }
    required_core = [
        dict(zip(spec["coreAxes"], values))
        for values in itertools.product(*(axes[name] for name in spec["coreAxes"]))
        if values[1] in applicable_layouts[values[0]]
    ]
    missing_core = [row for row in required_core if row not in plan["targetCore"]]
    unexpected_core = [row for row in plan["targetCore"] if row not in required_core]
    if missing_core or unexpected_core:
        record_issue(
            manifest, "core_layout_contract_conflict",
            missingApplicableCompletions=missing_core, unexpectedLayoutCompletions=unexpected_core,
        )
    # Applicability is explicit language input, not the present search's
    # ability to find an example. A missing canonical completion stays due.
    plan["targetCore"] = required_core
    records = [classify_candidate(row, spec["rules"]) for row in plan["candidateRows"]]
    for record in records:
        record["expected"] = expected_for(record["legality"], spec)
    # Every extendable form has a canonical positive fact completion in this
    # pool. Diagnose absent eligible forms independently of emission being
    # withheld by another input issue; candidates earn no emitted coverage.
    eligible_forms = {
        row["axes"]["statement_kind"] for row in records if row["legality"] == "valid"
    }
    missing_forms = [form for form in axes["statement_kind"] if form not in eligible_forms]
    manifest["candidates"] = records
    manifest["inventory"] = {
        "axes": spec["raw"]["axes"], "constraints": spec["raw"]["constraints"],
        "layoutApplicability": spec["raw"]["layoutApplicability"],
        "unresolvedEvidence": spec["raw"]["unresolvedEvidence"],
        "excludedCoreTuples": plan["excludedCore"], "excludedPairs": plan["excludedPairs"],
        "excludedRoles": plan["roleInventory"],
    }
    coverage["candidateCount"] = len(records)
    coverage["enumeratedCandidateCount"] = len(records)
    coverage["enumeratedCaseCount"] = len(records) + extra_cases
    coverage["pairInventory"] = plan["pairInventory"]
    coverage["candidateInventoryMode"] = "canonical_core_pair_and_positive_fact_completion_union"
    coverage["candidateClassificationCounts"] = {
        label: sum(row["legality"] == label for row in records)
        for label in sorted({row["legality"] for row in records})
    }
    collisions = sorted(set(spec["raw"]["requiredSeeds"]) & {row["id"] for row in records})
    if collisions:
        raise MatrixInputError(f"ordinary and seed ids collide: {collisions!r}")
    core_order = {name: index for index, name in enumerate(plan["coreIds"])}
    pair_order = {name: index for index, name in enumerate(plan["pairwiseIds"])}
    by_id = {row["id"]: row for row in records}
    selected_records = []
    for row_axes in plan["selected"]:
        record = dict(by_id[case_id(row_axes)])
        record.update(
            renderFamily="ordinary", seedProvenance=None,
            groups={"core": record["id"] in core_order, "pairwise": record["id"] in pair_order},
            selectionOrder={
                "core": core_order.get(record["id"]), "pairwise": pair_order.get(record["id"]),
            },
            sourceProvenance={"input": "spec", "specSourceHash": manifest["specSourceHash"]},
            sourcePath=None, sourceSha256=None, renderStatus="UNEXECUTED",
            comparisonSha256=None, comparisonBasis=None,
            validityProvenance={"basis": record["classificationBasis"], "facts": record["evidence"]},
            expectedDiagnosticCode=None, expectedDiagnosticPhase=None, expectedDiagnosticHint=None,
            invalidInputFact=None, sourceObligations=list(spec["raw"]["inputFactPolicy"]["sourceObligations"]),
        )
        record["coverageMembership"] = dict(record["groups"])
        selected_records.append(record)
    control_records = []
    for control in spec["controls"]:
        expected = dict(spec["outcomes"]["language_invalid"])
        expected.update(
            diagnosticCode=control["expectedDiagnosticCode"],
            diagnosticMessage=control["expectedDiagnosticMessage"],
            diagnosticPhase=control["expectedDiagnosticPhase"], diagnosticHint=control["expectedDiagnosticHint"],
        )
        control_records.append({
            "id": "control-" + control["id"], "renderFamily": "invalid_control",
            "axes": dict(control["axes"]), "constraints": [], "legality": "invalid",
            "expected": expected, "expectedDiagnosticCode": control["expectedDiagnosticCode"],
            "expectedDiagnosticMessage": control["expectedDiagnosticMessage"],
            "expectedDiagnosticPhase": control["expectedDiagnosticPhase"],
            "expectedDiagnosticHint": control["expectedDiagnosticHint"],
            "invalidInputFact": control["invalidInputFact"],
            "validityProvenance": {
                "languageBasis": control["languageBasis"], "languageReference": control["languageReference"],
                "catalogSource": control["catalogSource"], "emitterSource": control["emitterSource"],
                "testSource": control["testSource"],
            },
            "seedProvenance": None, "controlProvenance": dict(control),
            "groups": {"core": False, "pairwise": False},
            "coverageMembership": {"core": False, "pairwise": False},
            "sourcePath": None, "sourceSha256": None, "renderStatus": "UNEXECUTED",
        })
    planned = selected_records + control_records
    for record in planned:
        if record["id"] in manifest["results"]["cases"]:
            raise MatrixInputError(f"duplicate planned case id {record['id']}")
        manifest["cases"].append(record)
        manifest["results"]["cases"][record["id"]] = fresh_results()
    coverage.update(
        coreTupleCount=len(plan["targetCore"]), coreCaseCount=len(plan["coreIds"]),
        pairwiseCaseCount=len(plan["pairwiseIds"]), overlapCaseCount=len(plan["overlapIds"]),
        positiveUnionCount=len(selected_records), invalidControlCount=len(control_records),
        ordinarySelectedCount=len(planned), selectionSeed=spec["seed"],
        coreCartesianTupleCount=plan["coreProduct"], pairQueryCount=plan["pairQueryProduct"],
        metadataSelectionComplete=plan["complete"] and not missing_core and not unexpected_core,
        capSatisfied=plan["capSatisfied"],
    )
    if not plan["capSatisfied"]:
        record_issue(
            manifest, "ordinary_case_cap_exceeded", limit=ordinary_limit,
            positiveUnionCount=len(selected_records), invalidControlCount=len(control_records),
            ordinarySelectedCount=len(planned),
        )
        for record in planned:
            record["renderStatus"] = "WITHHELD_CAP"
    elif manifest["generation"]["issues"]:
        for record in planned:
            record["renderStatus"] = "WITHHELD_INPUT"
    else:
        ordinary_forms: dict[str, tuple[str, str]] = {}
        for record in planned:
            if record["renderFamily"] == "ordinary":
                try:
                    source, issues, details = render_case(record["axes"])
                except MatrixInputError as exc:
                    record["renderStatus"] = "INCOMPLETE"
                    record_issue(manifest, "renderer_mismatch", caseId=record["id"], detail=str(exc))
                    break
                record["renderDetails"] = details
                if issues:
                    record["renderStatus"] = "INCOMPLETE"
                    record_issue(manifest, "renderer_scope_unresolved", caseId=record["id"], details=issues)
                    break
                try:
                    fingerprint = comparison_fingerprint(source)
                except NormalFormError as exc:
                    record["renderStatus"] = "INCOMPLETE"
                    record_issue(
                        manifest, "renderer_source_normal_form_unsupported", caseId=record["id"],
                        reason=exc.reason, detail=exc.detail, sourceSha256=sha256(source),
                        comparisonBasis=COMPARISON_BASIS,
                    )
                    break
                record["comparisonSha256"] = fingerprint
                record["comparisonBasis"] = COMPARISON_BASIS
                if fingerprint in ordinary_forms:
                    first_id, first_sha = ordinary_forms[fingerprint]
                    record["renderStatus"] = "INCOMPLETE"
                    record_issue(
                        manifest, "renderer_source_collision", caseId=record["id"],
                        firstCaseId=first_id, sourceSha256=sha256(source),
                        firstSourceSha256=first_sha, comparisonSha256=fingerprint,
                        comparisonBasis=COMPARISON_BASIS,
                    )
                    break
                ordinary_forms[fingerprint] = (record["id"], sha256(source))
            else:
                source = record["controlProvenance"]["source"].encode("utf-8")
            emit_case(output, manifest, record, source)
            record["renderStatus"] = "GENERATED"
    emitted = [
        record for record in selected_records
        if record["renderStatus"] == "GENERATED" and record["sourcePath"]
    ]
    pair_emitted = [row for row in emitted if row["groups"]["pairwise"]]
    core_emitted = emitted
    target = plan["targetPairs"]
    pair_achieved = set().union(*(pairs_for(row["axes"]) for row in pair_emitted)) if pair_emitted else set()
    achieved = pair_achieved
    pair_facts = set().union(*(
        positive_row_facts(row["axes"], spec["coreAxes"], spec["rules"]) for row in pair_emitted
    )) if pair_emitted else set()
    target_facts = plan["targetFacts"]
    pair_facts &= target_facts
    achieved_facts = pair_facts
    core_achieved = []
    for row in core_emitted:
        projected = {name: row["axes"][name] for name in spec["coreAxes"]}
        if projected not in core_achieved:
            core_achieved.append(projected)
    core_achieved.sort(key=lambda row: tuple(axes[name].index(row[name]) for name in spec["coreAxes"]))
    core_uncovered = [row for row in plan["targetCore"] if row not in core_achieved]
    coverage["groups"] = {
        "core": {
            "axes": list(spec["coreAxes"]), "selectionOrder": plan["coreIds"],
            "targetTuples": plan["targetCore"], "achievedTuples": core_achieved,
            "uncoveredTuples": core_uncovered, "excludedTuples": plan["excludedCore"],
            "targetCount": len(plan["targetCore"]), "achievedCount": len(core_achieved),
            "uncoveredCount": len(core_uncovered), "excludedCount": len(plan["excludedCore"]),
            "caseIds": plan["coreIds"],
        },
        "pairwise": {
            "axes": list(axes), "seed": spec["seed"], "selectionOrder": plan["pairwiseIds"],
            "targetPairs": pair_records(target, axes), "achievedPairs": pair_records(pair_achieved, axes),
            "uncoveredPairs": pair_records(target - pair_achieved, axes),
            "targetInputFacts": input_fact_records(target_facts, axes, spec["rules"]),
            "achievedInputFacts": input_fact_records(pair_facts, axes, spec["rules"]),
            "uncoveredInputFacts": input_fact_records(target_facts - pair_facts, axes, spec["rules"]),
            "caseIds": plan["pairwiseIds"],
            "targetCount": len(target), "achievedCount": len(pair_achieved),
            "uncoveredCount": len(target - pair_achieved),
            "targetInputFactCount": len(target_facts), "achievedInputFactCount": len(pair_facts),
            "uncoveredInputFactCount": len(target_facts - pair_facts),
        },
        "overlapIds": plan["overlapIds"],
    }
    coverage["core"] = coverage["groups"]["core"]
    coverage["pairwise"] = coverage["groups"]["pairwise"]
    coverage["overlapCaseIds"] = plan["overlapIds"]
    coverage["positiveUnionCaseIds"] = sorted(row["id"] for row in selected_records)
    negative_target = {
        f"invalid:{control['id']}:{field}"
        for control in spec["controls"]
        for field in ("source", "expected_code", "catalog_source", "test_source")
    }
    negative_achieved = {
        f"invalid:{row['controlProvenance']['id']}:{field}"
        for row in control_records if row["renderStatus"] == "GENERATED" and row["sourcePath"]
        for field in ("source", "expected_code", "catalog_source", "test_source")
    }
    coverage["invalidInputFacts"] = {
        "target": sorted(negative_target), "achieved": sorted(negative_achieved),
        "uncovered": sorted(negative_target - negative_achieved),
        "basis": "exact_control_source_and_expected_metadata_only",
        "refusalVerification": "UNEXECUTED",
    }
    coverage["invalidControlFacts"] = [
        {
            "id": row["id"], "invalidInputFact": row["invalidInputFact"],
            "expectedDiagnosticCode": row["expectedDiagnosticCode"],
            "expectedDiagnosticPhase": row["expectedDiagnosticPhase"],
            "expectedDiagnosticHint": row["expectedDiagnosticHint"],
            "sourcePath": row["sourcePath"], "sourceSha256": row["sourceSha256"],
            "catalogSource": row["controlProvenance"]["catalogSource"],
            "testSource": row["controlProvenance"]["testSource"], "status": "UNEXECUTED",
        }
        for row in control_records
    ]
    seeds = coverage["seedInputFacts"]
    all_target_facts = target_facts
    all_achieved_facts = achieved_facts
    uncovered = target - achieved
    uncovered_facts = all_target_facts - all_achieved_facts
    unresolved_rules = [
        {
            "id": rule["id"], "matches": dict(rule["matches"]),
            "reason": rule["reason"], "languageBasis": rule["languageBasis"],
        }
        for rule in spec["rules"] if rule_resolution(rule)["classification"] == "unresolved"
    ]
    coverage["unresolvedEvidence"] = {
        "declared": spec["raw"]["unresolvedEvidence"], "selectors": unresolved_rules,
        "coreTuples": [row for row in plan["excludedCore"] if row["status"] == "unresolved"],
        "pairs": [row for row in plan["excludedPairs"] if row["status"] == "unresolved"],
    }
    inventory_unresolved = bool(spec["raw"]["unresolvedEvidence"] or unresolved_rules)
    coverage["inventoryStatus"] = "UNRESOLVED" if inventory_unresolved else "ACKNOWLEDGED"
    coverage["inapplicableInventoryFacts"] = [
        dict(rule) for rule in spec["raw"]["constraints"] if rule["classification"] == "inapplicable"
    ]
    coverage["invalidInventoryFacts"] = [
        dict(rule) for rule in spec["raw"]["constraints"] if rule["classification"] == "invalid"
    ]
    coverage["formCounts"] = {
        form: sum(row["axes"]["statement_kind"] == form for row in emitted)
        for form in axes["statement_kind"]
    }
    coverage["layoutCounts"] = {
        form: {
            layout: sum(
                row["axes"]["statement_kind"] == form and row["axes"]["body_layout"] == layout
                for row in emitted
            )
            for layout in axes["body_layout"]
        }
        for form in axes["statement_kind"]
    }
    coverage["formPositiveCounts"] = dict(coverage["formCounts"])
    if missing_forms:
        record_issue(manifest, "zero_positive_form_rows", forms=missing_forms)
    if not records:
        record_issue(manifest, "zero_positive_candidates")
    if (
        uncovered or uncovered_facts or core_uncovered or negative_target - negative_achieved
        or seeds["uncovered"]
    ):
        record_issue(
            manifest, "uncovered_requirements", pairs=pair_records(uncovered, axes),
            inputFacts=input_fact_records(uncovered_facts, axes, spec["rules"]), coreTuples=core_uncovered,
            invalidInputFacts=sorted(negative_target - negative_achieved), seedInputFacts=seeds["uncovered"],
        )
    coverage.update(
        targetPairs=pair_records(target, axes), achievedPairs=pair_records(achieved, axes),
        uncoveredPairs=pair_records(uncovered, axes),
        targetInputFacts=input_fact_records(all_target_facts, axes, spec["rules"]),
        achievedInputFacts=input_fact_records(all_achieved_facts, axes, spec["rules"]),
        uncoveredInputFacts=input_fact_records(uncovered_facts, axes, spec["rules"]),
        ordinaryTargetInputFacts=input_fact_records(target_facts, axes, spec["rules"]),
        ordinaryAchievedInputFacts=input_fact_records(achieved_facts, axes, spec["rules"]),
        ordinaryEmittedCount=sum(bool(row["sourcePath"]) for row in planned),
        positiveEmittedCount=len(emitted),
        totalEmittedCount=sum(bool(row.get("sourcePath")) for row in manifest["cases"]),
        targetPairCount=len(target), achievedPairCount=len(achieved), uncoveredPairCount=len(uncovered),
        targetInputFactCount=len(all_target_facts), achievedInputFactCount=len(all_achieved_facts),
        uncoveredInputFactCount=len(uncovered_facts),
    )
    coverage["positiveDomainStatus"] = (
        "COMPLETE" if records and plan["complete"] and not manifest["generation"]["issues"]
        else "INCOMPLETE"
    )
    coverage["status"] = (
        "COMPLETE" if coverage["positiveDomainStatus"] == "COMPLETE" and not inventory_unresolved
        else "INCOMPLETE"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="explicit finite coverage specification")
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory; existing paths are refused")
    parser.add_argument(
        "--seed-index", type=Path,
        default=Path(__file__).absolute().parent.parent / "tests/fixtures/generated_compile_matrix/seed-index.json",
        help="exact witness index and adjacent fixtures",
    )
    args = parser.parse_args(argv)
    try:
        output = checked_path(args.output_dir)
        if output.exists():
            raise MatrixInputError(f"output directory already exists: {output}")
        output.mkdir(parents=False, exist_ok=False)
    except (OSError, MatrixInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    manifest = empty_manifest()
    inputs = InputFiles()
    try:
        initial_paths = [
            checked_path(args.spec), checked_path(args.seed_index), checked_path(__file__),
        ]
        print("Input files:", *(str(path) for path in initial_paths), sep="\n", file=sys.stderr, flush=True)
        raw, spec_bytes = inputs.json(initial_paths[0], identity="spec")
        manifest["specSourceHash"] = sha256(spec_bytes)
        if isinstance(raw, dict):
            manifest["draftRevision"] = raw.get("draftRevision")
            manifest["specification"] = raw
        spec = validate_spec(raw)
        manifest["generatorSourceHash"] = sha256(inputs.read(initial_paths[2], identity="generator"))
        (output / "cases").mkdir(exist_ok=False)
        seeds = load_seeds(spec, initial_paths[1], inputs, manifest)
        for record, source in seeds:
            emit_case(output, manifest, record, source)
        manifest["coverage"]["seedCount"] = len(seeds)
        manifest["coverage"]["seedInputFacts"] = seed_fact_coverage(spec, manifest)
        if not manifest["generation"]["issues"]:
            build_matrix(spec, output, manifest)
        exit_code = 0 if manifest["coverage"]["positiveDomainStatus"] == "COMPLETE" else 2
    except MatrixInputError as exc:
        record_issue(manifest, "input_contract_error", detail=str(exc))
        exit_code = 2
    except Exception as exc:
        record_issue(manifest, "generation_crash", exception=type(exc).__name__, detail=str(exc))
        exit_code = 1
    manifest["cases"].sort(key=lambda row: (row["renderFamily"], row["id"]))
    manifest["coverage"]["totalEmittedCount"] = sum(
        bool(row.get("sourcePath")) for row in manifest["cases"]
    )
    manifest["inputIdentities"] = dict(sorted(inputs.identities.items()))
    manifest["generation"].update(
        status="COMPLETE" if exit_code == 0 else "INCOMPLETE", exitCode=exit_code,
        sourceStatus=(
            "COMPLETE" if manifest["cases"] and not manifest["generation"]["issues"]
            and all(row.get("sourcePath") for row in manifest["cases"]) else "INCOMPLETE"
        ),
        inventoryStatus=manifest["coverage"]["inventoryStatus"],
    )
    try:
        write_new(
            output / "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"),
        )
    except (OSError, ValueError) as exc:
        print(f"could not retain generation manifest: {exc}", file=sys.stderr)
        return 1
    print(f"Generation {manifest['generation']['status']}; verification INCOMPLETE", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
