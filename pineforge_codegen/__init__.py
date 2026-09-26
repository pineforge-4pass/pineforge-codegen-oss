"""PineScript v6 to C++ transpiler."""

from .lexer import Lexer
from .parser import Parser
from .analyzer import Analyzer
from .codegen import CodeGen
from .errors import CompileError, Level, Phase
from .finite_ta_length import expand_finite_choice_extrema_lengths
from .limits import TimeBudget, check_ast_depth, check_source_size, ensure_recursion_headroom
from .pragmas import extract_pf_trace_pragmas
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


def _generate(pine_source: str, check_support: bool, filename: str):
    """One pipeline pass, repeated once without the per-call clones of the
    functions whose ``session.<flag>[k]`` reads the C++ does not hold (they
    sit in arguments the codegen leaves out), so a script whose reads are all
    left out keeps the functions it compiled with before: when the C++ reads
    none of a function's reads, and when the first pass fails after cloning
    functions for their reads (their clones can put two call sites' types on
    one variant). A second pass that fails keeps the first one's error.

    Returns ``(codegen, ctx, cpp, support_diagnostics)``."""
    budget = None
    dropped: frozenset[str] = frozenset()
    first_error: CompileError | None = None
    for second in (False, True):
        ast, pragmas, budget = _parse_bounded(pine_source, filename, budget)
        support_diagnostics = []
        if check_support:
            support_diagnostics = _support_diagnostics(ast, filename=filename)
            if any(d.level == Level.ERROR for d in support_diagnostics):
                raise CompileError(support_diagnostics)
        budget.check(phase=Phase.ANALYZER)
        ast = expand_finite_choice_extrema_lengths(ast)
        check_ast_depth(ast, filename)
        budget.check(phase=Phase.ANALYZER)
        analyzer = Analyzer(ast, filename=filename, budget=budget,
                            session_reads_dropped=dropped)
        try:
            ctx = analyzer.analyze()
            budget.check(phase=Phase.ANALYZER)
            # Attach after analysis: pragma expressions are not part of the
            # program body, so the analyzer never inspects them; the codegen
            # consumes them directly from the context to emit the on_bar tail
            # ``if (trace_enabled_) { trace(...); ... }`` block.
            ctx.pf_trace_pragmas = pragmas
            gen = CodeGen(ctx, budget=budget)
            cpp = gen.generate()
            budget.check(phase=Phase.CODEGEN)
        except CompileError as error:
            if first_error is not None:
                raise first_error from None
            if second or not analyzer.session_history_functions:
                raise
            first_error, dropped = error, analyzer.session_history_functions
            del ast, analyzer
            continue
        unemitted = gen.session_functions_without_emitted_reads()
        if second or not unemitted:
            return gen, ctx, cpp, support_diagnostics
        dropped = unemitted
        # The second pass parses again: let the first one's objects go.
        del ast, analyzer, ctx, gen, cpp


def transpile(pine_source: str, *, check_support: bool = True, filename: str = "<input>") -> str:
    """Transpile PineScript v6 source code to C++ code.

    Semantic rules enforced in :class:`Analyzer` (before codegen), including:
    user ``enum`` blocks must appear **above** ``input.enum(Enum.member, ...)`` uses.

    Before analysis, :func:`support_checker.check_support_or_raise` rejects
    scripts that use language constructs PineForge cannot faithfully execute
    (e.g. ``indicator()`` declarations, prohibited variables such as
    ``bar_index``, disallowed ``request.security`` parameters). Pass
    ``check_support=False`` to bypass this gate (intended for tests of legacy
    fixtures only).

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

    Returns:
        Generated C++ source string.
    """
    _gen, _ctx, cpp, _support = _generate(pine_source, check_support, filename)
    return cpp


def transpile_full(pine_source: str, *, check_support: bool = True,
                   filename: str = "<input>") -> dict:
    """Transpile like :func:`transpile`, plus the host-UI input manifest.

    Runs the pipeline (Lexer -> Parser -> support check -> Analyzer ->
    CodeGen.generate) once, twice when its per-call clones for
    ``session.<flag>[k]`` reads the C++ does not hold are dropped (see
    ``_generate``), and returns the generated C++ alongside the data the
    cloud Studio needs to auto-build a backtest "override params" form:

    - ``cpp``: the generated C++ source (identical to :func:`transpile`).
    - ``inputs``: a list of ``InputDef`` dicts (one per top-level
      ``var = input.*(...)`` declaration). Each has ``title`` / ``type`` /
      ``default`` and optionally ``min`` / ``max`` / ``step`` / ``options``
      (omitted when the corresponding signature argument is absent or
      references a non-const value). See
      :meth:`CodeGen.extract_input_manifest`.
    - ``strategyParams``: the literal ``strategy(...)`` kwargs the analyzer
      surfaced (e.g. ``initial_capital``, ``pyramiding``).
    - ``diagnostics``: the warnings (:class:`~pineforge_codegen.errors.Diagnostic`,
      ``Level.WARNING``) the support checker and the analyzer raised for a
      script that transpiled -- e.g. an approximated ``ta.vwap`` anchor. An
      error still raises ``CompileError``, which carries the warnings too.

    Args mirror :func:`transpile`.

    Returns:
        ``{"cpp": str, "inputs": list[dict], "strategyParams": dict,
        "diagnostics": list[Diagnostic]}``.
    """
    gen, ctx, cpp, support_diagnostics = _generate(pine_source, check_support, filename)
    return {
        "cpp": cpp,
        "inputs": gen.extract_input_manifest(),
        "strategyParams": dict(ctx.strategy_params),
        "diagnostics": [d for d in (*support_diagnostics, *ctx.diagnostics)
                        if d.level == Level.WARNING],
    }
