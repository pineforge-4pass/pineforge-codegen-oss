"""Batch timeframe-token transport for the native admission's canonicalizer.

``python -m pineforge_codegen.timeframe_token_cli`` reads one request on stdin
and writes one result on stdout::

    request: {"version": 1, "values": [<raw JSON value>, ...]}
    result:  {"version": 1, "tokens": [<canonical token or null>, ...]}

The result keeps the request's order and cardinality and ends with one LF.
Each element goes once, in order, to
``request_feed_inventory.canonical_wire_timeframe``, which decides every token:
a value that is no string, or no wire token, gives ``null``, and an empty batch
gives an empty list.

Exit status: 0 for a result (``null`` elements included). 2 for a malformed
request: not UTF-8, not strict JSON, a repeated key, a key other than
``version`` and ``values``, a ``version`` that is not the integer 1, or a
``values`` that is not a list. 1 for any other failure. Both failures write
nothing to stdout and one generic line to stderr, which never echoes the
request.

The transport reads no source, strategy, feed or artifact, runs no code from
its input, converts no number and caps no input size.
"""

import json
import sys

from .request_feed_inventory import canonical_wire_timeframe

PROTOCOL_VERSION = 1
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_MALFORMED = 2
_REQUEST_KEYS = frozenset({"version", "values"})
_MALFORMED_MESSAGE = "malformed timeframe token request"
_INTERNAL_MESSAGE = "internal failure in the timeframe token transport"


class _Malformed(Exception):
    """The request is malformed (exit 2)."""


class _JsonNumber:
    """A JSON number, kept as its literal: nothing converts it, so no digit
    limit applies either. It is no string, hence no token; the version must be
    the integer literal ``1``."""

    __slots__ = ("literal", "is_integer")

    def __init__(self, literal: str, is_integer: bool) -> None:
        self.literal = literal
        self.is_integer = is_integer


def _object(pairs: list) -> dict:
    """A JSON object's members; a repeated key is refused (strict JSON)."""
    members = {}
    for key, value in pairs:
        if key in members:
            raise ValueError("duplicate key")
        members[key] = value
    return members


def _refuse_constant(name: str):
    """NaN, Infinity and -Infinity are not JSON."""
    raise ValueError("not strict JSON")


def _decode(raw: bytes):
    """The request's JSON value; bytes that are no UTF-8 or no strict JSON are
    malformed."""
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_int=lambda literal: _JsonNumber(literal, True),
            parse_float=lambda literal: _JsonNumber(literal, False),
            parse_constant=_refuse_constant,
        )
    except (ValueError, RecursionError):
        # UnicodeDecodeError is a ValueError. A document nested past the
        # parser's recursion limit is JSON the parser cannot read.
        raise _Malformed from None


def _values(request) -> list:
    """The batch's values, once the envelope is exact."""
    if type(request) is not dict or set(request) != _REQUEST_KEYS:
        raise _Malformed
    version = request["version"]
    if not (isinstance(version, _JsonNumber) and version.is_integer
            and version.literal == "1"):
        raise _Malformed
    if type(request["values"]) is not list:
        raise _Malformed
    return request["values"]


def _render(raw: bytes) -> bytes:
    """The whole result, as the bytes to write at once."""
    values = _values(_decode(raw))
    # One direct call per value, in order: nothing is deduplicated or reused.
    tokens = [canonical_wire_timeframe(value) for value in values]
    text = json.dumps({"version": PROTOCOL_VERSION, "tokens": tokens},
                      separators=(",", ":"))
    return (text + "\n").encode("ascii")


def _refuse(code: int, message: str) -> int:
    sys.stderr.write(message + "\n")
    return code


def main() -> int:
    """Run one request and return the exit status (module docstring)."""
    try:
        output = _render(sys.stdin.buffer.read())
    except _Malformed:
        return _refuse(EXIT_MALFORMED, _MALFORMED_MESSAGE)
    except Exception:  # noqa: BLE001 -- every other failure is internal
        return _refuse(EXIT_INTERNAL, _INTERNAL_MESSAGE)
    try:
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    except OSError:
        return _refuse(EXIT_INTERNAL, _INTERNAL_MESSAGE)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
