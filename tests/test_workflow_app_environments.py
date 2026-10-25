"""Exercise the shipped workflow-lint preflight, including an absent policy.

On the pre-CR304 workflow the selected policy step is absent, so bash executes
no policy commands and exits zero. Unsafe fixtures then fail the exit-1
assertion, rather than failing to import a checker that main does not have.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from tests.test_pr_workflows import job_blocks


ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github/workflows/test.yml"
CHECKER = ROOT / "scripts/check_workflow_app_environments.py"
STEP = "Check App token environments"
COMMAND = "python3 scripts/check_workflow_app_environments.py"


def policy_step(workflow):
    job = job_blocks(workflow)["workflow-lint"]
    steps = re.split(r"(?=^      - )", job, flags=re.M)
    selected = [
        step for step in steps
        if re.match(rf'^      - name: "{re.escape(STEP)}"\n', step)
    ]
    assert len(selected) <= 1, "the environment preflight must not be duplicated"
    return "".join(selected)


def run_policy(tmp_path, files, *, ci=None, source=None):
    """Run the actual selected CI shell commands, with no policy stub."""
    workflow = CI.read_text(encoding="utf-8") if ci is None else ci
    step = policy_step(workflow)
    scripts = re.findall(r"^        run: \|\n((?:          [^\n]*\n|\n)*)", step, re.M)
    assert not step or len(scripts) == 1, "preflight must have a checked run block"
    workflow_dir = tmp_path / ".github/workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    followed = []
    for name, text in files.items():
        assert re.fullmatch(r"[\w-]+\.ya?ml", name), "fixture must be a workflow basename"
        path = workflow_dir / name
        path.write_text(text, encoding="utf-8")
        followed.append(path)
    # Main has no checker and no command invoking it. Do not manufacture one.
    if COMMAND in step:
        if source is None:
            source = CHECKER.read_text(encoding="utf-8")
        checker = tmp_path / "scripts/check_workflow_app_environments.py"
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_text(source, encoding="utf-8")
        followed.append(checker)
    script = tmp_path / "preflight.sh"
    script.write_text(
        "set -euo pipefail\n" + "".join(dedent(body) for body in scripts),
        encoding="utf-8",
    )
    # The checker itself refuses credential paths and symlinks before reading.
    # These are the only input files this test copy will follow.
    print("Preflight test inputs:", *(str(path) for path in [*followed, script]), sep="\n")
    bash = shutil.which("bash")
    assert bash is not None, "workflow-lint requires bash"
    return subprocess.run(
        [bash, "--noprofile", "--norc", str(script)],
        cwd=tmp_path, env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, capture_output=True, check=False,
    )


def mint_workflow(environment="", *, action="actions/create-github-app-token@v2"):
    return (
        "name: fixture\non: workflow_dispatch\njobs:\n  mint:\n"
        f"{environment}"
        "    runs-on: ubuntu-24.04\n    steps:\n"
        f"      - uses: {action}\n"
    )


def assert_refused(result, filename="unsafe.yml", job="mint", reason="create-github-app-token"):
    assert result.returncode == 1, (
        f"expected CR304 refusal (exit 1), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert re.search(
        rf"^{re.escape(filename)}:\d+:jobs\.{re.escape(job)}: "
        r"CR304: App token minting requires a literal, non-empty environment"
        rf" \(.*{re.escape(reason)}.*\)$",
        result.stderr, re.M,
    ), result.stderr
    assert "Traceback" not in result.stderr, result.stderr


def assert_accepted(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == "", result.stderr
    assert "App-token environment check: PASS" in result.stdout, result.stdout


@pytest.mark.parametrize("environment", [
    "",
    "    environment:\n",
    '    environment: ""\n',
    "    environment: '   '\n",
    "    environment: null\n",
    "    environment: ${{ inputs.environment }}\n",
    '    environment: "release-${{ github.ref_name }}"\n',
    "    environment:\n      url: https://example.invalid\n",
    "    environment:\n      name:\n",
    "    environment:\n      name: ${{ vars.ENVIRONMENT }}\n",
    '    environment: {"name": ""}\n',
    '    environment: {name: "${{ inputs.environment }}"}\n',
])
def test_unresolved_environment_is_refused_before_literal_controls(tmp_path, environment):
    assert_refused(run_policy(tmp_path / "unsafe", {"unsafe.yml": mint_workflow(environment)}))
    for index, literal in enumerate((
        "    environment: release\n",
        '    environment: "release-bot"\n',
        "    environment: release\n    # a comment before the next job property\n",
        "    environment:\n      name: release\n      url: https://example.invalid\n",
        '    environment: {"name": "release-bot", url: "https://example.invalid"}\n',
    )):
        assert_accepted(run_policy(
            tmp_path / f"literal-{index}", {"safe.yml": mint_workflow(literal)},
        ))


def test_every_workflow_file_and_job_is_checked(tmp_path):
    safe = mint_workflow("    environment: release\n")
    unsafe = mint_workflow().replace(
        "jobs:\n", "jobs:\n  harmless:\n    runs-on: ubuntu-24.04\n"
        "    steps:\n      - run: echo ordinary\n", 1,
    )
    files = {"a-safe.yml": safe, "z-new.yaml": unsafe}
    assert_refused(run_policy(tmp_path / "unsafe", files), filename="z-new.yaml")
    files["z-new.yaml"] = unsafe.replace(
        "  mint:\n", "  mint:\n    environment: release-bot\n", 1,
    )
    assert_accepted(run_policy(tmp_path / "safe", files))


@pytest.mark.parametrize("quote", ["'", '"'])
def test_quoted_security_keys_are_checked(tmp_path, quote):
    def quoted(text):
        for key in ("jobs", "mint", "steps", "uses", "environment", "name"):
            text = re.sub(rf"(?m)^(\s*(?:- )?){key}:", rf"\1{quote}{key}{quote}:", text)
        return text

    assert_refused(run_policy(
        tmp_path / "unsafe", {"unsafe.yml": quoted(mint_workflow('    environment: ""\n'))},
    ))
    assert_accepted(run_policy(
        tmp_path / "safe", {"safe.yml": quoted(mint_workflow(
            "    environment:\n      name: release\n",
        ))},
    ))


def test_escaped_keys_and_action_names_cannot_hide_minting(tmp_path):
    text = mint_workflow(
        '    "environ\\u006dent": ""\n',
        action='"actions/create-\\u0067ithub-app-token@a-future-pin"',
    )
    assert_refused(run_policy(tmp_path / "unsafe", {"unsafe.yml": text}))
    assert_accepted(run_policy(
        tmp_path / "safe", {"safe.yml": text.replace('": ""', '": "release"', 1)},
    ))


@pytest.mark.parametrize("unsafe,safe", [
    (
        "jobs:\n  mint:\n    runs-on: ubuntu-24.04\n"
        "    steps: [{uses: actions/create-github-app-token@v99}]\n",
        "jobs:\n  mint:\n    environment: {name: release}\n    runs-on: ubuntu-24.04\n"
        "    steps: [{uses: actions/create-github-app-token@v99}]\n",
    ),
    (
        "jobs:\n  mint: {runs-on: ubuntu-24.04, steps: [{uses: actions/create-github-app-token@main}]}\n",
        "jobs:\n  mint: {environment: release, runs-on: ubuntu-24.04, "
        "steps: [{uses: actions/create-github-app-token@main}]}\n",
    ),
    (
        'jobs: {"mint": {"steps": [{"uses": "actions/create-github-app-token@abc123"}]}}\n',
        'jobs: {"mint": {"environment": {"name": "release"}, '
        '"steps": [{"uses": "actions/create-github-app-token@abc123"}]}}\n',
    ),
])
def test_flow_mappings_cannot_hide_minting(tmp_path, unsafe, safe):
    prefix = "name: fixture\non: workflow_dispatch\n"
    assert_refused(run_policy(tmp_path / "unsafe", {"unsafe.yml": prefix + unsafe}))
    assert_accepted(run_policy(tmp_path / "safe", {"safe.yml": prefix + safe}))


@pytest.mark.parametrize("body,reason", [
    (
        "      - uses: another/token-action@arbitrary-ref\n        with:\n"
        "          private-key: ${{ secrets.UNRELATED_NAME }}\n",
        "private key secret",
    ),
    (
        "      - run: python mint.py\n        env:\n"
        "          KEY: ${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}\n",
        "private key secret",
    ),
    (
        "      - run: python mint.py\n        env:\n"
        "          KEY: ${{ secrets['PINEFORGE_APP_PRIVATE_KEY'] }}\n",
        "private key secret",
    ),
    (
        "      - run: gh api --method POST /app/installations/123/access_tokens\n",
        "possible scripted App token mint",
    ),
    (
        "      - run: node -e 'require(\"@octokit/auth-app\").createAppAuth(options)'\n",
        "possible scripted App token mint",
    ),
])
def test_other_actions_and_scripted_mints_are_checked(tmp_path, body, reason):
    text = "on: workflow_dispatch\njobs:\n  mint:\n    runs-on: ubuntu-24.04\n    steps:\n" + body
    assert_refused(run_policy(tmp_path / "unsafe", {"unsafe.yml": text}), reason=reason)
    safe = text.replace("  mint:\n", "  mint:\n    environment: release-bot\n", 1)
    result = run_policy(tmp_path / "safe", {"safe.yml": safe})
    assert_accepted(result)
    if reason.startswith("possible"):
        assert "CR304-REVIEW: possible scripted App token mint; human review required" in result.stdout


def test_workflow_env_cannot_hide_private_key_exposure(tmp_path):
    text = (
        "env:\n  KEY: ${{ secrets.PINEFORGE_APP_PRIVATE_KEY }}\n"
        + mint_workflow(action="actions/checkout@v7")
    )
    assert_refused(run_policy(tmp_path / "unsafe", {"unsafe.yml": text}), reason="private key secret")
    safe = text.replace("  mint:\n", "  mint:\n    environment: release\n", 1)
    assert_accepted(run_policy(tmp_path / "safe", {"safe.yml": safe}))


def test_a_step_environment_does_not_protect_its_job(tmp_path):
    text = mint_workflow().replace(
        "    steps:\n", "    steps:\n      - run: echo fixture\n"
        "        env:\n          environment: release\n", 1,
    )
    assert_refused(run_policy(tmp_path, {"unsafe.yml": text}))


@pytest.mark.parametrize("environment", [
    "    environment: release\n    environment: release-bot\n",
    "    environment: {name: release, name: release-bot}\n",
    "    environment: &deploy release\n",
    "    environment: *deploy\n",
])
def test_ambiguous_metadata_is_refused_for_review(tmp_path, environment):
    assert_refused(run_policy(tmp_path / "baseline", {"unsafe.yml": mint_workflow()}))
    result = run_policy(tmp_path / "ambiguous", {"unsafe.yml": mint_workflow(environment)})
    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(
        r"^unsafe\.yml:\d+: CR304: cannot resolve workflow metadata; human review required:",
        result.stderr, re.M,
    ), result.stderr
    assert "Traceback" not in result.stderr, result.stderr


@pytest.mark.parametrize("filename,job,expected", [
    ("release.yml", "release", "release"),
    ("promote-baseline.yml", "promote", "release-bot"),
])
def test_real_minting_jobs_pin_the_assigned_environment(filename, job, expected):
    body = job_blocks((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))[job]

    def assert_assignment(candidate):
        assert re.findall(r"^    environment: (.*)$", candidate, re.M) == [expected], (
            f"{filename}:jobs.{job} must use {expected}"
        )

    assert_assignment(body)  # Red on main: [] != [expected].
    original = f"    environment: {expected}\n"
    assert body.count(original) == 1
    with pytest.raises(AssertionError, match="must use"):
        assert_assignment(body.replace(original, "", 1))
    wrong = "release-bot" if expected == "release" else "release"
    with pytest.raises(AssertionError, match="must use"):
        assert_assignment(body.replace(original, f"    environment: {wrong}\n", 1))


def test_preflight_accepts_the_repository_workflows(tmp_path):
    assert_refused(run_policy(tmp_path / "baseline", {"unsafe.yml": mint_workflow()}))
    paths = sorted((ROOT / ".github/workflows").iterdir())
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in paths if path.suffix in {".yml", ".yaml"}
    }
    result = run_policy(tmp_path / "repository", files)
    assert_accepted(result)
    assert f"PASS ({len(files)} workflow files; 2 minting jobs)" in result.stdout


def test_preflight_remains_a_required_authorized_check(tmp_path):
    assert_refused(run_policy(tmp_path / "baseline", {"unsafe.yml": mint_workflow()}))
    ci = CI.read_text(encoding="utf-8")

    def assert_required(workflow):
        jobs = job_blocks(workflow)
        step = policy_step(workflow)
        assert re.findall(r"^        if: (.*)$", step, re.M) == [
            '"${{ needs.authz.result == \'success\' }}"',
        ], "environment preflight must run after successful authorization"
        assert "continue-on-error:" not in jobs["workflow-lint"], (
            "environment preflight must remain blocking"
        )
        assert re.search(
            r"^    needs: \[[^\n]*\bworkflow-lint\b[^\n]*\]$", jobs["test-check"], re.M,
        ), "test-check must require workflow-lint"
        assert not re.search(r"^    if:", jobs["workflow-lint"], re.M), (
            "workflow-lint must not be conditionally disabled"
        )

    assert_required(ci)
    step = policy_step(ci)
    assert step.count('        if: "${{ needs.authz.result == \'success\' }}"\n') == 1
    disabled = step.replace(
        '        if: "${{ needs.authz.result == \'success\' }}"\n',
        "        if: false\n", 1,
    )
    with pytest.raises(AssertionError, match="must run after successful authorization"):
        assert_required(ci.replace(step, disabled, 1))
    softened = step.replace('        shell: "bash"\n', '        continue-on-error: true\n        shell: "bash"\n', 1)
    assert softened != step
    with pytest.raises(AssertionError, match="must remain blocking"):
        assert_required(ci.replace(step, softened, 1))


@pytest.mark.parametrize("mutation", ["disabled", "removed", "loosened", "crash"])
def test_policy_mutations_cannot_pass_the_refusal_control(tmp_path, mutation):
    files = {"unsafe.yml": mint_workflow()}
    assert_refused(run_policy(tmp_path / "baseline", files))
    ci = CI.read_text(encoding="utf-8")
    step = policy_step(ci)
    assert step.count(COMMAND) == 1, "mutate the shipped command exactly once"
    source = None
    if mutation == "disabled":
        ci = ci.replace(COMMAND, "true", 1)
    elif mutation == "removed":
        ci = ci.replace(step, "", 1)
    else:
        source = CHECKER.read_text(encoding="utf-8")
        if mutation == "loosened":
            anchor = 'if not literal_environment(fields.get("environment")):'
            assert source.count(anchor) == 1
            source = source.replace(anchor, "if False:", 1)
        else:
            anchor = "raise SystemExit(main())"
            assert source.count(anchor) == 1
            source = source.replace(anchor, 'raise RuntimeError("deliberate CR304 crash")', 1)
    result = run_policy(tmp_path / "mutant", files, ci=ci, source=source)
    if mutation == "crash":
        assert result.returncode == 1, result.stdout + result.stderr
        assert "RuntimeError: deliberate CR304 crash" in result.stderr
        assert "App-token environment check: PASS" not in result.stdout
        with pytest.raises(AssertionError):
            assert_refused(result)  # A crash is not a policy verdict.
    else:
        with pytest.raises(AssertionError, match="expected CR304 refusal"):
            assert_refused(result)
