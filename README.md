# pineforge-codegen

> PineScript v6 → C++ transpiler that emits against the [pineforge-engine](https://github.com/pineforge-4pass/pineforge-engine) runtime.

[![PyPI](https://img.shields.io/pypi/v/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![Python](https://img.shields.io/pypi/pyversions/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange.svg)](LICENSE)
[![Personal use](https://img.shields.io/badge/personal%20trading-free-22c55e.svg)](#license)

A pure-Python library that turns a PineScript v6 strategy into a complete C++
source file you can compile against the [`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)
runtime — a deterministic native backtester validated **trade-for-trade against
TradingView** (231/232 corpus parity).

It is **source-available and free for personal trading** — research, backtest,
and trade your own account with your own capital at no cost. See
[License](#license) for the line between personal and commercial use.

- **Pure Python, zero runtime dependencies** — one function, `transpile()`.
- **Fails loud, never silent** — a support checker rejects Pine the engine can't
  faithfully run *before* codegen, with a `file:line:col` error. You never get
  silently-wrong C++.
- First complete PineScript v6 → C++ transpiler with a real support checker (to
  our knowledge).

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

The output `#include`s `<pineforge/engine.hpp>`, `<pineforge/ta.hpp>`, … and
compiles into a `.so` exposing the engine's documented C-ABI.

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
`pineforge_codegen.errors.CompileError` on any unsupported construct or syntax
error.

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

`check_support=False` bypasses the gate (intended only for tests of legacy
fixtures — it can produce C++ the engine won't accept):

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
tokens, the AST, or the analyzer context:

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

## Compile & run against the engine

The emitted C++ targets the C-ABI in `<pineforge/pineforge.h>`. To build and run
a strategy:

```bash
# Get the runtime (Apache-2.0) next to this repo
git clone https://github.com/pineforge-4pass/pineforge-engine.git
```

Follow the engine's [`tutorial/`](https://github.com/pineforge-4pass/pineforge-engine/tree/main/tutorial)
to build `libpineforge.a`, compile your transpiled `.cpp` into a strategy `.so`,
feed it OHLCV, and read back the closed-trade list. The codegen version must
target a matching engine ABI (see [`VERSION`](VERSION)).

Prefer no local build? A hosted transpile API + MCP server is available so AI
agents can transpile and backtest for you — see <https://www.pineforge.dev>.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

The pure-transpiler suite is fast (< 1 s) and has no native dependencies — it
checks token streams, parse trees, analyzer output, and canonical C++ strings
without invoking a C++ compiler.

Opt-in compile checks (`tests/test_compile_smoke.py`, `tests/test_compile_corpus.py`)
run `g++ -fsyntax-only` on transpiled C++ against the engine headers. They
auto-detect a sibling `../pineforge-engine` checkout, or set the path explicitly:

```bash
export PINEFORGE_ENGINE_INCLUDE=/path/to/pineforge-engine/include
pytest
```

Without an engine checkout these tests skip cleanly, so CI stays green.

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
