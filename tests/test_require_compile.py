"""Run the real compile helper in isolated pytest processes, without mocks."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


HELPER = Path(__file__).resolve().parent / "_compile.py"


def _checked_path(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (path.absolute(), resolved):
        parts = candidate.parts
        if (
            any(part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
                for part in parts)
            or "/.config/zsh/keys" in candidate.as_posix()
            or "/.config/pineforge" in candidate.as_posix()
        ):
            raise ValueError(f"refusing control input path: {candidate}")
    return resolved


def _run_control(tmp_path: Path, body: str, *, required: bool):
    helper = _checked_path(HELPER)
    probe = _checked_path(tmp_path / "test_helper_probe.py")
    result_file = _checked_path(tmp_path / "result.xml")
    probe.write_text(
        "import importlib.util\n"
        "import pytest\n"
        f"spec = importlib.util.spec_from_file_location('compile_probe', {str(helper)!r})\n"
        "compile_env = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(compile_env)\n"
        "def test_probe():\n"
        + "".join(f"    {line}\n" for line in body.splitlines()),
        encoding="utf-8",
    )
    # No inherited pytest plugins, engine paths, shell startup or credentials.
    env = {
        "PATH": str(tmp_path),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PINEFORGE_ENGINE_INCLUDE": str(tmp_path / "missing-engine"),
        "PINEFORGE_EIGEN_INCLUDE": str(tmp_path / "missing-eigen"),
        "CXX": str(tmp_path / "missing-compiler"),
    }
    if required:
        env["PINEFORGE_REQUIRE_COMPILE"] = "1"
    # List and check every file named in this diagnostic command before it runs.
    print("Compile-helper control files:", helper, probe, result_file, sep="\n", flush=True)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", "-q", "-ra", "--noconftest",
         "-p", "no:cacheprovider", "-o", "addopts=", "--rootdir", str(tmp_path),
         "--junitxml", str(result_file), str(probe)],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result_file.is_file(), result.stdout + result.stderr
    cases = ET.parse(result_file).findall(".//testcase")
    assert len(cases) == 1, result_file.read_text(encoding="utf-8")
    return result, cases[0]


@pytest.mark.parametrize("body", [
    "compile_env.skip_if_no_compile_env()",
    'compile_env.compile_cpp("int value;\\n", label="missing-compiler")',
    "if not compile_env.have_compile_env():\n"
    "    pytest.skip('collection guard has no compile environment')",
], ids=["skip-helper", "compile-helper", "collection-guard"])
@pytest.mark.parametrize("required", [False, True], ids=["opt-in", "required"])
def test_missing_compiler_fails_only_when_required(tmp_path, body, required):
    result, case = _run_control(tmp_path, body, required=required)
    output = result.stdout + result.stderr
    if required:
        assert result.returncode == 1, output
        failure = case.find("failure")
        assert failure is not None, output
        assert "PINEFORGE_REQUIRE_COMPILE=1" in failure.get("message", ""), output
        assert "CXX compiler not found" in failure.get("message", ""), output
        assert case.find("skipped") is None, output
    else:
        assert result.returncode == 0, output
        assert case.find("skipped") is not None, output
        assert case.find("failure") is None, output
    assert case.find("error") is None, output


def test_control_crash_is_a_nonzero_failed_result(tmp_path):
    result, case = _run_control(
        tmp_path, "raise RuntimeError('deliberate crash control')", required=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    failure = case.find("failure")
    assert failure is not None
    assert "deliberate crash control" in failure.get("message", "")
    assert case.find("skipped") is None
