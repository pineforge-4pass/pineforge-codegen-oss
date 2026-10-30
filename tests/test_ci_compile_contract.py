"""Keep the shipped PR workflow dependent on real, paired compile checks."""

from pathlib import Path
import re

import pytest


WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/test.yml"


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
