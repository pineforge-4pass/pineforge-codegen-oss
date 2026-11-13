"""Optional split of the test suite into parts, so that CI can run one part per job.

PF_TEST_SHARD unset or empty: nothing happens and the whole suite is collected.

PF_TEST_SHARD=I/N, where I and N are two decimal numbers without sign, spaces or
leading zeros and 1 <= I <= N <= 64: only the tests of part I are kept, in their
original order, and the others are deselected. The part of a test is the SHA-256
of its node id encoded as UTF-8, with the whole digest read as one big-endian
integer, modulo N, plus 1. Every test is therefore in exactly one of the N parts,
whatever the collection order or the number of xdist workers.

Any other value is a pytest usage error, raised before collection.
"""
import hashlib
import os
import re

import pytest

SHARD_VARIABLE = "PF_TEST_SHARD"
MAX_PARTS = 64
_SHARD = re.compile(r"([1-9][0-9]?)/([1-9][0-9]?)")


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
    parse_shard(os.environ.get(SHARD_VARIABLE))


def pytest_collection_modifyitems(config, items):
    shard = parse_shard(os.environ.get(SHARD_VARIABLE))
    if shard is None:
        return
    index, parts = shard
    kept, dropped = [], []
    for item in items:
        (kept if part_of(item.nodeid, parts) == index else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
