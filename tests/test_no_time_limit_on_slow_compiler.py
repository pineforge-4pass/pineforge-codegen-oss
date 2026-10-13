"""A slow but correct compiler must never fail a check; this test runs about
65 seconds on purpose to exercise a compiler slower than the old helper limit.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._compile import skip_if_no_compile_env


def test_compile_cpp_waits_for_slow_compiler(tmp_path):
    skip_if_no_compile_env()
    real_compiler = shutil.which(os.environ.get("CXX", "g++"))
    if real_compiler is None:
        pytest.skip("CXX compiler not found on PATH (try CXX=clang++).")

    wrapper = tmp_path / "slow-compiler"
    wrapper.write_text(
        "#!/bin/sh\n"
        "sleep 62\n"
        f'exec {shlex.quote(str(Path(real_compiler).absolute()))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    child_env = os.environ.copy()
    child_env["CXX"] = str(wrapper)
    child_code = (
        "from tests._compile import compile_cpp\n"
        'compile_cpp("int main() { return 0; }\\n", label="slow-compiler")\n'
        'print("SLOW_COMPILER_OK")\n'
    )
    child = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        capture_output=True,
        text=True,
    )

    output = f"stdout:\n{child.stdout}\nstderr:\n{child.stderr}"
    assert child.returncode == 0, output
    assert "SLOW_COMPILER_OK" in child.stdout.splitlines(), output
    for word in ("TimeoutExpired", "timed out", "Timeout"):
        assert word not in child.stdout, output
        assert word not in child.stderr, output
