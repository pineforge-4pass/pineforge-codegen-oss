"""Missing native inputs can be required without invoking a compiler."""

import pytest

from tests import _compile


@pytest.mark.parametrize("required", [None, "0", "true", "1"])
@pytest.mark.parametrize("missing, message", [
    ("_COMPILER", "CXX compiler not found on PATH (try CXX=clang++)."),
    ("_ENGINE_INC", "PINEFORGE_ENGINE_INCLUDE not set or invalid. "
     "Point it at pineforge-engine/include (containing pineforge/engine.hpp)."),
    ("_EIGEN_INC", "Eigen3 headers not found. Set PINEFORGE_EIGEN_INCLUDE to the dir "
     "whose child is Eigen/ (or install eigen3 to a system path)."),
    ("_ENGINE_LIB", "No built libpineforge.a. Set PINEFORGE_ENGINE_LIB to the archive "
     "(or configure a build*/lib/ beside the engine checkout) to run the "
     "emitted translation unit."),
])
def test_missing_native_inputs_skip_or_fail_with_the_same_message(monkeypatch, required, missing, message):
    for name in ("_COMPILER", "_ENGINE_INC", "_EIGEN_INC", "_ENGINE_LIB"):
        monkeypatch.setattr(_compile, name, object())
    monkeypatch.setattr(_compile, missing, None)
    if required is None:
        monkeypatch.delenv("PINEFORGE_REQUIRE_NATIVE", raising=False)
    else:
        monkeypatch.setenv("PINEFORGE_REQUIRE_NATIVE", required)
    outcome = pytest.fail.Exception if required == "1" else pytest.skip.Exception
    helpers = [_compile.skip_if_no_engine_lib]
    if missing != "_ENGINE_LIB":
        helpers.append(_compile.skip_if_no_compile_env)
    for helper in helpers:
        # Catch skips too so an unexpected skip fails this assertion.
        with pytest.raises((pytest.fail.Exception, pytest.skip.Exception)) as error:
            helper()
        assert isinstance(error.value, outcome)
        assert str(error.value) == message


def test_required_native_inputs_allow_a_complete_environment(monkeypatch):
    monkeypatch.setenv("PINEFORGE_REQUIRE_NATIVE", "1")
    for name in ("_COMPILER", "_ENGINE_INC", "_EIGEN_INC", "_ENGINE_LIB"):
        monkeypatch.setattr(_compile, name, object())
    _compile.skip_if_no_compile_env()
    _compile.skip_if_no_engine_lib()
