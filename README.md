# pineforge-codegen

> PineScript v6 → C++ transpiler that emits against the [pineforge-engine](https://github.com/pineforge-4pass/pineforge-engine) runtime.

[![PyPI](https://img.shields.io/pypi/v/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![Python](https://img.shields.io/pypi/pyversions/pineforge-codegen.svg)](https://pypi.org/project/pineforge-codegen/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange.svg)](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/LICENSE)
[![Personal use](https://img.shields.io/badge/personal%20trading-free-22c55e.svg)](#license)

A pure-Python library that turns a PineScript v6 strategy into a complete C++
source file you can compile against the [`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine)
runtime.

**Measured <!-- pf:scoreboard.date -->2026-10-03<!-- /pf -->** on main engine <!-- pf:scoreboard.engineCommit|short-code -->`700c5d24`<!-- /pf --> with codegen-oss <!-- pf:scoreboard.codegenCommit|short-code -->`13b9ccfd`<!-- /pf --> (baseline <!-- pf:scoreboard.id|code -->`pineforge-parity-baseline-20261003-engine-700c5d24`<!-- /pf -->, snapshot <!-- pf:scoreboard.snapshotSha256|short-code -->`3ee846c5`<!-- /pf -->): <!-- pf:scoreboard.excellent|int -->7,949<!-- /pf --> of <!-- pf:scoreboard.graded|int -->7,989<!-- /pf --> TradingView probes
graded excellent and <!-- pf:scoreboard.strong|int -->40<!-- /pf --> strong, with <!-- pf:scoreboard.belowStrong|int -->0<!-- /pf --> below strong;
<!-- pf:scoreboard.anomaliesExcluded|int -->17<!-- /pf --> more probes are held out as TradingView-side anomalies.
A probe is a strategy exported from TradingView with its trade list and replayed
trade for trade on the same bars.

Release **1.0.1 still grades <!-- pf:releases[1.0.1].scoreboard.excellent|int -->7,905<!-- /pf --> excellent / <!-- pf:releases[1.0.1].scoreboard.strong|int -->84<!-- /pf --> strong until the next release**, on <!-- pf:releases[1.0.1].scoreboard.graded|int -->7,989<!-- /pf --> probes (baseline <!-- pf:releases[1.0.1].scoreboard.id|code -->`pineforge-parity-baseline-20261001-codegen-67892cda`<!-- /pf -->, <!-- pf:releases[1.0.1].scoreboard.date -->2026-10-01<!-- /pf -->). A main scoreboard advance does not change release results.

The quantities above render from the public [facts tokens](https://github.com/pineforge-4pass/pineforge-release/blob/main/facts/facts.json). Maintain them with `lab facts render --repo . --facts <local facts file or pinned raw URL>`; `lab facts check` with the same inputs reports drift. Grades are registry-derived; the authored-script and closed-trade inventory is explicitly sourced to a historical public README for the identical population, not to registry row or slug totals.

The engine's [validation scoreboard](https://github.com/pineforge-4pass/pineforge-engine#validation-scoreboard)
describes how a probe is graded.

It is **source-available and free for personal trading** — research, backtest,
and trade your own account with your own capital at no cost. See
[License](#license) for the line between personal and commercial use.

See the [changelog](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CHANGELOG.md)
for the changes in each release from 1.0.0 on and the release-note policy.

- **Pure Python, zero runtime dependencies** — `transpile()` and
  `transpile_full()` are the supported Python entry points.
- **Located diagnostics** — the support checker rejects unsupported Pine with
  a `file:line:col` location before any C++ is emitted, and
  `transpile_full()` returns warnings for supported scripts with documented
  approximations.

## Releases and this README

<!-- Release lane: before a release is tagged, add it to the Engine pairing
table and update every line that names `1.0.1` or `v1.0.1` as the current
release or pair (this section's version and date, the baseline paragraph at
the top, the engine `src/source/` link, the `@pf-trace` note, the clone
command and its example output, "This section describes …", the timing note)
and every "on `main`" marker; lines saying what changed in a release stay.
PyPI shows the README as it is at the tag. The Install note and the
hosted-server line name no version. -->

This README ships with each release as its package description on PyPI
(`pineforge-codegen`); releases from 0.7.0 on are also on npm as
`@pineforge/codegen-pyodide`. It describes 1.0.1 (2026-10-02) and what changed
since 0.10.4. The
[PyPI release history](https://pypi.org/project/pineforge-codegen/#history)
lists every release; the
[changelog](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CHANGELOG.md)
covers 1.0.0 on and links the notes of earlier releases. A source install
reports the version in `VERSION`, which the release workflow sets when it tags
a release.

### Upgrading from 0.10.4

0.10.4 (2026-09-06) was the last 0.x release. Its README is at the
[`v0.10.4` tag](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/v0.10.4/README.md).
This README marks where 0.10.4 is known to differ from 1.0.0; the changelog is
the complete list. The differences a user meets first:

- 0.10.4's C++ derives `GeneratedStrategy` from `BacktestEngine` in
  `<pineforge/engine.hpp>`; 1.0.0's derives it from
  `pineforge::source::PineStrategyHost`. The two need different engines (see
  [Engine pairing](#engine-pairing)).
- 0.10.4 has no `libraries=` argument, no `diagnostics` key in
  `transpile_full()`'s result and none of the [input limits](#limits).
- 0.10.4 recovers from some syntax errors and still returns C++; 1.0.0 raises
  a located `CompileError` instead.
- Many scripts that 0.10.4 refuses transpile with 1.0.0, and some lower
  differently; the [changelog](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CHANGELOG.md)
  lists them.

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
([`src/source/`](https://github.com/pineforge-4pass/pineforge-engine/tree/v1.0.1/src/source)
in engine `v1.0.1`), which maps them onto the engine's Pine-agnostic kernel. See
the engine's [architecture notes](https://github.com/pineforge-4pass/pineforge-engine#architecture-kernel-vs-parity).

---

## Install

```bash
pip install pineforge-codegen
```

This installs the latest release. Requires Python ≥ 3.11. No runtime
dependencies.

To get `main`, install from source (this is also the development setup):

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

The output `#include`s `<pineforge/source/pine_strategy_host.hpp>`,
`<pineforge/ta.hpp>`, …; its `GeneratedStrategy` derives from
`pineforge::source::PineStrategyHost` and compiles into a `.so` exposing the
engine's documented C-ABI. 0.10.4's output includes `<pineforge/engine.hpp>`
and derives from `BacktestEngine`. [Compile & run against the engine](#compile--run-against-the-engine)
builds and runs this strategy.

## Usage

### The `transpile()` function

```python
transpile(
    pine_source: str,
    *,
    check_support: bool = True,   # run the support checker before codegen
    filename: str = "<input>",    # name used in error locations
    libraries: Mapping[str, str] | None = None,   # imported Pine libraries (new in 1.0.0)
) -> str
```

Returns the generated C++ source as a string. Raises
`pineforge_codegen.errors.CompileError` on a rejected construct and, since 1.0.0,
on a syntax error or an input limit (0.10.4 recovers from some syntax errors
and has no input limits). It does not return nonfatal warnings; use
`transpile_full()` to inspect them.

`libraries` is new in 1.0.0. It maps an import path to the library's
source (`{"user/name/version": source_text}`), and each import the script uses
is inlined from it. With `libraries=None` (the default) the sources are read
through the script's own requests manifest when the environment names one
(`$PINEFORGE_PINE_LIBRARIES` with `$PINEFORGE_REQUESTS_ROOT`); otherwise an
import is refused by name, as in 0.10.4. Since 1.0.0, an import whose alias is
`ta`, `math` or `str` and that names only that namespace's built-ins needs no
source.

### The `transpile_full()` function

```python
transpile_full(
    pine_source: str,
    *,
    check_support: bool = True,
    filename: str = "<input>",
    libraries: Mapping[str, str] | None = None,   # new in 1.0.0
) -> dict
```

It returns `{"cpp": str, "inputs": list[dict], "strategyParams": dict,
"diagnostics": list[Diagnostic]}` on success (0.10.4 returns the first three
keys). `inputs` is the input manifest; its `title` is the actual override key.
`diagnostics` contains nonfatal warnings. A rejected script raises
`CompileError` with its diagnostics. The Pyodide package ships `gate/glue.py`'s
`transpile_json(source) -> str`, whose JSON success and error envelopes carry
the same manifest and, since 1.0.0, the same warnings. See the
[1.0 public contract](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/docs/PUBLIC_CONTRACT.md)
for the exact fields, severity values, and input key rules. There is no
installed CLI or exit-code contract.

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
              'x = request.seed("seed_crypto_santiment", "BTC_SENTIMENT_POSITIVE_TOTAL", close)\n')
except CompileError as e:
    print(e)
    # <input>:3:17: request.seed(...) is not supported.
```

Pass `filename=` so the location points back at the user's file:

```python
transpile(src, filename="my_strategy.pine")
# raises e.g.  my_strategy.pine:12:5: ...
```

Not every request for outside data is refused. A
`request.financial()` whose value reaches only plots, alerts, tables or logs
transpiles with a warning and reads `na`; 0.10.4 refuses it. The
[changelog](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CHANGELOG.md)
lists what 1.0.0 reads, warns about or refuses for such requests.

### Skip the support checker

`check_support=False` on either Python function is **experimental**. It
bypasses the gate and can produce C++ the engine will not accept or execute
faithfully:

```python
from pathlib import Path
from pineforge_codegen import transpile

src = Path("strategy.pine").read_text()
cpp = transpile(src, check_support=False)
```

### Trace intermediate expressions (`@pf-trace`)

A `// @pf-trace name=expr` comment, alone on its line, makes the compiled
strategy record `name`'s value on every bar when tracing is enabled with the
engine's `strategy_set_trace_enabled()`; the values come back in the report's
`trace` array (`pf_report_t::trace`). That is useful for debugging parity
against TradingView:

```python
from pineforge_codegen import transpile

pine = """
//@version=6
strategy("traced")
// @pf-trace rsi=r
r = ta.rsi(close, 14)
e = ta.ema(close, 20)
if close > e
    strategy.entry("L", strategy.long)
"""
cpp = transpile(pine)   # emitted on_bar tail records `r` each bar
```

Trace a script variable or an expression over script variables and bar
fields, such as `// @pf-trace gap=close - e` or
`// @pf-trace body=math.abs(close - open)`. A `ta.*` call written in the
pragma itself is not computed: `// @pf-trace rsi=ta.rsi(close, 14)` records
`na` on every bar, and its C++ carries an `/* unsupported: ta.rsi */` marker.
0.10.4, 1.0.0 and 1.0.1 all behave this way.

### Advanced: run the pipeline stages directly

Drive the main stages yourself to inspect tokens, the AST, or the analyzer
context. These classes are outside the
[1.0 public contract](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/docs/PUBLIC_CONTRACT.md):

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

This skips the library inlining, the AST rewrites and the reruns that
`transpile()` also performs (see [How it works](#how-it-works)). Its C++ equals
`transpile()`'s only for scripts those passes leave unchanged, such as the
quick-start strategy.

## Limits

These limits are new in 1.0.0; 0.10.4 has none of them. They turn a crash or
a hang on untrusted source into a `CompileError` with a Pine `file:line:col`
location. Where TradingView documents a limit, PineForge's is at least as
large. Exceeding one does not return partial C++, and `check_support=False`
does not bypass them.

| Limit | Maximum | TradingView's documented limit | Largest in the 325 public corpus sources and 277 gate fixtures |
| --- | ---: | --- | ---: |
| Source size | 5,242,880 characters (5 MiB) | Compilation request of at most 5MB | 9,869 characters |
| Nesting depth | 512 levels | None | 10 levels |
| Transpilation time | 120 seconds | Two-minute compilation limit | 0.05 seconds |

The last column was measured on 2026-09-29 with 70c2b4a (the same code as
1.0.0) on CPython 3.14 on an Apple M4 Max, over the engine corpus that engine
35db01c8 pins (as `v1.0.0` and `v1.0.1` do) and this repository's
`tests/gate-corpus`. On 2026-10-02, on CPython 3.14 on an Apple M4 Max, 1.0.1
transpiled each of those sources in at most 0.05 seconds.

Nesting counts brackets, indented blocks, prefix operators, `?:` and
`else if` chains, and the depth of the parsed syntax tree, in which an
operator chain such as `a + b + c` takes one level per operator. It stops
well before Pyodide's stack does, at about 2,000 levels. There is no
statement-count or statement-size limit: TradingView measures a script in
compiled tokens, not source lines. `transpile()` raises Python's recursion
limit to 20,480 frames when it is lower, and never lowers it. The
elapsed-time guard checks the lexer, parser, analyzer and code generator
cooperatively. Numeric literals outside the generated C++ range also raise a
located error.

## How it works

`transpile()` runs these passes, in order:

```
pine source
  │
  ├─ 1. extract_pf_trace_pragmas   // @pf-trace comments pulled out first
  ├─ 2. Lexer → Parser             token stream → Pine v6 AST
  ├─ 3. library inlining           imported Pine libraries inlined into the AST
  ├─ 4. support_checker            reject anything the engine can't run faithfully
  ├─ 5. AST rewrites               requests with no data, request.security contexts,
  │                                bounded TA lengths, builtin keyword arguments
  ├─ 6. Analyzer                   type inference, scope resolution, TA bookkeeping
  └─ 7. CodeGen                    → C++ source string
```

The passes run again, from pass 1, when the generated C++ needs a renamed
block-local declaration or a per-call-site clone of a function that reads
`session.<flag>[k]`; `_generate` in `pineforge_codegen/__init__.py` holds the
loop. 0.10.4 runs passes 1, 2, 4, 6 and 7 once, with only the bounded TA
length rewrite of pass 5.

The emitted `GeneratedStrategy` does not execute orders itself: its
`strategy.*` calls go to the engine's Pine execution adapter, which it attaches
in its constructor.

## Engine pairing

Generated C++ compiles only against the engine it was generated for:

| Codegen | Engine | Status |
| --- | --- | --- |
| 0.10.4 (PyPI, 2026-09-06) | `v0.13.1` | The last 0.x pair, which the `pineforge-release` image `0.1.25` ships. Its C++ does not compile against engine `v1.0.0`. |
| 1.0.0 (PyPI, 2026-09-30) | `v1.0.0` | The pair the `pineforge-release` image `1.0.0` ships. Its C++ needs `pineforge/source/pine_strategy_host.hpp`, which engine `v0.13.1` does not have. |
| 1.0.1 (PyPI, 2026-10-02) | `v1.0.1` | The pair the `pineforge-release` image `1.0.1` ships. Engine `v1.0.1` changes only documentation since `v1.0.0`; regenerate and relink all the same. |
| Later `X.Y.Z` releases | `vX.Y.Z` of the same version | See below. |

On the 0.x line the engine and codegen versions are independent, and the
[`pineforge-release`](https://github.com/pineforge-4pass/pineforge-release)
image records which pair it ships. From 1.0.0 on, a released codegen `X.Y.Z`
is supported only with engine tag `vX.Y.Z`, using that release's generated
headers and static library. Prereleases match exactly: codegen `1.0.0-rc.1`
requires engine `v1.0.0-rc.1`. On every pair change, regenerate the strategy
C++ from Pine and relink it against that engine release's headers and
`libpineforge.a`. `PF_ABI_VERSION` equality alone is insufficient; it does not
guarantee the C++ source layout or behavior. Development branches can test
paired in-progress commits, but they are not supported cross-version release
pairs. See [CONTRIBUTING.md](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CONTRIBUTING.md#engine-pairing)
for the setup and checks.

## Compile & run against the engine

The emitted C++ targets the C-ABI in `<pineforge/pineforge.h>`. The engine's
[`tutorial/`](https://github.com/pineforge-4pass/pineforge-engine/tree/main/tutorial)
builds `libpineforge.a` and a strategy `.so` and runs it on 672 frozen
BTCUSDT 15m bars. To run your transpiled strategy there, save the quick-start
Pine as `strategy.pine`, write `strategy.generated.cpp` with the
[file example](#transpile-a-file-to-a-cpp) above, then from the same directory:

```bash
# 1.0.1 pairs with engine v1.0.1. For 0.10.4 use --branch v0.13.1.
git clone --branch v1.0.1 https://github.com/pineforge-4pass/pineforge-engine.git
cd pineforge-engine
cp ../strategy.generated.cpp tutorial/macd/generated.cpp   # the tutorial's strategy slot
bash tutorial/run.sh    # needs cmake, a C++17 compiler and python3
```

`run.sh` configures CMake once, builds `libpineforge.a` and
`tutorial/macd/strategy.so`, and runs `tutorial/run.py`, which loads the `.so`,
feeds it the bars and reads back the closed trades. For the quick-start SMA
cross, 1.0.1 with engine `v1.0.1` prints:

```
MACD(12,26,9) on BTCUSDT 15m — 672 bars, 2026-04-29 18:15 → 2026-05-06 18:00 UTC
  trades:    9  (6W / 3L, 66.7% win)
  net pnl:   +738.20
  best/worst:+679.22 / -335.02
  max dd:    -788.79
  elapsed:   1.4 ms
```

The `MACD(12,26,9)` label on the first line is fixed text in `run.py`,
whatever strategy the `.so` holds, and `elapsed` varies by machine. 0.10.4
with engine `v0.13.1` books 13 trades on the same bars. The difference is
order sizing: 1.0.0 gives an omitted
`initial_capital`, `default_qty_type` and `default_qty_value` TradingView's
Pine v6 defaults (100,000, `strategy.percent_of_equity`, 100), where 0.10.4
leaves the engine's own (1,000,000 and 1 contract). Declaring those in
`strategy()` books the same 13 trades on 1.0.1.

Since 1.0.0, generated strategies reset persistent Pine state before each new
batch or stream warmup through the engine's script-run preparation hook. Input
settings survive a new run, and ticks within one stream preserve accumulated
state. Regenerate the strategy C++ and rebuild compiled modules with matching
engine headers and library to use this lifecycle; replacing only the runtime
archive does not retrofit already compiled modules.

Since 1.0.0, generated constructors do not configure order behavior from the
presence of `strategy.close` or `strategy.close_all` in the source. Older C++
(0.10.4's included) assigns `script_has_strategy_close_`, which engine `v1.0.0`
has removed; regenerate it before compiling there. Reachable close commands
keep their ordinary runtime lowering.

Prefer no local build? [pineforge.dev](https://www.pineforge.dev) runs a free
hosted MCP server, with a weekly backtest quota, whose `backtest_pine` tool
transpiles and backtests a strategy for an AI agent. The
[`pineforge-backtest-mcp`](https://github.com/pineforge-4pass/pineforge-backtest-mcp)
Docker image is a local MCP server with `transpile_pine` and `backtest_pine`
tools that runs on your machine.
Both are built on the `pineforge-release` image, which ships a released codegen
and engine pair; their `engine_info` tool reports the image's version.

## Running tests

The full release check needs matching engine headers, a generated
`pineforge/version.h`, Eigen, the built runtime, and the public engine corpus.
Run `python -m pytest -ra` and then
`python -m pytest -ra tests/test_compile_corpus.py` with those paths set; check
the skip reasons so compiler and runtime coverage actually ran. The full
Pyodide parity gate and npm audit are also required. The exact setup and
commands are in [CONTRIBUTING.md](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/CONTRIBUTING.md#required-checks);
use `python -m pytest --collect-only -q` for the current collection count.

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/LICENSE),
with two supplemental sections (the `LICENSE` file is the controlling text;
where they conflict with the base license, the supplemental sections control):

- **Additional Permission — Personal Trading** — a natural person may use the
  software free of charge to research, develop, backtest and execute trades
  for their own account, funded solely by their own capital.
- **Commercial Use** — managing, advising on or trading anyone else's capital;
  use by, for or on behalf of a company, fund, partnership or other
  organization (including an individual's work for one); embedding the
  software or its output in a product or service made available to others; and
  operating a hosted, software-as-a-service or other public-facing service
  that uses the software all require a separate commercial license.
  So does any other use that is neither a permitted purpose under the base
  license nor covered by the Personal Trading permission.

This is source-available, not OSI open source.

### Buying a commercial license

Commercial licenses are available — flexible terms for funds, products, and
hosted/embedded use. Email **luis@4pass.com.tw** with your use case for a quote.

## Explicit Pine execution attachment

This section describes 1.0.1 and engine `v1.0.1`. The Pine execution adapter is
the engine's full Pine execution runtime (`PineExecutionAdapter` and
`PineStrategyHost` in the engine's `src/source/`): order lifecycle, bracket
legs, fill-price and slippage rules, POOC / `calc_on_order_fills`, margin
revival, trail/stop semantics, the intraday caps and the retained-parent
priority rule. Codegen's job is to emit the strategy that attaches it and the
`strategy.*` calls it executes, against a matching engine ABI.

Generated constructors configure their `PineStrategyConfig` before host
metadata and select `attach_pine_execution_adapter()` when
`PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1` is available, with a
guarded `enable_pine_intraday_cap()` fallback for
`PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1` alone. Every engine header the generated
C++ can compile against (those with `pineforge/source/pine_strategy_host.hpp`)
defines both macros, so the adapter branch is the one compiled; see
[`docs/pine-cap-activation.md`](https://github.com/pineforge-4pass/pineforge-codegen-oss/blob/main/docs/pine-cap-activation.md).
Risk statements remain in source execution order. This bridge requires matching
engine headers and runtime; it is not cross-version C++ binary compatibility.

Regenerate old generated C++ before using a new engine for Pine execution. C++
generated before the source-layer cut
([#129](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/129)),
0.10.4's included, derives from `BacktestEngine` and does not compile against
engine `v1.0.1`; old cap-only C++ does not attach the priority rule, and
metadata cannot silently restore it. Rebuild all modules against the
new matching C++ layout (`engine_script_run_v19` in engine `v1.0.1`); old
fingerprint versions are not comparable. The extraction preserves Pine policy
under explicit attachment; it does not implement the generic native
child-activation scheduler or prove campaign neutrality. Compile-only corpus
checks do not run Pine backtests.
