"""Session-end pytest gate that fails the run on ANY unallowed skip.

Hosted CI runs ``python -m pytest -q`` on a machine with no engine library,
no Eigen and no clang, so every test that compiles an emitted translation
unit *skips*.  Because they skip, pytest exits 0 and the badge is green while
roughly a third of the suite never runs.  A gate that can pass on missing
input is not a gate.

This module turns the skips into a verdict, and it is **DEFAULT-DENY**: every
skip reason that occurs, whatever its wording, is a row the allowance must
name with its exact count.  There is no allow-list of "known" wordings -- an
earlier version keyed only on compile-environment patterns, so a skip reason
the list did not know passed silently (a spot run produced 2,261 skips under
a wording the list missed).  At session end the gate collects every skipped
test's reason from the terminal reporter and compares the *exact* reason text
and count against the selected profile of the allowance file
``tests/ci_env_skip_allowance.json``:

    {"default": {"<exact reason text>": <count>, ...},
     "engine_built": {"<exact reason text>": <count>, ...}}

The exact reason TEXT -- not a class -- is the key, so a reason that was
reworded is drift too.  The profile is chosen by ``PINEFORGE_SKIP_PROFILE``
(default ``default``); the CI ``engine-built`` job sets ``engine_built``.

``classify`` and ``COMPILE_ENV_PATTERNS`` remain, but only to LABEL the log
lines (and to name, for the ``engine_built`` profile, the reasons that must be
zero there).  They never decide pass or fail: the decision is the table below
alone.

The session FAILS (non-zero exit) when:

  (a) an observed skip reason is not a key of the selected profile;
  (b) a listed reason's count differs from what was observed;
  (c) a listed reason with count > 0 was NOT observed (a stale allowance);
  (d) the session reported no skip summary at all, while the profile lists
      any reason with count > 0 (a plugin that never ran is not a gate);
  (e) the selected profile is missing or empty, or the allowance file is
      missing or unreadable.

The gate is ON by default and may be turned off only with
``PINEFORGE_SKIP_GATE=off`` -- the CI workflow asserts that variable is not
set, because a gate that can be disabled silently would defeat the point.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The allowance file lives beside this module.
ALLOWANCE_PATH = Path(__file__).resolve().parent / "ci_env_skip_allowance.json"

# A skip reason is in the COMPILE-ENVIRONMENT class when it mentions any of
# these knobs (case-insensitive substring match).  This list is NO LONGER a
# decision: the gate is default-deny, so an unknown reason fails wherever it
# comes from.  The class only LABELS the log lines and names the reasons the
# ``engine_built`` profile pins at 0.  It is deliberately exhaustive about
# *wording families*: the engine library archive, the Eigen and generated
# headers, the compiler/AST tooling, the parity corpus and the
# population-sources directory a compile or byte-comparison test needs.
COMPILE_ENV_PATTERNS = (
    # Added 10-11 04:20 after a spot run proved the list was incomplete: on a machine with NO
    # engine variables set at all, the tests skip at the INCLUDE check, not at the library check,
    # and those skips were invisible to this gate (191 of them in one run) - a hole of exactly the
    # class the gate exists to close. The include check comes first, so its wording, not the
    # library's, is what hosted CI actually produces.
    "PINEFORGE_ENGINE_INCLUDE",
    "engine include / compiler unavailable",
    "No built libpineforge.a",
    "set PINEFORGE_ENGINE_LIB",
    "no clang++",
    "PINEFORGE_ENGINE_CORPUS",
    "PINEFORGE_STRING_SETTINGS_SOURCES",
    "population sources require",
)

CLASS_COMPILE_ENV = "COMPILE-ENVIRONMENT"

GATE_ENV = "PINEFORGE_SKIP_GATE"
PROFILE_ENV = "PINEFORGE_SKIP_PROFILE"
DEFAULT_PROFILE = "default"


def gate_disabled(env=None):
    """True when the gate is turned off via ``PINEFORGE_SKIP_GATE=off``.

    This is the single source of truth for the disable switch: the workflow
    assertion imports and calls it, so the CI check and the gate can never
    disagree about the normalization (a shell reimplementation could, e.g.
    on whitespace or a non-ASCII case).
    """
    env = os.environ if env is None else env
    return str(env.get(GATE_ENV, "")).strip().lower() == "off"


def classify(reason):
    """Return the class of a skip reason, or ``None`` when it is not compile-env.

    Matching is a case-insensitive substring test against
    ``COMPILE_ENV_PATTERNS``.  This is used ONLY to label the log lines and to
    name the reasons the ``engine_built`` profile pins at 0 -- it never
    decides whether the gate passes.  The decision keys on the *exact* reason
    text against the selected profile.
    """
    if not reason:
        return None
    text = str(reason).lower()
    for pattern in COMPILE_ENV_PATTERNS:
        if pattern.lower() in text:
            return CLASS_COMPILE_ENV
    return None


def load_allowance(path=ALLOWANCE_PATH):
    """Read the allowance JSON, or return ``None`` when it is missing/unreadable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def profile_entries(allowance, profile_name):
    """Return ``{reason: count}`` for a profile, or ``None`` when it is absent.

    A missing profile, a profile that is not a mapping, and an EMPTY profile
    all return ``None``: an empty table allows nothing and explains nothing,
    so the gate treats it as a fail (condition (e)).  Keys beginning with
    ``_`` are file metadata (a ``_note`` comment field) and are ignored, so the
    allowance can carry human notes without polluting the reason set.
    """
    if not isinstance(allowance, dict):
        return None
    profile = allowance.get(profile_name)
    if not isinstance(profile, dict):
        return None
    entries = {k: v for k, v in profile.items() if not str(k).startswith("_")}
    return entries or None


def skip_reason(report):
    """Extract the reason text from a skipped ``TestReport``.

    pytest records a skip's ``longrepr`` as the 3-tuple
    ``(location, lineno, reason)``; some plugins hand back a plain string.
    Both are normalised to the reason text, with any ``"Skipped: "`` display
    prefix stripped, so the same skip compares equal however it is reported.
    """
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) >= 3:
        reason = longrepr[2]
    elif longrepr is not None:
        reason = longrepr
    else:
        reason = ""
    text = str(reason)
    if text.startswith("Skipped: "):
        text = text[len("Skipped: "):]
    return text


def observed_counts(skipped_reports):
    """Map each skipped test's exact reason text to how many tests reported it."""
    counts: dict[str, int] = {}
    for report in skipped_reports:
        reason = skip_reason(report)
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def check(profile, observed, *, summary_present=True):
    """Compare observed skip counts against a profile; return a problem list.

    DEFAULT-DENY: this does not consult ``classify`` or any pattern list --
    EVERY observed reason must be a key of the profile, with the exact count.
    ``profile`` maps an exact reason text to its expected count; ``observed``
    maps an exact reason text to its count.  An empty list means PASS.
    """
    if not isinstance(profile, dict):
        return ["the selected allowance profile is missing or empty"]

    problems: list[str] = []

    # (d) no skip summary at all: a session with no summary ALWAYS fails,
    # whatever the profile expects -- a plugin that never ran is not a gate.
    if not summary_present:
        problems.append(
            "no skip summary was reported by the terminal reporter"
        )

    # (a) ANY observed reason the profile does not list (default-deny).
    for reason, count in sorted(observed.items()):
        if reason not in profile:
            problems.append(f"unlisted skip reason ({count}x): {reason!r}")

    # (b) / (c) listed reasons that did not match the observation.
    for reason, expected in sorted(profile.items()):
        if not isinstance(expected, int):
            problems.append(f"allowance count for {reason!r} is not an integer: {expected!r}")
            continue
        got = observed.get(reason, 0)
        if expected == got:
            continue
        if expected > 0 and got == 0:
            problems.append(
                f"stale allowance: listed {reason!r} expects {expected} but it was "
                "NOT observed"
            )
        else:
            problems.append(
                f"count mismatch: {reason!r} expected {expected}, observed {got}"
            )

    return problems


def _format_counts(counts):
    """Render a ``{reason: count}`` mapping as one ``n x reason`` line."""
    if not counts:
        return "(none)"
    return "; ".join(f"{n}x {reason!r}" for reason, n in sorted(counts.items()))


def run_gate(terminalreporter, allowance_path=ALLOWANCE_PATH, env=None):
    """Evaluate the gate and return ``(problems, observed)``.

    Returns ``(None, observed)`` when the gate is disabled via
    ``PINEFORGE_SKIP_GATE=off`` (the caller prints nothing).
    """
    env = os.environ if env is None else env
    if gate_disabled(env):
        # A workflow must not be able to turn the gate off: keyed on the GitHub
        # Actions variable, so the refusal stands even if the workflow deletes
        # its own assertion step.  Only reached when the gate is disabled, so a
        # normal Actions run (gate on) is unaffected.
        if env.get("GITHUB_ACTIONS") == "true":
            return ["PINEFORGE_SKIP_GATE=off is not allowed under GitHub Actions"], {}
        return None, {}

    stats = getattr(terminalreporter, "stats", None)
    summary_present = isinstance(stats, dict)
    skipped_reports = stats.get("skipped", []) if summary_present else []
    observed = observed_counts(skipped_reports)

    allowance = load_allowance(allowance_path)
    if allowance is None:
        return (
            [f"the allowance file is missing or unreadable: {allowance_path}"],
            observed,
        )

    profile_name = str(env.get(PROFILE_ENV, "") or DEFAULT_PROFILE)
    profile = profile_entries(allowance, profile_name)
    if profile is None:
        return (
            [f"the allowance file has no usable {profile_name!r} profile (missing or empty)"],
            observed,
        )

    problems = check(profile, observed, summary_present=summary_present)
    return problems, observed


def _emit(terminalreporter, lines):
    """Print ``lines`` through the terminal reporter, falling back to stderr."""
    write_line = getattr(terminalreporter, "write_line", None)
    for line in lines:
        if callable(write_line):
            write_line(line)
        else:
            print(line, file=sys.stderr)


def pytest_sessionfinish(session, exitstatus):
    """Fire the gate at session end and fail the session on any drift.

    ``pytest_sessionfinish`` runs even when the session has failures; after
    the hook returns pytest reads ``session.exitstatus`` back, so setting it
    to 1 here makes the whole process exit non-zero.
    """
    terminalreporter = session.config.pluginmanager.getplugin("terminalreporter")

    # A collection-only run executes no test, so it has no skip summary to
    # judge; failing it on the allowance's expected counts would be noise.
    if getattr(session.config.option, "collectonly", False):
        return

    problems, observed = run_gate(terminalreporter)

    if problems is None:  # gate disabled
        _emit(
            terminalreporter,
            ["skip gate DISABLED (PINEFORGE_SKIP_GATE=off): no skip verdict was produced"],
        )
        return

    compile_env = {
        reason: count
        for reason, count in observed.items()
        if classify(reason) == CLASS_COMPILE_ENV
    }

    if problems:
        _emit(terminalreporter, [""])
        _emit(
            terminalreporter,
            ["skip gate FAILED (default-deny: every observed skip reason must "
             "be an allowance key with its exact count):"],
        )
        for problem in problems:
            _emit(terminalreporter, [f"  - {problem}"])
        _emit(
            terminalreporter,
            [f"  compile-environment skips: {_format_counts(compile_env)}"],
        )
        _emit(
            terminalreporter,
            [f"  observed skips: {_format_counts(observed)}"],
        )
        session.exitstatus = 1
        return

    _emit(
        terminalreporter,
        [
            "skip gate OK (default-deny): "
            f"observed={len(observed)} reason(s), "
            f"compile-environment={len(compile_env)}, "
            f"observed skips: {_format_counts(observed)}"
        ],
    )
