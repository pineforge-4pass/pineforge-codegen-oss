"""Per-call-site bodies of a callable read the script variables it does not bind.

A callable that reads a script variable through history gets one body per call
site (``tests/test_e2e_function_global_history.py``). Two consequences of that
cloning are pinned here:

* A call-site body's var remap lists every callable's history and ``var``
  members, so nested instances can compose them. A read of a script variable
  (also before a same-named local or loop binder shadows it), of a loop binder
  or of a parameter keeps its own name: renaming ``src`` to the unrelated
  ``src_cs1`` (another callable's history parameter, never written) made the
  second body compare ``na``. Callables cloned for TA state had the same
  defect before.
* A callable that became stateful only through such a read used to share one
  body; when a wrapper hands it an int and a float through one variant, it
  keeps that body's typing and warns instead of refusing the script.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile, transpile_full
from tests import _compile as compile_env
from tests.test_runtime_var_initialization import _compile_and_run


def _source(rising: str) -> str:
    # mom1's history parameter shares the script variable's name ``src``;
    # ``prev`` reads the script variable's own history at top level.
    return f'''//@version=6
strategy("clone reads the script variable")
src = input.source(close, "Source")
prev = src[1]
mom1(src) => src - src[1]
rising2() => {rising}
m = mom1(close)
var int firstHits = 0
var int secondHits = 0
if rising2()
    firstHits += 1
if rising2()
    secondHits += 1
'''


HISTORY_READER = _source("src > src[1] and src[1] > src[2]")
TA_READER = _source("ta.sma(close, 2) > 0 and src > open")

_DRIVER = r'''
#include <iostream>
int main() {
    Bar bars[8];
    for (int i = 0; i < 8; ++i) {
        double close = 100.0 + 2.0 * i;
        bars[i] = Bar{close - 1.0, close, close - 1.0, close, 1.0,
                      1700000000000LL + i * 60000LL};
    }
    GeneratedStrategy strategy;
    strategy.run(bars, 8);
    std::cout << strategy.firstHits << " " << strategy.secondHits << "\n";
}
'''


def _body(cpp: str, name: str) -> str:
    return cpp.split(f"bool {name}() {{", 1)[1].split("\n    }", 1)[0]


def test_every_call_site_body_reads_the_script_variable():
    for source in (HISTORY_READER, TA_READER):
        cpp = transpile(source)
        for body in (_body(cpp, "rising2_cs0"), _body(cpp, "rising2_cs1")):
            assert "src[0]" in body
            assert "src_cs1" not in body


def test_both_call_sites_see_the_rising_source():
    # Closes rise by 2 per bar over 8 bars and open = close - 1: the history
    # reader holds from bar 2 on (6 bars), the TA reader once its 2-bar SMA
    # is warm, from bar 1 (7 bars), at both call sites.
    assert _compile_and_run(transpile(HISTORY_READER) + _DRIVER) == "6 6\n"
    assert _compile_and_run(transpile(TA_READER) + _DRIVER) == "7 7\n"


SHADOWED = {
    # The body reads the script variable, then binds the same name itself.
    "later_local": "src = up ? 1.0 : 0.0\n    src > 0",
    "later_loop": ("float acc = 0.0\n    for src = 0 to 2\n"
                   "        acc += src\n    up and acc > 0"),
}


def _shadowed(tail: str) -> str:
    return _source("up = src > src[1]").replace(
        "rising2() => up = src > src[1]\n",
        f"rising2() =>\n    up = src > src[1]\n    {tail}\n",
    )


def test_a_later_same_named_binding_keeps_the_script_variable_read():
    for label, tail in SHADOWED.items():
        cpp = transpile(_shadowed(tail))
        for name in ("rising2_cs0", "rising2_cs1"):
            body = _body(cpp, name)
            up = next(line for line in body.splitlines() if "bool up =" in line)
            assert "src[0]" in up and "src_cs1" not in up, (label, name)
        if label == "later_loop":
            assert "acc += src;" in _body(cpp, "rising2_cs1")
        # up holds from bar 1 on: 7 of the 8 rising bars, at both call sites.
        assert _compile_and_run(cpp + _DRIVER) == "7 7\n", label


def test_a_script_udt_field_read_keeps_the_script_object():
    """A field read's receiver resolves like any other identifier read."""
    source = '''//@version=6
strategy("clone reads the script object")
type P
    float v = 1.0
var P obj = P.new()
var float gv = 0.0
gv := bar_index
hobj(obj) => obj - obj[1]
f() => obj.v + gv[1]
x1 = f()
x2 = f()
plot(x1 + x2 + hobj(close))
'''
    cpp = transpile(source)
    for name in ("f_cs0", "f_cs1"):
        body = cpp.split(f"double {name}() {{", 1)[1].split("\n    }", 1)[0]
        assert "_pf_udt_P.read(obj).v" in body, name
    compile_env.compile_cpp(cpp, label="clone-script-udt-field")


def test_shared_variant_typing_warns_instead_of_refusing():
    source = '''//@version=6
strategy("shared variant typing")
var float gv = 0.0
gv := bar_index
f(x) => x + gv[1]
g(y) => f(y)
a = g(1)
b = g(1.5)
c = f(2)
d = f(close)
'''
    result = transpile_full(source)
    warnings = [d.message for d in result["diagnostics"]
                if "Untyped parameter 'x' of callable 'f'" in d.message]
    assert warnings == [
        "Untyped parameter 'x' of callable 'f' receives int and float through "
        "calls that share one written-call variant (cs0); PineForge types it "
        "int, so the other argument is converted. Declare the parameter type "
        "to choose it."
    ]
    assert re.search(r"double f_cs2\(double x\)", result["cpp"])
    compile_env.compile_cpp(result["cpp"], label="shared-variant-typing")
