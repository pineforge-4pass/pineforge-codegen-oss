"""Pins the suite shard selector in tests/conftest.py and its real pytest hook.

PF_TEST_SHARD=I/N keeps the tests whose part is I and deselects the others. A
test's part is the SHA-256 of its node id (UTF-8), read as one big-endian
integer, modulo N, plus one. Unset or empty, the variable changes nothing.

The selector is imported inside each test, so a missing selector fails each
test here on its own and not the whole file at collection.
"""
import hashlib
import importlib
import itertools
import os
from pathlib import Path
import random
import re
import subprocess
import sys

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")
PARAMETERS = ("a", "b", "c")
PLAIN = ("-p", "no:xdist")


def load_selector():
    return importlib.import_module("tests.conftest")


def synthetic_node_ids(count):
    """Distinct node ids from a formula, with no randomness."""
    return [
        "tests/test_area_" + str(number % 83) + ".py::Test" + str(number % 7)
        + "::test_case_" + str(number) + "[" + str(number * 37 % 101) + "]"
        for number in range(count)
    ]


def generated_ids():
    """The 40 node ids of the generated module: 10 plain tests and 10 with 3 parameters."""
    ids = ["test_generated.py::test_plain_" + str(number) for number in range(10)]
    for number in range(10):
        ids += ["test_generated.py::test_param_" + str(number) + "[" + value + "]"
                for value in PARAMETERS]
    return ids


def generated_module():
    lines = ["import pytest", ""]
    for number in range(10):
        lines += ["def test_plain_" + str(number) + "():", "    assert True", ""]
    for number in range(10):
        lines += ["@pytest.mark.parametrize('value', " + repr(list(PARAMETERS)) + ")",
                  "def test_param_" + str(number) + "(value):",
                  "    assert value in 'abc'", ""]
    return "\n".join(lines) + "\n"


def write_suite(directory, name, source):
    """A directory whose own conftest is a copy of the real selector."""
    (directory / "conftest.py").write_bytes(CONFTEST.read_bytes())
    (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (directory / name).write_text(source, encoding="utf-8")


def run_pytest(directory, shard, *arguments):
    """Run pytest in a directory with PF_TEST_SHARD set to shard, or removed for None.

    The environment is a copy of the current one, so this gives the same result
    when the outer run is itself sharded: PF_TEST_SHARD is set or removed
    explicitly, and no PYTEST_* variable of the outer run (options, plugins,
    xdist worker names) reaches the inner run.
    """
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("PYTEST_") and name != "PF_TEST_SHARD"}
    if shard is not None:
        environment["PF_TEST_SHARD"] = shard
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *arguments],
        cwd=directory, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=300,
    )


def collected(directory, shard):
    result = run_pytest(directory, shard, *PLAIN, "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines()
            if line.startswith("test_generated.py::")]


def count_of(output, word):
    match = re.search(r"(\d+) " + word + r"\b", output)
    assert match, output
    return int(match[1])


def test_parse_shard_reads_i_of_n_and_refuses_every_other_value():
    selector = load_selector()
    assert selector.parse_shard(None) is None
    assert selector.parse_shard("") is None
    for value, pair in [("1/4", (1, 4)), ("4/4", (4, 4)), ("1/1", (1, 1)), ("64/64", (64, 64))]:
        assert selector.parse_shard(value) == pair
    for value in [
        "0/4", "5/4", "1/0", "1/65", "a/4", "1/4 ", " 1/4", "1/4/2", "-1/4", "1", "/",
        "01/4", "1/04",
        # Only ASCII digits count (0xFF11 is a fullwidth digit one), and a trailing
        # newline is not part of the number.
        "1/4\n", chr(0xFF11) + "/4",
    ]:
        with pytest.raises(pytest.UsageError) as error:
            selector.parse_shard(value)
        assert "PF_TEST_SHARD" in str(error.value) and repr(value) in str(error.value)


def test_parts_are_disjoint_balanced_and_cover_every_id():
    selector = load_selector()
    ids = synthetic_node_ids(6000)
    assert len(set(ids)) == len(ids) >= 5000
    shuffled = list(ids)
    random.Random(20240601).shuffle(shuffled)
    assert shuffled != ids
    for parts in range(1, 9):
        kept = []
        for index in range(1, parts + 1):
            chosen = selector.parse_shard(str(index) + "/" + str(parts))
            kept.append({nodeid for nodeid in ids
                         if selector.part_of(nodeid, chosen[1]) == chosen[0]})
        for first, second in itertools.combinations(kept, 2):
            assert first.isdisjoint(second)
        assert set().union(*kept) == set(ids)
        assert all(kept)
        mean = len(ids) / parts
        assert all(abs(len(part) - mean) <= 0.2 * mean for part in kept)
        # The part of an id does not depend on the order in which ids arrive.
        assert {nodeid: selector.part_of(nodeid, parts) for nodeid in shuffled} == {
            nodeid: selector.part_of(nodeid, parts) for nodeid in ids
        }


def test_part_is_the_sha256_of_the_utf8_id_modulo_n_plus_one():
    selector = load_selector()
    ids = synthetic_node_ids(60) + [
        "tests/test_a.py::test_one",
        "tests/test_b.py::TestGroup::test_two[param-3]",
        # u and i with a diaeresis and three Japanese ideographs: encoded as UTF-8.
        "tests/test_" + chr(0xFC) + ".py::test_na" + chr(0xEF) + "ve["
        + chr(0x65E5) + chr(0x672C) + chr(0x8A9E) + "]",
    ]
    for nodeid in ids:
        digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()
        for parts in (1, 2, 3, 4, 5, 7, 8, 63, 64):
            assert selector.part_of(nodeid, parts) == int(digest, 16) % parts + 1
    # Two literal pins for N = 4. Each expected part was worked out with the shell,
    # not with the selector: the digest is `printf %s '<id>' | sha256sum`, and the
    # part is its last hexadecimal digit modulo four, plus one (sixteen is a
    # multiple of four, so the whole digest modulo four is that digit modulo four).
    # sha256 = 808b0db0fa67f05edf895cab7e7f2d9e03879c1e7543a17196cc140b0557f319 ends in 9;
    # 9 % 4 = 1; the part is 1 + 1 = 2. (Read little-endian it would be 1.)
    assert selector.part_of("tests/test_a.py::test_one", 4) == 2
    # sha256 = 45131bafcc6ccebeaf55c15f73cf834c64899bcf7381c69ce9643f49d63cd8ca ends in a (ten);
    # 10 % 4 = 2; the part is 2 + 1 = 3. (Read little-endian it would be 2.)
    assert selector.part_of("tests/test_alpha.py::test_beta", 4) == 3


def test_hook_collects_all_without_the_variable_and_one_part_with_it(tmp_path):
    selector = load_selector()
    write_suite(tmp_path, "test_generated.py", generated_module())
    everything = generated_ids()
    assert len(everything) == len(set(everything)) == 40
    unsharded = collected(tmp_path, None)
    assert sorted(unsharded) == sorted(everything)
    assert collected(tmp_path, "") == unsharded
    parts = []
    for index in range(1, 5):
        part = collected(tmp_path, str(index) + "/4")
        # The real hook keeps exactly what the rule says, in the original order.
        assert part == [nodeid for nodeid in unsharded if selector.part_of(nodeid, 4) == index]
        parts.append(part)
    for first, second in itertools.combinations(parts, 2):
        assert set(first).isdisjoint(second)
    assert sorted(itertools.chain.from_iterable(parts)) == sorted(everything)


def test_hook_runs_each_part_and_only_its_tests(tmp_path):
    selector = load_selector()
    write_suite(tmp_path, "test_generated.py", generated_module())
    everything = generated_ids()
    total = 0
    for index in range(1, 5):
        expected = len([nodeid for nodeid in everything
                        if selector.part_of(nodeid, 4) == index])
        result = run_pytest(tmp_path, str(index) + "/4", *PLAIN)
        assert result.returncode == 0, result.stdout + result.stderr
        assert count_of(result.stdout, "passed") == expected
        assert count_of(result.stdout, "deselected") == len(everything) - expected
        total += expected
    assert total == len(everything)


def test_hook_refuses_a_malformed_value_before_collection(tmp_path):
    write_suite(tmp_path, "test_generated.py", generated_module())
    result = run_pytest(tmp_path, "5/4", *PLAIN)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "PF_TEST_SHARD" in result.stderr and "5/4" in result.stderr
    assert "passed" not in result.stdout and "collected" not in result.stdout


def test_hook_reports_an_empty_part_as_no_tests_collected(tmp_path):
    selector = load_selector()
    write_suite(tmp_path, "test_single.py", "def test_only():\n    assert True\n")
    holder = selector.part_of("test_single.py::test_only", 2)
    empty = 3 - holder
    held = run_pytest(tmp_path, str(holder) + "/2", *PLAIN)
    assert held.returncode == 0 and count_of(held.stdout, "passed") == 1
    result = run_pytest(tmp_path, str(empty) + "/2", *PLAIN)
    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED


def test_hook_selects_the_same_part_under_xdist(tmp_path):
    selector = load_selector()
    write_suite(tmp_path, "test_generated.py", generated_module())
    expected = len([nodeid for nodeid in generated_ids() if selector.part_of(nodeid, 4) == 2])
    plain = run_pytest(tmp_path, "2/4", *PLAIN)
    parallel = run_pytest(tmp_path, "2/4", "-n", "2")
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert count_of(parallel.stdout, "passed") == count_of(plain.stdout, "passed") == expected
