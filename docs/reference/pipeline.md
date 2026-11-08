## Pipeline

`transpile()` runs six passes in this order; respect the order when
adding work:

Before these passes, `_parse_bounded` checks the original source size and
constructs the lexer, which folds CRLF to LF, including inside strings, and
lone CR code/comment line endings. A lone CR inside a string is kept as data
and does not end an unterminated-string recovery. Its normalized source goes
to pragma extraction and the parser too; direct `Lexer` callers share the rule.

Before lowering, `method_binding.validate_call_argument_order` checks each v6
call and declaration: a positional argument after a keyword raises PF-E2074
at that expression's first token. `_parse_bounded`, direct `Analyzer` clients
and v6 library parsing share this rule, including unused bodies and parameter
defaults; imported v5 subtrees keep their existing policy.

In call argument lists, the parser accepts a primitive type keyword
(`int`, `float`, `bool`, `string`) followed by `=` as an argument label.
It records the token text as the label and retains written argument order
and source locations. Declaration names and type annotations keep their
existing parser grammar.

1. `pragmas.extract_pf_trace_pragmas` — pulls `// @pf-trace name=expr`
  comments out before the lexer strips comments.
2. `Lexer` → `Parser` → `Program` AST. Before the support check,
  `library_inline.inline_libraries` inlines the libraries the script imports
  and `block_locals.rename_block_locals` names apart the block declarations a
  previous run asked for.
3. `support_checker.check_support_or_raise` — rejects any Pine surface
  PineForge cannot faithfully execute (see "Support contracts" below).
4. `analyzer.value_bindings.ValueBindings` resolves authored value uses through
  scopes with declaration positions before request lowering, retaining their
  exact bindings through copies. Every block and callable owns its declarations;
  an initializer precedes its binding, and a counted-loop binder follows its
  start but precedes its end/step. A callable parameter default that names an
  earlier global is still accepted (TradingView refuses it: a known gap);
  callable parameters and field labels cannot bind defaults. Type field
  defaults containing calls or authored variables raise PF-E2087 in
  `//@version=6` scripts and libraries; v5 sources keep their earlier handling.
  A qualifier with no visible value binding falls back to its namespace/type
  handler, including after a block-local declaration's scope ends.
  Unavailable authored declarations use PF-E2075, inaccessible block bindings
  PF-E2054, and missing write targets PF-E2020; a later direct global in a
  callable keeps PF-E2053. Wholly unknown reads and strategy declaration
  constants retain their existing diagnostic handlers.
  `ValueBindings.binding_of(node)` returns the binding the walk annotated on an identifier (kind and declaring callable); it performs no resolution.
  TradingView accepts a namespace call after an expired block local with the
  same name; PineForge's analysis no longer refuses it, but code generation
  still rejects some such calls (a known gap).
5. `Analyzer.analyze()` → `AnalyzerContext` (type inference, scope
  resolution, per-call-site TA bookkeeping, security registration). Four
  AST passes run before it: `external_requests.lower_no_data_requests`
  (requests the support checker lowered), `security_contexts.
  specialize_security_contexts` (helper request contexts),
  `finite_ta_length`'s bounded extrema lengths and `builtin_keywords`
  (keyword arguments of `nz`, `fixnan`, the math records, `timeframe.change`,
  `timeframe.in_seconds`, the `strategy.closedtrades` / `strategy.opentrades`
  accessors and `matrix.new` in parameter order, a skipped parameter that
  declares a literal default taking it; the helper copies `security_contexts`
  made included).
6. `CodeGen(ctx).generate()` → C++ source string.

Pragmas are reattached to `ctx.pf_trace_pragmas` between (5) and (6)
because the analyzer never inspects them; codegen emits the trace tail
in `on_bar`.

`_generate` runs (2)-(6) first without the per-call clones of functions that
read a `session.<flag>[k]` (the calls they had before such reads were
supported), then again with the functions whose reads that C++ holds cloned
as well (`session_clones`), until the C++ asks for no more: a clone can make
a caller's read reach the C++. A read in an argument the codegen leaves out
never costs a clone, which along a deep call tree grow with every path.

