# tests/test_check_npm_payload.py
"""scripts/check_npm_payload.py: the release refuses an npm package that does not
ship the archive names the build writes (tunaxis_codegen-<VERSION>.tar.gz).

Trees are built from literals; the script runs as the release workflow runs it.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

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


def tree(tmp_path, files, index, version="1.6.3"):
    (tmp_path / "npm").mkdir()
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf8")
    (tmp_path / "npm" / "package.json").write_text(json.dumps({"files": files}), encoding="utf8")
    (tmp_path / "npm" / "index.mjs").write_text(index, encoding="utf8")
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
    root = tree(tmp_path, NEW_FILES, NEW_INDEX)
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


def test_release_workflow_runs_the_check_before_building():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf8")
    assert "python scripts/check_npm_payload.py" in wf
    assert wf.index("python scripts/check_npm_payload.py") < wf.index("- name: Build sdist + wheel")
