# tests/test_release_workflows.py
"""Pin hosted runners, main-ref dispatch and verified-tag npm publication.

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
            assert not any(label == "self-hosted" or label.startswith("pf-ci") for label in labels), (
                f"{path.name}:{index + 1}: runs-on names a retired runner label: {value}"
            )
            checked += 1
    assert checked, "must inspect actual runs-on values"


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_retired_runner_guard_rejects_workflow_mutations(tmp_path, monkeypatch, suffix):
    monkeypatch.setitem(globals(), "WORKFLOWS", tmp_path)
    path = tmp_path / f"release{suffix}"
    path.write_text(RELEASE, encoding="utf-8")
    test_no_workflow_uses_retired_runner_labels()
    mutant = mutate_once(
        RELEASE, "    runs-on: ubuntu-latest\n", "    runs-on: self-hosted\n",
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


def assert_no_npm_workflow_dispatch(text):
    text = re.sub(r"\\\r?\n[ \t]*", " ", text)
    # No workflow of this repository dispatches any workflow, so the pin does
    # not depend on how the target is named (file, display name, numeric id,
    # expression or a separately supplied variable).
    for pattern in (
        r"\bworkflow\s+run\b",
        r"\bcreateWorkflowDispatch\b",
        r"\bactions/workflows/[^\s/]+/dispatches\b",
    ):
        assert re.search(pattern, text) is None, "no workflow may dispatch any workflow"


def test_no_workflow_dispatches_publish_pyodide():
    paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    assert paths, "must discover workflow files for the manual-dispatch pin"
    for path in paths:
        assert_no_npm_workflow_dispatch(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("command", [
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
def test_manual_dispatch_pin_rejects_a_workflow_dispatch_step(command):
    assert_no_npm_workflow_dispatch(RELEASE)
    marker = "      - name: Create GitHub Release\n"
    mutant = mutate_once(
        RELEASE, marker,
        "      - name: Dispatch publish-pyodide on main\n"
        "        run: |\n"
        f"          {command}\n\n" + marker,
    )
    with pytest.raises(AssertionError, match="no workflow may dispatch"):
        assert_no_npm_workflow_dispatch(mutant)


def assert_release_app_token_scope(text):
    release = workflow_jobs(text).get("release", "")
    app = step(release, "Mint org App token")
    assert "actions/create-github-app-token@" in app
    assert "permission-actions:" not in app, "release App token must not request Actions permission"
    # gh/main's App-token step has no explicit permission inputs.
    assert re.findall(r"^\s+permission-[\w-]+:", app, re.M) == [], (
        "release App token must keep main's permission inputs"
    )
    # The whole `with:` block is main's, so dropping `repositories:` (which
    # would widen the token to every repository of the installation) fails.
    assert app.rstrip("\n").endswith(
        "        with:\n"
        "          app-id: ${{ secrets.PINEFORGE_APP_ID }}\n"
        "          private-key: ${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}\n"
        "          owner: ${{ github.repository_owner }}\n"
        "          repositories: pineforge-codegen-oss,pineforge-release"
    ), "release App token inputs must stay exactly main's"


def test_release_preserves_main_app_token_scope():
    assert_release_app_token_scope(RELEASE)


@pytest.mark.parametrize("permission", ["actions", "contents", "issues"])
def test_app_token_pin_rejects_new_permission_inputs(permission):
    assert_release_app_token_scope(RELEASE)
    release = workflow_jobs(RELEASE).get("release", "")
    app = step(release, "Mint org App token")
    replacement = mutate_once(
        app, "        with:\n",
        f"        with:\n          permission-{permission}: write\n",
    )
    mutant = mutate_once(RELEASE, app, replacement)
    with pytest.raises(AssertionError):
        assert_release_app_token_scope(mutant)


@pytest.mark.parametrize("line", [
    "          repositories: pineforge-codegen-oss,pineforge-release\n",
    "          owner: ${{ github.repository_owner }}\n",
])
def test_app_token_pin_rejects_a_dropped_input(line):
    release = workflow_jobs(RELEASE).get("release", "")
    app = step(release, "Mint org App token")
    dropped = line.rstrip("\n")
    assert dropped in app, "the mutation anchor must be a real App-token input"
    mutant = mutate_once(RELEASE, app, app.replace(dropped, "").rstrip(" \n") + "\n")
    with pytest.raises(AssertionError):
        assert_release_app_token_scope(mutant)


def test_release_keeps_pypi_tag_release_and_hub_order():
    assert set(workflow_triggers(RELEASE)) == {"workflow_dispatch"}, "release must be dispatch-only"
    release = workflow_jobs(RELEASE).get("release", "")
    bodies = [step(release, name) for name in (
        "Publish to PyPI", "Commit + tag + push",
        "Create GitHub Release", "Dispatch codegen-release",
    )]
    for body in bodies:
        assert "        if: ${{ !inputs.dry_run }}\n" in body, "release side effects must skip dry runs"
    assert "git push origin HEAD --tags" in bodies[1], "release must push its tag"
    positions = [release.index(body) for body in bodies]
    assert positions == sorted(positions), "keep PyPI, tag push, GitHub Release and hub order"


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
