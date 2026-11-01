"""Fresh chart arrays read in a request use the requested bar's inputs."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


PRELUDE = '//@version=6\nstrategy("Array request")\n'
ARRAY = 'array<float> samples = array.from(close - open, open - close)\n'


def _evaluator(cpp: str, sec_id: int = 0) -> str:
    start = cpp.index(f"    void _eval_security_{sec_id}(")
    end = cpp.index("\n    }\n", start)
    return " ".join(cpp[start:end].split())


def _script(declarations: str, payload: str, timeframe="60", flags="") -> str:
    return (PRELUDE + declarations
            + f'x = request.security(syminfo.tickerid, "{timeframe}", {payload}{flags})\n'
            + 'plot(x)\n')


@pytest.mark.parametrize("timeframe, flags", [
    # Default merging changes the array's inputs, not the request's clock.
    ("60", ""),
    # A different fixed timeframe still supplies its own open and close.
    ("240", ", lookahead = barmerge.lookahead_off"),
    # Calendar bars and lookahead use the same expression on the supplied bar.
    ("D", ", lookahead = barmerge.lookahead_on, gaps = barmerge.gaps_on"),
])
@pytest.mark.parametrize("payload", [
    # Method syntax selects element zero from the requested array.
    "samples.get(0)",
    # Namespace syntax selects the same receiver and index.
    "array.get(samples, 0)",
    # Keywords bind by slot even when the index is written first.
    "array.get(index = 0, id = samples)",
    # The method's index keyword is the same argument as positional zero.
    "samples.get(index = 0)",
])
def test_fresh_array_uses_requested_inputs(timeframe, flags, payload):
    body = _evaluator(transpile(_script(ARRAY, payload, timeframe, flags)))
    # Both elements are constructed from the supplied bar before checked access.
    assert ("const auto _pf_shared_0_0 = "
            "std::vector<double>{(bar.close - bar.open), (bar.open - bar.close)};") in body
    # Index zero is checked against the requested array, with no chart read.
    assert "}((0)); }((_pf_shared_0_0));" in body
    assert "}((samples))" not in body


def test_scalar_dependencies_and_input_getters_are_recomputed():
    cpp = transpile(_script(
        'gain = input.float(2.0, "Gain")\n'
        'delta = close - open\n'
        'scaled = delta * gain\n'
        'array<float> samples = array.from(scaled, math.abs(delta))\n',
        "samples.get(0)"))
    body = _evaluator(cpp)
    # input.float reads get_input_double; expanding scaled and delta uses the requested bar.
    assert ('std::vector<double>{((bar.close - bar.open) * get_input_double("Gain", 2.0)), '
            'std::abs((bar.close - bar.open))}') in body
    assert "}((samples))" not in body


def test_pure_helper_arguments_are_requested_values():
    cpp = transpile(_script(
        'difference(float source) => source - open\n'
        'array<float> samples = array.from(difference(close), difference(high))\n',
        "samples.get(1)"))
    body = _evaluator(cpp)
    # Each helper substitutes its argument and the requested open.
    assert "std::vector<double>{(bar.close - bar.open), (bar.high - bar.open)}" in body
    assert "}((1)); }((_pf_shared_0_0));" in body


def test_repeated_reads_share_one_array_per_evaluation():
    body = _evaluator(transpile(_script(
        ARRAY, "samples.get(0) + array.get(samples, 1)")))
    # One declaration constructs the two elements; both getters borrow it.
    assert body.count("std::vector<double>{") == 1
    assert body.count("}((_pf_shared_0_0))") == 2
    assert "}((samples))" not in body


def test_requests_have_separate_array_values():
    cpp = transpile(PRELUDE + ARRAY
                    + 'x = request.security(syminfo.tickerid, "60", samples.get(0))\n'
                    + 'y = request.security(syminfo.tickerid, "240", samples.get(0))\n'
                    + 'plot(x + y)\n')
    # Each evaluator constructs its own value from its own supplied bar.
    assert "const auto _pf_shared_0_0 = std::vector<double>{" in _evaluator(cpp, 0)
    assert "const auto _pf_shared_1_0 = std::vector<double>{" in _evaluator(cpp, 1)


@pytest.mark.parametrize("payload", [
    # Negative one selects the last of two elements after bounds normalization.
    "samples.get(-1)",
    # Two equals the size, so the existing bounds check must stop the run.
    "samples.get(2)",
    # An na integer index must reach the existing na-index check.
    "samples.get(int(na))",
])
def test_checked_access_and_element_na_are_preserved(payload):
    body = _evaluator(transpile(_script(
        'array<float> samples = array.from(na, close)\n', payload)))
    # Element na remains a typed na and the other element is the requested close.
    assert "std::vector<double>{na<double>(), bar.close}" in body
    # The normal checked getter still handles negative, invalid and na indices.
    assert ("int64_t __pf_array_index="
            "__pf_raw_index<0?__pf_raw_index+__pf_array_size:__pf_raw_index;") in body
    assert 'if(is_na(__pf_raw_index_value)) _PF_ARRAY_STOP("index_out_of_bounds", "get",' in body
    assert 'if(__pf_array_index<0||__pf_array_index>=__pf_array_size)' in body


def test_a_skipped_getter_keeps_its_bounds_check_in_the_branch():
    body = _evaluator(transpile(_script(
        'take = input.bool(false, "Take")\n' + ARRAY, "take ? samples.get(2) : 0.0")))
    # Index two is invalid only when the true arm is evaluated.
    assert '_req_sec_0 = ((get_input_bool("Take", false)) ? ([&](auto&& __pf_array)' in body
    assert "}((2)); }((_pf_shared_0_0))) : (0.0));" in body


def test_a_getter_under_a_builtin_uses_the_requested_array():
    body = _evaluator(transpile(_script(ARRAY, "nz(samples.get(0), 7.0)")))
    # nz wraps the checked result; it must not capture the chart's receiver.
    assert "std::vector<double>{(bar.close - bar.open), (bar.open - bar.close)}" in body
    assert "auto _nz_v = ([&](auto&& __pf_array)" in body
    assert "}((0)); }((_pf_shared_0_0)));" in body


def test_an_input_index_is_read_before_the_chart_member_is_assigned():
    body = _evaluator(transpile(_script(
        'index = input.int(1, "Index")\n' + ARRAY, "samples.get(int(index))")))
    # The first evaluator can run before on_bar, so the index needs its getter.
    assert "const auto _pf_shared_0_0 = std::vector<double>{" in body
    assert 'get_input_int("Index", 1)' in body
    assert "}((samples))" not in body


def test_unrelated_matrix_keywords_do_not_bind_as_array_arguments():
    body = _evaluator(transpile(_script(
        ARRAY + 'matrix<float> grid = matrix.new<float>(1, 1, close)\n'
        + 'cell = grid.get(row = 0, column = 0)\n', "samples.get(0)")))
    # The matrix read does not escape samples; its two indices belong to matrix.get.
    assert "const auto _pf_shared_0_0 = std::vector<double>{" in body
    assert "}((0)); }((_pf_shared_0_0));" in body


@pytest.mark.parametrize("declarations, payload", [
    # A write changes the object after construction, so its initializer is insufficient.
    (ARRAY + "samples.set(0, high)\n", "samples.get(0)"),
    # Namespace writes have the same alias and lifetime constraints as method writes.
    (ARRAY + "array.set(samples, 0, high)\n", "array.get(samples, 0)"),
    # An alias can carry writes; an initializer alone does not prove the object immutable.
    (ARRAY + "alias = samples\nalias.set(0, high)\n", "samples.get(0)"),
    # A function argument escapes the object, even when this function only reads it.
    ('count(array<float> a) => array.size(a)\n' + ARRAY + "n = count(samples)\n",
     "samples.get(0)"),
    # A persistent array's first initialization belongs to its existing state machine.
    ('var array<float> samples = array.from(close - open, open - close)\n',
     "samples.get(0)"),
    # Intrabar persistence likewise cannot use a fresh requested-bar constructor.
    ('varip array<float> samples = array.from(close - open, open - close)\n',
     "samples.get(0)"),
    # A reassigned object cannot be rebuilt from its first declaration.
    (ARRAY + "samples := array.from(high, low)\n", "samples.get(0)"),
    # A sized constructor is outside the fresh array.from rule.
    ('array<float> samples = array.new_float(2, close)\n', "samples.get(0)"),
    # Random inputs carry chart state and cannot be moved into a pure reconstruction.
    ('array<float> samples = array.from(math.random(), close)\n', "samples.get(0)"),
    # Stateful scalar dependencies are not declaration-only values.
    ('var float held = na\nheld := close\n'
     'array<float> samples = array.from(held, open)\n', "samples.get(0)"),
    # TA inputs need their own state and are outside the pure reconstruction rule.
    ('array<float> samples = array.from(ta.sma(close, 2), open)\n', "samples.get(0)"),
    # Source history needs requested-clock storage and is outside this rule.
    ('array<float> samples = array.from(close[1], open)\n', "samples.get(0)"),
    # Other array reads are kept together with the binding's existing representation.
    (ARRAY + "n = samples.size()\n", "samples.get(0)"),
])
def test_stateful_or_unproven_arrays_keep_the_existing_lowering(declarations, payload):
    body = _evaluator(transpile(_script(declarations, payload)))
    # These shapes never construct a shared requested array.
    assert "_pf_shared_" not in body


def test_array_history_keeps_its_existing_request_diagnostic():
    # A previous array object needs requested-clock copies, which remain unsupported.
    with pytest.raises(CompileError) as caught:
        transpile(_script(ARRAY, "array.get(samples[1], 0)"))
    assert any(
        "inside a request.security expression is not supported in PineForge" in diagnostic.message
        for diagnostic in caught.value.diagnostics)


def test_a_chart_read_keeps_the_whole_evaluator_on_its_existing_path():
    body = _evaluator(transpile(_script(
        ARRAY, "samples.get(0) + strategy.position_size")))
    # Mixing an account value with an inlined array retains the evaluator's fallback.
    assert "}((0)); }((samples))" in body
    assert "_pf_shared_" not in body


def test_lower_timeframe_array_requests_keep_the_existing_getter():
    cpp = transpile(PRELUDE + ARRAY
                    + 'x = request.security_lower_tf(syminfo.tickerid, "5", '
                      'float(samples.get(0)))\n'
                    + 'plot(array.size(x))\n')
    body = _evaluator(cpp)
    # The cast gives the lower-timeframe request a scalar element type.
    # Its accumulation path still owns the chart array getter.
    assert "}((0)); }((samples))" in body
    assert "_pf_shared_" not in body


def test_scalar_and_inline_array_requests_keep_the_existing_text():
    cpp = transpile(_script(
        "delta = close - open\n", "delta"))
    # A scalar global has always expanded to this one requested expression.
    assert _evaluator(cpp) == (
        "void _eval_security_0(const Bar& bar, bool is_complete) { "
        "_req_sec_0 = (bar.close - bar.open);")
    body = _evaluator(transpile(_script("", "array.get(array.from(close, open), 0)")))
    # An inline constructor already spells the supplied bar and has no chart binding.
    assert "}((0)); }((std::vector<double>{bar.close, bar.open}));" in body
    assert "_pf_shared_" not in body
