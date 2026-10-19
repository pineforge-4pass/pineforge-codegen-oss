"""Protect existing workflow concurrency and the trusted gate status."""
import re
from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
CONCURRENCY_RULES = {
    "test.yml": ("test", "github.event_name == 'pull_request'"),
    "gate.yml": (
        "gate",
        "github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_target'",
    ),
}


def job_blocks(text):
    """Read the top-level jobs in the repository's block-style workflows."""
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(
        r"""^  ([\w-]+|'[^']+'|"[^"]+"):[ \t]*(?:#.*)?$""", body, re.M
    ))
    assert matches, "workflow jobs must use the checked block format"
    return {
        match[1].strip("'\""): body[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)
        ]
        for index, match in enumerate(matches)
    }


def assert_no_pr_gate(text):
    trigger = re.search(r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""", text, re.M)
    assert trigger is not None, "workflow must declare its trigger"
    if not re.search(r"\bpull_request\b", trigger[0]):
        return
    for job_id, body in job_blocks(text).items():
        assert job_id != "gate", "pull_request must not publish the trusted gate check"
        name = re.search(r"^    name:[ \t]*([^\n]+)", body, re.M)
        if name:
            assert name[1].split("#", 1)[0].strip().strip("'\"") != "gate", (
                "pull_request must not publish the trusted gate check"
            )


def test_no_pull_request_workflow_can_claim_the_gate_context():
    for workflow in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        assert_no_pr_gate(workflow.read_text(encoding="utf-8"))


@pytest.mark.parametrize("trigger", [
    "on:\n  pull_request:\n    branches: [main]",
    "on: [push, pull_request]",
    "on: pull_request",
    "'on':\n  'pull_request': {}",
])
@pytest.mark.parametrize("job", [
    "  gate:\n    runs-on: ubuntu-24.04",
    "  'gate':\n    runs-on: ubuntu-24.04",
    '  "gate":\n    runs-on: ubuntu-24.04',
    "  tests:\n    name: gate\n    runs-on: ubuntu-24.04",
    "  tests:\n    name: 'gate'\n    runs-on: ubuntu-24.04",
    '  tests:\n    name: "gate" # reserved\n    runs-on: ubuntu-24.04',
])
def test_gate_context_guard_rejects_reserved_ids_and_names(trigger, job):
    with pytest.raises(AssertionError, match="trusted gate check"):
        assert_no_pr_gate(f"{trigger}\n\njobs:\n{job}\n")


def test_gate_context_guard_leaves_the_trusted_target_workflow():
    assert_no_pr_gate("on:\n  pull_request_target:\n\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n")


def assert_pr_concurrency(workflow, prefix, cancel_when):
    assert (
        f"concurrency:\n  group: {prefix}-"
        "${{ github.event.pull_request.number || github.run_id }}\n"
        f"  cancel-in-progress: ${{{{ {cancel_when} }}}}\n"
    ) in workflow
    assert "cancel-in-progress: true" not in workflow
    assert "|| github.ref }}" not in workflow


@pytest.mark.parametrize("filename", CONCURRENCY_RULES)
def test_concurrency_cancels_only_pull_requests(filename):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    assert_pr_concurrency(workflow, *CONCURRENCY_RULES[filename])


@pytest.mark.parametrize("filename,bad_cancel", [
    ("gate.yml", "${{ github.event_name == 'pull_request' }}"),
    ("gate.yml", "true"),
    (
        "gate.yml",
        "${{ github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_target' || github.event_name == 'push' }}",
    ),
    ("test.yml", "true"),
    (
        "test.yml",
        "${{ github.event_name == 'pull_request' || github.event_name == 'push' }}",
    ),
])
def test_concurrency_guard_rejects_cancel_rule_mutations(filename, bad_cancel):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    prefix, cancel_when = CONCURRENCY_RULES[filename]
    original = f"  cancel-in-progress: ${{{{ {cancel_when} }}}}\n"
    assert original in workflow
    mutant = workflow.replace(original, f"  cancel-in-progress: {bad_cancel}\n", 1)
    with pytest.raises(AssertionError):
        assert_pr_concurrency(mutant, prefix, cancel_when)


@pytest.mark.parametrize("filename", CONCURRENCY_RULES)
@pytest.mark.parametrize("bad_group", [
    "${{ github.event.pull_request.number || github.ref }}",
    "${{ github.sha }}",
])
def test_concurrency_guard_rejects_shared_non_pr_groups(filename, bad_group):
    workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
    original = "${{ github.event.pull_request.number || github.run_id }}"
    assert original in workflow
    mutant = workflow.replace(original, bad_group, 1)
    with pytest.raises(AssertionError):
        assert_pr_concurrency(mutant, *CONCURRENCY_RULES[filename])
