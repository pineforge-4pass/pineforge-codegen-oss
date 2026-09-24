"""Glue contract smoke test for gate/glue.py.

Loads gate/glue.py directly (it carries a `sys.path.insert(0, "/codegen")`
preamble; `pineforge_codegen` still resolves because the repo root is on
sys.path when pytest runs from there) and asserts the transpile_json envelope:

- valid source -> ok:true with inputs[] and strategyParams (the new manifest)
- invalid source -> ok:false with a diagnostics key (error branch untouched)
- valid source -> its warnings under diagnostics, in the same entry format
"""
import importlib.util
import json
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GLUE_PATH = os.path.join(_REPO_ROOT, "gate", "glue.py")


def _load_glue():
    spec = importlib.util.spec_from_file_location("pineforge_glue_under_test", _GLUE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_source_emits_manifest():
    glue = _load_glue()
    src = (
        "//@version=6\n"
        'strategy("T", initial_capital=5000)\n'
        'length = input.int(14, "Length")\n'
        "plot(close)\n"
    )
    d = json.loads(glue.transpile_json(src))
    assert d["ok"] is True
    assert any(i["title"] == "Length" for i in d["inputs"])
    assert d["strategyParams"]["initial_capital"] == 5000


def test_invalid_source_still_returns_error_envelope():
    glue = _load_glue()
    d = json.loads(glue.transpile_json("strategy("))
    assert d["ok"] is False
    assert "diagnostics" in d


def test_valid_source_carries_its_warnings():
    # Explicit anchors are routed exactly by TA1 and therefore produce no
    # approximation warning in the success envelope.
    glue = _load_glue()
    src = (
        "//@version=6\n"
        'strategy("T")\n'
        '[v, u, l] = ta.vwap(close, timeframe.change("W"), 1.0)\n'
        "if close > u\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    d = json.loads(glue.transpile_json(src))
    assert d["ok"] is True
    assert [e for e in d["diagnostics"]
            if e["message"].startswith("ta.vwap anchor")] == []
