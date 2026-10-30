"""Keep the scheduled engine-main drift check separate from PR compile CI."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/engine-drift.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
ENGINE_PIN = "1065942c812ad4e2f15274284a26209fdf237190"
DRIFT_COMMAND = (
    "python -m pytest -q tests/test_host_member_names.py "
    "tests/test_translate_implies_compile.py"
)


def _job(workflow: str) -> str:
    match = re.search(r"^  drift:\n(.*?)(?=\Z)", workflow, re.M | re.S)
    assert match is not None, "missing drift job"
    return match[1]


def _step(job: str, name: str) -> str:
    match = re.search(
        rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - |\Z)",
        job, re.M | re.S,
    )
    assert match is not None, f"missing {name} step"
    return match[1]


def _assert_drift_contract(workflow: str) -> None:
    assert workflow.startswith("name: Engine drift\n")
    trigger = re.search(r"^on:\n(.*?)(?=^permissions:)", workflow, re.M | re.S)
    assert trigger is not None, "missing workflow trigger"
    trigger_body = trigger[1]
    assert re.findall(r"^  ([a-z_]+):\n", trigger_body, re.M) == [
        "schedule", "workflow_dispatch",
    ], "engine drift must have only schedule and workflow_dispatch triggers"
    assert re.findall(r"^  schedule:\n", trigger_body, re.M) == ["  schedule:\n"]
    assert re.findall(r"^    - cron: [^\n]+\n", trigger_body, re.M) == [
        '    - cron: "17 4 * * *"\n',
    ]
    assert "  workflow_dispatch:\n" in trigger_body
    assert not re.search(r"^  (?:push|pull_request):", trigger_body, re.M), (
        "engine drift must not run on repository or pull-request events"
    )
    assert "permissions:\n  contents: read\n" in workflow
    assert re.findall(r"^  [\w-]+:\n", workflow.split("jobs:\n", 1)[1], re.M) == [
        "  drift:\n",
    ]

    job = _job(workflow)
    assert "    runs-on: ubuntu-24.04\n" in job
    assert not re.search(
        r"^\s*(?:timeout-minutes|continue-on-error):", workflow, re.M
    ), "workflow must not set time limits"
    assert "cancel-in-progress:" not in workflow
    steps = job.split("    steps:\n", 1)[1]
    assert steps.startswith("      - name: Check out codegen\n")

    codegen = _step(job, "Check out codegen")
    assert f"        uses: {CHECKOUT}\n" in codegen
    assert "        ref:" not in codegen
    assert "          fetch-depth: 0\n" in codegen
    assert "          persist-credentials: false\n" in codegen

    engine = _step(job, "Check out engine main and its corpus")
    assert f"        uses: {CHECKOUT}\n" in engine
    assert "          repository: pineforge-4pass/pineforge-engine\n" in engine
    assert "          ref: main\n" in engine, (
        "engine drift must keep the engine checkout floating"
    )
    assert "          path: .ci/pineforge-engine\n" in engine
    assert "          submodules: true\n" in engine
    assert "          persist-credentials: false\n" in engine

    env = re.search(r"^    env:\n(.*?)(?=^    steps:)", job, re.M | re.S)
    assert env is not None
    assert '      PINEFORGE_REQUIRE_COMPILE: "1"\n' in env[1], (
        "engine drift must require compile inputs"
    )
    for line in (
        "      PINEFORGE_ENGINE_INCLUDE: ${{ github.workspace }}/.ci/pineforge-engine/include\n",
        "      PINEFORGE_ENGINE_CORPUS: ${{ github.workspace }}/.ci/pineforge-engine/corpus\n",
        "      PINEFORGE_EIGEN_INCLUDE: /usr/include/eigen3\n",
        "      CXX: /usr/bin/g++\n",
        '      PINEFORGE_COMPILE_SHARD: "0"\n',
        '      PINEFORGE_COMPILE_SHARDS: "1"\n',
        "      PINEFORGE_COMPILE_REPORT_DIR: ${{ github.workspace }}/.ci/compile-report\n",
    ):
        assert line in env[1]

    install = _step(job, "Install compile-only prerequisites")
    assert "sudo apt-get install --yes --no-install-recommends g++ libeigen3-dev" in install
    assert "PINEFORGE_ENGINE_SHA=$(git -C .ci/pineforge-engine rev-parse HEAD)" in install
    assert "PINEFORGE_CORPUS_SHA=$(git -C .ci/pineforge-engine/corpus rev-parse HEAD)" in install
    assert 'printf \'PINEFORGE_ENGINE_SHA=%s\\n\' "$PINEFORGE_ENGINE_SHA" >> "$GITHUB_ENV"' in install
    assert 'printf \'PINEFORGE_CORPUS_SHA=%s\\n\' "$PINEFORGE_CORPUS_SHA" >> "$GITHUB_ENV"' in install

    python = _step(job, "Set up Python")
    assert f"        uses: {SETUP_PYTHON}\n" in python
    assert '          python-version: "3.12"\n' in python
    assert '        run: python -m pip install -e ".[dev]"\n' in _step(
        job, "Install with dev extras"
    )

    tests = _step(job, "Run engine drift tests")
    assert "        id: drift_tests\n" in tests
    assert f"        run: {DRIFT_COMMAND}\n" in tests

    summary = _step(job, "Report engine drift")
    assert "        if: always()\n" in summary
    assert "$GITHUB_STEP_SUMMARY" in summary
    assert "$PINEFORGE_ENGINE_SHA" in summary
    assert "ci/engine-pin" in summary
    assert "steps.drift_tests.outcome" in summary

    artifact = _step(job, "Retain drift compile report")
    assert "        if: always()\n" in artifact
    assert f"        uses: {UPLOAD}\n" in artifact
    assert "          name: compile-report-engine-drift\n" in artifact
    assert "          path: ${{ github.workspace }}/.ci/compile-report\n" in artifact
    assert "          if-no-files-found: error\n" in artifact

    actions = set(re.findall(r"^\s+uses: ([^\s]+)", workflow, re.M))
    assert actions <= {CHECKOUT, SETUP_PYTHON, UPLOAD}


def test_engine_drift_workflow_has_the_scheduled_floating_pair():
    _assert_drift_contract(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("old,new,message", [
    (
        "  workflow_dispatch:\n",
        "  pull_request:\n  workflow_dispatch:\n",
        "engine drift must not run on repository or pull-request events",
    ),
    (
        "  drift:\n",
        "  drift:\n    timeout-minutes: 30\n",
        "workflow must not set time limits",
    ),
    (
        "          ref: main\n",
        f"          ref: {ENGINE_PIN}\n",
        "engine drift must keep the engine checkout floating",
    ),
    (
        '      PINEFORGE_REQUIRE_COMPILE: "1"\n',
        "",
        "engine drift must require compile inputs",
    ),
])
def test_engine_drift_workflow_mutations_are_rejected(old, new, message):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _assert_drift_contract(workflow)
    assert workflow.count(old) == 1, "drift mutation must target the shipped workflow once"
    mutated = workflow.replace(old, new, 1)
    with pytest.raises(AssertionError, match=re.escape(message)):
        _assert_drift_contract(mutated)
