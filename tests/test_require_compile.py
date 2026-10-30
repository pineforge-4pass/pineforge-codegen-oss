"""Run the real compile helper in isolated pytest processes, without mocks."""

from __future__ import annotations

import hashlib
import json
import os
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


def _pairing_values(tmp_path: Path) -> dict[str, str]:
    return {
        name: hashlib.sha1(f"{tmp_path}/{name}".encode("utf-8")).hexdigest()
        for name in ("PINEFORGE_ENGINE_SHA", "PINEFORGE_CORPUS_SHA")
    }


def _run_pairing_control(
    tmp_path: Path, pairing: dict[str, str], *, required: bool, crash: bool = False,
):
    root = _checked_path(HELPER.parent.parent)
    sweep = _checked_path(HELPER.parent / "test_translate_implies_compile.py")
    helper = _checked_path(HELPER)
    corpus_helper = _checked_path(HELPER.parent / "test_compile_corpus.py")
    inputs = [helper, corpus_helper, sweep]
    for variable in (
        "PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_EIGEN_INCLUDE",
        "PINEFORGE_GENERATED_INCLUDE", "PINEFORGE_ENGINE_LIB", "PINEFORGE_ENGINE_CORPUS",
    ):
        if os.environ.get(variable):
            inputs.append(_checked_path(Path(os.environ[variable]).expanduser()))
    print("Pairing control input files and roots:", *inputs, sep="\n", flush=True)
    from tests import _compile

    _compile.skip_if_no_compile_env()
    probe = _checked_path(tmp_path / "test_pairing_probe.py")
    result_file = _checked_path(tmp_path / "pairing-result.xml")
    report = _checked_path(tmp_path / "compile-report")
    python = Path(sys.executable)
    _checked_path(python)
    assert _compile._ENGINE_INC is not None and _compile._EIGEN_INC is not None
    assert _compile._COMPILER is not None
    engine = _checked_path(_compile._ENGINE_INC)
    eigen = _checked_path(_compile._EIGEN_INC)
    compiler = Path(_compile._COMPILER)
    _checked_path(compiler)
    body = (
        "raise RuntimeError('deliberate pairing control crash')\n"
        if crash else
        # This C++ path is a diagnostic label; no file is opened at it.
        "sources = [(Path(__file__).with_suffix('.cpp'), 'int pairing_control = 1;\\n')]\n"
        "sweep_results.extend(sweep._compile_sources(sources))\n"
        "assert [row.outcome for row in sweep_results] == ['compiled']\n"
    )
    probe.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "import tests.test_translate_implies_compile as sweep\n"
        "sweep_results = sweep.sweep_results\n"
        "def test_probe(sweep_results):\n"
        + "".join(f"    {line}\n" for line in body.splitlines()),
        encoding="utf-8",
    )
    # Use real, resolved native inputs, without inheriting credentials or plugins.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PINEFORGE_ENGINE_INCLUDE": str(engine),
        "PINEFORGE_EIGEN_INCLUDE": str(eigen),
        "PINEFORGE_ENGINE_LIB": str(tmp_path / "missing-runtime.a"),
        "PINEFORGE_ENGINE_CORPUS": str(tmp_path / "missing-corpus"),
        "CXX": str(compiler),
        "PINEFORGE_COMPILE_SHARD": "0",
        "PINEFORGE_COMPILE_SHARDS": "1",
        "PINEFORGE_COMPILE_REPORT_DIR": str(report),
        "TMPDIR": str(tmp_path),
        **pairing,
    }
    followed = [helper, corpus_helper, sweep, probe, result_file, python, compiler,
                engine / "pineforge/engine.hpp", eigen / "Eigen/Dense",
                report / "results.json", report / "pairing.txt",
                report / "compile-failures.txt", report / "front-end-rejections.txt"]
    if _compile._GENERATED_INC is not None:
        generated = _checked_path(_compile._GENERATED_INC)
        env["PINEFORGE_GENERATED_INCLUDE"] = str(generated)
        followed.append(generated / "pineforge/version.h")
    if required:
        env["PINEFORGE_REQUIRE_COMPILE"] = "1"
    for path in followed:
        _checked_path(path)
    print("Pairing control files:", *followed, sep="\n", flush=True)
    result = subprocess.run(
        [str(python), "-I", "-m", "pytest", "-q", "-ra", "--noconftest",
         "-p", "no:cacheprovider", "-o", "addopts=", "--rootdir", str(tmp_path),
         "--junitxml", str(result_file), str(probe)],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result_file.is_file(), result.stdout + result.stderr
    cases = ET.parse(result_file).findall(".//testcase")
    assert len(cases) == 1, result_file.read_text(encoding="utf-8")
    return result, cases[0], report


@pytest.mark.parametrize("variable", ["PINEFORGE_ENGINE_SHA", "PINEFORGE_CORPUS_SHA"])
@pytest.mark.parametrize("bad_sha", [None, "a" * 39, "a" * 41, "g" * 40],
                         ids=["missing", "short", "long", "nonhex"])
def test_required_pairing_rejects_missing_or_malformed_sha(tmp_path, variable, bad_sha):
    pairing = _pairing_values(tmp_path)
    if bad_sha is None:
        pairing.pop(variable)
    else:
        pairing[variable] = bad_sha
    result, case, _ = _run_pairing_control(tmp_path, pairing, required=True)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    error = case.find("error")
    assert error is not None, output
    assert (
        f"PINEFORGE_REQUIRE_COMPILE=1: {variable} must be 40 hex characters"
        in error.get("message", "")
    ), output
    assert case.find("failure") is None, output
    assert case.find("skipped") is None, output


@pytest.mark.parametrize("recorded", [False, True], ids=["unrecorded", "recorded"])
def test_pairing_report_reads_both_environment_values(tmp_path, recorded):
    pairing = _pairing_values(tmp_path) if recorded else {}
    result, case, report = _run_pairing_control(tmp_path, pairing, required=recorded)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert case.find("error") is None, output
    assert case.find("failure") is None, output
    assert case.find("skipped") is None, output
    payload = json.loads((report / "results.json").read_text(encoding="utf-8"))
    engine = pairing.get("PINEFORGE_ENGINE_SHA", "unrecorded")
    corpus = pairing.get("PINEFORGE_CORPUS_SHA", "unrecorded")
    assert payload.get("engine_sha") == engine
    assert payload.get("corpus_sha") == corpus
    assert [row["outcome"] for row in payload["results"]] == ["compiled"]
    assert (report / "pairing.txt").read_text(encoding="utf-8") == (
        f"PINEFORGE_ENGINE_SHA={engine} PINEFORGE_CORPUS_SHA={corpus}\n"
    )


def test_pairing_control_crash_is_a_nonzero_failed_result(tmp_path):
    pairing = _pairing_values(tmp_path)
    result, case, report = _run_pairing_control(
        tmp_path, pairing, required=True, crash=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    failure = case.find("failure")
    assert failure is not None, output
    assert "deliberate pairing control crash" in failure.get("message", ""), output
    assert case.find("error") is None, output
    assert case.find("skipped") is None, output
    payload = json.loads((report / "results.json").read_text(encoding="utf-8"))
    assert payload["results"] == []
    assert payload.get("engine_sha") == pairing["PINEFORGE_ENGINE_SHA"]
    assert payload.get("corpus_sha") == pairing["PINEFORGE_CORPUS_SHA"]
