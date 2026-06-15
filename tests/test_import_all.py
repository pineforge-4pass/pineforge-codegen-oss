"""Floor guard: every pineforge_codegen submodule must import on the running
Python. On the 3.11 CI matrix leg this catches any 3.12+ syntax in a module
that no other test imports directly (a gap a plain pytest run would miss).
"""

from __future__ import annotations

import importlib
import pkgutil

import pineforge_codegen


def test_all_submodules_import() -> None:
    failures: list[str] = []
    for mod in pkgutil.walk_packages(
        pineforge_codegen.__path__, "pineforge_codegen."
    ):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 — report every failure, not the first
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")
    assert not failures, "modules failed to import:\n" + "\n".join(failures)
