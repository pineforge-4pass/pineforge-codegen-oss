# Public contract for 1.0

The supported programmatic entry points are the Python functions
`pineforge_codegen.transpile` and `pineforge_codegen.transpile_full`, plus the
`gate/glue.py` `transpile_json` protocol shipped in the Pyodide package. The
pipeline classes shown in the [README](../README.md#advanced-run-the-pipeline-stages-directly)
are for advanced inspection and are not part of this 1.0 stability promise.

## Python functions

```python
transpile(pine_source: str, *, check_support: bool = True,
          filename: str = "<input>") -> str

transpile_full(pine_source: str, *, check_support: bool = True,
               filename: str = "<input>") -> dict
```

`transpile()` returns one complete C++ source string. It raises
`pineforge_codegen.errors.CompileError` for a located parse, support, analysis,
or generation error; its `diagnostics` attribute carries `Diagnostic` objects.
`filename` appears in their source locations and in `str(error)`.
`transpile()` does not return nonfatal warnings.

`transpile_full()` runs the same translation once and returns these keys on
success:

| Key | Python value | Meaning |
| --- | --- | --- |
| `cpp` | `str` | The generated source, identical to `transpile()` for the same inputs. |
| `inputs` | `list[dict]` | Input manifest in global source order, one entry per global-scope input call, including inline calls. |
| `strategyParams` | `dict` | Values extracted from the `strategy(...)` declaration; a nonliteral value can be `None`. |
| `diagnostics` | `list[Diagnostic]` | Nonfatal warnings only, with `Level.WARNING` and source locations. |

An error still raises `CompileError`; there is no partial success dict. The
diagnostic severity enum values are `"warning"` and `"error"`. A successful
`transpile_full()` returns only warnings; a `CompileError` can carry both
warnings and errors.

### Input manifest and override keys

Each manifest entry has `title` (string), `type` (one of `int`, `float`,
`bool`, `string`, `source`, `enum`), and `default` (a literal scalar or `None`).
It may also have `min`, `max`, `step` numeric values or a string `options`
list. An optional field is omitted when its argument is absent or cannot be
reduced to the supported literal form. The `title` is the **actual override
key read by the emitted C++**:

1. The value of an explicit compile-time constant `title`, if supplied.
2. Otherwise, the name of the declaration containing the input call.
3. Otherwise, the empty string `""`.

For example, `length = input.int(14)` has key `"length"`, and
`ta.ema(close, input.int(9, "Fast"))` has key `"Fast"`. The manifest includes
both declared and inline global-scope calls. If several calls share a key,
one override sets all of them; the translator emits a warning naming the
colliding inputs. Different `group=` labels do not separate override keys.
An explicit title that is not a compile-time string constant raises
`CompileError`.

## Pyodide and gate/glue JSON

`gate/glue.py` defines `transpile_json(source: str) -> str`. It returns a
serialized JSON **string**. The shipped Pyodide worker runs that glue. Its
success and compile-error envelopes are:

```json
{"ok":true,"cpp":"...","inputs":[],"strategyParams":{},"diagnostics":[]}
```

```json
{"ok":false,"error":"<input>:2:1: ...","diagnostics":[{"line":2,"col":1,"message":"...","severity":"error","endCol":10}]}
```

`ok` is a boolean. On success, `cpp`, `inputs`, and `strategyParams` have the
same meanings as in `transpile_full()`, and `diagnostics` contains warnings.
On a `CompileError`, `error` is `str(error)` and `diagnostics` contains the
error's diagnostics; `cpp`, `inputs`, and `strategyParams` are absent. Every
JSON diagnostic has 1-based integer `line` and `col`, a `message` string, and
`severity` equal to `"warning"` or `"error"`. `endCol` is included when the
source location provides it. A diagnostic hint, when present, is appended to
`message` after ` — `. The glue catches `CompileError`; an unexpected Python
exception may propagate instead of producing an envelope. The JSON entry
point does not accept a `filename` argument.

## Compatibility boundary

`check_support=False` on either Python function is **experimental**. It skips
the support gate and can produce C++ that fails to compile or does not match
Pine behavior; the 1.0 compatibility promise applies with the default
`check_support=True`.

There is no installed command-line interface. This package makes no
process-exit-code promises for a CLI, shell wrapper, or gate script. Consumers
should use the Python exception and return-value contract or the JSON
`ok`/`diagnostics` contract.

Generated C++ has a separate runtime pairing requirement: codegen `X.Y.Z`
supports only engine `vX.Y.Z` with that release's generated headers and static
library; prerelease tags match exactly. Regenerate C++ and relink strategy
libraries on every pair change. `PF_ABI_VERSION` equality alone is
insufficient. See [CONTRIBUTING.md](../CONTRIBUTING.md#engine-pairing).
