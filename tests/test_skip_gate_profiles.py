"""The shipped ``engine_built*`` profiles must satisfy the gate's own rules.

Engine-free, environment-free and self-contained: this test loads the allowance
JSON beside :mod:`tests.skip_gate` and, for every profile whose name starts
with ``engine_built``, runs ``skip_gate.check`` with the profile's OWN declared
counts as the observation (its metadata keys stripped).  The profile then has
to come back clean -- so a rule that contradicts the shipped data, for example
an ``engine_built`` rule demanding a 0 the build cannot produce, fails HERE, in
the engine-less run, instead of only on the CI leg that actually executes that
profile.

No engine, no ``PINEFORGE_*`` variable, no fixture and no skip: it reads the
shipped file and calls the pure ``check`` function.  If a profile is
inconsistent, the assertion names it (the fix is either the profile's counts or
the rule, never this test).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import skip_gate

_METADATA_PREFIX = "_"


def _load_allowance():
    """Return the shipped allowance JSON, failing loudly if it is unreadable."""
    path = Path(skip_gate.ALLOWANCE_PATH)
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(profile):
    """A profile's skip rows: everything but its ``_``-prefixed metadata.

    ``_note``, ``_unmeasured`` and any other ``_`` key are human metadata, not
    skip reasons, so they are stripped from both the profile and the
    observation passed to ``check`` -- ``check`` iterates a profile's keys and
    would flag a ``_note``'s string value as a non-integer count.
    """
    return {
        key: value
        for key, value in profile.items()
        if not str(key).startswith(_METADATA_PREFIX)
    }


def test_engine_built_profiles_pass_the_gate_on_their_own_counts():
    allowance = _load_allowance()
    names = [name for name in allowance if str(name).startswith("engine_built")]
    assert names, "no engine_built* profile in the shipped allowance"

    failures = []
    for name in names:
        # A profile with no usable reason rows (missing or empty) is rejected by
        # the gate's real path: ``profile_entries`` returns None and the profile
        # would silently allow nothing.  Fail here instead of passing silently.
        assert skip_gate.profile_entries(allowance, name) is not None, (
            f"{name} has no usable reason rows "
            "(profile_entries returned None)"
        )
        rows = _rows(allowance[name])
        problems = skip_gate.check(
            rows, rows, summary_present=True, profile_name=name
        )
        if problems:
            failures.append((name, problems))

    assert not failures, "\n".join(
        f"{name} is inconsistent with the skip gate's own rules:\n    "
        + "\n    ".join(problems)
        for name, problems in failures
    )
