"""``tests._private_fixtures.private_fixture``: how a codegen test finds a fixture that lives in the private repository.

The private fixtures are overlaid onto the paired ENGINE tree (``<engine>/tests/codegen-fixtures/...``), never onto
this repository. A test names the file relative to that folder. Three worlds:

* the file is there: the helper returns its path;
* the file is not there and no overlay was asked for: the test SKIPS with ``private fixtures not present: <path>``;
* the file is not there but the job says the overlay is present (``PINEFORGE_PRIVATE_FIXTURES`` is nonempty): that is
  a failure, never a skip, so a broken overlay cannot turn a private test into a silent pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _private_fixtures as pf


def _engine(tmp_path: Path) -> Path:
    engine = tmp_path / "pineforge-engine"
    (engine / "include").mkdir(parents=True)
    return engine


def _must_not_skip(rel: str) -> Path:
    try:
        return pf.private_fixture(rel)
    except pytest.skip.Exception as exc:
        pytest.fail(f"private_fixture unexpectedly skipped: {exc}")


def test_present_file_is_returned(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    target = engine / "tests" / "codegen-fixtures" / "group" / "tape" / "tv_trades.csv"
    target.parent.mkdir(parents=True)
    target.write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    assert _must_not_skip("group/tape/tv_trades.csv") == target


def test_present_file_with_overlay_is_returned(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    target = engine / "tests" / "codegen-fixtures" / "group" / "tape" / "tv_trades.csv"
    target.parent.mkdir(parents=True)
    target.write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.setenv("PINEFORGE_PRIVATE_FIXTURES", "0" * 40)
    assert _must_not_skip("group/tape/tv_trades.csv") == target


def test_absent_file_without_overlay_skips_with_the_path(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(pytest.skip.Exception) as info:
        pf.private_fixture("group/tape/tv_trades.csv")
    assert str(info.value) == "private fixtures not present: group/tape/tv_trades.csv"


def test_absent_file_with_overlay_fails_not_skips(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.setenv("PINEFORGE_PRIVATE_FIXTURES", "0" * 40)
    with pytest.raises(AssertionError) as info:
        _must_not_skip("group/tape/tv_trades.csv")
    assert "group/tape/tv_trades.csv" in str(info.value)


def test_no_engine_dir_is_the_absent_world(monkeypatch):
    monkeypatch.delenv("PINEFORGE_ENGINE_INCLUDE", raising=False)
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(pytest.skip.Exception):
        pf.private_fixture("group/tape/tv_trades.csv")


def test_a_path_that_leaves_the_folder_is_refused(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    (engine / "tests" / "codegen-fixtures").mkdir(parents=True)
    target = engine / "tests" / "tests" / "other.py"
    target.parent.mkdir(parents=True)
    target.write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(ValueError):
        _must_not_skip("../tests/other.py")


def test_an_absolute_path_is_refused(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    target = tmp_path / "outside.csv"
    target.write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(ValueError):
        _must_not_skip(str(target))


def test_empty_overlay_is_the_absent_world_and_skips(tmp_path, monkeypatch):
    """An empty PINEFORGE_PRIVATE_FIXTURES counts as no overlay: skip, with the path."""
    engine = _engine(tmp_path)
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.setenv("PINEFORGE_PRIVATE_FIXTURES", "")
    with pytest.raises(pytest.skip.Exception) as info:
        pf.private_fixture("group/tape/tv_trades.csv")
    assert str(info.value) == "private fixtures not present: group/tape/tv_trades.csv"


def test_overlay_declared_without_an_engine_tree_fails_not_skips(monkeypatch):
    """The overlay is declared but there is no paired engine tree to look in: a failure, never a skip."""
    monkeypatch.delenv("PINEFORGE_ENGINE_INCLUDE", raising=False)
    monkeypatch.setenv("PINEFORGE_PRIVATE_FIXTURES", "0" * 40)
    with pytest.raises(AssertionError) as info:
        _must_not_skip("group/tape/tv_trades.csv")
    assert "group/tape/tv_trades.csv" in str(info.value)


def test_a_path_that_climbs_out_after_a_descent_is_refused(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    (engine / "tests" / "codegen-fixtures" / "a").mkdir(parents=True)
    (engine / "tests" / "x.csv").write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(ValueError):
        _must_not_skip("a/../../x.csv")


def test_a_directory_at_the_path_is_not_a_fixture(tmp_path, monkeypatch):
    """Only a file counts: a directory of that name is the absent world (skip), and the overlay case fails."""
    engine = _engine(tmp_path)
    (engine / "tests" / "codegen-fixtures" / "group" / "tape").mkdir(parents=True)
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(pytest.skip.Exception):
        pf.private_fixture("group/tape")
    monkeypatch.setenv("PINEFORGE_PRIVATE_FIXTURES", "0" * 40)
    with pytest.raises(AssertionError):
        _must_not_skip("group/tape")
