"""PineScript v6 to C++ transpiler."""

from .lexer import Lexer
from .parser import Parser
from .analyzer import Analyzer
from .codegen import CodeGen
from .pragmas import extract_pf_trace_pragmas
from .support_checker import check_support_or_raise


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
    pragmas = extract_pf_trace_pragmas(pine_source)
    tokens = Lexer(pine_source, filename=filename).tokenize()
    ast = Parser(tokens, source=pine_source, filename=filename).parse()
    if check_support:
        check_support_or_raise(ast, filename=filename)
    ctx = Analyzer(ast, filename=filename).analyze()
    # Attach after analysis: pragma expressions are not part of the
    # program body, so the analyzer never inspects them; the codegen
    # consumes them directly from the context to emit the on_bar tail
    # ``if (trace_enabled_) { trace(...); ... }`` block.
    ctx.pf_trace_pragmas = pragmas
    return CodeGen(ctx).generate()
