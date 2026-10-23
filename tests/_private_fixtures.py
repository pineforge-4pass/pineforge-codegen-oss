"""Find a fixture that lives in the private fixtures repository.

Fixtures that cannot be public are overlaid by the lab onto the paired ENGINE tree, under ``tests/codegen-fixtures/``, with
``PINEFORGE_PRIVATE_FIXTURES=<commit>`` exported (``lab remote submit --private-fixtures``). A test names such a file
relative to that folder and calls ``private_fixture``:

* the file is there: its path comes back;
* the file is not there and no overlay was asked for: the test skips with ``private fixtures not present: <path>``;
* the file is not there although the overlay is present: ``AssertionError``, never a skip, so a broken overlay cannot turn a
  private test into a silent pass.

The engine tree is found the way the rest of the suite finds it: ``PINEFORGE_ENGINE_INCLUDE`` is ``<engine>/include``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FOLDER = Path("tests") / "codegen-fixtures"


def _engine_dir() -> Path | None:
    raw = os.environ.get("PINEFORGE_ENGINE_INCLUDE", "")
    return Path(raw).resolve().parent if raw else None


def private_fixture(rel: str) -> Path:
    """The path of ``tests/codegen-fixtures/<rel>`` in the paired engine tree, or a skip (see the module text)."""
    target = Path(rel)
    if target.is_absolute() or ".." in target.parts:
        raise ValueError(f"private fixture path must stay inside the folder: {rel}")
    engine = _engine_dir()
    path = engine / FOLDER / target if engine is not None else None
    if path is not None and path.is_file():
        return path
    if os.environ.get("PINEFORGE_PRIVATE_FIXTURES"):
        raise AssertionError(
            f"the private fixtures are overlaid (PINEFORGE_PRIVATE_FIXTURES is set) but {rel} is missing from "
            f"{FOLDER} in the engine tree")
    pytest.skip(f"private fixtures not present: {rel}")
