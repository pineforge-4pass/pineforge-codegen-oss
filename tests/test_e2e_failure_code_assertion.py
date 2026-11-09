"""A stopped run is asserted by the engine's failure code, not by a substring.

The end-to-end error texts embed the job's work directory. Three tests used
``"-11" not in error`` to say "the run stopped, it did not crash with signal
11", which fails when a job id contains ``-11`` and passes for a crash that
prints no such text. ``assert_run_stopped_with`` reads the failure code the
engine runner prints on stderr instead.
"""

import pytest

from tests._e2e import Build, Outcome, assert_run_stopped_with

MESSAGE = "Cannot call matrix methods when id of matrix is na."


def _outcomes(error):
    return {"case": Outcome(build=Build(source=""), error=error)}


def _stopped(workdir, *codes):
    lines = "".join(f"run failure code: {code}\n" for code in codes)
    return f"run_strategy failed in {workdir}:\n\nError: {MESSAGE}\n{lines}"


def test_a_stopped_run_passes_whatever_its_work_directory_is():
    error = _stopped("/tmp/rj-20261104t114002-11a387/matrix_bound", "pine_na_reference")
    # The substring check this assertion replaces fails on such a directory.
    assert "-11" in error
    assert_run_stopped_with(_outcomes(error), "case", "pine_na_reference", MESSAGE)


def test_a_crash_prints_no_failure_code_and_fails():
    crash = ("run_strategy failed in /tmp/rj-1/matrix_bound:\n\n"
             "Segmentation fault (core dumped)\n")
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(crash), "case", "pine_na_reference", MESSAGE)


def test_a_crash_after_the_message_still_fails():
    # The message reached stderr, then the process died: no code line.
    error = f"run_strategy failed in /tmp/rj-2/matrix_bound:\n\nError: {MESSAGE}\nAborted (core dumped)\n"
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(error), "case", "pine_na_reference", MESSAGE)


def test_another_failure_code_fails():
    error = _stopped("/tmp/rj-3/matrix_bound", "engine_unclassified_error")
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(error), "case", "pine_na_reference", MESSAGE)


def test_two_failure_codes_fail():
    error = _stopped("/tmp/rj-4/matrix_bound", "pine_na_reference", "engine_unclassified_error")
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(error), "case", "pine_na_reference", MESSAGE)


def test_another_message_fails():
    error = _stopped("/tmp/rj-5/matrix_bound", "pine_na_reference")
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(error), "case", "pine_na_reference", "another message")


def test_a_run_that_did_not_stop_fails():
    with pytest.raises(AssertionError):
        assert_run_stopped_with(_outcomes(None), "case", "pine_na_reference", MESSAGE)
