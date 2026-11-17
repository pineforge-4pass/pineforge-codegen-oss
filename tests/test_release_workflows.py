# tests/test_release_workflows.py
"""Pin hosted runners, main-ref dispatch and verified-tag npm publication.

Take channel spellings from scripts/release_version.py so a prerelease never
reaches a `latest` channel. Execute the shipped tag guard against temporary
git repositories; mutation tests use the shipped workflow text too. Execute
the shipped npm, read-back and hub scripts of release.yml against recorded API
and registry answers: only the network is replaced, never a decision.
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
NPM = (WORKFLOWS / "publish-pyodide.yml").read_text(encoding="utf-8")


def step(text, name):
    """The body of the workflow step whose `- name:` line starts with name."""
    marker = f"- name: {name}"
    assert marker in text, f"workflow step {name} must be present"
    start = text.index(marker)
    nxt = re.search(r"\n(?:      - |  [\w-]+:)", text[start + 1:])
    return text[start:] if nxt is None else text[start:start + 1 + nxt.start()]


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


def test_no_workflow_uses_retired_runner_labels():
    # PF_CI_LABELS env entries in gate.yml/test.yml are out of scope:
    # they are not runs-on values.
    paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    assert paths, "must discover workflow files for the hosted-runner guard"
    checked = 0
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            runner = re.match(r"""^( +)(?:runs-on|'runs-on'|"runs-on"):[ \t]*(.*)$""", line)
            if runner is None:
                continue
            indent = len(runner[1])
            value_lines = [runner[2]]
            # Include block lists, flow lists and multiline fromJSON literals,
            # stopping at the next job property instead of reading its env.
            for continuation in lines[index + 1:]:
                stripped = continuation.lstrip()
                if not stripped or stripped.startswith("#"):
                    continue
                padding = len(continuation) - len(stripped)
                if padding < indent or (
                    padding == indent
                    and re.match(r"""(?:[\w-]+|'[^']+'|"[^"]+"):""", stripped)
                ):
                    break
                value_lines.append(continuation)
            # Ignore comments while preserving quoted YAML/expression strings.
            value = re.sub(
                r"""("(?:\\.|[^"\\])*"|'(?:''|[^'])*')|#[^\n]*""",
                lambda token: token[1] or "",
                "\n".join(value_lines),
            )
            labels = re.findall(r"[a-z0-9_-]+", value.lower())
            assert not any(
                label == "self-hosted" or label.startswith(("pf-ci", "gke-", "gkp-"))
                for label in labels
            ), (
                f"{path.name}:{index + 1}: runs-on names a retired runner label: {value}"
            )
            checked += 1
    assert checked, "must inspect actual runs-on values"


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
@pytest.mark.parametrize("runner", [
    "self-hosted", "pf-ci", "pf-ci-x64", "gke-8cpu-16gb-amd64", "gkp-8cpu-16gb-amd64",
])
def test_retired_runner_guard_rejects_workflow_mutations(tmp_path, monkeypatch, suffix, runner):
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    path = tmp_path / f"release{suffix}"
    path.write_text(RELEASE, encoding="utf-8")
    test_no_workflow_uses_retired_runner_labels()
    mutant = mutate_once(
        RELEASE, "    runs-on: ubuntu-latest\n", f"    runs-on: {runner}\n",
    )
    path.write_text(mutant, encoding="utf-8")
    with pytest.raises(
        AssertionError,
        match=rf"{re.escape(path.name)}:\d+: runs-on names a retired runner label",
    ):
        test_no_workflow_uses_retired_runner_labels()


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


def mutate_once(text, original, replacement):
    """A stale literal is a failed test, never an unchanged passing mutant."""
    assert text.count(original) == 1, f"mutation must match shipped text exactly once: {original!r}"
    mutant = text.replace(original, replacement, 1)
    assert mutant != text, "mutation must change shipped workflow text"
    return mutant


TAG_REF_BLOCK = (
    '          if [ "$DISPATCH_REF" != refs/heads/main ]; then\n'
    '            echo "::error::workflow must be dispatched on refs/heads/main"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_FORMAT_CHECK = r'if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$ ]]; then'
TAG_PATTERN_BLOCK = (
    f"          {TAG_FORMAT_CHECK}\n"
    '            echo "::error::invalid release tag \'$TAG\': expected vX.Y.Z or vX.Y.Z-{alpha,beta,rc}.N"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_EXISTENCE_BLOCK = (
    '          if ! TAG_COMMIT=$(git rev-parse -q --verify "refs/tags/${TAG}^{commit}"); then\n'
    '            echo "::error::release tag \'$TAG\' does not exist or does not point to a commit"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_COMMIT_SHAPE_BLOCK = (
    '          if ! [[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then\n'
    '            echo "::error::release tag \'$TAG\' did not resolve to a 40-hex commit"\n'
    "            exit 1\n"
    "          fi\n"
)
TAG_REACHABILITY_BLOCK = (
    '          if ! git merge-base --is-ancestor "$TAG_COMMIT" refs/remotes/origin/main; then\n'
    '            echo "::error::release tag \'$TAG\' is not reachable from refs/remotes/origin/main"\n'
    "            exit 1\n"
    "          fi\n"
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
    for block in (
        TAG_REF_BLOCK, TAG_PATTERN_BLOCK, TAG_EXISTENCE_BLOCK,
        TAG_COMMIT_SHAPE_BLOCK, TAG_REACHABILITY_BLOCK,
    ):
        assert block in validation, "every tag-guard refusal must report its check and exit"
    assert 'if ! [[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then' in script
    assert 'printf \'sha=%s\\n\' "$TAG_COMMIT" >> "$GITHUB_OUTPUT"' in script
    assert "git fetch --tags origin\n" in script, "guard must fetch repository tags"
    assert "git fetch origin +refs/heads/main:refs/remotes/origin/main\n" in script, (
        "guard must refresh refs/remotes/origin/main"
    )
    first_git = re.search(r"\bgit\s+", script)
    assert first_git is not None and script.index(TAG_FORMAT_CHECK) < first_git.start(), (
        "validate the tag pattern before every git command"
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
    assert (
        "concurrency:\n"
        "  group: publish-pyodide-${{ inputs.tag }}\n"
        "  cancel-in-progress: false\n"
    ) in text, "serialize by release tag without cancelling an active publish"
    for job_id, claim in (("publish", "!inputs.claim_placeholder"), ("claim-npm", "inputs.claim_placeholder")):
        assert (
            f"    if: ${{{{ github.ref == 'refs/heads/main' && {claim} }}}}\n"
        ) in workflow_jobs(text).get(job_id, ""), f"{job_id} must refuse dispatches outside main"
        # The server-side bound: both npm jobs run in the `npm-publish` environment (main-only policy).
        env_lines = re.findall(r"^    environment:[^\n]*$", workflow_jobs(text).get(job_id, ""), re.M)
        assert env_lines == ["    environment: npm-publish"], (
            f"{job_id} must declare exactly `environment: npm-publish`"
        )
    assert text.count("environment:") == 2, "only the two npm jobs may declare an environment"
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
    assert has_env(publish, "EVENT", "github.event_name")
    assert has_env(publish, "DRY_RUN", "inputs.dry_run")
    assert '[ "$EVENT" = "workflow_dispatch" ] && [ "$DRY_RUN" = "false" ]' in publish, (
        "a real dispatched publish requires dry_run=false"
    )
    assert '[ "$EVENT" = "push" ]' not in publish, "tag pushes must never publish"


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
    ("  cancel-in-progress: false\n", "  cancel-in-progress: true\n"),
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
    (TAG_REF_BLOCK, ""),
    (TAG_PATTERN_BLOCK, ""),
    (TAG_EXISTENCE_BLOCK, ""),
    (TAG_COMMIT_SHAPE_BLOCK, ""),
    (TAG_REACHABILITY_BLOCK, ""),
    ("          git fetch --tags origin\n", ""),
    ("          git fetch origin +refs/heads/main:refs/remotes/origin/main\n", ""),
    ("          fetch-depth: 0\n", "          fetch-depth: 1\n"),
    ("          ref: main\n", "          ref: ${{ inputs.tag }}\n"),
    ('if ! [[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then', 'if false; then'),
    ('printf \'sha=%s\\n\' "$TAG_COMMIT"', 'printf \'sha=%s\\n\' "$TAG"'),
    ('          printf \'sha=%s\\n\' "$TAG_COMMIT" >> "$GITHUB_OUTPUT"\n', ""),
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


@pytest.mark.parametrize("job_id", ["publish", "claim-npm"])
@pytest.mark.parametrize("original,replacement", [
    ("    environment: npm-publish\n", ""),
    ("    environment: npm-publish\n", "    environment: production\n"),
    ("    environment: npm-publish\n", "    environment: npm-publish\n    environment: npm-publish-2\n"),
    ("    environment: npm-publish\n", "    environment: ${{ inputs.tag }}\n"),
])
def test_npm_jobs_must_run_in_the_main_only_environment(job_id, original, replacement):
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
    git("tag", "v1.2.0-alpha.1", head)
    git("tag", "v1.2.0-beta.1", head)
    git("tag", "v1.2.0-gamma.1", head)
    git("tag", "-a", "v1.1.0", "-m", "annotated main tag", head)
    annotated_object = git("rev-parse", "refs/tags/v1.1.0")
    git("tag", "v2.0.0", git("rev-parse", "HEAD^{tree}"))
    git("switch", "-c", "side", base)
    git("commit", "--allow-empty", "-m", "unmerged side commit")
    git("tag", "v9.9.9")
    git("switch", "main")
    # Short refs can resolve these tags before branches or remote refs. Put
    # every decoy on the unmerged side commit to expose ancestry bypasses.
    for name in ("origin/main", "main", "heads/main", "remotes/origin/main"):
        git("tag", name, "refs/heads/side")
    checkout = tmp_path / "checkout"
    # --single-branch: with the decoy tags above (one is named main), a plain
    # --no-tags clone of --branch main still brings the tags along.
    git(
        "clone", "--no-local", "--no-tags", "--single-branch", "--branch", "main",
        str(origin), str(checkout),
    )
    assert git("tag", "--list", cwd=checkout) == "", "guard must fetch its tags"
    assert git("rev-list", "--count", "refs/remotes/origin/main", cwd=checkout) == "2"
    assert git("rev-parse", "refs/remotes/origin/main", cwd=checkout) == head
    assert annotated_object != head, "fixture must distinguish tag object and commit"
    return GuardRepository(checkout, env, base, head, annotated_object)


def run_tag_guard(text, repository, tmp_path, tag, dispatch_ref, local_branches=()):
    for name, commit in local_branches:
        branch = run_guard_command(
            ["git", "branch", name, getattr(repository, commit)],
            repository.checkout,
            repository.env,
        )
        assert branch.returncode == 0, f"local branch fixture failed: {branch.stdout}\n{branch.stderr}"
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
    "tag,dispatch_ref,status,commit,error,local_branches",
    [
        pytest.param("v1.0.0", "refs/heads/main", 0, "base", None, (), id="main-ancestor"),
        pytest.param("v1.1.0-rc.2", "refs/heads/main", 0, "head", None, (), id="main-rc"),
        pytest.param("v1.2.0-alpha.1", "refs/heads/main", 0, "head", None, (), id="main-alpha"),
        pytest.param("v1.2.0-beta.1", "refs/heads/main", 0, "head", None, (), id="main-beta"),
        pytest.param(
            "v1.2.0-gamma.1", "refs/heads/main", 1, None,
            "invalid release tag", (), id="unsupported-prerelease",
        ),
        pytest.param(
            "not-a-release", "refs/heads/main", 1, None,
            "invalid release tag", (), id="invalid-name-existing-tag",
        ),
        pytest.param(
            "v8.8.8", "refs/heads/main", 1, None,
            "release tag 'v8.8.8' does not exist or does not point to a commit",
            (), id="missing-tag",
        ),
        pytest.param(
            "v1.2.9", "refs/heads/main", 1, None,
            "release tag 'v1.2.9' does not exist or does not point to a commit",
            (("v1.2.9", "head"),), id="local-branch-is-not-a-tag",
        ),
        pytest.param(
            "v1.0.0", "refs/heads/main", 0, "base", None,
            (("v1.0.0", "head"),), id="tag-sha-wins-over-local-branch",
        ),
        pytest.param(
            "v9.9.9", "refs/heads/main", 1, None,
            "release tag 'v9.9.9' is not reachable from refs/remotes/origin/main",
            (), id="unmerged-side-branch",
        ),
        pytest.param("v1.1.0", "refs/heads/main", 0, "head", None, (), id="annotated-main"),
        pytest.param(
            "v2.0.0", "refs/heads/main", 1, None,
            "release tag 'v2.0.0' does not exist or does not point to a commit",
            (), id="tag-points-to-tree",
        ),
        pytest.param("", "refs/heads/main", 1, None, "invalid release tag", (), id="empty"),
        pytest.param(
            "v1.0.0\n", "refs/heads/main", 1, None,
            "invalid release tag", (), id="trailing-newline",
        ),
        pytest.param(
            "v1.0.0\nv9.9.9", "refs/heads/main", 1, None,
            "invalid release tag", (), id="embedded-newline",
        ),
        pytest.param(
            "v1.*", "refs/heads/main", 1, None,
            "invalid release tag", (), id="glob-input",
        ),
        pytest.param(
            "v1.0.0; echo injected", "refs/heads/main", 1, None,
            "invalid release tag", (), id="shell-input",
        ),
        pytest.param(
            "v1.0.0", "refs/heads/side", 1, None,
            "workflow must be dispatched on refs/heads/main", (), id="branch-dispatch",
        ),
        pytest.param(
            "v1.0.0", "refs/tags/v1.0.0", 1, None,
            "workflow must be dispatched on refs/heads/main", (), id="tag-dispatch",
        ),
    ],
)
def test_tag_guard_executes_against_real_git(
    guard_repository, tmp_path, tag, dispatch_ref, status, commit, error, local_branches
):
    result, output = run_tag_guard(NPM, guard_repository, tmp_path, tag, dispatch_ref, local_branches)
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
            "release tag 'v9.9.9' is not reachable from refs/remotes/origin/main",
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


@pytest.mark.parametrize("original,replacement,tag,status,commit,error,local_branches", [
    (
        TAG_REACHABILITY_BLOCK,
        TAG_REACHABILITY_BLOCK.replace(' refs/remotes/origin/main;', ' origin/main;'),
        "v9.9.9", 1, None,
        "release tag 'v9.9.9' is not reachable from refs/remotes/origin/main", (),
    ),
    (
        TAG_EXISTENCE_BLOCK,
        TAG_EXISTENCE_BLOCK.replace('"refs/tags/${TAG}^{commit}"', '"${TAG}^{commit}"'),
        "v1.2.9", 1, None,
        "release tag 'v1.2.9' does not exist or does not point to a commit",
        (("v1.2.9", "head"),),
    ),
])
def test_real_git_oracle_rejects_ambiguous_ref_mutants(
    guard_repository, tmp_path, original, replacement, tag, status, commit, error, local_branches
):
    expected_sha = getattr(guard_repository, commit) if commit is not None else None
    baseline = run_tag_guard(
        NPM, guard_repository, tmp_path, tag, "refs/heads/main", local_branches
    )
    assert_guard_result(*baseline, status, expected_sha, error)
    mutant = mutate_once(NPM, original, replacement)
    with pytest.raises(AssertionError):
        assert_tagged_npm_dispatch(mutant)
    result, output = run_tag_guard(mutant, guard_repository, tmp_path, tag, "refs/heads/main")
    with pytest.raises(AssertionError, match="guard status"):
        assert_guard_result(result, output, status, expected_sha, error)


# release.yml dispatches publish-pyodide.yml on main for its own tag with this
# one command, which its saved npm script runs for the dry run and then for the
# publish. It is the only command that carries the App token.
NPM_DISPATCH = (
    'GH_TOKEN="$APP_TOKEN" gh api --method POST'
    ' "repos/${REPO}/actions/workflows/publish-pyodide.yml/dispatches"'
    " -f ref=main -F return_run_details=true"
    ' -f "inputs[tag]=${GIT_TAG}" -f "inputs[dry_run]=${dry_run}"'
    " --jq .workflow_run_id"
)
# The step of publish-pyodide.yml whose success the release requires of both runs.
NPM_PUBLISH_STEP = "Publish (real on dispatch dry_run=false; dry-run otherwise)"


def joined(text):
    """A command that continues over several lines, as its one line."""
    return re.sub(r"[ \t]*\\\r?\n[ \t]*", " ", text)


def assert_no_npm_workflow_dispatch(text, admitted=0):
    text = joined(text)
    assert text.count(NPM_DISPATCH) == admitted, (
        "no workflow may dispatch any workflow but release.yml, once, with its checked npm command"
    )
    text = text.replace(NPM_DISPATCH, "")
    # Nothing else in this repository dispatches a workflow, so the pin does
    # not depend on how the target is named (file, display name, numeric id,
    # expression or a separately supplied variable).
    for pattern in (
        r"\bworkflow\s+run\b",
        r"\bcreateWorkflowDispatch\b",
        r"\bactions/workflows/[^\s/]+/dispatches\b",
    ):
        assert re.search(pattern, text) is None, "no workflow may dispatch any workflow"


def test_only_release_dispatches_publish_pyodide():
    paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    assert paths, "must discover workflow files for the dispatch pin"
    for path in paths:
        assert_no_npm_workflow_dispatch(
            path.read_text(encoding="utf-8"), admitted=int(path.name == "release.yml"),
        )


@pytest.mark.parametrize("command", [
    # A second copy of the checked command, and that command made to publish or
    # to leave main, are dispatches of their own.
    NPM_DISPATCH,
    NPM_DISPATCH.replace("${dry_run}", "false"),
    NPM_DISPATCH.replace("ref=main", "ref=${GITHUB_REF_NAME}"),
    NPM_DISPATCH.replace('GH_TOKEN="$APP_TOKEN" ', ""),
    'gh workflow run publish-pyodide.yml --ref main -f tag="${GIT_TAG}" -f dry_run=false',
    'workflow run "publish-pyodide.yml" --ref main',
    'gh workflow run \\\n'
    '            publish-pyodide.yml --ref main',
    "await github.rest.actions.createWorkflowDispatch({\n"
    "            workflow_id: 'publish-pyodide.yml', ref: 'main'\n"
    "          });",
    "gh api --method POST repos/pineforge-4pass/pineforge-codegen-oss/"
    "actions/workflows/publish-pyodide.yml/dispatches -f ref=main",
    'gh workflow run "Publish codegen-pyodide" --ref main -f tag=v1.0.0',
    "gh api -X POST repos/pineforge-4pass/pineforge-codegen-oss/"
    "actions/workflows/123456789/dispatches -f ref=main",
    "await github.request(\n"
    "            'POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches',\n"
    "            {owner, repo, workflow_id: 123456789, ref: 'main'}\n"
    "          );",
])
def test_dispatch_pin_rejects_any_other_workflow_dispatch_step(command):
    assert_no_npm_workflow_dispatch(RELEASE, admitted=1)
    marker = "      - name: Create GitHub Release\n"
    mutant = mutate_once(
        RELEASE, marker,
        "      - name: Dispatch publish-pyodide on main\n"
        "        run: |\n"
        f"          {command}\n\n" + marker,
    )
    with pytest.raises(AssertionError, match="no workflow may dispatch"):
        assert_no_npm_workflow_dispatch(mutant, admitted=1)


def test_npm_dispatch_pin_requires_the_app_token_on_the_command():
    assert_no_npm_workflow_dispatch(RELEASE, admitted=1)
    mutant = mutate_once(
        RELEASE, 'GH_TOKEN="$APP_TOKEN" gh api --method POST', "gh api --method POST",
    )
    with pytest.raises(AssertionError, match="no workflow may dispatch"):
        assert_no_npm_workflow_dispatch(mutant, admitted=1)


# The steps of the release job that publish npm and notify the hub.
RELEASE_NPM_WORKFLOW = "Find the npm workflow"
RELEASE_NPM_DRY = "Dry-run npm for the tag"
RELEASE_NPM_PUBLISH = "Publish npm for the tag"
RELEASE_READBACK = "Read npm back from the registry"
RELEASE_HUB = "Dispatch codegen-release"
# Its App-token mints, each with the lines between its name and its action.
# The first serves the checkout, the tag push and the GitHub Release; each
# later one serves the one dispatch of the step right after it.
APP_TOKEN_STEPS = {
    "Mint org App token": "        id: app\n",
    "Mint org App token (npm dry run)": (
        "        id: app_npm_dry\n        if: ${{ !inputs.dry_run }}\n"
    ),
    "Mint org App token (npm publish)": (
        "        id: app_npm\n        if: ${{ !inputs.dry_run }}\n"
    ),
    "Mint org App token (hub)": "        id: app_hub\n        if: ${{ !inputs.dry_run }}\n",
}
APP_TOKEN_INPUTS = (
    "        with:\n"
    "          app-id: ${{ secrets.PINEFORGE_APP_ID }}\n"
    "          private-key: ${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}\n"
    "          owner: ${{ github.repository_owner }}\n"
    "          repositories: pineforge-codegen-oss,pineforge-release"
)


def assert_release_app_token_scope(text):
    release = workflow_jobs(text).get("release", "")
    pins = re.findall(
        r"^        uses: (actions/create-github-app-token@[^\n]*)$", release, re.M,
    )
    assert len(pins) == release.count("create-github-app-token") == len(APP_TOKEN_STEPS), (
        "the release job mints exactly its checked App tokens"
    )
    assert len(set(pins)) == 1, "every release App token must come from the same pinned action"
    for name, header in APP_TOKEN_STEPS.items():
        app = step(release, name)
        assert app.startswith(f"- name: {name}\n{header}        uses: {pins[0]}\n"), (
            f"{name}: the mint must keep its id, its condition and the pinned action"
        )
        assert "permission-actions:" not in app, "release App token must not request Actions permission"
        # gh/main's App-token step has no explicit permission inputs.
        assert re.findall(r"^\s+permission-[\w-]+:", app, re.M) == [], (
            "release App token must keep main's permission inputs"
        )
        # The whole `with:` block is main's, so dropping `repositories:` (which
        # would widen the token to every repository of the installation) fails.
        assert app.rstrip("\n").endswith(APP_TOKEN_INPUTS), (
            "release App token inputs must stay exactly main's"
        )


def test_release_preserves_main_app_token_scope():
    assert_release_app_token_scope(RELEASE)


@pytest.mark.parametrize("name", APP_TOKEN_STEPS)
@pytest.mark.parametrize("permission", ["actions", "contents", "issues"])
def test_app_token_pin_rejects_new_permission_inputs(name, permission):
    assert_release_app_token_scope(RELEASE)
    release = workflow_jobs(RELEASE).get("release", "")
    app = step(release, name)
    replacement = mutate_once(
        app, "        with:\n",
        f"        with:\n          permission-{permission}: write\n",
    )
    mutant = mutate_once(RELEASE, app, replacement)
    with pytest.raises(AssertionError):
        assert_release_app_token_scope(mutant)


@pytest.mark.parametrize("name", APP_TOKEN_STEPS)
@pytest.mark.parametrize("line", [
    "          repositories: pineforge-codegen-oss,pineforge-release\n",
    "          owner: ${{ github.repository_owner }}\n",
])
def test_app_token_pin_rejects_a_dropped_input(name, line):
    release = workflow_jobs(RELEASE).get("release", "")
    app = step(release, name)
    dropped = line.rstrip("\n")
    assert dropped in app, "the mutation anchor must be a real App-token input"
    mutant = mutate_once(RELEASE, app, app.replace(dropped, "").rstrip(" \n") + "\n")
    with pytest.raises(AssertionError):
        assert_release_app_token_scope(mutant)


@pytest.mark.parametrize("name", list(APP_TOKEN_STEPS)[1:])
def test_app_token_pin_rejects_another_action_or_an_unchecked_mint(name):
    assert_release_app_token_scope(RELEASE)
    release = workflow_jobs(RELEASE).get("release", "")
    app = step(release, name)
    pin = re.search(r"create-github-app-token@[0-9a-f]{40}", app)
    assert pin is not None, "the mutation anchor must be the pinned action"
    for replacement in (
        mutate_once(app, pin[0], "create-github-app-token@" + "e" * 40),
        mutate_once(app, f"- name: {name}\n", f"- name: {name}, unchecked\n"),
        app + "\n      " + app.replace(name, name + " again"),
    ):
        with pytest.raises(AssertionError):
            assert_release_app_token_scope(mutate_once(RELEASE, app, replacement))


def assert_release_token_flow(text):
    """No App token is carried across a wait: one mint right before each dispatch."""
    release = workflow_jobs(text).get("release", "")
    find, dry, publish, hub = (step(release, name) for name in (
        RELEASE_NPM_WORKFLOW, RELEASE_NPM_DRY, RELEASE_NPM_PUBLISH, RELEASE_HUB,
    ))
    for mint, consumer in (
        ("Mint org App token (npm dry run)", RELEASE_NPM_DRY),
        ("Mint org App token (npm publish)", RELEASE_NPM_PUBLISH),
        ("Mint org App token (hub)", RELEASE_HUB),
    ):
        body = step(release, mint)
        assert release.index(f"- name: {consumer}") == (
            release.index(body) + len(body) + len("\n      ")
        ), f"{mint} must be the step right before {consumer}"
    # Waiting and reading use the job's own token, which lasts as long as the
    # job. The App token of a step is the one minted right before it, and it
    # reaches the dispatch command only.
    for body in (find, dry, publish):
        assert has_env(body, "GH_TOKEN", "github.token"), "npm reads must use the job's own token"
    assert has_env(dry, "APP_TOKEN", "steps.app_npm_dry.outputs.token"), (
        "the npm dry run must dispatch with the token minted for it"
    )
    assert has_env(publish, "APP_TOKEN", "steps.app_npm.outputs.token"), (
        "the npm publish must dispatch with the token minted for it"
    )
    assert has_env(hub, "GH_TOKEN", "steps.app_hub.outputs.token"), (
        "the hub must be notified with the token minted for it"
    )
    for mint in ("app_npm_dry", "app_npm", "app_hub"):
        assert release.count(f"steps.{mint}.outputs.token") == 1, (
            f"{mint}: a minted token serves one step"
        )
    assert (
        release.count("$APP_TOKEN") == 2
        and '[ -n "$APP_TOKEN" ]' in dry
        and NPM_DISPATCH in joined(dry)
    ), "the App token must reach the npm dispatch and no other command"
    # The first token serves the checkout and the GitHub Release: before any wait.
    assert release.count("steps.app.outputs.token") == 2, (
        "the first App token serves the checkout and the GitHub Release only"
    )
    assert release.rindex("steps.app.outputs.token") < release.index(
        f"- name: {RELEASE_NPM_WORKFLOW}"
    ), "the first App token must not be used after an npm wait"


def test_release_mints_an_app_token_right_before_each_dispatch():
    assert_release_token_flow(RELEASE)


@pytest.mark.parametrize("name,original,replacement", [
    # A dispatch with a token that was minted before a wait.
    (RELEASE_HUB, "steps.app_hub.outputs.token", "steps.app.outputs.token"),
    (RELEASE_HUB, "steps.app_hub.outputs.token", "steps.app_npm.outputs.token"),
    (RELEASE_NPM_PUBLISH, "steps.app_npm.outputs.token", "steps.app_npm_dry.outputs.token"),
    (RELEASE_NPM_DRY, "steps.app_npm_dry.outputs.token", "steps.app.outputs.token"),
    # A wait with an App token, which lives one hour.
    (
        RELEASE_NPM_DRY, "GH_TOKEN:        ${{ github.token }}",
        "GH_TOKEN:        ${{ steps.app_npm_dry.outputs.token }}",
    ),
    (
        RELEASE_NPM_PUBLISH, "GH_TOKEN:        ${{ github.token }}",
        "GH_TOKEN:        ${{ steps.app_npm.outputs.token }}",
    ),
    # A read with the App token.
    (RELEASE_NPM_DRY, 'if out=$(gh api "$@")', 'if out=$(GH_TOKEN="$APP_TOKEN" gh api "$@")'),
])
def test_token_flow_pin_rejects_a_token_carried_across_a_wait(name, original, replacement):
    assert_release_token_flow(RELEASE)
    body = step(workflow_jobs(RELEASE).get("release", ""), name)
    mutant = mutate_once(RELEASE, body, mutate_once(body, original, replacement))
    with pytest.raises(AssertionError):
        assert_release_token_flow(mutant)


@pytest.mark.parametrize("consumer", [
    RELEASE_NPM_DRY, RELEASE_NPM_PUBLISH, "Dispatch codegen-release -> pineforge-release",
])
def test_token_flow_pin_rejects_a_step_between_a_mint_and_its_dispatch(consumer):
    assert_release_token_flow(RELEASE)
    marker = f"      - name: {consumer}\n"
    mutant = mutate_once(
        RELEASE, marker, "      - name: Wait\n        run: sleep 3600\n\n" + marker,
    )
    with pytest.raises(AssertionError, match="must be the step right before"):
        assert_release_token_flow(mutant)


def test_release_keeps_pypi_tag_release_npm_and_hub_order():
    assert set(workflow_triggers(RELEASE)) == {"workflow_dispatch"}, "release must be dispatch-only"
    release = workflow_jobs(RELEASE).get("release", "")
    pypi, tag, github, dry, npm, readback, hub = bodies = [step(release, name) for name in (
        "Publish to PyPI", "Commit + tag + push", "Create GitHub Release",
        RELEASE_NPM_DRY, RELEASE_NPM_PUBLISH, RELEASE_READBACK, RELEASE_HUB,
    )]
    for body in (pypi, tag, github, dry, npm, hub):
        assert "        if: ${{ !inputs.dry_run }}\n" in body, "release side effects must skip dry runs"
    assert "git push origin HEAD --tags" in tag, "release must push its tag"
    positions = [release.index(body) for body in bodies]
    assert positions == sorted(positions), (
        "keep PyPI, tag push, GitHub Release, npm dry run, npm publish, registry read-back and hub order"
    )
    # npm: one saved script with the one checked dispatch. It refuses a release
    # dry run before dispatching, the dry-run step runs it first, and the
    # publish step runs it only with that step's receipt for this tag.
    assert NPM_DISPATCH in joined(dry), "the dry-run step holds the checked dispatch"
    assert "gh api" not in npm, "the publish step runs the saved script and nothing of its own"
    for body in (dry, npm):
        assert has_env(body, "RELEASE_DRY_RUN", "inputs.dry_run")
        assert has_env(body, "WORKFLOW_ID", "steps.npm_workflow.outputs.id")
    assert dry.index('[ "$RELEASE_DRY_RUN" = false ]') < dry.index(
        'if [ "$dry_run" = false ] && [ "$DRY_RUN_TAG" != "$GIT_TAG" ]; then'
    ) < dry.index('GH_TOKEN="$APP_TOKEN" gh api --method POST'), (
        "the dry-run refusal and the dry-run receipt must precede the dispatch"
    )
    assert 'bash "$RUNNER_TEMP/npm-run.sh" true "dry run"\n' in dry, "the dry run comes first"
    assert 'bash "$RUNNER_TEMP/npm-run.sh" false publish\n' in npm, "the publish follows it"
    assert has_env(npm, "DRY_RUN_TAG", "steps.npm_dry.outputs.tag")
    assert f'select(.name == "{NPM_PUBLISH_STEP}"' in dry, "check that the publish step ran"
    assert f"      - name: {NPM_PUBLISH_STEP}\n" in NPM, "publish-pyodide.yml must keep that step"
    # The read-back only reads the registry, so a dry run exercises it; a release
    # reads back this job's publish of this tag.
    assert re.search(r"^        if:", readback, re.M) is None, "the read-back runs in a dry run too"
    assert has_env(readback, "PUBLISHED_TAG", "steps.npm.outputs.tag")
    assert '[ "$PUBLISHED_TAG" = "$GIT_TAG" ]' in readback
    # hub: only with the read-back of this very version.
    assert has_env(hub, "NPM_VERIFIED", "steps.npm_readback.outputs.verified")
    assert hub.index(
        'if [ "$NPM_VERIFIED" != "@pineforge/codegen-pyodide@${NEW_VERSION}" ]; then'
    ) < hub.index("gh api repos/pineforge-4pass/pineforge-release/dispatches"), (
        "the hub must not be notified before this version's npm read-back"
    )
    # A release dry run makes one read of the npm workflow and dispatches nothing.
    find = step(release, RELEASE_NPM_WORKFLOW)
    assert re.search(r"^        if:", find, re.M) is None, "a dry run checks the npm workflow too"
    assert "--method" not in find and "dispatches" not in find, "the lookup only reads"


def test_step_slices_stop_before_the_next_job_and_require_a_step():
    body = step(RELEASE, "Dispatch codegen-release")
    assert "gh api repos/pineforge-4pass/pineforge-release/dispatches" in body
    assert "  claim-name:" not in body
    with pytest.raises(AssertionError, match="must be present"):
        step(workflow_jobs(RELEASE).get("release", ""), "Missing release step")


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


# Executable controls: the scripts release.yml ships for its npm and hub steps,
# run by bash as they are. Only the network is replaced. `gh api` answers from
# recorded responses and applies the script's own --jq filter with the real jq;
# curl serves registry files by URL; sleep returns at once. Each stand-in
# records what it was asked, and gh with which token. Every decision is taken
# by the shipped bash and Python. A fixture cannot show what a hosted token may
# do or when it expires: these tests show which request carries which token.
GH_STUB = r'''
"""`gh api` without the network: recorded responses, the caller's own jq filter."""
import json
import os
import subprocess
import sys

args = sys.argv[1:]
if args[:1] != ["api"]:
    sys.exit(f"gh stand-in: only `gh api` is served, not {args!r}")
method, endpoint, query, fields = "", "", "", []
rest = iter(args[1:])
for arg in rest:
    if arg == "--method":
        method = next(rest)
    elif arg in ("-f", "-F"):
        fields.append(next(rest))
    elif arg == "--jq":
        query = next(rest)
    elif arg.startswith("-") or endpoint:
        sys.exit(f"gh stand-in: unexpected argument {arg!r}")
    else:
        endpoint = arg
method = method or ("POST" if fields else "GET")
root = os.environ["STUB_DIR"]
log = os.path.join(root, "calls")
with open(log, encoding="utf-8") as stream:
    earlier = [json.loads(line) for line in stream]
seen = sum(1 for call in earlier if (call["method"], call["endpoint"]) == (method, endpoint))
with open(log, "a", encoding="utf-8") as stream:
    stream.write(json.dumps({
        "method": method, "endpoint": endpoint, "fields": fields,
        "token": os.environ.get("GH_TOKEN", ""),
    }) + "\n")
with open(os.path.join(root, "responses.json"), encoding="utf-8") as stream:
    served = json.load(stream).get(f"{method} {endpoint}") or [[404, {"message": "Not Found"}]]
status, body = served[min(seen, len(served) - 1)]
text = "" if body is None else json.dumps(body)
if status >= 400:
    # As gh does: the error body on stdout, unfiltered, and a failing exit.
    print(text)
    print(f"gh: HTTP {status}", file=sys.stderr)
    sys.exit(1)
if not text:
    sys.exit(0)
if not query:
    print(text)
    sys.exit(0)
found = subprocess.run(["jq", "-c", query], input=text, capture_output=True, text=True)
if found.returncode != 0:
    sys.stderr.write(found.stderr)
    sys.exit(1)
for line in found.stdout.splitlines():
    value = json.loads(line)
    # As gh does: a string as it is, null as an empty line, the rest as JSON.
    print(value if isinstance(value, str) else "" if value is None else json.dumps(value))
'''
CURL_STUB = r'''
"""curl without the network: registry files by URL, in the order they are served."""
import json
import os
import shutil
import sys

args = sys.argv[1:]
url, output = args[-1], args[args.index("--output") + 1]
root = os.environ["STUB_DIR"]
log = os.path.join(root, "fetches")
with open(log, encoding="utf-8") as stream:
    seen = stream.read().splitlines().count(url)
with open(log, "a", encoding="utf-8") as stream:
    stream.write(url + "\n")
with open(os.path.join(root, "registry.json"), encoding="utf-8") as stream:
    served = json.load(stream).get(url) or [None]
source = served[min(seen, len(served) - 1)]
if source is not None:
    shutil.copyfile(source, output)
elif "--fail" in args:
    # As curl --fail does on a 404: no output file and exit status 22.
    print("curl: (22) The requested URL returned error: 404", file=sys.stderr)
    sys.exit(22)
else:
    with open(output, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"error": "Not found"}))
'''
SLEEP_STUB = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$STUB_DIR/sleeps\"\n"


@dataclass(frozen=True)
class StepRun:
    status: int
    stdout: str
    stderr: str
    output: str   # what the step appended to GITHUB_OUTPUT
    calls: list   # every `gh api` request: method, endpoint, fields, token
    fetches: list  # every URL curl was asked for
    sleeps: list  # every sleep, in seconds

    def __str__(self):
        return f"exit {self.status}\nstdout:\n{self.stdout}\nstderr:\n{self.stderr}"


def release_script(text, name):
    """The bash script the release job ships for one step; never a test copy."""
    body = step(workflow_jobs(text).get("release", ""), name)
    scripts = re.findall(r"^        run: \|\n((?: {10}[^\n]*\n|\n)+)", body + "\n", re.M)
    assert len(scripts) == 1, f"{name} must contain one literal bash script"
    return dedent(scripts[0])


def run_release_step(text, name, sandbox, env, responses=None, registry=None):
    """Run one shipped release step against recorded API and registry answers.

    Steps run one after another in the same sandbox share RUNNER_TEMP, as the
    steps of one job do. Missing bash or jq is a failure, never a skip.
    """
    assert shutil.which("jq") is not None, (
        "release fixture tests need jq: the gh stand-in runs the shipped --jq filters with it"
    )
    stubs, temp = sandbox / "stubs", sandbox / "runner-temp"
    if not stubs.exists():
        stubs.mkdir()
        temp.mkdir()
        for tool, source in (
            ("gh", f"#!{sys.executable}\n{GH_STUB}"),
            ("curl", f"#!{sys.executable}\n{CURL_STUB}"),
            ("sleep", SLEEP_STUB),
        ):
            (stubs / tool).write_text(source, encoding="utf-8")
            (stubs / tool).chmod(0o755)
    for log in ("calls", "fetches", "sleeps", "github-output"):
        (sandbox / log).write_text("", encoding="utf-8")
    (sandbox / "responses.json").write_text(json.dumps(responses or {}), encoding="utf-8")
    (sandbox / "registry.json").write_text(json.dumps(registry or {}), encoding="utf-8")
    try:
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", release_script(text, name)],
            cwd=sandbox, capture_output=True, text=True,
            env={
                "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
                "STUB_DIR": str(sandbox),
                "RUNNER_TEMP": str(temp),
                "GITHUB_OUTPUT": str(sandbox / "github-output"),
                "GITHUB_SERVER_URL": "https://github.example",
                **env,
            },
        )
    except OSError as exc:
        pytest.fail(f"release fixture tests require executable bash: {exc}")

    def lines(log):
        return (sandbox / log).read_text(encoding="utf-8").splitlines()

    return StepRun(
        result.returncode, result.stdout, result.stderr,
        (sandbox / "github-output").read_text(encoding="utf-8"),
        [json.loads(line) for line in lines("calls")], lines("fetches"), lines("sleeps"),
    )


def step_outputs(result):
    return dict(line.split("=", 1) for line in result.output.splitlines())


def assert_step_refused(result, reason):
    """A refusal: a failing exit, its ::error:: reason, no receipt for the next step."""
    assert result.status != 0, f"the step must refuse\n{result}"
    assert f"::error::{reason}" in result.stdout, f"the refusal must name its reason\n{result}"
    assert result.output == "", f"a refused step must write no receipt\n{result}"


FIXTURE_REPO = "owner/project"
RUNS_API = f"repos/{FIXTURE_REPO}/actions"
NPM_WORKFLOW_API = f"GET {RUNS_API}/workflows/publish-pyodide.yml"
NPM_DISPATCH_API = f"POST {RUNS_API}/workflows/publish-pyodide.yml/dispatches"
NPM_WORKFLOW_RECORD = {"id": 77, "state": "active", "path": ".github/workflows/publish-pyodide.yml"}
HUB_API = "repos/pineforge-4pass/pineforge-release/dispatches"
WAITING = {"status": "in_progress", "conclusion": None}
UNREADABLE = [401, {"message": "Bad credentials"}]


def run_record(run_id, **changes):
    """A completed, successful publish-pyodide.yml dispatch on main, or a variation."""
    return [200, {
        "id": run_id, "status": "completed", "conclusion": "success",
        "event": "workflow_dispatch", "head_branch": "main", "workflow_id": 77, **changes,
    }]


def npm_jobs(guard="success", **publish):
    """The jobs of such a run: guard and publish succeeded, the claim was skipped."""
    return [200, {"jobs": [
        {"name": "tag-guard", "conclusion": guard, "steps": []},
        {"name": "publish", "conclusion": "success", "steps": [
            {"name": "Assemble the package", "conclusion": "success"},
            {"name": NPM_PUBLISH_STEP, "conclusion": "success"},
        ], **publish},
        {"name": "claim-npm", "conclusion": "skipped", "steps": []},
    ]}]


def npm_responses(run_id, polls, jobs=None, dispatch=None):
    """One dispatched run: the dispatch answer, its polls in order, its jobs."""
    return {
        NPM_DISPATCH_API: [dispatch or [200, {"workflow_run_id": run_id}]],
        f"GET {RUNS_API}/runs/{run_id}": polls,
        f"GET {RUNS_API}/runs/{run_id}/jobs?per_page=100": [jobs or npm_jobs()],
    }


def npm_env(**changes):
    return {
        "GH_TOKEN": "job-token", "APP_TOKEN": "app-token", "REPO": FIXTURE_REPO,
        "RELEASE_DRY_RUN": "false", "GIT_TAG": "v1.2.3", "WORKFLOW_ID": "77", **changes,
    }


def run_npm_publish(sandbox, env, responses):
    """The publish step, after the dry-run step saved the script as the job does."""
    dry = run_release_step(
        RELEASE, RELEASE_NPM_DRY, sandbox, npm_env(), npm_responses(9001, [run_record(9001)]),
    )
    assert dry.status == 0, dry
    return run_release_step(RELEASE, RELEASE_NPM_PUBLISH, sandbox, env, responses)


@pytest.mark.parametrize("served,sleeps", [
    pytest.param([[200, NPM_WORKFLOW_RECORD]], [], id="first-read"),
    pytest.param(
        [[502, {"message": "Bad Gateway"}]] * 2 + [[200, NPM_WORKFLOW_RECORD]], ["10", "10"],
        id="third-read",
    ),
])
def test_npm_workflow_lookup_reads_the_active_workflow_with_the_job_token(tmp_path, served, sleeps):
    result = run_release_step(
        RELEASE, RELEASE_NPM_WORKFLOW, tmp_path,
        {"GH_TOKEN": "job-token", "REPO": FIXTURE_REPO}, {NPM_WORKFLOW_API: served},
    )
    assert result.status == 0, result
    assert result.output == "id=77\n"
    assert result.sleeps == sleeps
    assert {(call["method"], call["token"]) for call in result.calls} == {("GET", "job-token")}


@pytest.mark.parametrize("served,requests", [
    pytest.param([[200, {**NPM_WORKFLOW_RECORD, "state": "disabled_manually"}]], 1, id="disabled"),
    pytest.param(
        [[200, {**NPM_WORKFLOW_RECORD, "path": ".github/workflows/other.yml"}]], 1, id="other-path",
    ),
    pytest.param([[200, {"state": "active", "path": NPM_WORKFLOW_RECORD["path"]}]], 1, id="no-id"),
    pytest.param([[404, {"message": "Not Found"}]], 5, id="not-found"),
    pytest.param(
        [[403, {"message": "Resource not accessible by integration"}]], 5, id="token-cannot-read",
    ),
])
def test_npm_workflow_lookup_refuses_what_it_cannot_read_as_the_active_workflow(
    tmp_path, served, requests
):
    result = run_release_step(
        RELEASE, RELEASE_NPM_WORKFLOW, tmp_path,
        {"GH_TOKEN": "job-token", "REPO": FIXTURE_REPO}, {NPM_WORKFLOW_API: served},
    )
    assert_step_refused(result, "publish-pyodide.yml is not an active workflow of owner/project")
    assert len(result.calls) == requests


def test_npm_dry_run_dispatches_with_the_app_token_and_waits_with_the_job_token(tmp_path):
    polls = [
        run_record(9001, status="queued", conclusion=None), run_record(9001, **WAITING),
        run_record(9001),
    ]
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(), npm_responses(9001, polls),
    )
    assert result.status == 0, result
    assert result.output == "tag=v1.2.3\n"
    assert result.calls[0] == {
        "method": "POST",
        "endpoint": f"{RUNS_API}/workflows/publish-pyodide.yml/dispatches",
        "fields": ["ref=main", "return_run_details=true", "inputs[tag]=v1.2.3", "inputs[dry_run]=true"],
        "token": "app-token",
    }
    # Three polls and the jobs: read with the job's token, never the App token.
    assert [(call["method"], call["token"]) for call in result.calls[1:]] == [("GET", "job-token")] * 4
    assert result.sleeps == ["30", "30"]


def test_npm_publish_runs_the_saved_script_with_its_own_token(tmp_path):
    result = run_npm_publish(
        tmp_path, npm_env(APP_TOKEN="publish-app-token", DRY_RUN_TAG="v1.2.3"),
        npm_responses(9002, [run_record(9002)]),
    )
    assert result.status == 0, result
    assert result.output == "tag=v1.2.3\n"
    assert result.calls[0]["fields"] == [
        "ref=main", "return_run_details=true", "inputs[tag]=v1.2.3", "inputs[dry_run]=false",
    ]
    assert [call["token"] for call in result.calls] == ["publish-app-token", "job-token", "job-token"]


def test_a_wait_longer_than_an_app_token_lives_carries_no_app_token(tmp_path):
    # 130 polls, 30 s apart, are 65 minutes of waiting: past the hour an App
    # token lives. After the dispatch, no request carries the App token.
    polls = [run_record(9001, **WAITING)] * 130 + [run_record(9001)]
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(), npm_responses(9001, polls),
    )
    assert result.status == 0, result
    assert result.sleeps == ["30"] * 130
    assert (result.calls[0]["method"], result.calls[0]["token"]) == ("POST", "app-token")
    assert {(call["method"], call["token"]) for call in result.calls[1:]} == {("GET", "job-token")}


@pytest.mark.parametrize("changes,reason", [
    pytest.param({"RELEASE_DRY_RUN": "true"}, "a release dry run publishes no npm", id="release-dry-run"),
    pytest.param({"RELEASE_DRY_RUN": ""}, "a release dry run publishes no npm", id="no-dry-run-verdict"),
    pytest.param({"GIT_TAG": "1.2.3"}, "no release tag for the npm dry run", id="no-release-tag"),
    pytest.param({"WORKFLOW_ID": ""}, "no publish-pyodide.yml workflow id", id="no-workflow-id"),
    pytest.param({"APP_TOKEN": ""}, "no App token for the npm dry run dispatch", id="no-app-token"),
])
def test_npm_dry_run_refuses_before_any_request(tmp_path, changes, reason):
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(**changes),
        npm_responses(9001, [run_record(9001)]),
    )
    assert_step_refused(result, reason)
    assert result.calls == [], "a refused npm run must make no request"


@pytest.mark.parametrize("changes,reason", [
    pytest.param({"DRY_RUN_TAG": ""}, "this job has no verified npm dry run of v1.2.3", id="no-receipt"),
    pytest.param(
        {"DRY_RUN_TAG": "v1.2.2"}, "this job has no verified npm dry run of v1.2.3", id="other-tag",
    ),
    pytest.param(
        {"DRY_RUN_TAG": "v1.2.3", "RELEASE_DRY_RUN": "true"}, "a release dry run publishes no npm",
        id="release-dry-run",
    ),
])
def test_npm_publish_refuses_without_this_jobs_dry_run_of_the_tag(tmp_path, changes, reason):
    result = run_npm_publish(
        tmp_path, npm_env(**changes), npm_responses(9002, [run_record(9002)]),
    )
    assert_step_refused(result, reason)
    assert result.calls == [], "a refused npm publish must make no request"


def test_npm_publish_cannot_run_without_the_script_of_the_dry_run_step(tmp_path):
    result = run_release_step(
        RELEASE, RELEASE_NPM_PUBLISH, tmp_path, npm_env(DRY_RUN_TAG="v1.2.3"),
        npm_responses(9002, [run_record(9002)]),
    )
    assert result.status != 0 and result.output == "" and result.calls == [], result


@pytest.mark.parametrize("responses,reason,requests", [
    pytest.param(
        npm_responses(9001, [run_record(9001)], dispatch=[
            403, {"message": "Resource not accessible by integration"},
        ]),
        "the npm dry run dispatch failed", 1, id="dispatch-forbidden",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], dispatch=[204, None]),
        "the npm dry run dispatch returned no run id", 1, id="dispatch-without-run-details",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], dispatch=[200, {"run_url": "https://github.example"}]),
        "the npm dry run dispatch returned no run id", 1, id="dispatch-without-run-id",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001, conclusion="failure")]),
        "the npm dry run (run 9001) concluded 'failure'", 2, id="run-failed",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001, conclusion="cancelled")]),
        "the npm dry run (run 9001) concluded 'cancelled'", 2, id="run-cancelled",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9002)]),
        "run 9001 is not a publish-pyodide.yml dispatch on main", 2, id="another-run",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001, event="push")]),
        "run 9001 is not a publish-pyodide.yml dispatch on main", 2, id="not-a-dispatch",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001, head_branch="side")]),
        "run 9001 is not a publish-pyodide.yml dispatch on main", 2, id="not-on-main",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001, workflow_id=78)]),
        "run 9001 is not a publish-pyodide.yml dispatch on main", 2, id="another-workflow",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], jobs=npm_jobs(conclusion="skipped")),
        "run 9001 succeeded without one tag-guard job and one publish job", 3,
        id="publish-job-skipped",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], jobs=npm_jobs(steps=[
            {"name": NPM_PUBLISH_STEP, "conclusion": "skipped"},
        ])),
        "run 9001 succeeded without one tag-guard job and one publish job", 3,
        id="publish-step-skipped",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], jobs=npm_jobs(steps=[])),
        "run 9001 succeeded without one tag-guard job and one publish job", 3,
        id="publish-step-absent",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], jobs=npm_jobs(guard="skipped")),
        "run 9001 succeeded without one tag-guard job and one publish job", 3,
        id="guard-skipped",
    ),
    pytest.param(
        npm_responses(9001, [run_record(9001)], jobs=[500, {"message": "Server Error"}]),
        "the jobs of run 9001 could not be read", 7, id="jobs-unreadable",
    ),
])
def test_npm_run_refuses_what_is_not_this_tags_successful_run(tmp_path, responses, reason, requests):
    result = run_release_step(RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(), responses)
    assert_step_refused(result, reason)
    assert len(result.calls) == requests, f"the refusal must follow request {requests}\n{result}"
    assert result.calls[0]["method"] == "POST" and all(
        call["method"] == "GET" for call in result.calls[1:]
    ), "a refused run must not be dispatched again"


def test_npm_wait_never_takes_an_unreadable_run_for_a_completed_one(tmp_path):
    # What an expired or unauthorized token gets on every poll.
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(), npm_responses(9001, [UNREADABLE]),
    )
    assert_step_refused(result, "run 9001 of the npm dry run could not be read 10 times in a row")
    assert len(result.calls) == 11 and result.sleeps == ["30"] * 9, result


def test_npm_wait_survives_unreadable_polls_that_are_not_ten_in_a_row(tmp_path):
    polls = [UNREADABLE] * 9 + [run_record(9001, **WAITING)] + [UNREADABLE] * 9 + [run_record(9001)]
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(), npm_responses(9001, polls),
    )
    assert result.status == 0, result
    assert result.output == "tag=v1.2.3\n" and result.sleeps == ["30"] * 19


def test_npm_wait_ends_after_two_hours_without_a_completed_run(tmp_path):
    result = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(),
        npm_responses(9001, [run_record(9001, **WAITING)]),
    )
    assert_step_refused(result, "the npm dry run (run 9001) is still 'in_progress' after 240 polls")
    assert len(result.calls) == 241 and result.sleeps == ["30"] * 239, result


NPM_NAME = "@pineforge/codegen-pyodide"
NPM_PACKUMENT = "https://registry.npmjs.org/@pineforge%2fcodegen-pyodide"


def npm_tarball_url(version):
    return f"https://registry.npmjs.org/{NPM_NAME}/-/codegen-pyodide-{version}.tgz"


def write_npm_tarball(path, version, name=NPM_NAME):
    """A package tarball as the registry serves it; returns its sha512 integrity."""
    manifest = json.dumps({"name": name, "version": version}).encode("utf-8")
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("package/package.json")
        member.size = len(manifest)
        archive.addfile(member, io.BytesIO(manifest))
    digest = hashlib.sha512(path.read_bytes()).digest()
    return "sha512-" + base64.b64encode(digest).decode("ascii")


def write_npm_document(path, versions, tags, name=NPM_NAME):
    """A registry document; versions maps a version to its (tarball URL, integrity)."""
    path.write_text(json.dumps({
        "name": name,
        "dist-tags": tags,
        "versions": {
            version: {
                "name": name, "version": version,
                "dist": {"tarball": url, "integrity": integrity},
            }
            for version, (url, integrity) in versions.items()
        },
    }), encoding="utf-8")
    return str(path)


@dataclass(frozen=True)
class NpmRegistry:
    root: Path
    old: tuple   # (tarball URL, integrity) of 1.2.2, the latest before the release
    new: tuple   # the same of 1.2.3, the released version
    before: str  # the registry document before the publish
    after: str   # the registry document after it

    def tarball(self, version):
        return str(self.root / f"{version}.tgz")


@pytest.fixture
def npm_registry(tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    old = (npm_tarball_url("1.2.2"), write_npm_tarball(root / "1.2.2.tgz", "1.2.2"))
    new = (npm_tarball_url("1.2.3"), write_npm_tarball(root / "1.2.3.tgz", "1.2.3"))
    before = write_npm_document(root / "before.json", {"1.2.2": old}, {"latest": "1.2.2"})
    after = write_npm_document(
        root / "after.json", {"1.2.2": old, "1.2.3": new}, {"latest": "1.2.3"},
    )
    return NpmRegistry(root, old, new, before, after)


def readback_env(**changes):
    return {
        "RELEASE_DRY_RUN": "false", "PUBLISHED_TAG": "v1.2.3", "GIT_TAG": "v1.2.3",
        "NPM_NAME": NPM_NAME, "NPM_VERSION": "1.2.3", "NPM_DIST_TAG": "latest", **changes,
    }


def test_readback_waits_for_the_registry_and_writes_the_hub_receipt(tmp_path, npm_registry):
    served = {
        NPM_PACKUMENT: [npm_registry.before, npm_registry.before, npm_registry.after],
        npm_registry.new[0]: [npm_registry.tarball("1.2.3")],
    }
    result = run_release_step(RELEASE, RELEASE_READBACK, tmp_path, readback_env(), registry=served)
    assert result.status == 0, result
    assert result.output == "verified=@pineforge/codegen-pyodide@1.2.3\n"
    assert result.sleeps == ["15", "15"], "the registry lagged the publish twice"
    assert npm_registry.new[1] in result.stdout, "the receipt line names the verified integrity"


@pytest.mark.parametrize("case", [
    "version-absent", "dist-tag-elsewhere", "tarball-absent", "other-integrity",
    "other-packed-version", "other-tarball-url", "other-package",
])
def test_readback_never_verifies_what_the_registry_does_not_serve(tmp_path, npm_registry, case):
    document, tarball = npm_registry.after, npm_registry.tarball("1.2.3")
    old, new = npm_registry.old, npm_registry.new
    changed = npm_registry.root / "changed.json"
    if case == "version-absent":
        document = npm_registry.before
    elif case == "dist-tag-elsewhere":
        document = write_npm_document(
            changed, {"1.2.2": old, "1.2.3": new}, {"latest": "1.2.2", "next": "1.2.3"},
        )
    elif case == "tarball-absent":
        tarball = None
    elif case == "other-integrity":
        document = write_npm_document(changed, {"1.2.3": (new[0], old[1])}, {"latest": "1.2.3"})
    elif case == "other-packed-version":
        # The entry and its integrity agree with the tarball, which packs 1.2.2.
        document = write_npm_document(changed, {"1.2.3": (new[0], old[1])}, {"latest": "1.2.3"})
        tarball = npm_registry.tarball("1.2.2")
    elif case == "other-tarball-url":
        document = write_npm_document(changed, {"1.2.3": (old[0], new[1])}, {"latest": "1.2.3"})
    else:
        document = write_npm_document(
            changed, {"1.2.3": new}, {"latest": "1.2.3"}, name="@example/another",
        )
    result = run_release_step(
        RELEASE, RELEASE_READBACK, tmp_path, readback_env(),
        registry={NPM_PACKUMENT: [document], new[0]: [tarball]},
    )
    assert_step_refused(result, "the registry never served @pineforge/codegen-pyodide@1.2.3")
    assert result.sleeps == ["15"] * 40, "every one of the 40 reads must have been tried"


@pytest.mark.parametrize("changes,reason", [
    pytest.param({"PUBLISHED_TAG": ""}, "this job has no verified npm publish of v1.2.3", id="no-receipt"),
    pytest.param(
        {"PUBLISHED_TAG": "v1.2.2"}, "this job has no verified npm publish of v1.2.3", id="other-tag",
    ),
    pytest.param({"RELEASE_DRY_RUN": ""}, "no dry_run verdict", id="no-dry-run-verdict"),
    pytest.param({"NPM_VERSION": "1.2.4"}, "npm version '1.2.4' is not the version of tag", id="other-version"),
    pytest.param({"NPM_DIST_TAG": ""}, "no npm dist-tag", id="no-dist-tag"),
])
def test_readback_reads_nothing_back_before_this_jobs_publish(tmp_path, npm_registry, changes, reason):
    # The registry would verify: only the missing receipt or input refuses.
    served = {
        NPM_PACKUMENT: [npm_registry.after], npm_registry.new[0]: [npm_registry.tarball("1.2.3")],
    }
    result = run_release_step(
        RELEASE, RELEASE_READBACK, tmp_path, readback_env(**changes), registry=served,
    )
    assert_step_refused(result, reason)
    assert result.fetches == [], "nothing is read back without this job's publish of the tag"


def test_release_dry_run_exercises_the_readback_and_writes_no_hub_receipt(tmp_path, npm_registry):
    served = {
        NPM_PACKUMENT: [npm_registry.before], npm_registry.old[0]: [npm_registry.tarball("1.2.2")],
    }
    result = run_release_step(
        RELEASE, RELEASE_READBACK, tmp_path,
        readback_env(RELEASE_DRY_RUN="true", PUBLISHED_TAG=""), registry=served,
    )
    assert result.status == 0, result
    assert result.output == "", "a release dry run must write no receipt for the hub"
    assert (
        f"Read-back exercised on the published {NPM_NAME}@1.2.2 (latest): {npm_registry.old[1]}"
    ) in result.stdout


def test_release_dry_run_refuses_a_version_that_is_already_on_npm(tmp_path, npm_registry):
    served = {
        NPM_PACKUMENT: [npm_registry.after], npm_registry.new[0]: [npm_registry.tarball("1.2.3")],
    }
    result = run_release_step(
        RELEASE, RELEASE_READBACK, tmp_path,
        readback_env(RELEASE_DRY_RUN="true", PUBLISHED_TAG=""), registry=served,
    )
    assert result.status != 0 and result.output == "", result
    assert f"{NPM_NAME}@1.2.3 is already on npm" in result.stderr


def hub_env(**changes):
    return {
        "GH_TOKEN": "hub-app-token", "NEW_VERSION": "1.2.3", "PRERELEASE": "false",
        "RUN_ID": "4242", "NPM_VERIFIED": f"{NPM_NAME}@1.2.3", **changes,
    }


def test_hub_is_notified_once_with_the_token_minted_for_it(tmp_path):
    result = run_release_step(
        RELEASE, RELEASE_HUB, tmp_path, hub_env(), {f"POST {HUB_API}": [[204, None]]},
    )
    assert result.status == 0, result
    assert result.calls == [{
        "method": "POST", "endpoint": HUB_API, "token": "hub-app-token",
        "fields": [
            "event_type=codegen-release", "client_payload[version]=1.2.3",
            "client_payload[prerelease]=false", "client_payload[run_id]=4242",
        ],
    }]


@pytest.mark.parametrize("receipt", [
    "", "@pineforge/codegen-pyodide@1.2.2", "@pineforge/codegen-pyodide", "1.2.3", "true",
])
def test_hub_is_not_notified_without_the_readback_of_this_version(tmp_path, receipt):
    result = run_release_step(
        RELEASE, RELEASE_HUB, tmp_path, hub_env(NPM_VERIFIED=receipt),
        {f"POST {HUB_API}": [[204, None]]},
    )
    assert_step_refused(result, "no npm read-back of 1.2.3")
    assert result.calls == [], "the hub must not be notified"


def test_hub_step_fails_when_its_dispatch_is_refused(tmp_path):
    result = run_release_step(
        RELEASE, RELEASE_HUB, tmp_path, hub_env(),
        {f"POST {HUB_API}": [[403, {"message": "Resource not accessible by integration"}]]},
    )
    assert result.status != 0 and "dispatched codegen-release" not in result.stdout, result


def test_release_receipts_carry_the_npm_order_to_the_hub(tmp_path, npm_registry):
    """Dry run, publish, read-back, hub: each runs on the receipt the one before wrote."""
    dry = run_release_step(
        RELEASE, RELEASE_NPM_DRY, tmp_path, npm_env(APP_TOKEN="dry-app-token"),
        npm_responses(9001, [run_record(9001)]),
    )
    assert dry.status == 0, dry
    publish = run_release_step(
        RELEASE, RELEASE_NPM_PUBLISH, tmp_path,
        npm_env(APP_TOKEN="publish-app-token", DRY_RUN_TAG=step_outputs(dry)["tag"]),
        npm_responses(9002, [run_record(9002, **WAITING), run_record(9002)]),
    )
    assert publish.status == 0, publish
    readback = run_release_step(
        RELEASE, RELEASE_READBACK, tmp_path,
        readback_env(PUBLISHED_TAG=step_outputs(publish)["tag"]),
        registry={
            NPM_PACKUMENT: [npm_registry.after],
            npm_registry.new[0]: [npm_registry.tarball("1.2.3")],
        },
    )
    assert readback.status == 0, readback
    hub = run_release_step(
        RELEASE, RELEASE_HUB, tmp_path,
        hub_env(NPM_VERIFIED=step_outputs(readback)["verified"]),
        {f"POST {HUB_API}": [[204, None]]},
    )
    assert hub.status == 0, hub
    assert [run.calls[0]["fields"][-1] for run in (dry, publish)] == [
        "inputs[dry_run]=true", "inputs[dry_run]=false",
    ]
    assert [run.calls[0]["token"] for run in (dry, publish, hub)] == [
        "dry-app-token", "publish-app-token", "hub-app-token",
    ]
    assert readback.calls == [], "the read-back carries no token"
