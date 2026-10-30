"""Keep the shipped PR workflow dependent on real, paired compile checks."""

from pathlib import Path
import re

import pytest


WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/test.yml"
COMPILE_DOC = WORKFLOW.parent.parent.parent / "docs/ci-compile.md"


def _job(workflow: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)",
                      workflow, re.M | re.S)
    assert match is not None, f"missing {name} job"
    return match[1]


def _assert_compile_contract(workflow: str) -> None:
    job = _job(workflow, "pytest")
    assert "    runs-on: ubuntu-24.04\n" in job
    assert '      PINEFORGE_REQUIRE_COMPILE: "1"\n' in job
    assert "      PINEFORGE_ENGINE_INCLUDE:" in job
    assert "      PINEFORGE_ENGINE_CORPUS:" in job
    assert "      PINEFORGE_EIGEN_INCLUDE: /usr/include/eigen3\n" in job
    assert "      CXX: /usr/bin/g++\n" in job
    assert "          repository: pineforge-4pass/pineforge-engine\n" in job
    assert "          ref: main\n" in job
    assert "          path: .ci/pineforge-engine\n" in job
    assert "          submodules: true\n" in job
    assert job.count("          persist-credentials: false\n") == 2
    assert "sudo apt-get install --yes --no-install-recommends g++ libeigen3-dev" in job
    assert "        run: python -m pytest -q\n" in job
    assert "continue-on-error:" not in job
    shards = re.findall(
        r'          - python-version: "(3\.\d+)"\n            compile-shard: (\d+)\n',
        job,
    )
    assert shards == [("3.11", "0"), ("3.12", "1"), ("3.13", "2"), ("3.14", "3")]
    assert '      PINEFORGE_COMPILE_SHARDS: "4"\n' in job
    assert "      PINEFORGE_COMPILE_SHARD: ${{ matrix.compile-shard }}\n" in job
    # The job-level env cannot use the runner context (actionlint refuses it).
    assert "runner." not in job.split("    steps:")[0]
    assert "      PINEFORGE_COMPILE_REPORT_DIR: ${{ github.workspace }}/.ci/compile-report\n" in job
    assert "        if: ${{ always() }}\n" in job
    assert "          path: ${{ github.workspace }}/.ci/compile-report\n" in job
    assert "          if-no-files-found: error\n" in job
    assert "pytest" in re.search(
        r"^    needs: \[([^\]]+)\]$", _job(workflow, "test-check"), re.M,
    )[1].split(", ")


def _assert_pairing_exports(workflow: str) -> None:
    job = _job(workflow, "pytest")
    install = re.search(
        r"^      - name: Install compile-only prerequisites\n(.*?)(?=^      - |\Z)",
        job, re.M | re.S,
    )
    assert install is not None, "missing compile prerequisite step"
    for variable, checkout in (
        ("PINEFORGE_ENGINE_SHA", ".ci/pineforge-engine"),
        ("PINEFORGE_CORPUS_SHA", ".ci/pineforge-engine/corpus"),
    ):
        assert f"          {variable}=$(git -C {checkout} rev-parse HEAD)\n" in install[1], (
            f"{variable} must come from the checked-out commit"
        )
        assert (
            f'          printf \'{variable}=%s\\n\' "${variable}" >> "$GITHUB_ENV"\n'
            in install[1]
        ), f"{variable} must be exported through GITHUB_ENV"


def test_pr_workflow_exports_the_swept_pairing():
    _assert_pairing_exports(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("variable,checkout", [
    ("PINEFORGE_ENGINE_SHA", ".ci/pineforge-engine"),
    ("PINEFORGE_CORPUS_SHA", ".ci/pineforge-engine/corpus"),
])
def test_pairing_workflow_controls_reject_an_unrecorded_or_literal_sha(variable, checkout):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_pairing_exports(workflow)
    assignment = f"{variable}=$(git -C {checkout} rev-parse HEAD)"
    export = f'printf \'{variable}=%s\\n\' "${variable}" >> "$GITHUB_ENV"'
    assert workflow.count(assignment) == 1
    assert workflow.count(export) == 1
    with pytest.raises(AssertionError, match=f"{variable} must come from"):
        _assert_pairing_exports(workflow.replace(assignment, f"{variable}={'a' * 40}", 1))
    with pytest.raises(AssertionError, match=f"{variable} must be exported"):
        _assert_pairing_exports(workflow.replace(export, f'printf "%s\\n" "${variable}"', 1))


@pytest.mark.parametrize("claim", [
    "Coverage is sharded by Python-version leg (3.11, 3.12, 3.13 and 3.14).",
    "Full coverage needs all four legs green.",
    "A re-run must include all four legs.",
    "Repository settings must require all four pytest legs.",
    "The sweeps cover every `.pine` under `tests/fixtures` and `tests/gate-corpus`, "
    "plus every recursive `strategy.pine` in the paired engine corpus.",
    "Tests that transpile inline strings elsewhere are not swept.",
])
def test_compile_docs_state_the_coverage_limits(claim):
    print(f"Compile coverage documentation: {COMPILE_DOC}", flush=True)
    document = " ".join(COMPILE_DOC.read_text(encoding="utf-8").split())
    assert claim in document, f"missing compile coverage requirement: {claim}"


def test_pr_workflow_requires_the_paired_compile_sweep():
    _assert_compile_contract(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("old,new", [
    ('PINEFORGE_REQUIRE_COMPILE: "1"', 'PINEFORGE_REQUIRE_COMPILE: "0"'),
    ("repository: pineforge-4pass/pineforge-engine", "repository: example/other-engine"),
    ("submodules: true", "submodules: false"),
    ("g++ libeigen3-dev", "g++"),
    ("run: python -m pytest -q\n", "run: echo pytest\n"),
    ("compile-shard: 3\n", "compile-shard: 2\n"),
    ("if-no-files-found: error\n", "if-no-files-found: ignore\n"),
    (
        "needs: [authz, changes, workflow-lint, package-files, pytest]",
        "needs: [authz, changes, workflow-lint, package-files]",
    ),
])
def test_compile_workflow_controls_reject_a_reverted_rule(old, new):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count(old) == 1, "mutation must target the shipped workflow once"
    with pytest.raises(AssertionError):
        _assert_compile_contract(workflow.replace(old, new, 1))
