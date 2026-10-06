"""PineScript v6 to C++ transpiler."""

from collections.abc import Mapping

from .lexer import Lexer
from .parser import Parser
from .analyzer import Analyzer
from .codegen import CodeGen
from .diagnostic_codes import diagnostics_catalog, render_diagnostic
from .errors import CompileError, Level, Phase
from .external_requests import lower_no_data_requests
from .builtin_keywords import bind_builtin_keywords
from .finite_ta_length import expand_finite_choice_extrema_lengths
from .library_inline import inline_libraries
from .limits import TimeBudget, check_ast_depth, check_source_size, ensure_recursion_headroom
from .pragmas import extract_pf_trace_pragmas
from .request_discovery import discover_requests, request_sites
from .security_contexts import specialize_security_contexts
from .block_locals import rename_block_locals
from .support_checker import check_support as _support_diagnostics
from .support_checker import check_support_or_raise


def _parse_bounded(pine_source: str, filename: str, budget: TimeBudget | None = None):
    ensure_recursion_headroom()
    check_source_size(pine_source, filename)
    budget = budget or TimeBudget(filename)
    pragmas = extract_pf_trace_pragmas(
        pine_source, filename=filename, budget=budget,
    )
    budget.check(phase=Phase.LEXER)
    tokens = Lexer(pine_source, filename=filename, budget=budget).tokenize()
    budget.check(phase=Phase.LEXER)
    ast = Parser(tokens, source=pine_source, filename=filename,
                 budget=budget).parse()
    check_ast_depth(ast, filename)
    budget.check(phase=Phase.PARSER)
    return ast, pragmas, budget


def _generate(pine_source: str, check_support: bool, filename: str,
              libraries: Mapping[str, str] | None = None):
    """The pipeline, first without the per-call clones of functions that read
    a ``session.<flag>[k]``, as scripts compiled before such reads were
    supported; then again with the functions whose reads that C++ holds
    cloned as well, until the C++ asks for no more (a clone can make a
    caller's read reach the C++: its callers are cloned per call site, which
    can retype their parameters). A read in an argument the codegen leaves
    out never costs a clone (the clones of a function called along a deep
    call tree grow with every path to it).

    Each pass inlines the libraries the script imports before the support
    check (``library_inline``). A declaration in a top-level block whose type
    the member of its name cannot hold gets a name of its own, and the
    pipeline runs again (``block_locals``).

    Returns ``(codegen, ctx, cpp, support_diagnostics, sites)``: ``sites``
    are the requests of another symbol's data the support checker lowered
    (``request_discovery.request_sites``)."""
    budget = None
    clones: frozenset[str] = frozenset()
    renamed: frozenset = frozenset()
    while True:
        ast, pragmas, budget = _parse_bounded(pine_source, filename, budget)
        ast = inline_libraries(ast, pine_source, libraries=libraries,
                               filename=filename, budget=budget)
        rename_block_locals(ast, renamed)
        support_diagnostics = []
        if check_support:
            support_diagnostics = _support_diagnostics(ast, filename=filename)
            if any(d.level == Level.ERROR for d in support_diagnostics):
                raise CompileError(support_diagnostics)
        budget.check(phase=Phase.ANALYZER)
        sites = request_sites(ast)
        ast = lower_no_data_requests(ast)
        ast = specialize_security_contexts(ast, filename=filename)
        ast = bind_builtin_keywords(expand_finite_choice_extrema_lengths(ast))
        check_ast_depth(ast, filename)
        budget.check(phase=Phase.ANALYZER)
        ctx = Analyzer(ast, filename=filename, budget=budget,
                       session_clones=clones).analyze()
        budget.check(phase=Phase.ANALYZER)
        # Attach after analysis: pragma expressions are not part of the
        # program body, so the analyzer never inspects them; the codegen
        # consumes them directly from the context to emit the on_bar tail
        # ``if (trace_enabled_) { trace(...); ... }`` block.
        ctx.pf_trace_pragmas = pragmas
        gen = CodeGen(ctx, budget=budget)
        cpp = gen.generate()
        budget.check(phase=Phase.CODEGEN)
        apart = gen.block_locals_needing_names - renamed
        if apart:
            renamed |= apart
            del ast, ctx, gen, cpp, sites
            continue
        needed = gen.session_functions_needing_clones
        if not needed:
            return gen, ctx, cpp, support_diagnostics, sites
        # Only uncloned functions ask, so the set grows each time and the
        # loop ends by the time every function that reads a flag at an
        # offset is cloned.
        if needed <= clones:
            raise AssertionError("a cloned function asked for session clones again")
        clones |= needed
        del ast, ctx, gen, cpp, sites


def transpile(pine_source: str, *, check_support: bool = True, filename: str = "<input>",
              libraries: Mapping[str, str] | None = None) -> str:
    """Transpile PineScript v6 source code to C++ code.

    Semantic rules enforced in :class:`Analyzer` (before codegen), including:
    user ``enum`` blocks must appear **above** ``input.enum(Enum.member, ...)`` uses.

    Before analysis, :func:`support_checker.check_support_or_raise` rejects
    scripts that use language constructs PineForge cannot faithfully execute
    (e.g. ``indicator()`` declarations, ``request.seed``, disallowed
    ``request.security`` parameters) and warns about approximated ones (e.g.
    ``bar_index``). Pass ``check_support=False`` to bypass this gate
    (experimental; intended for tests of legacy fixtures only).

    ``// @pf-trace name=expr`` pragmas are extracted from ``pine_source``
    via a pre-pass (the lexer strips comments before the parser sees them)
    and attached to the analyzer context for codegen. See
    :mod:`pineforge_codegen.pragmas` for the syntax.

    Args:
        pine_source: PineScript v6 source string.
        check_support: When True (default) the support checker runs after
            parsing and raises ``CompileError`` on any unsupported feature
            before semantic analysis or codegen.
        filename: Source name threaded into every ``SourceLocation`` so the
            ``file:line:col`` shown in ``CompileError`` (both ``str()`` and
            :meth:`~pineforge_codegen.errors.CompileError.format`) points back
            at the caller's file. Defaults to ``"<input>"``.
        libraries: The sources of the Pine libraries the script imports,
            by import path (``{"user/name/version": source_text}``); each
            import the script uses is inlined from its source (see
            :mod:`pineforge_codegen.library_inline`). ``None`` (default)
            reads them from ``$PINEFORGE_PINE_LIBRARIES`` through the
            script's own requests manifest under ``$PINEFORGE_REQUESTS_ROOT``
            (see :mod:`pineforge_codegen.pine_libraries`); with neither, an
            import is refused as before.

    Returns:
        Generated C++ source string.
    """
    _gen, _ctx, cpp, _support, _sites = _generate(pine_source, check_support, filename,
                                                  libraries)
    return cpp


def transpile_full(pine_source: str, *, check_support: bool = True,
                   filename: str = "<input>",
                   libraries: Mapping[str, str] | None = None) -> dict:
    """Transpile like :func:`transpile`, plus the host-UI input manifest.

    Runs the pipeline (Lexer -> Parser -> support check -> Analyzer ->
    CodeGen.generate) once, again while the C++ holds a
    ``session.<flag>[k]`` read of a function not yet cloned for it (see
    ``_generate``), and returns the generated C++ alongside the data the
    cloud Studio needs to auto-build a backtest "override params" form:

    - ``cpp``: the generated C++ source (identical to :func:`transpile`).
    - ``inputs``: a list of ``InputDef`` dicts (one per global-scope
      ``input(...)`` / ``input.*(...)`` call, inline calls included, in
      source order). Each has ``title`` / ``type`` / ``default`` /
      ``supported`` (the checked-settings receipt's flag) and optionally
      ``min`` / ``max`` / ``step`` / ``options`` (omitted when the
      corresponding signature argument is absent or is not a literal the
      receipt holds); an ``input.symbol`` entry also has ``kind: "symbol"``.
      See :meth:`CodeGen.extract_input_manifest`.
    - ``strategyParams``: the literal ``strategy(...)`` kwargs the analyzer
      surfaced (e.g. ``initial_capital``, ``pyramiding``).
    - ``diagnostics``: the warnings (:class:`~pineforge_codegen.errors.Diagnostic`,
      ``Level.WARNING``) the support checker and the analyzer raised for a
      script that transpiled -- e.g. an approximated ``ta.vwap`` anchor. An
      error still raises ``CompileError``, which carries the warnings too.
    - ``requests``: every request site that reads another symbol's feed,
      by line: its symbol and timeframe as registration computes them
      before the first bar (``literal`` / ``input`` / ``computed`` /
      ``unresolvable``; ``literal`` / ``chart`` / ``input`` / ``computed``),
      ``lookahead``, ``gaps`` and ``ignore_invalid_symbol``. A site lowered
      to ``na`` (its value reaches display sinks only) or in a helper
      nothing reaches is not listed. See
      :mod:`pineforge_codegen.request_discovery`.

    Args mirror :func:`transpile`.

    Returns:
        ``{"cpp": str, "inputs": list[dict], "strategyParams": dict,
        "diagnostics": list[Diagnostic], "requests": list[dict]}``.
    """
    gen, ctx, cpp, support_diagnostics, sites = _generate(
        pine_source, check_support, filename, libraries)
    return {
        "cpp": cpp,
        "inputs": gen.extract_input_manifest(),
        "strategyParams": dict(ctx.strategy_params),
        "diagnostics": [d for d in (*support_diagnostics, *ctx.diagnostics)
                        if d.level == Level.WARNING],
        "requests": discover_requests(gen, ctx, sites),
    }
