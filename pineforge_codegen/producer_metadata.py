"""Plain JSON metadata bound to the C++ returned by one translation.

``INPUT_KIND_SCHEMA`` is version 1 of the input-kind metadata: an input
manifest entry made by ``input.symbol()`` carries ``"kind": "symbol"``;
the key is absent from every other entry.
"""

import hashlib as _hashlib
import json as _json
import math as _math
import re as _re

from .errors import Diagnostic as _Diagnostic
from .request_feed_inventory import SCHEMA as _INVENTORY_SCHEMA


__all__ = (
    "PRODUCER_METADATA_KEYS", "INPUT_KIND_SCHEMA", "ProducerMetadataError",
    "build_producer_metadata", "validate_producer_metadata", "producer_metadata_json",
)

PRODUCER_METADATA_KEYS = (
    "inputs", "input_kind_schema", "strategy_params", "request_feed_inventory",
    "diagnostics", "filename", "cppSha256",
)
INPUT_KIND_SCHEMA = 1

_INVENTORY_KEYS = {
    "schema", "source_sha256", "artifact_sha256", "primary_chart_timeframe", "entries",
}
_DIAGNOSTIC_KEYS = {
    "severity", "phase", "message", "filename", "line", "column", "end_column", "hint",
}
_HEX64 = _re.compile(r"[0-9a-f]{64}")


class ProducerMetadataError(ValueError):
    """A value cannot form a valid producer metadata document."""


def _plain(value, path, *, convert, active=None):
    """Check plain JSON values, copying containers and converting tuples only
    while building. Every refusal identifies its place in the document."""
    kind = type(value)
    if value is None or kind in (bool, int):
        return value
    if kind is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ProducerMetadataError(f"{path}: string is not UTF-8 encodable") from error
        return value
    if kind is float:
        if not _math.isfinite(value):
            raise ProducerMetadataError(f"{path}: float must be finite")
        return value
    if kind not in (dict, list) and not (convert and kind is tuple):
        raise ProducerMetadataError(f"{path}: {kind.__name__} is not a plain JSON type")

    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ProducerMetadataError(f"{path}: circular container is not a JSON value")
    active.add(identity)
    try:
        if kind is dict:
            copied = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ProducerMetadataError(f"{path}: object key must be a string")
                _plain(key, f"{path}.<key>", convert=convert, active=active)
                item_path = f"{path}.{key}" if path else key
                checked = _plain(item, item_path, convert=convert, active=active)
                if convert:
                    copied[key] = checked
        else:
            copied = []
            for index, item in enumerate(value):
                checked = _plain(item, f"{path}[{index}]", convert=convert, active=active)
                if convert:
                    copied.append(checked)
        return copied if convert else value
    finally:
        active.remove(identity)


def _object_keys(value, keys, path):
    if type(value) is not dict or set(value) != set(keys):
        raise ProducerMetadataError(
            f"{path}: must be an object with exactly the keys {', '.join(sorted(keys))}"
        )


def _objects(value, path):
    if type(value) is not list:
        raise ProducerMetadataError(f"{path}: must be a list of objects")
    for index, item in enumerate(value):
        if type(item) is not dict:
            raise ProducerMetadataError(f"{path}[{index}]: must be an object")


def _hash(value, path):
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ProducerMetadataError(f"{path}: must be 64 lowercase hexadecimal digits")


def validate_producer_metadata(metadata) -> None:
    """Validate the exact document shape and plain JSON types without conversion.

    Tuples, non-finite numbers, enums, non-string object keys and strings
    that cannot be encoded as UTF-8 raise ``ProducerMetadataError``.
    """
    if type(metadata) is not dict:
        raise ProducerMetadataError("metadata: must be an object")
    _plain(metadata, "", convert=False)
    _object_keys(metadata, PRODUCER_METADATA_KEYS, "metadata")
    _objects(metadata["inputs"], "inputs")
    schema = metadata["input_kind_schema"]
    if type(schema) is not int or schema != INPUT_KIND_SCHEMA:
        raise ProducerMetadataError("input_kind_schema: must be the integer 1")
    if type(metadata["strategy_params"]) is not dict:
        raise ProducerMetadataError("strategy_params: must be an object")

    inventory = metadata["request_feed_inventory"]
    _object_keys(inventory, _INVENTORY_KEYS, "request_feed_inventory")
    if inventory["schema"] != _INVENTORY_SCHEMA:
        raise ProducerMetadataError("request_feed_inventory.schema: unsupported schema")
    _hash(inventory["source_sha256"], "request_feed_inventory.source_sha256")
    if inventory["artifact_sha256"] is not None:
        raise ProducerMetadataError("request_feed_inventory.artifact_sha256: must be null")
    if inventory["primary_chart_timeframe"] is not None and type(
        inventory["primary_chart_timeframe"]
    ) is not str:
        raise ProducerMetadataError(
            "request_feed_inventory.primary_chart_timeframe: must be a string or null"
        )
    _objects(inventory["entries"], "request_feed_inventory.entries")

    _objects(metadata["diagnostics"], "diagnostics")
    for index, item in enumerate(metadata["diagnostics"]):
        path = f"diagnostics[{index}]"
        _object_keys(item, _DIAGNOSTIC_KEYS, path)
        if item["severity"] not in ("warning", "note"):
            raise ProducerMetadataError(f"{path}.severity: must be warning or note")
        for key in ("phase", "message"):
            if type(item[key]) is not str:
                raise ProducerMetadataError(f"{path}.{key}: must be a string")
        for key in ("filename", "hint"):
            if item[key] is not None and type(item[key]) is not str:
                raise ProducerMetadataError(f"{path}.{key}: must be a string or null")
        for key in ("line", "column", "end_column"):
            if item[key] is not None and type(item[key]) is not int:
                raise ProducerMetadataError(f"{path}.{key}: must be an integer or null")

    if type(metadata["filename"]) is not str:
        raise ProducerMetadataError("filename: must be a string")
    _hash(metadata["cppSha256"], "cppSha256")
    if metadata["cppSha256"] != inventory["source_sha256"]:
        raise ProducerMetadataError(
            "cppSha256: does not match request_feed_inventory.source_sha256"
        )


def build_producer_metadata(result, *, filename) -> dict:
    """Copy and validate metadata from ``transpile_with_request_inventory``.

    Diagnostic objects become flat dictionaries, tuples become lists, and
    the C++ hash must match the inventory made by the same generation.
    """
    if type(result) is not dict:
        raise ProducerMetadataError("result: must be an object")
    for key in ("cpp", "inputs", "strategyParams", "request_feed_inventory", "diagnostics"):
        if key not in result:
            raise ProducerMetadataError(f"result.{key}: missing value")
    cpp = result["cpp"]
    if type(cpp) is not str:
        raise ProducerMetadataError("cpp: must be the generated C++ source string")
    try:
        digest = _hashlib.sha256(cpp.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as error:
        raise ProducerMetadataError("cpp: string is not UTF-8 encodable") from error

    if type(result["diagnostics"]) not in (list, tuple):
        raise ProducerMetadataError("diagnostics: must be a sequence of Diagnostic objects")
    diagnostics = []
    for index, diagnostic in enumerate(result["diagnostics"]):
        path = f"diagnostics[{index}]"
        if not isinstance(diagnostic, _Diagnostic):
            raise ProducerMetadataError(f"{path}: must be a Diagnostic")
        try:
            location = diagnostic.location
            diagnostics.append({
                "severity": diagnostic.level.value,
                "phase": diagnostic.phase.value,
                "message": diagnostic.message,
                "filename": location.file if location is not None else None,
                "line": location.line if location is not None else None,
                "column": location.col if location is not None else None,
                "end_column": location.end_col if location is not None else None,
                "hint": diagnostic.hint,
            })
        except AttributeError as error:
            raise ProducerMetadataError(f"{path}: invalid Diagnostic attributes") from error
    metadata = _plain({
        "inputs": result["inputs"],
        "input_kind_schema": INPUT_KIND_SCHEMA,
        "strategy_params": result["strategyParams"],
        "request_feed_inventory": result["request_feed_inventory"],
        "diagnostics": diagnostics,
        "filename": filename,
        "cppSha256": digest,
    }, "", convert=True)
    validate_producer_metadata(metadata)
    return metadata


def producer_metadata_json(metadata) -> bytes:
    """Validate and return canonical UTF-8 JSON bytes without a trailing newline.

    The bytes are the document. A reader must decode UTF-8 strictly, parse
    with NaN, Infinity and duplicate keys refused, and must neither require
    nor forbid a trailing newline.
    """
    validate_producer_metadata(metadata)
    return _json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
