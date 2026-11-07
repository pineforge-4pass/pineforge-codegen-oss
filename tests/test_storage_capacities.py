"""Source-selected history retention and validated operator options."""

import importlib.util
import json
from pathlib import Path

import pytest

from pineforge_codegen import (
    transpile, transpile_full, transpile_with_request_inventory,
)
from pineforge_codegen.errors import CompileError
from tests._e2e import reference_codegen, transpile_json


BASE = "8d615dd27b31acd4fe92b57be7c1811cc42ecbda"
HEADER = '//@version=6\nstrategy("storage capacity")\n'
APIS = (transpile, transpile_full, transpile_with_request_inventory)
OPTION_CODE = "PF-E5094"
OFFSET_CODE = "PF-E5095"


def glue():
    path = Path(__file__).resolve().parents[1] / "gate" / "glue.py"
    spec = importlib.util.spec_from_file_location("_capacity_glue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("option, value, minimum", [
    ("history_capacity", True, 2),
    ("history_capacity", False, 2),
    ("history_capacity", 100.0, 2),
    ("history_capacity", "100", 2),
    ("history_capacity", 0, 2),
    ("history_capacity", 1, 2),
    ("history_capacity", -1, 2),
    ("drawing_capacity", True, 1),
    ("drawing_capacity", False, 1),
    ("drawing_capacity", 40.0, 1),
    ("drawing_capacity", "40", 1),
    ("drawing_capacity", 0, 1),
    ("drawing_capacity", -1, 1),
])
def test_invalid_options_have_one_typed_identity(api, option, value, minimum):
    with pytest.raises(CompileError) as error:
        api(HEADER, filename="capacity.pine", **{option: value})
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.code == OPTION_CODE
    assert diagnostic.args == {"option": option, "minimum": minimum}
    assert diagnostic.location.file == "capacity.pine"
    result = json.loads(glue().transpile_json(HEADER, **{option: value}))
    assert not result["ok"]
    assert result["diagnostics"][0]["code"] == OPTION_CODE
    assert result["diagnostics"][0]["args"] == diagnostic.args


@pytest.mark.parametrize("api", APIS)
def test_all_apis_and_glue_forward_the_selected_capacity(api):
    source = HEADER + "value = close + 1\nobserved = value[150] + close[150]\n"
    result = api(source, history_capacity=200, drawing_capacity=40)
    cpp = result if isinstance(result, str) else result["cpp"]
    assert "Series<double> _s_close{200};" in cpp
    assert "Series<double> value{200};" in cpp
    result = json.loads(glue().transpile_json(
        source, history_capacity=200, drawing_capacity=40,
    ))
    assert result["ok"], result.get("diagnostics")
    assert result["cpp"] == cpp


def test_capacity_options_survive_session_generation_retries(monkeypatch):
    from pineforge_codegen.codegen import CodeGen

    seen = []
    initialize = CodeGen.__init__

    def capture(self, *args, **kwargs):
        seen.append((kwargs.get("history_capacity"), kwargs.get("drawing_capacity")))
        initialize(self, *args, **kwargs)

    monkeypatch.setattr(CodeGen, "__init__", capture)
    source = HEADER + (
        "read() => session.ismarket[1]\n"
        "a = read()\nb = read()\n"
        'if a or b\n    strategy.entry("L", strategy.long)\n'
    )
    cpp = transpile(source, history_capacity=128, drawing_capacity=40)
    assert len(seen) >= 2
    assert all(options == (128, 40) for options in seen)
    assert "Series<bool> _session_call_" in cpp
    assert "{128};" in cpp


@pytest.mark.parametrize("body", [
    "observed = close[150]\n",
    "value = close + 1\nobserved = value[150]\n",
    "a = array.from(close)\nobserved = na(a[150])\n",
])
def test_literal_offset_cannot_exceed_the_operator_capacity(body):
    source = HEADER + body
    with pytest.raises(CompileError) as error:
        transpile(source, history_capacity=100)
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.code == OFFSET_CODE
    assert diagnostic.args == {"offset": 150, "capacity": 100}
    assert transpile(source, history_capacity=200)


def test_literal_offset_grows_bare_default_without_changing_the_offset_guard():
    cpp = transpile(HEADER + "value = close + 1\nobserved = value[600] + close[600]\n")
    # Index 600 needs slots for current plus 600 prior executions.
    assert "Series<double> value{601};" in cpp
    assert "Series<double> _s_close{601};" in cpp


def test_history_inside_requested_tuple_uses_the_same_capacity_plan():
    source = HEADER + (
        '[older, current] = request.security(syminfo.tickerid, "60", [close[600], open])\n'
        "observed = older + current\n"
    )
    with pytest.raises(CompileError) as error:
        transpile(source, history_capacity=100)
    assert error.value.diagnostics[0].code == OFFSET_CODE
    assert error.value.diagnostics[0].args == {"offset": 600, "capacity": 100}
    cpp = transpile(source)
    assert "{601};" in cpp


def test_nonliteral_offset_selects_the_named_default():
    cpp = transpile(HEADER + "lag = bar_index % 2 + 599\nobserved = close[lag]\n")
    assert "Series<double> _s_close{5000};" in cpp
    assert "_pf_idx_v > 10000" in cpp


@pytest.mark.parametrize("declaration", [
    'strategy("storage capacity", max_bars_back=32)',
    'strategy("storage capacity")\nmax_bars_back(close, 32)',
])
def test_pine_directive_stays_authoritative(declaration):
    source = "//@version=6\n" + declaration + "\nobserved = close[1]\n"
    assert "Series<double> _s_close{32};" in transpile(source, history_capacity=16)
    assert "Series<double> _s_close{64};" in transpile(source, history_capacity=64)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("dynamic", [False, True])
def test_collection_constructor_and_reset_receive_the_option(cached, dynamic):
    seed = "var a" if cached else "a"
    index = "lag" if dynamic else "3"
    source = HEADER + f"{seed} = array.from(2, 5)\n"
    if dynamic:
        source += "lag = bar_index % 3 + 1\n"
    source += f"observed = na(a[{index}]) ? -1 : (a[{index}]).size()\n"
    cls = "_PFCachedCollectionHistory" if cached else "_PFCollectionHistory"
    suffix = "" if dynamic else "{4}"
    assert f"{cls}<decltype(a)> _pf_collection_hist_a{suffix};" in transpile(source)
    cpp = transpile(source, history_capacity=128)
    assert f"{cls}<decltype(a)> _pf_collection_hist_a{{128}};" in cpp
    assert "this->_pf_collection_hist_a = decltype(this->_pf_collection_hist_a){128};" in cpp
    if dynamic:
        assert '_PF_LIMIT_STOP("max_bars_back", 128LL,' in cpp


@pytest.mark.parametrize("value, store", [
    ("close + 1", "_security_helper_series_"),
    ('str.tostring(close)', "_security_helper_series_str_"),
])
def test_security_map_entry_constructs_once_with_resolved_capacity(value, store):
    source = HEADER + (
        f"read() =>\n    x = {value}\n    x[1]\n"
        'observed = request.security(syminfo.tickerid, "60", read())\n'
    )
    before = transpile(source)
    assert f'{store}["' in before
    assert f'{store}.try_emplace(' not in before
    after = transpile(source, history_capacity=128)
    assert f'{store}.try_emplace(' in after
    # The mapped Series is built in place with the same capacity on all uses.
    assert ', 128).first->second)' in after
    assert f'{store}["' not in after


def test_lazy_literal_requirement_is_not_truncated():
    source = HEADER + "observed = close > open and ta.roc(close, 200) > 0\n"
    assert "Series<double> _pf_lazy_src_hist_1{201};" in transpile(source)
    assert "Series<double> _pf_lazy_src_hist_1{201};" in transpile(
        source, history_capacity=128,
    )


def test_lazy_runtime_history_receives_the_capacity_and_a_checked_read():
    source = HEADER + (
        'length = input.int(3, "Length")\n'
        "observed = close > open and ta.roc(close, length) > 0\n"
    )
    before = transpile(source)
    assert "Series<double> _pf_lazy_src_hist_1;" in before
    assert '_PF_LIMIT_STOP("max_bars_back",' not in before
    cpp = transpile(source, history_capacity=128)
    assert "Series<double> _pf_lazy_src_hist_1{128};" in cpp
    assert '_PF_LIMIT_STOP("max_bars_back", 128LL,' in cpp
    assert "auto _pf_raw = (" in cpp


@pytest.mark.parametrize("value, store", [
    ("close + 1", "_security_helper_series_"),
    ("str.tostring(close)", "_security_helper_series_str_"),
])
def test_requested_helper_dynamic_reads_use_the_existing_typed_limit(value, store):
    source = HEADER + (
        'lag = input.int(150, "Lag")\n'
        f"read(int k) =>\n    x = {value}\n    x[k]\n"
        'observed = request.security(syminfo.tickerid, "1", read(lag))\n'
    )
    cpp = transpile(source, history_capacity=100)
    assert f"{store}.try_emplace(" in cpp
    assert ", 100).first->second)" in cpp
    assert '_PF_LIMIT_STOP("max_bars_back", 100LL,' in cpp


def test_requested_composed_literal_offset_cannot_hide_static_overflow():
    source = HEADER + (
        "read(float src, int k) => src[k]\n"
        'observed = request.security(syminfo.tickerid, "1", read(close[60], 60))\n'
    )
    with pytest.raises(CompileError) as error:
        transpile(source, history_capacity=100)
    assert error.value.diagnostics[0].code == OFFSET_CODE
    # Reading 60 bars back from a source already shifted 60 needs offset 120.
    assert error.value.diagnostics[0].args == {"offset": 120, "capacity": 100}
    assert error.value.diagnostics[0].location.line == 3
    assert transpile(source, history_capacity=200)


@pytest.mark.parametrize("body", [
    "lag = bar_index % 2 + 149\nobserved = close[lag]\n",
    'length = input.int(3)\nobserved = close > open and ta.roc(close, length) > 0\n',
])
def test_runtime_history_bound_emits_the_existing_limit_stop(body):
    cpp = transpile(HEADER + body, history_capacity=100)
    assert '_PF_LIMIT_STOP("max_bars_back", 100LL,' in cpp


@pytest.mark.parametrize("kind, arena, collector, ctor", [
    ("line", "_pf_lines_", "_pf_collect_lines_",
     "line.new(bar_index, close, bar_index + 1, close + 1)"),
    ("box", "_pf_boxes_", "_pf_collect_boxes_",
     "box.new(bar_index, close + 1, bar_index + 1, close)"),
    ("label", "_pf_labels_", "_pf_collect_labels_", "label.new(bar_index, close)"),
])
def test_live_drawing_limit_is_after_collection_and_before_publication(kind, arena, collector, ctor):
    source = HEADER + f"drawing = {ctor}\n"
    before = transpile(source)
    assert '_PF_LIMIT_STOP("live_drawings",' not in before
    assert transpile(source, drawing_capacity=None) == before
    cpp = transpile(source, drawing_capacity=40)
    body = cpp.split(f"    {kind.title()} {collector}(", 1)[1].split("\n    }", 1)[0]
    sentence = (
        "The script keeps more than 40 live drawings of one type; "
        "the configured drawing capacity is exceeded."
    )
    stop = (
        f'if ((int)this->{arena}.order().size() > 40) '
        f'_PF_LIMIT_STOP("live_drawings", 40LL, "{sentence}");'
    )
    assert stop in body
    assert body.index("_pf_collect_drawings(") < body.index(stop) < body.index("return _pf_new;")
    assert f"{collector}(pf_{kind}_new(" in cpp
    assert "_pf_collect_linefills_" not in cpp


@pytest.mark.parametrize("kind, collector, ctor", [
    ("line", "_pf_collect_lines_", "line.new(0, 1, 1, 2)"),
    ("box", "_pf_collect_boxes_", "box.new(0, 2, 1, 1)"),
    ("label", "_pf_collect_labels_", "label.new(0, 1)"),
])
def test_drawing_copy_forms_share_the_bounded_collector(kind, collector, ctor):
    source = HEADER + (
        f"drawing = {ctor}\n"
        f"copied = {kind}.copy(drawing)\n"
        "method_copy = drawing.copy()\n"
    )
    cpp = transpile(source, drawing_capacity=40)
    assert cpp.count(f"{collector}(pf_{kind}_copy(") >= 2


def test_native_drawing_limit_argument_domain_has_a_typed_diagnostic():
    with pytest.raises(CompileError) as error:
        transpile(HEADER + "label.new(0, 1)\n", drawing_capacity=9223372036854775808)
    assert error.value.diagnostics[0].code == "PF-E5098"
    # This is the first integer outside the helper's signed int64 max argument.
    assert error.value.diagnostics[0].args == {"capacity": 9223372036854775808}


@pytest.mark.parametrize("body", [
    'lag = input.int(150)\nobserved = close[lag]\n',
    'lag = input.int(150)\nobserved = request.security(syminfo.tickerid, "1", close[lag])\n',
    'lag = input.int(150)\nobserved = request.security(syminfo.tickerid, "1", ta.ema(close, 2)[lag])\n',
    'lag = input.int(150)\nobserved = request.security(syminfo.tickerid, "1", bar_index[lag])\n',
])
def test_capacity_guard_follows_the_existing_range_guard(body):
    before = transpile(HEADER + body)
    assert '_PF_LIMIT_STOP("max_bars_back",' not in before
    cpp = transpile(HEADER + body, history_capacity=100)
    stop = cpp.index('_PF_LIMIT_STOP("max_bars_back", 100LL,')
    assert cpp.rfind(". The history offset must be a value between 0 and 10000.", 0, stop) >= 0


def test_native_constructor_domain_is_diagnosed_before_emitting_cpp():
    with pytest.raises(CompileError) as error:
        transpile(HEADER + "observed = close[1]\n", history_capacity=2147483648)
    assert error.value.diagnostics[0].code == "PF-E5097"
    # This value is the first integer outside the Series constructor's int domain.
    assert error.value.diagnostics[0].args == {"capacity": 2147483648}


UNCHANGED = (
    "observed = close[1]\n",
    "value = close + 1\nobserved = value[20]\n",
    "const int back = 2\nobserved = close[back + 1] + close[int(4 / 2)]\n",
    "observed = ta.sma(close, 2)[1]\n",
    "read(float src) => src[1]\nobserved = read(close)\n",
    "a = array.from(close)\nobserved = na(a[3])\n",
    "var a = array.from(2, 5)\nobserved = na(a[3])\n",
    "observed = close > open and ta.roc(close, 3) > 0\n",
    'observed = request.security(syminfo.tickerid, "60", close[1])\n',
    "read() =>\n    x = close + 1\n    x[1]\n"
    'observed = request.security(syminfo.tickerid, "60", read())\n',
    "read() =>\n    x = str.tostring(close)\n    x[1]\n"
    'observed = request.security(syminfo.tickerid, "60", read())\n',
    "label.new(bar_index, close)\n",
    "line.new(bar_index, close, bar_index + 1, close + 1)\n",
    "box.new(bar_index, close + 1, bar_index + 1, close)\n",
    'lag = input.int(-1, "Lag")\nplot(close[lag])\n',
    'lag = input.int(1, "Lag")\na = array.from(close)\n'
    "float total = 0.0\nfor value in a[lag]\n    total += value\n",
)


@pytest.mark.parametrize("body", UNCHANGED)
@pytest.mark.parametrize("header", [HEADER, HEADER.replace('")', '", max_labels_count=10)')])
def test_under_capacity_scripts_keep_the_entire_reference_cpp(tmp_path, body, header):
    source = header + body
    reference = reference_codegen(BASE)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before.get("diagnostics")
    cpp = transpile(source)
    assert cpp == before["cpp"]
    assert transpile(source, history_capacity=None, drawing_capacity=None) == cpp
