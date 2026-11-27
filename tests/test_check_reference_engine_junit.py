"""The reference-engine job's JUnit check: scripts/check_reference_engine_junit.py.

The job runs four end-to-end modules with PINEFORGE_REFERENCE_ENGINE set. On a
runner without the private fixtures, exactly the cases of
tests/reference_engine/public_ids.txt must run and pass, and a case may skip
only because private fixtures are not present. Each test writes a JUnit XML
the way pytest writes one, runs the check on it, and reads the exit status and
the FAIL lines that name each problem. The check is run, not imported, so on a
tree without it every test fails here by name rather than the whole module
failing at collection.
"""
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_reference_engine_junit.py"
PUBLIC = ROOT / "tests" / "reference_engine" / "public_ids.txt"
REFERENCE = ROOT / "tests" / "reference_engine" / "needs_reference_ids.txt"
# Two cases that need private fixtures, skipped with the helper's own text.
PRIVATE_SKIPS = (
    ("tests/test_e2e_session_ismarket.py::test_private_tapes_are_the_recorded_exports",
     "private fixtures not present: session_ismarket/example/tape.csv"),
    ("tests/test_e2e_session_ismarket.py::"
     "test_private_six_chart_sets_have_the_recorded_bar_totals",
     "private fixtures not present: session_ismarket/example/chart.csv"),
)
# Public cases outside the reference list: dropping or failing one of them
# breaks only the rule a test targets.
UNREFERENCED = (
    "tests/test_e2e_bar_magnifier_flag.py::test_the_tapes_are_the_recorded_exports",
    "tests/test_e2e_session_ismarket.py::test_tapes_are_the_recorded_exports",
)


def public_ids():
    """The committed public ids, once the check and both of its lists exist."""
    for path in (SCRIPT, PUBLIC, REFERENCE):
        assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    public = PUBLIC.read_text(encoding="utf-8").splitlines()
    reference = REFERENCE.read_text(encoding="utf-8").splitlines()
    assert all(node_id in public and node_id not in reference for node_id in UNREFERENCED)
    return public


def case_element(node_id, child=""):
    """A <testcase> the way pytest writes one for this node id."""
    module, name = node_id.split("::", 1)
    classname = module.removesuffix(".py").replace("/", ".")
    return (f"<testcase classname={quoteattr(classname)} name={quoteattr(name)} "
            f'time="0.010">{child}</testcase>')


def report(passed, *, skips=(), failures=()):
    """A pytest JUnit XML: passed ids, (id, reason) skips and (id, message) failures."""
    cases = [case_element(node_id) for node_id in passed]
    cases += [
        case_element(node_id, f'<skipped type="pytest.skip" message={quoteattr(reason)}>'
                              f"{escape('tests/_private_fixtures.py:43: ' + reason)}</skipped>")
        for node_id, reason in skips
    ]
    cases += [
        case_element(node_id,
                     f"<failure message={quoteattr(message)}>{escape(message)}</failure>")
        for node_id, message in failures
    ]
    return ('<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
            f'<testsuite name="pytest" errors="0" failures="{len(failures)}" '
            f'skipped="{len(skips)}" tests="{len(cases)}" time="1.000" '
            'timestamp="2026-01-01T00:00:00.000000+00:00" hostname="runner">'
            + "".join(cases) + "</testsuite></testsuites>")


def id_list(tmp_path, ids):
    """A public list written the committed way: sorted, one id per line."""
    path = tmp_path / "public_ids.txt"
    path.write_text("".join(node_id + "\n" for node_id in sorted(ids)), encoding="utf-8")
    return path


def check(tmp_path, junit, *, public=PUBLIC):
    """Run the check on this JUnit text with the committed reference list."""
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(junit, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--junit", str(junit_path),
         "--public", str(public), "--reference", str(REFERENCE)],
        capture_output=True, text=True, check=False,
    )


def fail_lines(result):
    return [line for line in result.stdout.splitlines() if line.startswith("FAIL: ")]


def test_a_clean_report_passes(tmp_path):
    public = public_ids()
    result = check(tmp_path, report(public, skips=PRIVATE_SKIPS))
    assert result.returncode == 0, result.stdout + result.stderr
    assert fail_lines(result) == []
    assert result.stdout.splitlines()[-1].startswith("Reference-engine JUnit check: PASS ")


def test_a_public_list_with_one_id_removed_fails(tmp_path):
    public = public_ids()
    removed = UNREFERENCED[0]
    shorter = id_list(tmp_path, [node_id for node_id in public if node_id != removed])
    result = check(tmp_path, report(public, skips=PRIVATE_SKIPS), public=shorter)
    assert result.returncode == 1, result.stdout + result.stderr
    assert fail_lines(result) == [f"FAIL: (c) ran but is not in the public list: {removed}"]


def test_a_public_list_with_an_extra_id_fails(tmp_path):
    public = public_ids()
    extra = "tests/test_e2e_strategy_defaults.py::test_a_case_that_never_ran"
    longer = id_list(tmp_path, [*public, extra])
    result = check(tmp_path, report(public, skips=PRIVATE_SKIPS), public=longer)
    assert result.returncode == 1, result.stdout + result.stderr
    assert fail_lines(result) == [
        f"FAIL: (c) in the public list but absent from the report: {extra}"]


def test_a_skip_for_another_reason_fails(tmp_path):
    public = public_ids()
    other = ("tests/test_e2e_session_ismarket.py::test_a_case_skipped_for_another_reason",
             "the engine corpus is unavailable")
    result = check(tmp_path, report(public, skips=(*PRIVATE_SKIPS, other)))
    assert result.returncode == 1, result.stdout + result.stderr
    assert fail_lines(result) == [
        f"FAIL: (b) skipped for another reason: {other[0]}: {other[1]}"]


def test_a_failure_fails(tmp_path):
    public = public_ids()
    failed, message = UNREFERENCED[1], "AssertionError: the tape differs"
    passed = [node_id for node_id in public if node_id != failed]
    result = check(tmp_path, report(passed, skips=PRIVATE_SKIPS, failures=[(failed, message)]))
    assert result.returncode == 1, result.stdout + result.stderr
    assert fail_lines(result) == [
        "FAIL: (a) the report counts failures=1 errors=0",
        f"FAIL: (a) failure: {failed}: {message}",
    ]
