# tests/test_release_workflows.py
"""Pin isolated release runners, main-ref triggers and the tagged npm dispatch.

Take channel spellings from scripts/release_version.py so a prerelease never
reaches a `latest` channel. Execute the shipped tag guard against temporary
git repositories; mutation tests use the shipped workflow text too.
"""
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

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
    ("publish-pyodide.yml", "tag-guard"),
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
    marker = f"- name: {name}"
    assert marker in text, f"workflow step {name} must be present"
    start = text.index(marker)
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
    body = step(RELEASE, "Commit + tag + push release tree")
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


def workflow_triggers(text):
    """Read our block-style trigger keys, refusing an unchecked YAML shape."""
    assert len(re.findall(r"""^(?:on|'on'|"on"):""", text, re.M)) == 1, (
        "workflow must declare exactly one trigger block"
    )
    match = re.search(
        r"""^(?:on|'on'|"on"):[^\n]*(?:\n(?:[ \t#][^\n]*|))*""",
        text,
        re.M,
    )
    assert match is not None, "workflow must declare its trigger"
    trigger = match[0]
    assert re.fullmatch(r"""(?:on|'on'|"on"):[ \t]*(?:#.*)?""", trigger.splitlines()[0]), (
        "release triggers must use the checked block format"
    )
    events = list(re.finditer(r"^  ([a-z_]+):[ \t]*(?:#.*)?$", trigger, re.M))
    event_lines = re.findall(r"^  [^\s#][^\n]*$", trigger, re.M)
    assert events and len(events) == len(event_lines), (
        "release trigger events must use the checked block format"
    )
    names = [event[1] for event in events]
    assert len(names) == len(set(names)), "duplicate release trigger"
    return {
        event[1]: trigger[
            event.end():events[index + 1].start() if index + 1 < len(events) else len(trigger)
        ]
        for index, event in enumerate(events)
    }


def assert_main_ref_triggers(text):
    """A pf-ci-release workflow may only use the approved main-ref events."""
    events = workflow_triggers(text)
    allowed = {
        "workflow_dispatch", "schedule", "repository_dispatch", "workflow_call",
        "push", "pull_request_target",
    }
    assert set(events) <= allowed, "pf-ci-release trigger can start outside main"
    if "push" not in events:
        return
    push = events.get("push", "")
    filters = re.findall(r"^    ([a-z_-]+):", push, re.M)
    filter_lines = re.findall(r"^    [^\s#][^\n]*$", push, re.M)
    assert filters and len(filters) == len(filter_lines) and len(filters) == len(set(filters)), (
        "release push filters must use the checked block format without duplicates"
    )
    assert set(filters) <= {"branches", "paths", "paths-ignore"}, (
        "pf-ci-release push must select only main, without tags"
    )
    branches = re.search(r"^    branches:([^\n]*)(?:\n(?: {6,}[^\n]*|))*", push, re.M)
    assert branches is not None, "pf-ci-release push must select only main"
    branch_text = branches[0].split(":", 1)[1]
    branch_text = "\n".join(line.split("#", 1)[0] for line in branch_text.splitlines()).strip()
    assert re.fullmatch(r"""\[\s*(?:main|'main'|"main")\s*\]|-\s+(?:main|'main'|"main")""", branch_text), (
        "pf-ci-release push must select only main"
    )


def release_runner_workflows():
    """Discover every workflow using the release label, including future jobs."""
    for path in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        text = path.read_text(encoding="utf-8")
        if "pf-ci-release" in text:
            yield path.name, text


def mutate_once(text, original, replacement):
    """A stale literal is a failed test, never an unchanged passing mutant."""
    assert text.count(original) == 1, f"mutation must match shipped text exactly once: {original!r}"
    mutant = text.replace(original, replacement, 1)
    assert mutant != text, "mutation must change shipped workflow text"
    return mutant


def test_every_release_runner_workflow_has_only_main_ref_triggers():
    workflows = list(release_runner_workflows())
    assert workflows, "must discover the release runner workflows"
    for filename, text in workflows:
        assert_main_ref_triggers(text)


@pytest.mark.parametrize("event", [
    '  push:\n    tags: ["v*"]\n',
    "  release:\n    types: [published]\n",
    "  workflow_run:\n    workflows: [Release]\n    types: [completed]\n",
    "  pull_request:\n",
    "  push:\n",
    "  push:\n    branches: [main, develop]\n",
    "  push:\n    branches: ['main*']\n",
    "  push:\n    branches-ignore: [develop]\n",
    "  push:\n    branches: [main]\n    tags: ['v*']\n",
    "  push:\n    branches: [main]\n    'tags': ['v*']\n",
    "  push:\n    branches: [main]\n    branches: [develop]\n",
    "  push:\n    branches:\n      - main\n      - develop\n",
    "# A root-level comment must not hide a later event.\n  release:\n",
])
def test_generic_release_trigger_pin_rejects_real_workflow_mutations(event):
    for filename, text in release_runner_workflows():
        assert_main_ref_triggers(text)
        mutant = mutate_once(text, "\non:\n", "\non:\n" + event)
        with pytest.raises(AssertionError):
            assert_main_ref_triggers(mutant)


@pytest.mark.parametrize("replacement", [
    "\nremoved_on:\n",
    "\non: [workflow_dispatch, release]\n",
    "\non:\n  'release':\n",
    "\non:\n  release: {}\n",
    "\non:\n  workflow_dispatch:\n",
    "\non:\n  workflow_dispatch:\non:\n",
])
def test_generic_release_trigger_pin_rejects_unchecked_trigger_shapes(replacement):
    for filename, text in release_runner_workflows():
        assert_main_ref_triggers(text)
        mutant = mutate_once(text, "\non:\n", replacement)
        with pytest.raises(AssertionError):
            assert_main_ref_triggers(mutant)


@pytest.mark.parametrize("event", [
    "  push:\n    branches: [main]\n",
    "  push:\n    branches:\n      - main\n",
    "  schedule:\n    - cron: '0 0 * * *'\n",
    "  repository_dispatch:\n",
    "  workflow_call:\n",
])
def test_generic_release_trigger_pin_accepts_approved_events(event):
    assert_main_ref_triggers(mutate_once(NPM, "\non:\n", "\non:\n" + event))


TAG_FORMAT_CHECK = r'if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]]; then'
TAG_PATTERN_BLOCK = (
    f"          {TAG_FORMAT_CHECK}\n"
    '            echo "::error::invalid release tag \'$TAG\'"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_EXISTENCE_BLOCK = (
    '          if ! TAG_COMMIT=$(git rev-parse -q --verify "refs/tags/${TAG}^{commit}"); then\n'
    '            echo "::error::release tag \'$TAG\' does not exist or does not point to a commit"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_REACHABILITY_BLOCK = (
    '          if ! git merge-base --is-ancestor "$TAG_COMMIT" origin/main; then\n'
    '            echo "::error::release tag \'$TAG\' is not reachable from origin/main"\n'
    "            exit 1\n"
    "          fi\n"
)
NPM_DISPATCH_COMMAND = (
    'gh workflow run publish-pyodide.yml --ref main -f tag="${GIT_TAG}" -f dry_run=false'
)


def workflow_jobs(text):
    """Read checked job blocks; absent/deleted keys fail assertions, not KeyError."""
    assert text.count("\njobs:\n") == 1, "workflow must have one jobs block"
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([\w-]+):[ \t]*(?:#.*)?$", body, re.M))
    lines = re.findall(r"^  [^\s#][^\n]*$", body, re.M)
    assert matches and len(matches) == len(lines), "jobs must use the checked block format"
    names = [match[1] for match in matches]
    assert len(names) == len(set(names)), "duplicate workflow job"
    return {
        match[1]: body[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)
        ]
        for index, match in enumerate(matches)
    }


def checkout_steps(body):
    checkouts = re.findall(
        r"^      - uses: actions/checkout@[^\n]*\n(?: {8}[^\n]*\n)*", body, re.M
    )
    assert len(checkouts) == body.count("uses: actions/checkout@"), (
        "every checkout must use the checked step format"
    )
    return checkouts


def tag_guard_script(text):
    """Extract the actual bash script; never substitute a test implementation."""
    guard = workflow_jobs(text).get("tag-guard", "")
    body = step(guard, "Verify release tag")
    scripts = re.findall(r"^        run: \|\n((?: {10}[^\n]*\n|\n)+)", body, re.M)
    assert len(scripts) == 1, "tag guard must contain one literal bash script"
    return dedent(scripts[0])


def assert_tag_guard(text):
    jobs = workflow_jobs(text)
    assert next(iter(jobs), "") == "tag-guard", "tag-guard must be the first job"
    guard = jobs.get("tag-guard", "")
    assert guard, "tag-guard job must exist"
    header = guard.split("    steps:\n", 1)[0]
    assert re.search(r"^    if:", header, re.M) is None, "tag-guard must never be skipped"
    assert re.search(r"^    needs:", header, re.M) is None, "tag-guard must run first"
    assert "continue-on-error" not in guard, "tag-guard must fail closed"
    assert "secrets." not in guard, "tag-guard must not use secrets"
    permissions = re.search(r"^    permissions:\n((?: {6}[^\n]*\n)+)", guard, re.M)
    assert permissions is not None, "tag-guard must declare read-only permissions"
    assert permissions[1] == "      contents: read\n", "tag-guard may only read contents"
    assert "      sha: ${{ steps.tag.outputs.sha }}\n" in guard, "export the verified SHA"
    assert_isolated_release_runner(text, "tag-guard")
    checkouts = checkout_steps(guard)
    assert len(checkouts) == 1, "tag-guard must check out main once"
    checkout = checkouts[0]
    for line in (
        "          ref: main\n",
        "          fetch-depth: 0\n",
        "          persist-credentials: false\n",
    ):
        assert line in checkout, "tag-guard needs main, full history and no saved credentials"
    assert "          token:" not in checkout, "guard checkout must use the read-only token"
    validation = step(guard, "Verify release tag")
    assert "        id: tag\n" in validation
    assert "        shell: bash\n" in validation
    assert "        if:" not in validation, "tag verification must never be skipped"
    assert has_env(validation, "TAG", "inputs.tag"), "guard must use the input through env"
    assert has_env(validation, "DISPATCH_REF", "github.ref"), "guard must check the run ref"
    script = tag_guard_script(text)
    assert "${{" not in script, "guard inputs must never be interpolated in run"
    assert "set -euo pipefail\n" in script, "guard must fail on command errors"
    assert 'if [ "$DISPATCH_REF" != refs/heads/main ]; then' in script, (
        "tag-guard must refuse a dispatch outside main"
    )
    for block in (TAG_PATTERN_BLOCK, TAG_EXISTENCE_BLOCK, TAG_REACHABILITY_BLOCK):
        assert block in validation, "tag-guard must check pattern, existence and reachability"
    assert 'if ! [[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then' in script
    assert 'printf \'sha=%s\\n\' "$TAG_COMMIT" >> "$GITHUB_OUTPUT"' in script
    assert "git fetch --tags origin\n" in script, "guard must fetch repository tags"
    assert "git fetch origin +refs/heads/main:refs/remotes/origin/main\n" in script, (
        "guard must refresh origin/main"
    )
    assert script.index('if [ "$DISPATCH_REF"') < script.index(TAG_FORMAT_CHECK) < script.index(
        "git fetch"
    ) < script.index("git rev-parse") < script.index("git merge-base") < script.index(
        "printf 'sha="
    ), "validate the ref/name before git and emit only the fully verified commit"
    assert guard.index(checkout) < guard.index(validation), "guard must read a main checkout"
    assert guard.count("uses:") == 1 and guard.count("run:") == 1, (
        "guard must not run repository code or other actions"
    )
    for job_id, body in jobs.items():
        if job_id == "tag-guard":
            continue
        assert "    needs: tag-guard\n" in body, f"{job_id} must depend on tag-guard"
        assert "always()" not in body and "continue-on-error" not in body, (
            f"{job_id} must not bypass a failed or skipped guard"
        )
        for checkout in checkout_steps(body):
            assert "          ref: ${{ needs.tag-guard.outputs.sha }}\n" in checkout, (
                "tagged checkouts must use the verified commit, never the tag name"
            )
            assert "          persist-credentials: false\n" in checkout, (
                "tagged checkouts must not persist credentials"
            )
            assert "          token:" not in checkout, "tagged checkout must be read-only"
    assert "contents: write" not in text, "tagged workflow must keep read-only contents"


def assert_tagged_npm_dispatch(text):
    events = workflow_triggers(text)
    assert set(events) == {"workflow_dispatch"}, "npm must be dispatch-only"
    tag_input = re.search(
        r"^      tag:\n((?: {8}[^\n]*\n)+)", events.get("workflow_dispatch", ""), re.M
    )
    assert tag_input is not None, "required tag input must exist"
    assert "        required: true\n" in tag_input[1], "tag must be required"
    assert "        type: string\n" in tag_input[1], "tag must be a string"
    assert "        default:" not in tag_input[1], "tag must be supplied explicitly"
    assert "github.ref_name" not in text, "npm tag must come from the input"
    assert "startsWith(github.ref" not in text, "npm must not test for a tag-ref run"
    assert "group: publish-pyodide-${{ inputs.tag }}" in text
    for job_id, claim in (("publish", "!inputs.claim_placeholder"), ("claim-npm", "inputs.claim_placeholder")):
        assert (
            f"    if: ${{{{ github.ref == 'refs/heads/main' && {claim} }}}}\n"
        ) in workflow_jobs(text).get(job_id, ""), f"{job_id} must refuse dispatches outside main"
    scripts = re.findall(
        r"^(?:        run:|      - run:)[^\n]*(?:\n(?: {10}[^\n]*|))*", text, re.M
    )
    assert scripts, "npm workflow must contain checked run steps"
    assert all("${{" not in script for script in scripts), "run inputs must pass through env"

    assert_tag_guard(text)
    publish_job = workflow_jobs(text).get("publish", "")
    checkouts = checkout_steps(publish_job)
    assert len(checkouts) == 1, "npm must check out the verified commit once"
    verify = step(publish_job, "Verify tag matches VERSION")
    assert "        if:" not in verify, "every publish must verify VERSION"
    assert has_env(verify, "TAG_NAME", "inputs.tag"), "VERSION check must use the tag env"
    assert 'tag="${TAG_NAME#v}"; file="$(tr -d \'[:space:]\' < VERSION)"' in verify
    assert 'if [ "$tag" != "$file" ]; then' in verify, "tag must equal v plus VERSION"
    assert 'echo "::error::tag \'$TAG_NAME\' != VERSION \'$file\'"; exit 1' in verify
    assert publish_job.index(checkouts[0]) < publish_job.index(verify) < publish_job.index(
        "- name: Install + run the gate"
    ), "checkout and VERSION verification must precede the gate"
    publish = step(publish_job, "Publish (real on dispatch")
    assert has_env(publish, "DRY_RUN", "inputs.dry_run")
    assert '[ "$EVENT" = "workflow_dispatch" ] && [ "$DRY_RUN" = "false" ]' in publish, (
        "a real dispatched publish requires dry_run=false"
    )


def test_npm_dispatch_uses_main_and_a_verified_tag_commit():
    assert_tagged_npm_dispatch(NPM)


@pytest.mark.parametrize("original,replacement", [
    ("\non:\n", '\non:\n  push:\n    tags: ["v*"]\n'),
    ("\non:\n", "\non:\n  release:\n"),
    ("\non:\n", "\non:\n  workflow_run:\n"),
    ("      tag:\n", "      removed_tag:\n"),
    ("        required: true\n        type: string\n", "        required: false\n        type: string\n"),
    ("        required: true\n        type: string\n", "        required: true\n        type: boolean\n"),
    ("        required: true\n        type: string\n", '        required: true\n        type: string\n        default: ""\n'),
    ("group: publish-pyodide-${{ inputs.tag }}", "group: publish-pyodide-${{ github.ref }}"),
    ("github.ref == 'refs/heads/main' && !inputs.claim_placeholder", "!inputs.claim_placeholder"),
    ("github.ref == 'refs/heads/main' && inputs.claim_placeholder", "inputs.claim_placeholder"),
    ("  tag-guard:\n", "  removed-tag-guard:\n"),
    ("  tag-guard:\n", "  tag-guard:\n    if: false\n"),
    ("  tag-guard:\n", "  tag-guard:\n    needs: publish\n"),
    ("  tag-guard:\n", "  tag-guard:\n    continue-on-error: true\n"),
    ("      contents: read\n", "      contents: write\n"),
    ("      contents: read\n", "      contents: read\n      id-token: write\n"),
    ("    permissions:\n      contents: read\n", ""),
    ("      sha: ${{ steps.tag.outputs.sha }}\n", ""),
    ("      sha: ${{ steps.tag.outputs.sha }}", "      sha: ${{ inputs.tag }}"),
    ("        id: tag\n", "        id: unchecked\n"),
    ("        shell: bash\n", "        shell: sh\n"),
    ('if [ "$DISPATCH_REF" != refs/heads/main ]; then', 'if [ "$DISPATCH_REF" = refs/heads/main ]; then'),
    (TAG_PATTERN_BLOCK, ""),
    (TAG_EXISTENCE_BLOCK, ""),
    (TAG_REACHABILITY_BLOCK, ""),
    ("          git fetch --tags origin\n", ""),
    ("          git fetch origin +refs/heads/main:refs/remotes/origin/main\n", ""),
    ("          fetch-depth: 0\n", "          fetch-depth: 1\n"),
    ("          ref: main\n", "          ref: ${{ inputs.tag }}\n"),
    ('if ! [[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then', 'if false; then'),
    ('printf \'sha=%s\\n\' "$TAG_COMMIT"', 'printf \'sha=%s\\n\' "$TAG"'),
    ("          ref: ${{ needs.tag-guard.outputs.sha }}\n", ""),
    ("          ref: ${{ needs.tag-guard.outputs.sha }}", "          ref: ${{ inputs.tag }}"),
    ('if [ "$tag" != "$file" ]; then', 'if [ "$tag" = "$file" ]; then'),
    ('[ "$DRY_RUN" = "false" ]', '[ "$DRY_RUN" = "true" ]'),
])
def test_npm_dispatch_pin_rejects_real_workflow_mutations(original, replacement):
    assert_tagged_npm_dispatch(NPM)
    mutant = mutate_once(NPM, original, replacement)
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


@pytest.mark.parametrize("job_id", ["publish", "claim-npm"])
@pytest.mark.parametrize("original,replacement", [
    ("    needs: tag-guard\n", ""),
    ("    needs: tag-guard\n", "    needs: tag-guard\n    continue-on-error: true\n"),
    ("    if: ${{ github.ref", "    if: ${{ always() && github.ref"),
])
def test_npm_jobs_cannot_bypass_a_failed_or_skipped_guard(job_id, original, replacement):
    assert_tagged_npm_dispatch(NPM)
    body = workflow_jobs(NPM).get(job_id, "")
    mutant = mutate_once(NPM, body, mutate_once(body, original, replacement))
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


@pytest.mark.parametrize("job_id", ["tag-guard", "publish"])
@pytest.mark.parametrize("original,replacement", [
    ("          persist-credentials: false\n", ""),
    ("          persist-credentials: false\n", "          persist-credentials: true\n"),
    ("        with:\n", "        with:\n          token: ${{ secrets.PINEFORGE_APP_ID }}\n"),
])
def test_guard_and_tagged_checkouts_cannot_persist_or_elevate_credentials(job_id, original, replacement):
    assert_tagged_npm_dispatch(NPM)
    body = workflow_jobs(NPM).get(job_id, "")
    # Restrict the mutation to the checkout; publish has other with blocks.
    checkout = checkout_steps(body)[0]
    mutant = mutate_once(NPM, checkout, mutate_once(checkout, original, replacement))
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


@pytest.mark.parametrize("step_name", ["Verify release tag", "Verify tag matches VERSION"])
def test_npm_dispatch_pin_rejects_ref_name_and_run_interpolation(step_name):
    assert_tagged_npm_dispatch(NPM)
    original = step(NPM, step_name)
    for replacement in (
        original.replace("${{ inputs.tag }}", "${{ github.ref_name }}"),
        original.replace(
            "        run: |\n",
            '        run: |\n          echo "${{ inputs.tag }}"\n',
            1,
        ),
    ):
        mutant = mutate_once(NPM, original, replacement)
        with pytest.raises(AssertionError):
            assert_tagged_npm_dispatch(mutant)


def test_npm_version_verification_cannot_be_skipped():
    assert_tagged_npm_dispatch(NPM)
    original = step(NPM, "Verify tag matches VERSION")
    replacement = mutate_once(original, "        env:\n", "        if: false\n        env:\n")
    mutant = mutate_once(NPM, original, replacement)
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


@pytest.mark.parametrize("original,replacement", [
    ("          set -euo pipefail\n", ""),
    ("          DISPATCH_REF: ${{ github.ref }}\n", ""),
    ("          TAG: ${{ inputs.tag }}\n", ""),
    ("        run: |\n", "        if: false\n        run: |\n"),
])
def test_guard_step_cannot_lose_its_checks_or_environment(original, replacement):
    assert_tagged_npm_dispatch(NPM)
    guard = workflow_jobs(NPM).get("tag-guard", "")
    mutant = mutate_once(NPM, guard, mutate_once(guard, original, replacement))
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


def test_guard_pattern_must_precede_every_git_command():
    assert_tagged_npm_dispatch(NPM)
    mutant = mutate_once(NPM, TAG_PATTERN_BLOCK, "")
    mutant = mutate_once(
        mutant,
        "          git fetch --tags origin\n",
        "          git fetch --tags origin\n" + TAG_PATTERN_BLOCK,
    )
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


def test_guard_cannot_run_repository_code_before_validation():
    assert_tagged_npm_dispatch(NPM)
    guard = workflow_jobs(NPM).get("tag-guard", "")
    replacement = mutate_once(
        guard,
        "    steps:\n",
        "    steps:\n      - run: python scripts/release_version.py channels 1.0.0\n",
    )
    mutant = mutate_once(NPM, guard, replacement)
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)


@dataclass(frozen=True)
class GuardRepository:
    checkout: Path
    env: dict[str, str]
    base: str
    head: str
    annotated_object: str


def run_guard_command(argv, cwd, env):
    """Missing/blocked git or bash is a hard test failure, never a silent skip."""
    try:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    except OSError as exc:
        pytest.fail(f"real-git tag-guard tests require executable git and bash: {exc}")


@pytest.fixture
def guard_repository(tmp_path):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "TAG_COMMIT"
    }
    env.pop("BASH_ENV", None)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_AUTHOR_NAME": "Guard test",
        "GIT_AUTHOR_EMAIL": "guard-test@example.invalid",
        "GIT_COMMITTER_NAME": "Guard test",
        "GIT_COMMITTER_EMAIL": "guard-test@example.invalid",
    })
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args, cwd=origin):
        result = run_guard_command(["git", *args], cwd, env)
        assert result.returncode == 0, f"git fixture failed: {args!r}\n{result.stdout}\n{result.stderr}"
        return result.stdout.strip()

    git("init", "--initial-branch=main", "--object-format=sha1")
    git("commit", "--allow-empty", "-m", "main one")
    base = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "main two")
    head = git("rev-parse", "HEAD")
    git("tag", "v1.0.0", base)
    git("tag", "not-a-release", base)
    git("tag", "v1.1.0-rc.2", head)
    git("tag", "-a", "v1.1.0", "-m", "annotated main tag", head)
    annotated_object = git("rev-parse", "refs/tags/v1.1.0")
    git("tag", "v2.0.0", git("rev-parse", "HEAD^{tree}"))
    git("switch", "-c", "side", base)
    git("commit", "--allow-empty", "-m", "unmerged side commit")
    git("tag", "v9.9.9")
    git("switch", "main")
    checkout = tmp_path / "checkout"
    git("clone", "--no-local", "--no-tags", "--branch", "main", str(origin), str(checkout))
    assert git("tag", "--list", cwd=checkout) == "", "guard must fetch its tags"
    assert git("rev-list", "--count", "main", cwd=checkout) == "2"
    assert annotated_object != head, "fixture must distinguish tag object and commit"
    return GuardRepository(checkout, env, base, head, annotated_object)


def run_tag_guard(text, repository, tmp_path, tag, dispatch_ref):
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = dict(repository.env, TAG=tag, DISPATCH_REF=dispatch_ref, GITHUB_OUTPUT=str(output))
    result = run_guard_command(
        ["bash", "--noprofile", "--norc", "-c", tag_guard_script(text)],
        repository.checkout,
        env,
    )
    return result, output.read_text(encoding="utf-8")


def assert_guard_result(result, output, expected_status, expected_sha, error):
    assert result.returncode == expected_status, (
        f"guard status {result.returncode}, expected {expected_status}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    if expected_status == 0:
        assert output == f"sha={expected_sha}\n", "output must be the peeled commit SHA"
        assert re.fullmatch(r"sha=[0-9a-f]{40}\n", output), "emit exactly one 40-hex SHA"
        assert "::error::" not in result.stdout
    else:
        assert output == "", "a refused tag must never expose a checkout SHA"
        assert f"::error::{error}" in result.stdout, "refusals must explain the failure"


# Literal expectations; every row executes the script extracted from the workflow.
@pytest.mark.parametrize(
    "tag,dispatch_ref,status,commit,error",
    [
        pytest.param("v1.0.0", "refs/heads/main", 0, "base", None, id="main-ancestor"),
        pytest.param("v1.1.0-rc.2", "refs/heads/main", 0, "head", None, id="main-rc"),
        pytest.param(
            "not-a-release", "refs/heads/main", 1, None,
            "invalid release tag", id="invalid-name-existing-tag",
        ),
        pytest.param(
            "v8.8.8", "refs/heads/main", 1, None,
            "release tag 'v8.8.8' does not exist or does not point to a commit",
            id="missing-tag",
        ),
        pytest.param(
            "v9.9.9", "refs/heads/main", 1, None,
            "release tag 'v9.9.9' is not reachable from origin/main",
            id="unmerged-side-branch",
        ),
        pytest.param("v1.1.0", "refs/heads/main", 0, "head", None, id="annotated-main"),
        pytest.param(
            "v2.0.0", "refs/heads/main", 1, None,
            "release tag 'v2.0.0' does not exist or does not point to a commit",
            id="tag-points-to-tree",
        ),
        pytest.param("", "refs/heads/main", 1, None, "invalid release tag", id="empty"),
        pytest.param(
            "v1.0.0; echo injected", "refs/heads/main", 1, None,
            "invalid release tag", id="shell-input",
        ),
        pytest.param(
            "v1.0.0", "refs/heads/side", 1, None,
            "workflow must be dispatched on refs/heads/main", id="branch-dispatch",
        ),
        pytest.param(
            "v1.0.0", "refs/tags/v1.0.0", 1, None,
            "workflow must be dispatched on refs/heads/main", id="tag-dispatch",
        ),
    ],
)
def test_tag_guard_executes_against_real_git(
    guard_repository, tmp_path, tag, dispatch_ref, status, commit, error
):
    result, output = run_tag_guard(NPM, guard_repository, tmp_path, tag, dispatch_ref)
    expected_sha = getattr(guard_repository, commit) if commit is not None else None
    assert_guard_result(result, output, status, expected_sha, error)
    if tag == "v1.1.0":
        assert output != f"sha={guard_repository.annotated_object}\n"


@pytest.mark.parametrize(
    "block,tag,status,commit,error",
    [
        (TAG_PATTERN_BLOCK, "not-a-release", 1, None, "invalid release tag"),
        (TAG_EXISTENCE_BLOCK, "v1.0.0", 0, "base", None),
        (
            TAG_REACHABILITY_BLOCK, "v9.9.9", 1, None,
            "release tag 'v9.9.9' is not reachable from origin/main",
        ),
    ],
)
def test_real_git_oracle_rejects_removed_guard_checks(
    guard_repository, tmp_path, block, tag, status, commit, error
):
    expected_sha = getattr(guard_repository, commit) if commit is not None else None
    baseline = run_tag_guard(NPM, guard_repository, tmp_path, tag, "refs/heads/main")
    assert_guard_result(*baseline, status, expected_sha, error)
    mutant = mutate_once(NPM, block, "")
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)
    result, output = run_tag_guard(mutant, guard_repository, tmp_path, tag, "refs/heads/main")
    # Removing existence resolution also removes TAG_COMMIT, so a valid tag
    # must stop satisfying the success oracle; checking only missing tags
    # here would mistake an unbound-variable error for a working guard.
    with pytest.raises(AssertionError, match="guard status"):
        assert_guard_result(result, output, status, expected_sha, error)


def assert_release_dispatches_npm(text):
    assert set(workflow_triggers(text)) == {"workflow_dispatch"}, "release must be dispatch-only"
    jobs = re.findall(r"^  release:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S)
    assert len(jobs) == 1, "release job must be present exactly once"
    release = jobs[0]
    tag = step(release, "Commit + tag + push release tree")
    dispatch = step(release, "Dispatch publish-pyodide on main")
    for body in (tag, dispatch):
        assert "        if: ${{ !inputs.dry_run }}\n" in body, "tag and npm dispatch must skip dry runs"
        assert has_env(body, "GIT_TAG", "steps.ver.outputs.git_tag")
    assert "git push origin HEAD --tags" in tag, "push the tag before dispatching npm"
    assert release.index(tag) < release.index(dispatch), "npm dispatch must follow the tag push"
    assert NPM_DISPATCH_COMMAND in dispatch, "npm dispatch must target main with the release tag"
    assert has_env(dispatch, "GH_TOKEN", "steps.app.outputs.token"), "dispatch must use the App token"
    assert "continue-on-error" not in dispatch, "a dropped npm dispatch must fail the release"
    app = step(release, "Mint org App token")
    assert "actions/create-github-app-token@" in app
    assert "          permission-actions: write\n" in app, "App token must request Actions write"
    assert "          permission-contents: write\n" in app, "App token must retain Contents write"


def test_release_dispatches_npm_on_main_after_pushing_the_tag():
    assert_release_dispatches_npm(RELEASE)


@pytest.mark.parametrize("original,replacement", [
    ("  release:\n", "  removed-release:\n"),
    (NPM_DISPATCH_COMMAND, NPM_DISPATCH_COMMAND.replace("--ref main", '--ref "$GIT_TAG"')),
    (NPM_DISPATCH_COMMAND, NPM_DISPATCH_COMMAND.replace(' -f tag="${GIT_TAG}"', "")),
    (NPM_DISPATCH_COMMAND, NPM_DISPATCH_COMMAND.replace("-f dry_run=false", "-f dry_run=true")),
    ("git push origin HEAD --tags", "git push origin HEAD"),
    ("          permission-actions: write\n", ""),
    ("          permission-contents: write\n", ""),
    ("- name: Dispatch publish-pyodide on main", "- name: Removed npm dispatch"),
])
def test_release_dispatch_pin_rejects_real_workflow_mutations(original, replacement):
    assert_release_dispatches_npm(RELEASE)
    mutant = mutate_once(RELEASE, original, replacement)
    with pytest.raises(AssertionError):
        assert_release_dispatches_npm(mutant)


@pytest.mark.parametrize("step_name", ["Commit + tag + push release tree", "Dispatch publish-pyodide on main"])
def test_release_dispatch_pin_rejects_dry_run_and_token_mutations(step_name):
    assert_release_dispatches_npm(RELEASE)
    original = step(RELEASE, step_name)
    replacements = [original.replace("        if: ${{ !inputs.dry_run }}\n", "")]
    if step_name.startswith("Dispatch"):
        replacements.extend([
            original.replace("${{ steps.app.outputs.token }}", "${{ github.token }}"),
            original.replace("        run: |\n", "        continue-on-error: true\n        run: |\n"),
        ])
    for replacement in replacements:
        mutant = mutate_once(RELEASE, original, replacement)
        with pytest.raises(AssertionError):
            assert_release_dispatches_npm(mutant)


def test_release_dispatch_pin_rejects_dispatch_before_tag_push():
    assert_release_dispatches_npm(RELEASE)
    tag = step(RELEASE, "Commit + tag + push release tree")
    dispatch = step(RELEASE, "Dispatch publish-pyodide on main")
    mutant = mutate_once(RELEASE, tag, dispatch)
    # Replace the later occurrence only; both real step bodies remain intact.
    start = mutant.rfind(dispatch)
    assert start > mutant.index(dispatch), "order mutation must find the later dispatch"
    mutant = mutant[:start] + tag + mutant[start + len(dispatch):]
    with pytest.raises(AssertionError, match="follow the tag push"):
        assert_release_dispatches_npm(mutant)


def test_guard_must_remain_the_first_job():
    assert_tagged_npm_dispatch(NPM)
    jobs = workflow_jobs(NPM)
    guard = "  tag-guard:" + jobs.get("tag-guard", "")
    publish = "  publish:" + jobs.get("publish", "")
    mutant = mutate_once(NPM, guard + publish, publish + guard)
    with pytest.raises(AssertionError, match="tag-guard must be the first job"):
        assert_tagged_npm_dispatch(mutant)


def test_guard_must_check_out_main_before_running_git_checks():
    assert_tagged_npm_dispatch(NPM)
    guard = workflow_jobs(NPM).get("tag-guard", "")
    checkout = checkout_steps(guard)[0]
    validation = "      " + step(guard, "Verify release tag")
    replacement = mutate_once(guard, checkout, "")
    replacement = mutate_once(replacement, validation, validation + "\n" + checkout)
    mutant = mutate_once(NPM, guard, replacement)
    with pytest.raises(AssertionError, match="guard must read a main checkout"):
        assert_tagged_npm_dispatch(mutant)
