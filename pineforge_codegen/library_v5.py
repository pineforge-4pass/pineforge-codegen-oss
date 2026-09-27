"""Pine v5 rules inside the bodies of an inlined v5 library.

A library keeps its own ``//@version``: TradingView compiles it on its own,
and a v6 script can import a v5 one. Where v5 and v6 read the same code
differently, a v5 library body must be lowered with v5's rules. None is
implemented yet, so a reachable v5 library is refused by name.
"""

from __future__ import annotations

from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .library_modules import LibraryModule


def lower_v5_modules(modules: list[tuple[LibraryModule, list]], filename: str) -> None:
    """Lower the reachable definitions of every v5 module in ``modules``."""
    for lib, _definitions in modules:
        if lib.pine_version != 5:
            continue
        raise CompileError([Diagnostic(
            level=Level.ERROR, phase=Phase.PARSER,
            location=SourceLocation(file=lib.path, line=1, col=1, end_col=1),
            message=(f"library '{lib.path}' is //@version=5; PineForge does not "
                     "implement v5's rules inside a library yet"),
        )])
