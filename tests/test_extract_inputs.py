from pineforge_codegen import transpile_full

PREAMBLE = "//@version=6\nstrategy(\"T\")\n"


def _full(body: str):
    return transpile_full(PREAMBLE + body)


def test_int_input_manifest():
    r = _full("length = input.int(14, \"Length\", minval=2, maxval=200, step=2)\n")
    inp = next(i for i in r["inputs"] if i["title"] == "Length")
    assert inp["type"] == "int"
    assert inp["default"] == 14
    assert inp["min"] == 2
    assert inp["max"] == 200
    assert inp["step"] == 2


def test_float_omits_absent_bounds():
    r = _full("mult = input.float(2.0, \"Mult\")\n")
    inp = next(i for i in r["inputs"] if i["title"] == "Mult")
    assert inp["type"] == "float"
    assert inp["default"] == 2.0
    assert "min" not in inp and "max" not in inp


def test_bool_and_title_fallback_to_var_name():
    r = _full("use_x = input.bool(true)\n")
    inp = next(i for i in r["inputs"] if i["title"] == "use_x")
    assert inp["type"] == "bool"
    assert inp["default"] is True


def test_string_options():
    r = _full("mode = input.string(\"a\", \"Mode\", options=[\"a\", \"b\", \"c\"])\n")
    inp = next(i for i in r["inputs"] if i["title"] == "Mode")
    assert inp["type"] == "string"
    assert inp["options"] == ["a", "b", "c"]


def test_non_const_options_omitted():
    # options referencing a non-literal must be omitted, not crash
    r = _full("v = \"x\"\nmode = input.string(\"a\", \"Mode\", options=[v, \"b\"])\n")
    inp = next(i for i in r["inputs"] if i["title"] == "Mode")
    assert "options" not in inp


def test_source_type():
    r = _full("src = input.source(close, \"Source\")\n")
    inp = next(i for i in r["inputs"] if i["title"] == "Source")
    assert inp["type"] == "source"


def test_zero_inputs():
    r = _full("a = close + 1\n")
    assert r["inputs"] == []


def test_strategy_params_surfaced():
    r = transpile_full("//@version=6\nstrategy(\"T\", initial_capital=5000, pyramiding=2)\na = close\n")
    assert r["strategyParams"]["initial_capital"] == 5000
    assert r["strategyParams"]["pyramiding"] == 2


def test_cpp_still_returned():
    r = _full("length = input.int(14, \"Length\")\n")
    assert 'get_input_int("Length", 14)' in r["cpp"]
