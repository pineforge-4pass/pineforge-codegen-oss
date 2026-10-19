# tests/test_check_npm_payload.py
"""scripts/check_npm_payload.py: the release refuses an npm package that does not
ship the archive names the build writes (tunaxis_codegen-<VERSION>.tar.gz).

Trees are built from literals; the script runs as the release workflow runs it.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_npm_payload.py"
_spec = importlib.util.spec_from_file_location("check_npm_payload", SCRIPT)
cnp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cnp)

OLD_FILES = ["index.mjs", "pineforge_codegen-*.tar.gz", "pineforge_codegen/**/*",
             "tables.json", "release.json", "transpile.worker.mjs", "glue.py", "LICENSE"]
NEW_FILES = ["index.mjs", "tunaxis_codegen-*.tar.gz", "tunaxis_codegen/**/*",
             "pineforge_codegen/**/*", "tables.json", "release.json",
             "transpile.worker.mjs", "glue.py", "LICENSE"]

OLD_INDEX = (
    'export const archivePath = join(HERE, `pineforge_codegen-${release.codegen}.tar.gz`);\n'
    'export const codegenSourceDir = join(HERE, "pineforge_codegen");\n'
)
NEW_INDEX = (
    'export const archivePath = join(HERE, `tunaxis_codegen-${release.codegen}.tar.gz`);\n'
    'export const codegenSourceDir = join(HERE, "tunaxis_codegen");\n'
)


def tree(tmp_path, files, index, version="1.6.3", *, exports=None):
    (tmp_path / "npm").mkdir()
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf8")
    pkg = {"files": files}
    if exports is not None:
        pkg["exports"] = exports
    (tmp_path / "npm" / "package.json").write_text(json.dumps(pkg), encoding="utf8")
    (tmp_path / "npm" / "index.mjs").write_text(index, encoding="utf8")
    # The build extracts these two directories at their repository-relative
    # paths. The catalog belongs only to the canonical package.
    for path, content in {
        "tunaxis_codegen/__init__.py": '"""Canonical package."""\n',
        "tunaxis_codegen/diagnostics_catalog.json": '{"diagnostics": []}\n',
        "pineforge_codegen/__init__.py": '"""Compatibility alias."""\n',
    }.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf8")
    return tmp_path


def run(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True)


def test_main_style_npm_package_is_refused(tmp_path):
    # npm/ as it is on main today: names pineforge_codegen-*.
    root = tree(tmp_path, OLD_FILES, OLD_INDEX)
    r = run(root)
    assert r.returncode == 1
    assert "tunaxis_codegen-1.6.3.tar.gz" in r.stderr
    assert "release refused" in r.stderr
    assert len(cnp.check(root)) == 4  # files archive, files dir, archivePath, codegenSourceDir


def test_renamed_npm_package_passes(tmp_path):
    root = tree(tmp_path, NEW_FILES, NEW_INDEX, exports={
        ".": "./index.mjs",
        "./diagnostics_catalog.json": "./tunaxis_codegen/diagnostics_catalog.json",
    })
    r = run(root)
    assert r.returncode == 0, r.stderr
    assert cnp.check(root) == []


def test_files_list_alone_is_enough_to_refuse(tmp_path):
    # index.mjs renamed, package.json "files" not: npm pack would drop the archive.
    problems = cnp.check(tree(tmp_path, OLD_FILES, NEW_INDEX))
    assert len(problems) == 2
    assert all("package.json" in p for p in problems)


def test_index_alone_is_enough_to_refuse(tmp_path):
    problems = cnp.check(tree(tmp_path, NEW_FILES, OLD_INDEX))
    assert len(problems) == 2
    assert all("index.mjs" in p for p in problems)


def test_archive_name_follows_version(tmp_path):
    # A glob that cannot match this VERSION's archive is refused.
    files = [p.replace("tunaxis_codegen-*", "tunaxis_codegen-1.6.2") for p in NEW_FILES]
    problems = cnp.check(tree(tmp_path, files, NEW_INDEX, version="1.6.3"))
    assert len(problems) == 1 and "tunaxis_codegen-1.6.3.tar.gz" in problems[0]


@pytest.mark.parametrize("prefix", ["", "./"])
@pytest.mark.parametrize("entry", [
    "tunaxis_codegen", "tunaxis_codegen/", "tunaxis_codegen/*",
    "tunaxis_codegen/**", "tunaxis_codegen/**/*",
])
def test_full_source_directory_entries_pass(tmp_path, prefix, entry):
    root = tree(tmp_path, ["tunaxis_codegen-*.tar.gz", prefix + entry], NEW_INDEX)
    assert cnp.check(root) == []


@pytest.mark.parametrize("prefix", ["", "./"])
@pytest.mark.parametrize("entry", [
    "tunaxis_codegen/README.md", "tunaxis_codegen/**/*.json",
    "tunaxis_codegen/**/*.py", "tunaxis_codegen/subdir/**",
])
def test_partial_source_directory_entries_are_refused(tmp_path, prefix, entry):
    root = tree(tmp_path, ["tunaxis_codegen-*.tar.gz", prefix + entry], NEW_INDEX)
    problems = cnp.check(root)
    assert len(problems) == 1 and "source directory" in problems[0]
    r = run(root)
    assert r.returncode == 1 and "release refused" in r.stderr


@pytest.mark.parametrize("exports", [
    "./tunaxis_codegen/diagnostics_catalog.json",
    {
        ".": {
            "import": "./tunaxis_codegen/__init__.py",
            "default": "./pineforge_codegen/__init__.py",
        },
        "./catalog": "./tunaxis_codegen/diagnostics_catalog.json",
        "./outside": "./not-a-source-directory/missing.json",
    },
    [None, "./tunaxis_codegen/__init__.py", "./pineforge_codegen/__init__.py"],
])
def test_existing_source_export_targets_pass(tmp_path, exports):
    root = tree(tmp_path, NEW_FILES, NEW_INDEX, exports=exports)
    r = run(root)
    assert r.returncode == 0, r.stderr
    assert cnp.check(root) == []


@pytest.mark.parametrize("target", [
    "./tunaxis_codegen/missing.py",
    "./pineforge_codegen/diagnostics_catalog.json",
])
def test_missing_source_export_targets_are_refused(tmp_path, target):
    root = tree(tmp_path, NEW_FILES, NEW_INDEX, exports={
        ".": {
            "import": ["./tunaxis_codegen/__init__.py", target],
            "default": "./index.mjs",
        },
    })
    problems = cnp.check(root)
    assert len(problems) == 1 and target in problems[0]
    r = run(root)
    assert r.returncode == 1 and target in r.stderr


@pytest.mark.parametrize("style", ["line", "block"])
def test_commented_correct_definitions_cannot_hide_wrong_ones(tmp_path, style):
    if style == "line":
        commented = "".join("// " + line + "\n" for line in NEW_INDEX.splitlines())
    else:
        commented = "/*\n" + NEW_INDEX + "*/\n"
    root = tree(tmp_path, NEW_FILES, commented + OLD_INDEX)
    problems = cnp.check(root)
    assert len(problems) == 2
    assert all("pineforge_codegen" in problem for problem in problems)
    r = run(root)
    assert r.returncode == 1 and "release refused" in r.stderr


def test_commented_wrong_definitions_do_not_duplicate_real_ones(tmp_path):
    commented = "// " + OLD_INDEX.splitlines()[0] + "\n/*\n" + OLD_INDEX + "*/\n"
    literals = (
        'const url = "https://example.invalid/payload";\n'
        "const marker = '/* literal */ // literal';\n"
        'const template = `/* literal */ // literal`;\n'
    )
    root = tree(tmp_path, NEW_FILES, commented + literals + NEW_INDEX)
    assert cnp.check(root) == []


@pytest.mark.parametrize("definition", ["archivePath", "codegenSourceDir"])
@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_of_each_index_definition_is_required(tmp_path, definition, count):
    lines = NEW_INDEX.splitlines(keepends=True)
    definition_line = next(line for line in lines if f"const {definition} " in line)
    if count == 0:
        index = NEW_INDEX.replace(definition_line, "")
    else:
        index = NEW_INDEX + definition_line
    root = tree(tmp_path, NEW_FILES, index)
    problems = cnp.check(root)
    assert len(problems) == 1
    assert f"exactly one {definition} definition, found {count}" in problems[0]
    assert run(root).returncode == 1


def _workflow_steps(workflow, job):
    """Read step fields and literal run lines in the workflows' YAML layout."""
    body = re.search(
        rf"^  {re.escape(job)}:\n(.*?)(?=^  [\w-]+:|\Z)",
        workflow, re.M | re.S,
    )
    assert body, f"missing job {job}"
    steps = []
    for block in re.split(r"^      - ", body.group(1), flags=re.M)[1:]:
        fields = {"commands": []}
        in_run = False
        for i, line in enumerate(block.splitlines()):
            if in_run and (not line.strip() or line.startswith("          ")):
                if line.strip():
                    fields["commands"].append(line.strip())
                continue
            in_run = False
            if i == 0:
                field = line
            elif line.startswith("        "):
                field = line[8:]
            else:
                continue
            key, sep, value = field.partition(":")
            if not sep or key != key.strip():
                continue
            fields[key] = value.strip()
            if key == "run":
                if value.strip() in ("|", "|-", "|+"):
                    in_run = True
                else:
                    fields["commands"].append(value.strip())
        steps.append(fields)
    return steps


def _assert_workflow_guard(workflow, job, version_steps, build_step):
    steps = _workflow_steps(workflow, job)
    guards = [
        i for i, step in enumerate(steps) for command in step["commands"]
        if command == "python scripts/check_npm_payload.py"
    ]
    assert len(guards) == 1, f"{job}: expected one exact guard command in run:"
    guard = guards[0]
    assert "if" not in steps[guard]
    assert steps[guard].get("continue-on-error", "false") == "false"
    builds = [i for i, step in enumerate(steps) if step.get("name") == build_step]
    assert len(builds) == 1 and guard < builds[0]
    for name in version_steps:
        versions = [i for i, step in enumerate(steps) if step.get("name") == name]
        assert len(versions) == 1 and versions[0] < guard
    assert any(
        re.fullmatch(r"actions/setup-python@[0-9a-f]{40}(?:\s+#.*)?", step.get("uses", ""))
        for step in steps[:guard]
    ), f"{job}: guard needs Python setup pinned by full SHA"


@pytest.mark.parametrize("filename,job,version_steps,build_step", [
    ("release.yml", "release", ("Write VERSION",), "Build sdist + wheel"),
    (
        "publish-pyodide.yml", "publish",
        ("Verify tag matches VERSION", "Resolve npm dist-tag from VERSION"),
        "Assemble the package",
    ),
])
def test_release_workflow_runs_the_check_before_building(filename, job, version_steps, build_step):
    wf = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf8")
    _assert_workflow_guard(wf, job, version_steps, build_step)


def _guard_workflow(run_field):
    return (
        "jobs:\n"
        "  publish:\n"
        "    steps:\n"
        "      - name: Write VERSION\n"
        "        run: echo version\n"
        "      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065\n"
        "      - name: Check npm payload\n"
        f"{run_field}\n"
        "      - name: Assemble the package\n"
        "        run: node scripts/build-npm-package.mjs\n"
    )


@pytest.mark.parametrize("run_field", [
    "        run: python scripts/check_npm_payload.py",
    "        run: |\n          python scripts/check_npm_payload.py",
])
def test_wiring_accepts_exact_inline_and_block_run_commands(run_field):
    _assert_workflow_guard(
        _guard_workflow(run_field), "publish", ("Write VERSION",), "Assemble the package",
    )


@pytest.mark.parametrize("run_field", [
    "        # run: python scripts/check_npm_payload.py",
    "        run: |\n          # python scripts/check_npm_payload.py",
    "        run: python scripts/check_npm_payload.py || true",
    "        run: |\n          python scripts/check_npm_payload.py || true",
    "        env:\n          COMMAND: python scripts/check_npm_payload.py\n        run: echo skipped",
])
def test_wiring_refuses_comments_ignored_failures_and_non_run_fields(run_field):
    with pytest.raises(AssertionError):
        _assert_workflow_guard(
            _guard_workflow(run_field), "publish", ("Write VERSION",), "Assemble the package",
        )


@pytest.mark.parametrize("change", ["late-version", "early-build", "other-job", "ignored-failure"])
def test_wiring_requires_a_blocking_guard_in_the_publish_job_before_build(change):
    wf = _guard_workflow("        run: python scripts/check_npm_payload.py")
    if change == "late-version":
        version = "      - name: Write VERSION\n        run: echo version\n"
        wf = wf.replace(version, "").replace(
            "      - name: Assemble", version + "      - name: Assemble",
        )
    elif change == "early-build":
        build = "      - name: Assemble the package\n        run: node scripts/build-npm-package.mjs\n"
        wf = wf.replace(build, "").replace(
            "      - name: Check npm payload", build + "      - name: Check npm payload",
        )
    elif change == "other-job":
        wf = wf.replace(
            "        run: python scripts/check_npm_payload.py\n", "        run: echo skipped\n",
        )
        wf += "  unrelated:\n    steps:\n      - run: python scripts/check_npm_payload.py\n"
    else:
        wf = wf.replace(
            "      - name: Check npm payload\n",
            "      - name: Check npm payload\n        continue-on-error: true\n",
        )
    with pytest.raises(AssertionError):
        _assert_workflow_guard(wf, "publish", ("Write VERSION",), "Assemble the package")
