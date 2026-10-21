# tests/test_release_workflows.py
"""The release workflows take every channel's spelling from
scripts/release_version.py, so a prerelease never reaches a `latest` channel."""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
NPM = (WORKFLOWS / "publish-pyodide.yml").read_text(encoding="utf-8")


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


def npm_job_blocks(text):
    """The job blocks of the npm workflow, keyed by job id (the checked 2-space layout)."""
    assert text.count("\njobs:\n") == 1, "workflow must have one jobs block"
    parts = re.split(r"^  ([\w-]+):[ \t]*$", text.split("\njobs:\n", 1)[1], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def assert_npm_jobs_in_main_only_environment(text):
    """Both npm jobs declare exactly `environment: npm-publish` (main-only deployment-branch
    policy; npm trusted publishing requires it), and no other environment exists."""
    jobs = npm_job_blocks(text)
    assert set(jobs) == {"publish", "claim-npm"}, "npm workflow must have exactly the two npm jobs"
    for job_id, body in jobs.items():
        lines = re.findall(r"^    environment:[^\n]*$", body, re.M)
        assert lines == ["    environment: npm-publish"], f"{job_id} must declare `environment: npm-publish`"
    assert text.count("environment:") == 2, "only the two npm jobs may declare an environment"


def test_npm_jobs_run_in_the_main_only_environment():
    assert_npm_jobs_in_main_only_environment(NPM)


def test_npm_environment_pin_refuses_real_workflow_mutations():
    assert_npm_jobs_in_main_only_environment(NPM)
    jobs = npm_job_blocks(NPM)
    original = "    environment: npm-publish\n"
    for job_id in ("publish", "claim-npm"):
        body = jobs[job_id]
        assert body.count(original) == 1, f"mutation anchor must match {job_id} exactly once"
        for replacement in (
            "",
            "    environment: production\n",
            original + "    environment: npm-publish-2\n",
            "    environment: ${{ inputs.claim_version }}\n",
        ):
            mutant = NPM.replace(body, body.replace(original, replacement, 1), 1)
            assert mutant != NPM, "mutation must change the shipped workflow"
            try:
                assert_npm_jobs_in_main_only_environment(mutant)
            except AssertionError:
                continue
            raise AssertionError(f"{job_id}: mutant {replacement!r} was accepted")
