## How to add a new Pine v6 function

Worked example: adding hypothetical `ta.foo(source, length)`.

1. **Signature** — `signatures.py`, with TradingView's parameter names
  and required/optional split (a default marks an optional parameter): the
  support checker binds every call to it and refuses what does not bind.
  ```python
   _ta("foo", _sig([("source", F), ("length", I)]))
  ```
2. **Analyzer dispatch** — `analyzer/tables.py`:
  ```python
   TA_CLASS_MAP["foo"] = "ta::Foo"
   TA_PERIOD_ARG["foo"] = 1            # length-arg index
  ```
3. **Compute routing** — `analyzer/tables.py` (the table that routes;
  `codegen/tables.py` keeps a descriptive copy):
  ```python
   TA_COMPUTE_ARGS["foo"] = [0]         # which positional args go to .compute()
  ```
  Without an entry every non-constructor argument reaches `.compute()`: add
  one whenever `ta::Foo::compute` does not take every such Pine parameter,
  and refuse (support checker) a parameter value the class cannot compute.
4. **Support whitelist** — `support_checker.py`:
  `SUPPORTED_TA` is derived from `TA_CLASS_MAP` automatically; nothing
   to do.
5. **Surface lock** — `tests/test_official_surface.py`:
  add `"foo"` to `OFFICIAL_TA` (if you skipped this step, the test
   still passes for `ta.`* because it's in `test_ta_official_surface.py`
   — keep both files in sync).
6. **Smoke** — `tests/test_ta_official_surface.py::TA_SMOKE_CASES`:
  add `"foo": ("x = ta.foo(close, 5)", "ta::Foo")`.
7. **Engine** — `ta::Foo` class must exist in
  `pineforge-engine/include/pineforge/ta.hpp` with both `compute()`
   and `recompute()` methods. If it doesn't, the addition belongs in
   the engine repo first.
8. Run `pytest` (passes without engine env) and
  `PINEFORGE_ENGINE_INCLUDE=… pytest` (passes with the engine
   compile sweep) before opening a PR.

