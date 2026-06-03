"""Analyzer package facade.

Historically the semantic analyzer lived in a single ``analyzer.py`` module
that hit ~2,340 lines and ~74 methods on a single ``Analyzer`` class. This
package is the structural skeleton for an incremental split: ``base.py``
currently holds the original ``Analyzer`` verbatim, and future steps will
peel focused mixins out into sibling files (e.g. ``visit_func``,
``visit_udt``, ``visit_control``, ``visit_expr``, ``visit_call``, ``types``,
``helpers``) without changing the public API:

    from pineforge_codegen.analyzer import Analyzer            # primary
    from pineforge_codegen.analyzer import AnalyzerContext     # context
    from pineforge_codegen.analyzer import TA_CLASS_MAP, ...   # tables

Re-exports preserved for ``support_checker.py``, ``codegen/base.py`` and
external tests that imported the dataclasses or module-level dispatch
tables. Add new helper modules under ``compiler/transpiler/analyzer/`` and
re-export their public surface here.
"""

from .base import Analyzer
from .contracts import (
    # Output dataclasses (consumed by codegen + tests). Defined in
    # contracts.py so the package's import graph stays a strict DAG.
    AnalyzerContext,
    FixnanCallSite,
    FuncInfo,
    MutableGlobalInfo,
    SecurityCallInfo,
    TACallSite,
)
from .tables import (
    # Module-level dispatch tables (consumed by codegen + support_checker).
    TA_CLASS_MAP,
    TA_PERIOD_ARG,
    TA_TUPLE_RETURNS,
    TA_MULTI_CTOR,
    TA_NO_CTOR,
    BUILTIN_VARS,
    BAR_FIELDS,
    SKIP_FUNCS,
)

__all__ = [
    "Analyzer",
    "TACallSite",
    "FuncInfo",
    "FixnanCallSite",
    "MutableGlobalInfo",
    "SecurityCallInfo",
    "AnalyzerContext",
    "TA_CLASS_MAP",
    "TA_PERIOD_ARG",
    "TA_TUPLE_RETURNS",
    "TA_MULTI_CTOR",
    "TA_NO_CTOR",
    "BUILTIN_VARS",
    "BAR_FIELDS",
    "SKIP_FUNCS",
]
