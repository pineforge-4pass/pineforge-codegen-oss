"""Optional split of the test suite into parts, so that CI can run one part per job.

PF_TEST_SHARD unset or empty: nothing happens and the whole suite is collected.

PF_TEST_SHARD=I/N, where I and N are two decimal numbers without sign, spaces or
leading zeros and 1 <= I <= N <= 64: only the tests of part I are kept, in their
original order, and the others are deselected. The part of a test is the SHA-256
of its node id encoded as UTF-8, with the whole digest read as one big-endian
integer, modulo N, plus 1. Every test is therefore in exactly one of the N parts,
whatever the collection order or the number of xdist workers.

Any other value is a pytest usage error, raised before collection.

The variable is consumed when pytest starts: it is read once and then removed from
the environment of the process, so no child process that a test starts (a nested
pytest run, for example) can see it, and such a run collects the whole suite. Under
pytest-xdist the controller passes the value on to each worker through the worker
input that xdist provides, and a worker never reads the environment variable.
"""
import hashlib
import os
import re

import pytest

SHARD_VARIABLE = "PF_TEST_SHARD"
MAX_PARTS = 64
_SHARD = re.compile(r"([1-9][0-9]?)/([1-9][0-9]?)")
# The value of this run, (I, N) or None, kept on the pytest config.
SHARD_STASH = pytest.StashKey()
# Where an xdist controller puts the value for its workers: the text I/N, or the
# empty text when there is no value.
WORKER_INPUT_KEY = "pf_test_shard"


def parse_shard(value):
    """Return (I, N) for the value I/N, None for an unset or empty value, else refuse."""
    if not value:
        return None
    match = _SHARD.fullmatch(value)
    if match is not None:
        index, parts = int(match[1]), int(match[2])
        if 1 <= index <= parts <= MAX_PARTS:
            return index, parts
    raise pytest.UsageError(
        f"{SHARD_VARIABLE} must be I/N, two decimal numbers without sign, spaces or "
        f"leading zeros, with 1 <= I <= N <= {MAX_PARTS}; got {value!r}"
    )


def part_of(nodeid, parts):
    """The part, from 1 to parts, that holds the test with this node id."""
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % parts + 1


def pytest_configure(config):
    if hasattr(config, "workerinput"):
        # An xdist worker takes the value from its controller, never from the
        # environment.
        value = config.workerinput[WORKER_INPUT_KEY]
    else:
        # Read the variable once and remove it, so that no child process of this
        # run, an xdist worker included, inherits it. A malformed value is refused
        # here, before collection.
        value = os.environ.pop(SHARD_VARIABLE, None)
    config.stash[SHARD_STASH] = parse_shard(value)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    """Hook of pytest-xdist, optional so that this file loads without xdist.

    Hands a worker the value that its controller took from the environment.
    """
    shard = node.config.stash[SHARD_STASH]
    node.workerinput[WORKER_INPUT_KEY] = "" if shard is None else "%d/%d" % shard


def pytest_collection_modifyitems(config, items):
    shard = config.stash[SHARD_STASH]
    if shard is None:
        return
    index, parts = shard
    kept, dropped = [], []
    for item in items:
        (kept if part_of(item.nodeid, parts) == index else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
