# tests/test_release_workflows.py
"""Pin isolated release runners and take every channel's spelling from
scripts/release_version.py, so a prerelease never reaches a `latest` channel."""
import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
NPM = (WORKFLOWS / "publish-pyodide.yml").read_text(encoding="utf-8")
PROMOTE = (WORKFLOWS / "promote-baseline.yml").read_text(encoding="utf-8")
RELEASE_WORKFLOWS = {"release.yml": RELEASE, "publish-pyodide.yml": NPM}
RELEASE_RUNNER = '["self-hosted","pf-ci","pf-ci-release"]'
BAD_RELEASE_RUNNERS = (
    "ubuntu-latest",
    "ubuntu-24.04",
    '["self-hosted","pf-ci","pf-ci-x64"]',
    "${{ fromJSON('[\"self-hosted\",\"pf-ci\",\"pf-ci-release\"]') || 'ubuntu-24.04' }}",
)
RELEASE_JOBS = (
    ("release.yml", "release"),
    ("release.yml", "claim-name"),
    ("publish-pyodide.yml", "publish"),
    ("publish-pyodide.yml", "claim-npm"),
)


def assert_release_runner(text, job_id):
    """Check a named job and its workflow's trigger in the current block format."""
    trigger = re.search(
        r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t]+[^\n]*|))*""",
        text,
        re.M,
    )
    assert trigger is not None, "release workflow must declare its trigger"
    assert re.search(r"\bpull_request(?:_target)?\b", trigger[0]) is None, (
        "release runner must never run on pull_request or pull_request_target"
    )
    assert_isolated_release_runner(text, job_id)


def assert_isolated_release_runner(text, job_id):
    """Pin one named job's exact runner without changing trigger policy."""
    jobs = re.findall(
        rf"^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)",
        text,
        re.M | re.S,
    )
    assert len(jobs) == 1, f"release job {job_id} must be present exactly once"
    runners = re.findall(r"^    runs-on:[ \t]*([^\n]*)$", jobs[0], re.M)
    assert runners == [RELEASE_RUNNER], (
        f"{job_id} must use the isolated release runner without a hosted fallback"
    )


def assert_promote_runner(text):
    """L6 addendum 2 permits only promote's existing pull_request_target path."""
    assert re.search(r"^  pull_request:", text, re.M) is None, (
        "baseline promotion must never run on pull_request"
    )
    assert_isolated_release_runner(text, "promote")


@pytest.mark.parametrize("filename,job_id", RELEASE_JOBS)
def test_release_jobs_use_the_isolated_runner(filename, job_id):
    assert_release_runner(RELEASE_WORKFLOWS[filename], job_id)


@pytest.mark.parametrize("filename,job_id", RELEASE_JOBS)
def test_release_runner_pin_rejects_missing_jobs(filename, job_id):
    workflow = RELEASE_WORKFLOWS[filename]
    assert_release_runner(workflow, job_id)
    mutant, count = re.subn(
        rf"^  {re.escape(job_id)}:\n",
        f"  removed-{job_id}:\n",
        workflow,
        count=1,
        flags=re.M,
    )
    assert count == 1, "job mutation must change the workflow"
    with pytest.raises(AssertionError, match="must be present exactly once"):
        assert_release_runner(mutant, job_id)


@pytest.mark.parametrize("filename,job_id", RELEASE_JOBS)
@pytest.mark.parametrize("bad_runner", BAD_RELEASE_RUNNERS)
def test_release_runner_pin_rejects_hosted_and_shared_runners(filename, job_id, bad_runner):
    workflow = RELEASE_WORKFLOWS[filename]
    assert_release_runner(workflow, job_id)
    marker = f"\n  {job_id}:\n"
    before, after = workflow.split(marker, 1)
    mutant = before + marker + after.replace(
        f"    runs-on: {RELEASE_RUNNER}\n", f"    runs-on: {bad_runner}\n", 1
    )
    assert mutant != workflow, "runner mutation must change the named job"
    with pytest.raises(AssertionError, match="isolated release runner"):
        assert_release_runner(mutant, job_id)


@pytest.mark.parametrize("filename,job_id", RELEASE_JOBS)
@pytest.mark.parametrize("event", ["pull_request", "pull_request_target"])
def test_release_runner_pin_rejects_pull_request_triggers(filename, job_id, event):
    workflow = RELEASE_WORKFLOWS[filename]
    assert_release_runner(workflow, job_id)
    mutant, count = re.subn(
        r"^on:\n", f"on:\n  {event}:\n", workflow, count=1, flags=re.M
    )
    assert count == 1, "trigger mutation must change the workflow"
    with pytest.raises(AssertionError, match="must never run on pull_request"):
        assert_release_runner(mutant, job_id)


def test_baseline_promotion_uses_the_isolated_runner():
    assert_promote_runner(PROMOTE)


def test_promote_runner_pin_rejects_a_missing_job():
    assert_promote_runner(PROMOTE)
    mutant, count = re.subn(
        r"^  promote:\n", "  removed-promote:\n", PROMOTE, count=1, flags=re.M
    )
    assert count == 1, "job mutation must change the workflow"
    with pytest.raises(AssertionError, match="must be present exactly once"):
        assert_promote_runner(mutant)


@pytest.mark.parametrize("bad_runner", BAD_RELEASE_RUNNERS)
def test_promote_runner_pin_rejects_hosted_and_shared_runners(bad_runner):
    assert_promote_runner(PROMOTE)
    marker = "\n  promote:\n"
    before, after = PROMOTE.split(marker, 1)
    mutant = before + marker + after.replace(
        f"    runs-on: {RELEASE_RUNNER}\n", f"    runs-on: {bad_runner}\n", 1
    )
    assert mutant != PROMOTE, "runner mutation must change the promote job"
    with pytest.raises(AssertionError, match="isolated release runner"):
        assert_promote_runner(mutant)


def test_promote_runner_pin_rejects_a_pull_request_trigger():
    assert_promote_runner(PROMOTE)
    mutant, count = re.subn(
        r"^on:\n", "on:\n  pull_request:\n", PROMOTE, count=1, flags=re.M
    )
    assert count == 1, "trigger mutation must change the workflow"
    with pytest.raises(AssertionError, match="must never run on pull_request"):
        assert_promote_runner(mutant)


def step(text, name):
    """The body of the workflow step whose `- name:` line starts with name."""
    start = text.index(f"- name: {name}")
    nxt = text.find("\n      - ", start + 1)
    return text[start:] if nxt < 0 else text[start:nxt]


def has_env(body, name, expr):
    """True when the step sets env `name` to `${{ expr }}` (any alignment)."""
    return re.search(rf"^\s+{name}:\s+\$\{{\{{ {re.escape(expr)} \}}\}}$", body, re.M) is not None


def test_new_version_and_channels_come_from_the_script():
    body = step(RELEASE, "Compute new version")
    assert "python3 scripts/release_version.py next" in body
    assert '--override="$OVERRIDE"' in body
    assert 'tee -a "$GITHUB_OUTPUT"' in body


def test_built_dists_carry_the_pypi_spelling():
    body = step(RELEASE, "Build sdist + wheel")
    assert has_env(body, "PYPI_VERSION", "steps.ver.outputs.pypi")
    assert "dist/pineforge_codegen-${PYPI_VERSION}.tar.gz" in body


def test_git_tag_is_the_channel_tag():
    body = step(RELEASE, "Commit + tag + push")
    assert has_env(body, "GIT_TAG", "steps.ver.outputs.git_tag")
    assert 'git tag "${GIT_TAG}"' in body


def test_prerelease_github_release_is_never_latest():
    body = step(RELEASE, "Create GitHub Release")
    assert has_env(body, "PRERELEASE", "steps.ver.outputs.prerelease")
    assert "--prerelease --latest=false" in body
    assert "pip install pineforge-codegen==${PYPI_VERSION}" in body


def test_hub_dispatch_carries_the_prerelease_flag():
    body = step(RELEASE, "Dispatch codegen-release")
    assert '-F "client_payload[version]=${NEW_VERSION}"' in body
    assert '-F "client_payload[prerelease]=${PRERELEASE}"' in body


def test_npm_publish_names_a_dist_tag_only_for_a_prerelease():
    # An explicit --tag (even latest) bypasses npm's refusal to move latest to a
    # lower version, so a stable publish keeps the plain command.
    assert "python3 scripts/release_version.py channels" in step(NPM, "Resolve npm dist-tag")
    body = step(NPM, "Publish")
    assert has_env(body, "DIST_TAG", "steps.channel.outputs.npm_dist_tag")
    assert 'if [ "$DIST_TAG" != latest ]; then tag=(--tag "$DIST_TAG"); fi' in body
    assert body.count('${tag[@]+"${tag[@]}"}') == 2  # the real publish and the dry run
    assert "--tag latest" not in body


def test_github_release_flag_fails_closed():
    body = step(RELEASE, "Create GitHub Release")
    assert 'case "$PRERELEASE" in true|false) ;;' in body
