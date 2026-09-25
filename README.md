# pineforge-codegen

> PineScript v6 → C++ transpiler that emits against the [pineforge-engine](https://github.com/pineforge-4pass/pineforge-engine) runtime.

[![PyPI](https://img.shields.io/pypi/v/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![Python](https://img.shields.io/pypi/pyversions/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange.svg)](LICENSE)
[![Personal use](https://img.shields.io/badge/personal%20trading-free-22c55e.svg)](#license)

A pure-Python library that turns a PineScript v6 strategy into a complete C++
source file you can compile against the [`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)
runtime. See the engine's [current validation scoreboard](https://github.com/pineforge-4pass/pineforge-engine#validation-scoreboard)
for the complete TradingView comparison, pinned results, and grading details.

It is **source-available and free for personal trading** — research, backtest,
and trade your own account with your own capital at no cost. See
[License](#license) for the line between personal and commercial use.

See the [changelog](CHANGELOG.md) for the planned 1.0 release notes and
release-note policy.

- **Pure Python, zero runtime dependencies** — `transpile()` and
  `transpile_full()` are the supported Python entry points.
- **Located diagnostics** — the support checker rejects unsupported Pine
  before codegen, while `transpile_full()` returns warnings for supported
  scripts with documented approximations.
- First complete PineScript v6 → C++ transpiler with a real support checker (to
  our knowledge).

## What this owns

This repository owns **Pine → C++ translation only**. It turns a Pine v6 script
into a `GeneratedStrategy`: the indicator math plus the `strategy.entry` /
`exit` / `close` / … calls, emitted on the engine's
`pineforge::source::PineStrategyHost` with code that attaches the engine's Pine
execution adapter.

It does **not** own execution semantics. Order lifecycle, bracket legs,
fill-price and slippage rules, `process_orders_on_close` / `calc_on_order_fills`,
margin revival and trail/stop behaviour — everything TradingView parity depends
on at run time — live in the engine's source-adapter runtime
([`src/source/`](https://github.com/pineforge-4pass/pineforge-engine/tree/main/src/source)),
which maps them onto the engine's Pine-agnostic kernel. See the engine's
[architecture notes](https://github.com/pineforge-4pass/pineforge-engine#architecture-kernel-vs-parity).

---

## Install

```bash
pip install pineforge-codegen
```

Requires Python ≥ 3.11. No runtime dependencies.

From source (development / contributing):

```bash
git clone https://github.com/pineforge-4pass/pineforge-codegen-oss.git
cd pineforge-codegen-oss
pip install -e ".[dev]"
```

## Quick start

```python
from pineforge_codegen import transpile

pine = """
//@version=6
strategy("SMA cross", overlay=true)
fast = ta.sma(close, 10)
slow = ta.sma(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("long")
"""

cpp = transpile(pine)
print(cpp)          # complete C++ source string
```

The output `#include`s `<pineforge/source/pine_strategy_host.hpp>`, `<pineforge/ta.hpp>`, …; its `GeneratedStrategy` derives from `pineforge::source::PineStrategyHost` and compiles into a `.so` exposing the engine's documented C-ABI.

## Usage

### The `transpile()` function

```python
transpile(
    pine_source: str,
    *,
    check_support: bool = True,   # run the support checker before codegen
    filename: str = "<input>",    # name used in error locations
) -> str
```

Returns the generated C++ source as a string. Raises
`pineforge_codegen.errors.CompileError` on a rejected construct, syntax
error, or input limit. It does not return nonfatal warnings; use
`transpile_full()` to inspect them.

### The `transpile_full()` function

```python
transpile_full(
    pine_source: str,
    *,
    check_support: bool = True,
    filename: str = "<input>",
) -> dict
```

Returns `{"cpp": str, "inputs": list[dict], "strategyParams": dict,
"diagnostics": list[Diagnostic]}` on success. `inputs` is the input manifest;
its `title` is the actual override key. `diagnostics` contains nonfatal
warnings. A rejected script raises `CompileError` with its diagnostics. The
Pyodide package ships `gate/glue.py`'s `transpile_json(source) -> str`, whose
JSON success and error envelopes carry the same manifest and warnings. See the
[1.0 public contract](docs/PUBLIC_CONTRACT.md) for the exact fields, severity
values, and input key rules. There is no installed CLI or exit-code contract.

### Transpile a file to a `.cpp`

```python
from pathlib import Path
from pineforge_codegen import transpile

pine = Path("strategy.pine")
cpp = transpile(pine.read_text(), filename=pine.name)   # filename → better errors
Path("strategy.generated.cpp").write_text(cpp)
```

### Handle unsupported features

The support checker raises a `CompileError` with the exact source location
instead of emitting broken C++:

```python
from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

try:
    transpile('//@version=6\nindicator("x")\n')
except CompileError as e:
    print(e)
    # <input>:2:1: indicator() declarations are not supported; PineForge runs strategies only.

try:
    transpile('//@version=6\nstrategy("x")\n'
              'x = request.financial("AAPL", "REV", "FQ")\n')
except CompileError as e:
    print(e)
    # <input>:3:22: request.financial(...) is not supported.
```

Pass `filename=` so the location points back at the user's file:

```python
transpile(src, filename="my_strategy.pine")
# raises e.g.  my_strategy.pine:12:5: ...
```

### Skip the support checker

`check_support=False` on either Python function is **experimental**. It
bypasses the gate and can produce C++ the engine will not accept or execute
faithfully:

```python
cpp = transpile(src, check_support=False)
```

### Trace intermediate expressions (`@pf-trace`)

A `// @pf-trace name=expr` comment makes the engine emit `name`'s per-bar value
in the backtest report — useful for debugging parity against TradingView:

```python
pine = """
//@version=6
strategy("traced")
// @pf-trace rsi=ta.rsi(close, 14)
e = ta.ema(close, 20)
if close > e
    strategy.entry("L", strategy.long)
"""
cpp = transpile(pine)   # emitted on_bar tail records `rsi` each bar
```

### Advanced: run the pipeline stages directly

`transpile()` is a thin wrapper over five passes. Drive them yourself to inspect
tokens, the AST, or the analyzer context. These classes are outside the
[1.0 public contract](docs/PUBLIC_CONTRACT.md):

```python
from pineforge_codegen import (
    Lexer, Parser, Analyzer, CodeGen,
    extract_pf_trace_pragmas, check_support_or_raise,
)

src = open("strategy.pine").read()
pragmas = extract_pf_trace_pragmas(src)
tokens  = Lexer(src, filename="strategy.pine").tokenize()
ast     = Parser(tokens, source=src, filename="strategy.pine").parse()
check_support_or_raise(ast, filename="strategy.pine")
ctx     = Analyzer(ast, filename="strategy.pine").analyze()
ctx.pf_trace_pragmas = pragmas
cpp     = CodeGen(ctx).generate()
```

## Limits

Untrusted source has deterministic size and nesting limits. Exceeding one
raises a `CompileError` with a Pine `file:line:col` location; it does not
return partial C++. `check_support=False` does not bypass these limits.

| Limit | Maximum | Largest in the 314 public corpus scripts and 277 gate fixtures |
| --- | ---: | ---: |
| Source size | 131,072 characters | 9,869 |
| Logical statement size | 256 tokens or 4,096 characters | 64 tokens, 420 characters |
| Parenthesis/bracket depth | 32 | 3 |
| Indented block depth | 32 | 4 |
| Parsed AST depth | 64 nodes | 10 |
| Parsed statements | 1,024 | 140 |

A logical statement includes a continued expression across lines. A
cooperative 30-second elapsed-time guard also checks the lexer, parser,
analyzer, code generator, and the boundaries between passes. It returns a
located `CompileError` if a structurally valid input still takes too long.
Numeric literals outside the generated C++ range also raise a located error.

## How it works

`transpile()` runs five passes, in order:

```
pine source
  │
  ├─ 1. extract_pf_trace_pragmas   // @pf-trace comments pulled out first
  ├─ 2. Lexer → Parser             token stream → Pine v6 AST
  ├─ 3. support_checker            reject anything the engine can't run faithfully
  ├─ 4. Analyzer                   type inference, scope resolution, TA bookkeeping
  └─ 5. CodeGen                    → C++ source string
```

The emitted `GeneratedStrategy` does not execute orders itself: its
`strategy.*` calls go to the engine's Pine execution adapter, which it attaches
in its constructor.

## Engine pairing

A released codegen `X.Y.Z` is supported only with engine tag `vX.Y.Z`, using
that release's generated headers and static library. Prereleases match
exactly: codegen `1.0.0-rc.1` requires engine `v1.0.0-rc.1`. On every pair
change, regenerate the strategy C++ from Pine and relink it against that
engine release's headers and `libpineforge.a`. `PF_ABI_VERSION` equality alone
is insufficient; it does not guarantee the C++ source layout or behavior.
Development branches can test paired in-progress commits, but they are not
supported cross-version release pairs. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the setup and checks.

## Compile & run against the engine

The emitted C++ targets the C-ABI in `<pineforge/pineforge.h>`. To build and run
a strategy:

```bash
# Get the runtime (Apache-2.0) next to this repo
git clone https://github.com/pineforge-4pass/pineforge-engine.git
```

Follow the engine's [`tutorial/`](https://github.com/pineforge-4pass/pineforge-engine/tree/main/tutorial)
to build `libpineforge.a`, compile your transpiled `.cpp` into a strategy `.so`,
feed it OHLCV, and read back the closed-trade list. Use the exact
[engine pair](#engine-pairing) for the codegen release.

Generated strategies reset persistent Pine state before each new batch or stream
warmup through the engine's script-run preparation hook. Input settings survive a
new run, and ticks within one stream preserve accumulated state. Regenerate the
strategy C++ and rebuild compiled modules with matching engine headers and library
to use this lifecycle; replacing only the runtime archive does not retrofit
already compiled modules.

Generated constructors do not configure order behavior from the presence of
`strategy.close` or `strategy.close_all` in the source. Regenerate older C++
that assigns `script_has_strategy_close_` before compiling with engine headers
that remove this obsolete member. Reachable close commands keep their ordinary
runtime lowering.

Prefer no local build? A hosted transpile API + MCP server is available so AI
agents can transpile and backtest for you — see <https://www.pineforge.dev>.

## Running tests

The full release check needs matching engine headers, a generated
`pineforge/version.h`, Eigen, the built runtime, and the public engine corpus.
Run `python -m pytest -ra` and then
`python -m pytest -ra tests/test_compile_corpus.py` with those paths set; check
the skip reasons so compiler and runtime coverage actually ran. The full
Pyodide parity gate and npm audit are also required. The exact setup and
commands are in [CONTRIBUTING.md](CONTRIBUTING.md#required-checks); use
`python -m pytest --collect-only -q` for the current collection count.

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE), with
two supplemental terms (the `LICENSE` file is the controlling text):

- **Personal Trading exception** — free to research, backtest, and trade for your
  own account with your own capital.
- **Commercial use** — companies, funds, managing third-party capital, embedding
  in a product, or operating a hosted / public-facing service requires a
  commercial license.

Competing hosted services are not permitted under the noncommercial terms. This
is source-available, not OSI open source.

### Buying a commercial license

Commercial licenses are available — flexible terms for funds, products, and
hosted/embedded use. Email **luis@4pass.com.tw** with your use case for a quote.

## Explicit Pine execution attachment

The Pine execution adapter is the engine's full Pine execution runtime
(`PineExecutionAdapter` and `PineStrategyHost` in the engine's `src/source/`):
order lifecycle, bracket legs, fill-price and slippage rules, POOC /
`calc_on_order_fills`, margin revival, trail/stop semantics, the intraday caps
and the retained-parent priority rule. Codegen's job is to emit the strategy
that attaches it and the `strategy.*` calls it executes, against a matching
engine ABI.

Generated constructors configure their `PineStrategyConfig` before host
metadata and select `attach_pine_execution_adapter()` when
`PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1` is available.
A guarded `enable_pine_intraday_cap()` fallback supports existing cap-only
engines; engines with neither capability keep their established defaults.
Risk statements remain in source execution order. This bridge requires matching
engine headers and runtime; it is not cross-version C++ binary compatibility.

Regenerate old cap-only generated C++ before using the new engine for Pine
execution. Such old source may still compile but does not attach the priority
rule, and metadata cannot silently restore it. Rebuild all modules against the
new matching C++ layout (`engine_script_run_v19`); old fingerprint versions are
not comparable. The extraction preserves Pine policy under explicit attachment;
it does not implement the generic native child-activation scheduler or prove
campaign neutrality. Compile-only corpus checks do not run Pine backtests.
