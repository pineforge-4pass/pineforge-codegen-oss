# pineforge-codegen

> PineScript v6 → C++ transpiler that emits against the [pineforge-engine](https://github.com/pineforge-4pass/pineforge-engine) runtime.

[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange.svg)](LICENSE)
[![Personal use](https://img.shields.io/badge/personal%20trading-free-22c55e.svg)](#license)
[![Engine](https://img.shields.io/badge/runtime-pineforge--engine%20(Apache--2.0)-blue.svg)](https://github.com/pineforge-4pass/pineforge-engine)

Write a strategy once in PineScript v6, transpile it to C++, and run it on a
deterministic native runtime that is validated **trade-for-trade against
TradingView** (231/232 corpus parity). This repo is the transpiler: lexer →
parser → analyzer → support checker → C++ codegen.

It is **source-available and free for personal trading** — research, backtest,
and trade your own account with your own capital at no cost. See
[License](#license) for the line between personal and commercial use.

The PineForge stack:

- **[`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)** (Apache-2.0, open source) — the C++ runtime + 232-strategy parity corpus + benchmarks.
- **`pineforge-codegen`** (PolyForm Noncommercial, this repo) — the PineScript v6 → C++ transpiler.
- **`pineforge-backend`** (proprietary, separate repo) — the hosted transpile API, license server, and optimisation services.

---

## Two ways to use it

### 1. No build step — hosted API + MCP (easiest)

Let an AI agent (Claude Code, Cursor, Claude Desktop, any MCP client) write,
run, and tune strategies for you. Pine is transpiled by the hosted API; the
engine runs locally in Docker so your OHLCV never leaves your machine.

```bash
# Claude Code — one command. Get a free API key at https://www.pineforge.dev
claude mcp add pineforge-codegen \
  --transport stdio \
  --env PINEFORGE_API_KEY=pf_... \
  -- npx -y @pineforge/codegen-mcp
```

Full agent workflow, other MCP clients, and the Docker run loop are documented
in the [engine README](https://github.com/pineforge-4pass/pineforge-engine#backtest-pinescript-with-ai--no-build-step).
Live playground: <https://codegen.pineforge.dev>.

### 2. From source — this repo (free for personal trading)

Run the transpiler yourself, no API key, source never leaves your machine.

**Install**

```bash
git clone https://github.com/pineforge-4pass/pineforge-codegen-oss.git
cd pineforge-codegen-oss
pip install -e .          # pure-Python, zero runtime deps
```

**Transpile Pine → C++**

```python
from pineforge_codegen import transpile

pine_source = """
//@version=6
strategy("SMA cross", overlay=true)
fast = ta.sma(close, 10)
slow = ta.sma(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("long")
"""

cpp_source = transpile(pine_source)   # complete C++ source string
print(cpp_source)
```

`transpile(pine_source, *, check_support=True, filename="<input>")` runs:

1. `// @pf-trace` pragma extraction
2. Lex → Parse → AST
3. Support check — rejects Pine the engine cannot faithfully run (`indicator()`,
   prohibited vars like `bar_index`, disallowed `request.security` params, …)
   *before* codegen, so you never get silently-broken C++. Raises `CompileError`
   with a `file:line:col` location.
4. Analyze — type inference, scope resolution, symbol table
5. Codegen → returns a complete C++ source string

**Compile + run**

The emitted C++ includes `<pineforge/engine.hpp>`, `<pineforge/ta.hpp>`, etc.
and builds into a `.so` exposing the documented C-ABI in
`<pineforge/pineforge.h>`. To compile and run it against bars:

```bash
# Get the runtime (Apache-2.0) next to this repo
git clone https://github.com/pineforge-4pass/pineforge-engine.git ../pineforge-engine
```

Follow the engine's [`tutorial/`](https://github.com/pineforge-4pass/pineforge-engine/tree/main/tutorial)
to build `libpineforge.a`, compile your transpiled `.cpp` into a strategy `.so`,
load it, feed OHLCV, and read back the closed-trade list. The codegen version
must target a matching `pineforge-engine` ABI (see [`VERSION`](VERSION)).

---

### Self-host the transpile server (Docker)

Each release publishes a multi-arch image exposing `POST /transpile` and
`GET /healthz` — run the transpiler as a local HTTP service, no Python setup:

```bash
docker run --rm -p 8080:8080 ghcr.io/pineforge-4pass/pineforge-codegen-oss:latest

# In another shell:
curl -s localhost:8080/healthz
printf '//@version=6\nstrategy("t")\nx = ta.sma(close, 10)\n' \
  | curl -s -X POST --data-binary @- localhost:8080/transpile
```

## What it does (pipeline)

```
pineforge_codegen/
├── lexer.py / tokens.py        Token stream
├── parser.py / ast_nodes.py    Pine v6 AST
├── analyzer/                   Type inference + scope resolution + symbol table
├── codegen/                    AST → C++ source emitter (visitor per node kind)
├── pragmas.py                  // @pf-trace pragma extraction
├── support_checker.py          Reject unsupported Pine constructs before codegen
├── signatures.py               Pine builtin signatures (typed parameter table)
├── tv_input_choices.py         input.string options metadata
├── symbols.py                  PineType / Symbol / Scope / SymbolTable
└── errors.py                   CompileError + SourceLocation
tests/
└── pytest suite                Pure-transpiler unit tests (no native deps)
```

This is — to our knowledge — the first complete PineScript v6 → C++ transpiler
with a real support checker that rejects unsupported features before codegen
instead of emitting broken C++.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

The pure-transpiler suite is fast (< 1 s) and has no native dependencies — it
verifies token streams, parse trees, analyzer output, and canonical C++ source
strings without invoking a C++ compiler.

### Opt-in: compile-only tests against the engine headers

`tests/test_compile_smoke.py` and `tests/test_compile_corpus.py` run
`g++ -fsyntax-only` on transpiled C++ against the public `pineforge-engine`
headers — linker- and runtime-free; the contract is "the emitted C++ is
structurally and type-wise valid against the runtime ABI".

When `pineforge-engine` is checked out as a sibling (`../pineforge-engine`),
`tests/_compile.py` auto-detects it. Otherwise point at it explicitly:

```bash
export PINEFORGE_ENGINE_INCLUDE=/path/to/pineforge-engine/include
# Optional Eigen override if /opt/homebrew/include or /usr/local/include has none:
export PINEFORGE_EIGEN_INCLUDE=/opt/homebrew/include/eigen3
pytest
```

Without these env vars the relevant tests skip cleanly (naming the missing
knob), so CI without an engine checkout stays green.

## Hosted service

The transpiler is also offered as a hosted API + MCP server for users (and AI
agents) who want zero build. The engine runtime is free and open source
(Apache-2.0). Sign up and see current terms at <https://www.pineforge.dev>.

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
