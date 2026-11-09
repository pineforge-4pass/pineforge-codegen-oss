"""Producer metadata describes exactly one returned C++ translation."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path

import pytest

import pineforge_codegen
from pineforge_codegen.errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from pineforge_codegen.request_feed_inventory import RequestFeedInventoryError


# Input metadata of five kinds (string, int, symbol, float, bool), strategy()
# parameters, a same-ticker daily request, a feed read by a trade, and the two
# diagnostics this script raises: ta.ema's warmup note and bar_index's window warning.
METADATA = (
    '//@version=6\n'
    'strategy("metadata", overlay = true, initial_capital = 10000, pyramiding = 2)\n'
    'mode = input.string("fast", "Mode", options = ["fast", "slow"])\n'
    'length = input.int(14, "Length", options = [14, 21])\n'
    'other = input.symbol("BINANCE:ETHUSDT", "Other symbol")\n'
    'ratio = input.float(1.5, "Ratio")\n'
    'flag = input.bool(true, "Flag")\n'
    'e = ta.ema(close, length)\n'
    'daily = request.security(syminfo.tickerid, "D", close)\n'
    'feed = request.security(other, "240", close)\n'
    'if mode == "fast" and e > daily and feed > 0 and bar_index > 0\n'
    '    strategy.entry("L", strategy.long)\n'
)
ROOT = Path(__file__).resolve().parents[1]


def test_result_shape_and_key_order():
    result = pineforge_codegen.transpile_with_producer_metadata(METADATA)
    from pineforge_codegen.producer_metadata import PRODUCER_METADATA_KEYS

    keys = [
        "inputs", "input_kind_schema", "strategy_params", "request_feed_inventory",
        "diagnostics", "filename", "cppSha256",
    ]
    assert set(result) == {"cpp", "metadata"}
    assert list(result["metadata"]) == keys
    assert type(PRODUCER_METADATA_KEYS) is tuple
    assert PRODUCER_METADATA_KEYS == tuple(keys)


def test_cpp_identity_and_hash():
    result = pineforge_codegen.transpile_with_producer_metadata(
        METADATA, filename="strategy.pine",
    )
    cpp = pineforge_codegen.transpile(METADATA, filename="strategy.pine")
    digest = hashlib.sha256(cpp.encode("utf-8")).hexdigest()
    assert result["cpp"] == cpp
    assert result["metadata"]["cppSha256"] == digest
    assert result["metadata"]["request_feed_inventory"]["source_sha256"] == digest


def test_values_equal_the_sibling_apis():
    result = pineforge_codegen.transpile_with_producer_metadata(
        METADATA, filename="strategy.pine",
    )
    legacy = pineforge_codegen.transpile_full(METADATA, filename="strategy.pine")
    inventory = pineforge_codegen.transpile_with_request_inventory(
        METADATA, filename="strategy.pine",
    )
    metadata = result["metadata"]
    assert metadata["inputs"] == json.loads(json.dumps(legacy["inputs"]))
    assert metadata["strategy_params"] == json.loads(json.dumps(legacy["strategyParams"]))
    assert metadata["request_feed_inventory"] == inventory["request_feed_inventory"]
    assert metadata["filename"] == "strategy.pine"
    assert [entry["title"] for entry in metadata["inputs"]] == [
        "Mode", "Length", "Other symbol", "Ratio", "Flag",
    ]
    assert metadata["inputs"][2]["kind"] == "symbol"
    assert all(
        "kind" not in entry for index, entry in enumerate(metadata["inputs"]) if index != 2
    )


def test_input_kind_schema_is_tied_to_the_manifest():
    translate = pineforge_codegen.transpile_with_producer_metadata
    from pineforge_codegen.producer_metadata import INPUT_KIND_SCHEMA

    with_symbol = translate(METADATA)["metadata"]
    without_symbol = translate(
        '//@version=6\nstrategy("inputs")\n'
        'mode = input.string("fast", "Mode")\n'
        'length = input.int(14, "Length")\nplot(length)\n'
    )["metadata"]
    assert type(INPUT_KIND_SCHEMA) is int and INPUT_KIND_SCHEMA == 1
    for metadata in (with_symbol, without_symbol):
        value = metadata["input_kind_schema"]
        assert type(value) is int and value == 1
    assert with_symbol["inputs"][2]["kind"] == "symbol"
    assert without_symbol["inputs"]
    assert all("kind" not in entry for entry in without_symbol["inputs"])


def test_diagnostics_are_flat_and_preserve_attributes_and_order():
    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    diagnostics = pineforge_codegen.transpile_full(METADATA)["diagnostics"]
    assert {"warning", "note"} <= {item["severity"] for item in metadata["diagnostics"]}
    expected = []
    for diagnostic in diagnostics:
        loc = diagnostic.location
        expected.append({
            "severity": diagnostic.level.value,
            "phase": diagnostic.phase.value,
            "message": diagnostic.message,
            "filename": loc.file if loc else None,
            "line": loc.line if loc else None,
            "column": loc.col if loc else None,
            "end_column": loc.end_col if loc else None,
            "hint": diagnostic.hint,
        })
    assert metadata["diagnostics"] == expected
    for item in metadata["diagnostics"]:
        assert set(item) == {
            "severity", "phase", "message", "filename", "line", "column", "end_column", "hint",
        }
        assert "code" not in item and "args" not in item


def test_diagnostic_without_location_has_null_coordinates():
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    result["diagnostics"] = [
        Diagnostic(
            level=Level.NOTE, phase=Phase.ANALYZER, location=None,
            message="A note", hint="A hint",
        ),
    ]
    metadata = build_producer_metadata(result, filename="strategy.pine")
    assert metadata["diagnostics"] == [{
        "severity": "note", "phase": "ANALYZER", "message": "A note",
        "filename": None, "line": None, "column": None, "end_column": None, "hint": "A hint",
    }]


def test_generation_runs_exactly_once(monkeypatch):
    translate = pineforge_codegen.transpile_with_producer_metadata
    generate = pineforge_codegen._generate
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return generate(*args, **kwargs)

    monkeypatch.setattr(pineforge_codegen, "_generate", counted)
    translate(METADATA)
    assert len(calls) == 1


def test_wrapper_forwards_all_arguments_once(monkeypatch):
    translate = pineforge_codegen.transpile_with_producer_metadata
    original = pineforge_codegen.transpile_with_request_inventory
    source = (
        '//@version=6\nimport author/mathlib/1 as helpers\nstrategy("library")\n'
        'plot(helpers.twice(close))\n'
    )
    libraries = {
        "author/mathlib/1":
            '//@version=6\nlibrary("mathlib")\nexport twice(float x) => x * 2\n',
    }
    options = {
        "filename": "library.pine", "check_support": False, "libraries": libraries,
        "primary_chart_timeframe": "D", "history_capacity": 200, "drawing_capacity": 40,
    }
    expected = original(source, **options)
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(pineforge_codegen, "transpile_with_request_inventory", capture)
    result = translate(source, **options)
    assert calls == [((source,), options)]
    assert result["cpp"] == expected["cpp"]
    assert result["metadata"]["request_feed_inventory"] == expected["request_feed_inventory"]


def test_canonical_bytes_are_sorted_stable_and_utf8():
    result = pineforge_codegen.transpile_with_producer_metadata(METADATA)
    serialize = pineforge_codegen.producer_metadata_json
    metadata = result["metadata"]
    data = serialize(metadata)
    assert type(data) is bytes
    assert json.loads(data.decode("utf-8")) == metadata
    assert data == json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    assert not data.endswith(b"\n")

    def sorted_object(pairs):
        keys = [key for key, _value in pairs]
        assert keys == sorted(keys)
        return dict(pairs)

    assert json.loads(data.decode("utf-8"), object_pairs_hook=sorted_object) == metadata
    assert serialize(metadata) == data
    again = pineforge_codegen.transpile_with_producer_metadata(METADATA)
    assert serialize(again["metadata"]) == data
    non_ascii = deepcopy(metadata)
    non_ascii["filename"] = "stratégie.pine"
    encoded = serialize(non_ascii)
    assert "stratégie.pine".encode("utf-8") in encoded
    assert b"\\u00e9" not in encoded


class _Choice(Enum):
    VALUE = "value"


@dataclass
class _Record:
    value: int


_MISSING_KEYS = [
    "inputs", "input_kind_schema", "strategy_params", "request_feed_inventory",
    "diagnostics", "filename", "cppSha256",
]


@pytest.mark.parametrize("mutate", [
    *[
        pytest.param(lambda m, key=key: m.pop(key), id=f"missing-{key}")
        for key in _MISSING_KEYS
    ],
    pytest.param(lambda m: m.update(extra=None), id="extra-key"),
    pytest.param(lambda m: m.update(input_kind_schema=True), id="schema-bool"),
    pytest.param(lambda m: m.update(input_kind_schema=2), id="schema-version"),
    pytest.param(lambda m: m.update(input_kind_schema=1.0), id="schema-float"),
    pytest.param(lambda m: m["strategy_params"].update(value=float("nan")), id="nan"),
    pytest.param(lambda m: m["inputs"][0].update(default=float("inf")), id="infinity"),
    pytest.param(lambda m: m["strategy_params"].update({1: "value"}), id="non-string-key"),
    pytest.param(lambda m: m["strategy_params"].update(value={1}), id="set"),
    pytest.param(lambda m: m["strategy_params"].update(value=_Choice.VALUE), id="enum"),
    pytest.param(lambda m: m["strategy_params"].update(value=Level.NOTE), id="diagnostic-enum"),
    pytest.param(lambda m: m["strategy_params"].update(value=_Record(1)), id="dataclass"),
    pytest.param(lambda m: m["strategy_params"].update(value=b"value"), id="bytes"),
    pytest.param(lambda m: m["inputs"][0].update(options=("fast", "slow")), id="tuple"),
    pytest.param(lambda m: m.update(filename="\ud800"), id="surrogate"),
    pytest.param(lambda m: m["strategy_params"].update({"\ud800": 1}), id="surrogate-key"),
    pytest.param(lambda m: m.update(cppSha256="A" * 64), id="uppercase-hash"),
    pytest.param(
        lambda m: m.update(cppSha256=("1" if m["cppSha256"][0] == "0" else "0")
                           + m["cppSha256"][1:]),
        id="mismatched-hash",
    ),
    pytest.param(lambda m: m["request_feed_inventory"].pop("entries"), id="inventory-missing"),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(extra=None), id="inventory-extra",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(schema="wrong"), id="inventory-schema",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(source_sha256="A" * 64),
        id="inventory-uppercase-hash",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(artifact_sha256="0" * 64),
        id="inventory-bound-artifact",
    ),
    pytest.param(lambda m: m.update(request_feed_inventory=None), id="inventory-null"),
    pytest.param(lambda m: m["diagnostics"][0].pop("hint"), id="diagnostic-missing"),
    pytest.param(lambda m: m["diagnostics"][0].update(code="unused"), id="diagnostic-extra"),
    pytest.param(
        lambda m: m["diagnostics"][0].update(severity="error"), id="diagnostic-severity",
    ),
    pytest.param(lambda m: m["diagnostics"][0].update(line="1"), id="diagnostic-line"),
    pytest.param(lambda m: m["diagnostics"][0].update(line=True), id="diagnostic-line-bool"),
    pytest.param(lambda m: m["diagnostics"][0].update(message=None), id="diagnostic-message"),
    pytest.param(lambda m: m.update(inputs=[1]), id="input-not-object"),
    pytest.param(lambda m: m.update(strategy_params=[]), id="params-not-object"),
    pytest.param(lambda m: m.update(filename=1), id="filename-not-string"),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(primary_chart_timeframe=1),
        id="timeframe-int",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(primary_chart_timeframe=["D"]),
        id="timeframe-list",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(entries=None), id="entries-null",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(entries={}), id="entries-dict",
    ),
    pytest.param(
        lambda m: m["request_feed_inventory"].update(entries=[1]), id="entries-item",
    ),
    pytest.param(lambda m: m.update(diagnostics=None), id="diagnostics-null"),
    pytest.param(lambda m: m.update(diagnostics={}), id="diagnostics-dict"),
    pytest.param(lambda m: m["diagnostics"][0].update(phase=1), id="phase-int"),
    pytest.param(lambda m: m["diagnostics"][0].update(phase=None), id="phase-null"),
    pytest.param(lambda m: m["diagnostics"][0].update(filename=1), id="diag-filename"),
    pytest.param(lambda m: m["diagnostics"][0].update(hint=1), id="diag-hint"),
    pytest.param(lambda m: m["diagnostics"][0].update(column="1"), id="diag-column"),
    pytest.param(lambda m: m["diagnostics"][0].update(end_column=True), id="diag-end-bool"),
])
def test_strict_validation_and_serialization(mutate):
    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    from pineforge_codegen.producer_metadata import validate_producer_metadata

    error = pineforge_codegen.ProducerMetadataError
    assert issubclass(error, ValueError)
    assert validate_producer_metadata(metadata) is None
    assert pineforge_codegen.producer_metadata_json(metadata)
    invalid = deepcopy(metadata)
    mutate(invalid)
    with pytest.raises(error):
        validate_producer_metadata(invalid)
    with pytest.raises(error):
        pineforge_codegen.producer_metadata_json(invalid)


@pytest.mark.parametrize("value", [
    pytest.param("A" * 64, id="uppercase"),
    pytest.param("a" * 63, id="short"),
    pytest.param("a" * 65, id="long"),
    pytest.param("g" * 64, id="not-hex"),
    pytest.param("a" * 64 + "\n", id="trailing-newline"),
    pytest.param(1, id="int"),
    pytest.param(None, id="null"),
    pytest.param(True, id="bool"),
])
def test_matching_malformed_hashes_are_refused(value):
    from pineforge_codegen.producer_metadata import validate_producer_metadata

    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    assert validate_producer_metadata(metadata) is None
    assert type(pineforge_codegen.producer_metadata_json(metadata)) is bytes
    invalid = deepcopy(metadata)
    invalid["request_feed_inventory"]["source_sha256"] = value
    invalid["cppSha256"] = value
    reason = r"^request_feed_inventory\.source_sha256: must be 64 lowercase hexadecimal digits$"
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        validate_producer_metadata(invalid)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        pineforge_codegen.producer_metadata_json(invalid)


@pytest.mark.parametrize("value", [
    pytest.param("A" * 64, id="uppercase"),
    pytest.param("a" * 63, id="short"),
    pytest.param("a" * 65, id="long"),
    pytest.param("g" * 64, id="not-hex"),
    pytest.param("a" * 64 + "\n", id="trailing-newline"),
    pytest.param(1, id="int"),
    pytest.param(None, id="null"),
    pytest.param(True, id="bool"),
])
def test_malformed_cpp_hash_is_refused_for_its_format(value):
    from pineforge_codegen.producer_metadata import validate_producer_metadata

    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    assert validate_producer_metadata(metadata) is None
    assert type(pineforge_codegen.producer_metadata_json(metadata)) is bytes
    invalid = deepcopy(metadata)
    invalid["cppSha256"] = value
    reason = r"^cppSha256: must be 64 lowercase hexadecimal digits$"
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        validate_producer_metadata(invalid)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        pineforge_codegen.producer_metadata_json(invalid)


@pytest.mark.parametrize("container", ["dict", "list"])
def test_circular_containers_are_refused_by_validator_and_builder(container):
    from pineforge_codegen.producer_metadata import (
        build_producer_metadata, validate_producer_metadata,
    )

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    metadata = build_producer_metadata(result, filename="strategy.pine")
    assert validate_producer_metadata(metadata) is None
    if container == "dict":
        metadata["strategy_params"]["cycle"] = metadata["strategy_params"]
        result["strategyParams"]["cycle"] = result["strategyParams"]
    else:
        options = []
        options.append(options)
        metadata["inputs"][0]["options"] = options
        result["inputs"][0]["options"] = options
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match="circular"):
        validate_producer_metadata(metadata)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match="circular"):
        build_producer_metadata(result, filename="strategy.pine")


def test_shared_container_without_a_cycle_is_accepted():
    from pineforge_codegen.producer_metadata import (
        build_producer_metadata, validate_producer_metadata,
    )

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    metadata = build_producer_metadata(result, filename="strategy.pine")
    assert validate_producer_metadata(metadata) is None
    shared = {"value": 1}
    metadata["strategy_params"].update(first=shared, second=shared)
    result["strategyParams"].update(first=shared, second=shared)
    assert validate_producer_metadata(metadata) is None
    assert pineforge_codegen.producer_metadata_json(metadata) == json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    assert build_producer_metadata(result, filename="strategy.pine") == metadata


@pytest.mark.parametrize("root", [
    pytest.param([], id="list"),
    pytest.param((), id="tuple"),
    pytest.param(None, id="null"),
    pytest.param(float("nan"), id="nan"),
    pytest.param("metadata", id="string"),
    pytest.param(1, id="int"),
    pytest.param(True, id="bool"),
])
def test_validator_and_serializer_require_an_object_root(root):
    from pineforge_codegen.producer_metadata import validate_producer_metadata

    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    assert validate_producer_metadata(metadata) is None
    assert pineforge_codegen.producer_metadata_json(metadata)
    reason = r"^metadata: must be an object$"
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        validate_producer_metadata(root)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        pineforge_codegen.producer_metadata_json(root)


def test_diagnostics_items_must_be_objects():
    from pineforge_codegen.producer_metadata import validate_producer_metadata

    metadata = pineforge_codegen.transpile_with_producer_metadata(METADATA)["metadata"]
    assert validate_producer_metadata(metadata) is None
    assert pineforge_codegen.producer_metadata_json(metadata)
    invalid = deepcopy(metadata)
    invalid["diagnostics"] = [1]
    reason = r"^diagnostics\[0\]: must be an object$"
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        validate_producer_metadata(invalid)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        pineforge_codegen.producer_metadata_json(invalid)


def test_build_converts_tuples_to_lists_without_mutating_the_result():
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    result["inputs"][0]["options"] = ("fast", "slow")
    before = deepcopy(result)
    metadata = build_producer_metadata(result, filename="strategy.pine")
    assert metadata["inputs"][0]["options"] == ["fast", "slow"]
    assert result == before
    metadata["inputs"][0]["options"].append("other")
    assert result == before
    assert pineforge_codegen.producer_metadata_json(metadata)


@pytest.mark.parametrize("value", [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param({1}, id="set"),
    pytest.param(_Choice.VALUE, id="enum"),
    pytest.param(_Record(1), id="dataclass"),
    pytest.param(b"value", id="bytes"),
    pytest.param("\ud800", id="surrogate"),
])
def test_build_refuses_non_json_values_with_the_path(value):
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    result["inputs"][0]["default"] = value
    with pytest.raises(
        pineforge_codegen.ProducerMetadataError, match=r"inputs\[0\]\.default",
    ):
        build_producer_metadata(result, filename="strategy.pine")


@pytest.mark.parametrize("cpp", [
    pytest.param(None, id="not-text"),
    pytest.param("\ud800", id="not-utf8"),
    pytest.param("different C++", id="wrong-hash"),
])
def test_build_refuses_invalid_cpp(cpp):
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    result["cpp"] = cpp
    with pytest.raises(pineforge_codegen.ProducerMetadataError):
        build_producer_metadata(result, filename="strategy.pine")


@pytest.mark.parametrize("mutate,reason", [
    pytest.param(lambda r: [], r"^result: must be an object$", id="result-list"),
    pytest.param(lambda r: None, r"^result: must be an object$", id="result-null"),
    pytest.param(lambda r: (1,), r"^result: must be an object$", id="result-tuple"),
    *[
        pytest.param(
            lambda r, key=key: {name: value for name, value in r.items() if name != key},
            rf"^result\.{key}: missing value$", id=f"missing-{key}",
        )
        for key in ("cpp", "inputs", "strategyParams", "request_feed_inventory", "diagnostics")
    ],
    pytest.param(
        lambda r: {**r, "diagnostics": None},
        r"^diagnostics: must be a sequence of Diagnostic objects$", id="diagnostics-null",
    ),
    pytest.param(
        lambda r: {**r, "diagnostics": {}},
        r"^diagnostics: must be a sequence of Diagnostic objects$", id="diagnostics-dict",
    ),
    pytest.param(
        lambda r: {**r, "diagnostics": "text"},
        r"^diagnostics: must be a sequence of Diagnostic objects$", id="diagnostics-text",
    ),
    pytest.param(
        lambda r: {**r, "diagnostics": [{"severity": "note"}]},
        r"^diagnostics\[0\]: must be a Diagnostic$", id="diagnostic-dict",
    ),
    pytest.param(
        lambda r: {**r, "diagnostics": [
            Diagnostic(level=None, phase=Phase.ANALYZER, location=None, message="m"),
        ]},
        r"^diagnostics\[0\]: invalid Diagnostic attributes$", id="diagnostic-attributes",
    ),
])
def test_build_refusals_identify_the_reason(mutate, reason):
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    assert build_producer_metadata(result, filename="strategy.pine")
    invalid = mutate(result)
    with pytest.raises(pineforge_codegen.ProducerMetadataError, match=reason):
        build_producer_metadata(invalid, filename="strategy.pine")


def test_build_accepts_a_tuple_of_diagnostics():
    from pineforge_codegen.producer_metadata import build_producer_metadata

    result = pineforge_codegen.transpile_with_request_inventory(METADATA)
    diagnostics = (
        Diagnostic(
            level=Level.WARNING, phase=Phase.ANALYZER,
            location=SourceLocation("strategy.pine", 3, 5, 9),
            message="A warning", hint="A hint",
        ),
        Diagnostic(level=Level.NOTE, phase=Phase.CODEGEN, location=None, message="A note"),
    )
    result["diagnostics"] = diagnostics
    assert type(result["diagnostics"]) is tuple
    assert len(result["diagnostics"]) == 2
    expected = [
        {
            "severity": "warning", "phase": "ANALYZER", "message": "A warning",
            "filename": "strategy.pine", "line": 3, "column": 5, "end_column": 9,
            "hint": "A hint",
        },
        {
            "severity": "note", "phase": "CODEGEN", "message": "A note",
            "filename": None, "line": None, "column": None, "end_column": None,
            "hint": None,
        },
    ]
    tuple_metadata = build_producer_metadata(result, filename="strategy.pine")
    assert type(tuple_metadata["diagnostics"]) is list
    assert tuple_metadata["diagnostics"] == expected

    result["diagnostics"] = list(diagnostics)
    list_metadata = build_producer_metadata(result, filename="strategy.pine")
    assert type(list_metadata["diagnostics"]) is list
    assert list_metadata["diagnostics"] == expected
    assert tuple_metadata == list_metadata


def test_source_errors_and_chart_timeframe_binding():
    translate = pineforge_codegen.transpile_with_producer_metadata
    with pytest.raises(CompileError):
        translate('//@version=6\nstrategy("invalid")\nx = (1 + )\n')
    with pytest.raises(RequestFeedInventoryError):
        translate(METADATA, primary_chart_timeframe="1H")
    metadata = translate(METADATA, primary_chart_timeframe="D")["metadata"]
    assert metadata["request_feed_inventory"]["primary_chart_timeframe"] == "D"


@pytest.mark.parametrize("check_support", [True, False])
@pytest.mark.parametrize("rule,reason", [
    ("namespace", "namespace or mount primitive is not allowed in generated C++"),
    ("thread", "thread creation is not allowed in generated C++"),
])
def test_cpp_allowlist_refusal_excludes_script_text(monkeypatch, check_support, rule, reason):
    translate = pineforge_codegen.transpile_with_producer_metadata
    from pineforge_codegen.cpp_allowlist import Finding

    source = (
        '//@version=6\nstrategy("SCRIPT_TEXT_MARKER")\n'
        "SCRIPT_TEXT_MARKER = close\n"
        'plot(SCRIPT_TEXT_MARKER, title="SCRIPT_TEXT_MARKER")\n'
    )
    calls = []

    def refuse(cpp):
        calls.append(cpp)
        assert "SCRIPT_TEXT_MARKER" in cpp
        return [Finding(line=987, column=654, rule=rule, reason=reason)]

    monkeypatch.setattr(pineforge_codegen, "check_generated_cpp", refuse)
    with pytest.raises(CompileError) as caught:
        translate(source, check_support=check_support, filename="strategy.pine")
    assert len(calls) == 1
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.message == reason
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.location == SourceLocation("strategy.pine", 1, 1, 1)
    assert diagnostic.hint == "Report this generated C++ allowlist failure to PineForge."
    assert diagnostic.args == {}
    assert diagnostic.code != "PF-E0000"
    for value in (
        diagnostic.level, diagnostic.phase, diagnostic.location.file,
        diagnostic.location.line, diagnostic.location.col, diagnostic.location.end_col,
        diagnostic.message, diagnostic.hint, diagnostic.code, diagnostic.args,
        diagnostic.user_message, str(caught.value),
    ):
        assert "SCRIPT_TEXT_MARKER" not in repr(value)


@pytest.mark.parametrize("ending", ["\r\n", "\r", "mixed"])
def test_line_endings_preserve_cpp_and_metadata(ending):
    translate = pineforge_codegen.transpile_with_producer_metadata
    expected = translate(METADATA, filename="strategy.pine")
    if ending == "mixed":
        endings = ("\n", "\r\n", "\r")
        lines = METADATA.split("\n")
        source = "".join(
            line + endings[index % len(endings)] for index, line in enumerate(lines[:-1])
        ) + lines[-1]
    else:
        source = METADATA.replace("\n", ending)
    result = translate(source, filename="strategy.pine")
    assert result == expected
    cpp = pineforge_codegen.transpile(source, filename="strategy.pine")
    legacy = pineforge_codegen.transpile_full(source, filename="strategy.pine")
    inventory = pineforge_codegen.transpile_with_request_inventory(
        source, filename="strategy.pine",
    )
    assert result["cpp"] == cpp
    metadata = result["metadata"]
    assert metadata["inputs"] == json.loads(json.dumps(legacy["inputs"]))
    assert metadata["strategy_params"] == json.loads(json.dumps(legacy["strategyParams"]))
    assert metadata["request_feed_inventory"] == inventory["request_feed_inventory"]
    assert metadata["cppSha256"] == hashlib.sha256(cpp.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("option,value", [
    ("history_capacity", True), ("history_capacity", False),
    ("history_capacity", 100.0), ("history_capacity", "100"),
    ("history_capacity", 0), ("history_capacity", 1), ("history_capacity", -1),
    ("drawing_capacity", True), ("drawing_capacity", False),
    ("drawing_capacity", 40.0), ("drawing_capacity", "40"),
    ("drawing_capacity", 0), ("drawing_capacity", -1),
])
def test_invalid_capacities_match_the_sibling_api(option, value):
    translate = pineforge_codegen.transpile_with_producer_metadata
    options = {option: value, "filename": "capacity.pine"}
    with pytest.raises(CompileError) as sibling:
        pineforge_codegen.transpile_full(METADATA, **options)
    with pytest.raises(type(sibling.value)) as actual:
        translate(METADATA, **options)
    assert actual.value.diagnostics == sibling.value.diagnostics


def test_repository_sources_produce_matching_documents():
    translate = pineforge_codegen.transpile_with_producer_metadata
    # Every sixteenth path of the sorted repository sources is a stable sample.
    paths = sorted(
        path for directory in ("tests", "docs") for path in (ROOT / directory).rglob("*.pine")
    )[::16]
    assert len(paths) >= 25
    accepted = 0
    for path in paths:
        source = path.read_text(encoding="utf-8")
        filename = path.relative_to(ROOT).as_posix()
        try:
            cpp = pineforge_codegen.transpile(source, filename=filename)
        except CompileError:
            continue
        result = translate(source, filename=filename)
        metadata = result["metadata"]
        data = pineforge_codegen.producer_metadata_json(metadata)
        assert len(data) > 0
        assert json.loads(data.decode("utf-8")) == metadata, filename
        assert result["cpp"] == cpp, filename
        assert metadata["cppSha256"] == hashlib.sha256(cpp.encode("utf-8")).hexdigest(), filename
        accepted += 1
    assert accepted >= 25


def test_public_contract_documents_the_producer_metadata():
    text = (ROOT / "docs" / "PUBLIC_CONTRACT.md").read_text(encoding="utf-8")
    assert "## Producer metadata" in text
    section = text.split("## Producer metadata", 1)[1].split("\n## ", 1)[0]
    for name in (
        "transpile_with_producer_metadata", "producer_metadata_json", "ProducerMetadataError",
    ):
        assert f"`{name}`" in section, name
    table = section.split("| Key | Python value | Meaning |", 1)[1].split("\n\n", 1)[0]
    for name in (
        "inputs", "input_kind_schema", "strategy_params", "request_feed_inventory",
        "diagnostics", "filename", "cppSha256",
    ):
        assert f"\n| `{name}` |" in table, name
