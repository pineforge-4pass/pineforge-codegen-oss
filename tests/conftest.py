"""pytest configuration for the pineforge-codegen test suite.

Loads the compile-environment skip gate (:mod:`tests.skip_gate`), which fails
the session at the end when a skipped test's exact reason is not listed in
``tests/ci_env_skip_allowance.json`` (or its count differs).  Without this a
machine with no engine library, Eigen or clang skips every test that compiles
an emitted translation unit, pytest exits 0, and the suite looks green while a
third of it never ran.

The gate is ON by default; ``PINEFORGE_SKIP_GATE=off`` disables it, and the CI
workflow asserts that variable is not set the gate cannot be turned off
silently.
"""

from __future__ import annotations

from tests import skip_gate

# Re-exported so pytest registers the session-end hook from this conftest.
from tests.skip_gate import pytest_sessionfinish  # noqa: F401

__all__ = ["pytest_sessionfinish", "skip_gate"]
