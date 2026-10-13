"""``tests._private_fixtures.private_fixture``: how a codegen test finds a fixture that lives in the private repository.

The private fixtures are overlaid onto the paired ENGINE tree (``<engine>/tests/codegen-fixtures/...``), never onto
this repository. A test names the file relative to that folder. Three worlds:

* the file is there: the helper returns its path;
* the file is not there and no overlay was asked for: the test SKIPS with ``private fixtures not present: <path>``;
* the file is not there but the job says the overlay is present (``PINEFORGE_PRIVATE_FIXTURES`` is set): that is
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


def test_present_file_is_returned(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    target = engine / "tests" / "codegen-fixtures" / "group" / "tape" / "tv_trades.csv"
    target.parent.mkdir(parents=True)
    target.write_text("x\n")
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    assert pf.private_fixture("group/tape/tv_trades.csv") == target


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
        pf.private_fixture("group/tape/tv_trades.csv")
    assert "group/tape/tv_trades.csv" in str(info.value)


def test_no_engine_dir_is_the_absent_world(monkeypatch):
    monkeypatch.delenv("PINEFORGE_ENGINE_INCLUDE", raising=False)
    monkeypatch.delenv("PINEFORGE_PRIVATE_FIXTURES", raising=False)
    with pytest.raises(pytest.skip.Exception):
        pf.private_fixture("group/tape/tv_trades.csv")


def test_a_path_that_leaves_the_folder_is_refused(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setenv("PINEFORGE_ENGINE_INCLUDE", str(engine / "include"))
    with pytest.raises(ValueError):
        pf.private_fixture("../tests/other.py")
