# String position and boundary fixture

`strategy.pine` checks `str.pos` when a needle is absent, found, empty, or
longer than its text. It checks missing results through `na()` and `nz()`,
using both literal arguments and arguments that change on alternate bars.

The public source is the reporter's observations in
[issue #382](https://github.com/pineforge-4pass/pineforge-codegen-oss/issues/382):
item 5 for positions, item 9 for substring boundaries, and item 8 for trimming.
**`strategy.pine` has no captured TradingView trade tape.** Its expected report
is derived from those public observations, not a new TradingView capture.
Related substring and trim cases live in `tests/test_string_units.py`; the
strategy itself checks positions only.

## Expected position values

| Expression | Expected result |
| --- | --- |
| `str.pos("abc", "x")` | integer `na` |
| `na(str.pos("abc", "x"))` | `true` |
| `nz(str.pos("abc", "x"), 0)` | `0` |
| `str.tostring(str.pos("abc", "x"))` | `"NaN"`: generated-string contract, without a separate reported TradingView measurement |

The related native tests include these runtime inputs. The Unicode positions
are derived from UTF-16 units, where an emoji occupies two units; they are not
separate TradingView measurements of these exact inputs. The `"NaN"` entries
all use the generated-string contract above.

| Text | Needle | `na(p)` | `nz(p, 0)` | `str.tostring(p)` |
| --- | --- | --- | ---: | --- |
| `"abc"` | `"x"` | `true` | 0 | `"NaN"` |
| `"seed42"` | `"?"` | `true` | 0 | `"NaN"` |
| `"abc"` | `"b"` | `false` | 1 | `"1"` |
| `"abc"` | `"a"` | `false` | 0 | `"0"` |
| `"abc"` | `""` | `false` | 0 | `"0"` |
| `""` | `"x"` | `true` | 0 | `"NaN"` |
| `""` | `""` | `false` | 0 | `"0"` |
| `"ab"` | `"abc"` | `true` | 0 | `"NaN"` |
| `"a😀b"` | `"b"` | `false` | 3 | `"3"` |
| `"中😀tail"` | `"l"` | `false` | 6 | `"6"` |

The strategy opens a position on its first bar and closes it on its second.
The exact expected exit-comment report is:

```text
literal_na=true|literal_nz=0|variable_na=true|variable_nz=0|long_na=true|long_nz=0|start=0|found=1|empty=0
```

For comparison, the report derived from affected-version behavior is:

```text
literal_na=false|literal_nz=-1|variable_na=false|variable_nz=-1|long_na=false|long_nz=-1|start=0|found=1|empty=0
```

## Substring observations and controls

| Form | Expected result |
| --- | --- |
| `str.substring("abc", str.pos("abc", ":") + 1)` | `"abc"`, completed run |
| Substring with `na` start and omitted end | Whole text, completed run |
| Substring with `na` end | String `na`, completed run |
| `str.substring("abc", 2, j)` with finite runtime `j = 1` | Run-time stop; detail `end_pos is less than begin_pos` |

Item 9 reports undefined affected-version behavior that depends on platform
and compiled context. With runtime positions, releases 1.10.0 and 1.11.0
produced:

| Form on `"abc"` | Affected-version x86-64 output | Affected-version ARM64 output | TradingView observation |
| --- | --- | --- | --- |
| Start 2, end 1 | `"c"` | Empty text | Run-time error |
| Start -1, omitted end | Run-time error | `"abc"` | Run-time error |
| Start 0, end `na` | `"abc"` | Empty text, reported as `na()` | String `na` |
| Start `na`, omitted end | `"abc"` | `"abc"` | `"abc"` |

The affected-version results involving invalid or missing positions are not
guaranteed behavior. In particular, matching a TradingView value through an
undefined conversion does not make the result reliable.

Finite valid slices must also complete successfully:

| Expression | Expected result |
| --- | --- |
| `str.substring("abc", 1, 2)` | `"b"`, UTF-8 hex `62` |
| `str.substring("a😀b", 1, 3)` | Whole emoji, UTF-8 hex `f09f9880` |
| `str.substring("abc", 1, 9)` | Clamped `"bc"`, length 2 |
| `str.substring("abc", 3, 3)` | Empty text, length 0 |

The native representation stores string `na` as an empty string, so it cannot
distinguish a missing string from an authored empty string.

## Trim observations

Item 8 reports these exact TradingView results:

| Text passed to `str.trim` | Expected result |
| --- | --- |
| U+00A0, `a`, U+00A0 | `"a"` |
| U+2003, `a`, U+2003 | `"a"` |
| U+FEFF, `a` | `"a"` |
| Vertical tab U+000B, `a`, form feed U+000C | `"a"` |

Reported as removed at the ends: no-break space U+00A0, em space U+2003,
ideographic space U+3000, Ogham space mark U+1680, line separator U+2028,
zero-width no-break space U+FEFF, vertical tab U+000B, and form feed U+000C,
in addition to space, tab, line feed, and carriage return.

Reported as kept: next-line U+0085, unit separator U+001F, zero-width space
U+200B, and Mongolian vowel separator U+180E. Interior characters are kept.
The tests compose these characters into literal and runtime-input cases,
including mixed boundaries and empty text; those compositions are derived
contracts, not separate TradingView measurements or a full Unicode whitespace
inventory.

## How to run

Open `strategy.pine` in TradingView's Pine Editor and add it to a chart with
at least two bars. In the strategy's trade list, inspect the closing order's
comment and compare it with the expected report above.

To run the related native checks, configure the engine headers, generated
headers, built runtime, Eigen, and compiler as described in the repository
setup, then run:

```sh
PINEFORGE_REQUIRE_NATIVE=1 python -m pytest -q -ra tests/test_string_units.py
```

`PINEFORGE_REQUIRE_NATIVE=1` makes missing native inputs fail instead of skip.

## Unmeasured limits

- A substring with an `na` start and an explicit finite end has no reported
  TradingView observation or native case here.
- The variable idiom `p = str.pos(s, ":")` followed by
  `str.substring(s, p + 1)` has no native case here.
- Recorded native runs used ARM64. Finite slicing under UBSan, a sanitizer
  control demonstrating the old overflow, a sanitized runtime archive, and
  native behavior on x86-64 have not been established by these checks. The
  existing four UBSan cases return at the `na`-end guard before finite arithmetic.
- Boundary and consumer cases use `-O0` and `-O2`; the original position cases
  use `-O0`. Broader `-O2` coverage, `request.security`, array-index and plot
  compositions, surrogate-half search, and other issue #382 string functions
  are outside this fixture's coverage.
