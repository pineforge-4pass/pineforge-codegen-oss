"""Codegen package facade.

Historically the codegen lived in a single ``codegen.py`` module that hit
~5,700 lines and ~115 methods on a single ``CodeGen`` class. We are
incrementally splitting it into focused submodules without changing the
public API:

    from pineforge_codegen.codegen import CodeGen          # primary
    from pineforge_codegen.codegen import BAR_FIELDS, ...  # constants

Re-exports preserved for ``support_checker.py`` and any external consumers
that imported module-level rule tables. Add new helper modules under
``compiler/transpiler/codegen/`` and re-export their public surface here.
"""

from .base import CodeGen
from .helpers import CPP_RESERVED
from .tables import (
    # Bar-data tables (consumed by helpers + support_checker).
    BAR_FIELDS,
    BAR_BUILTINS,
    BAR_SERIES_PUSH,
    SECURITY_OHLC_BAR_FIELDS,
    # Runtime function-name constants emitted as C++ string literals.
    RUNTIME_REGISTER_SECURITY_EVAL_FN,
    RUNTIME_REGISTER_SECURITY_LOWER_TF_EVAL_FN,
    # TA dispatch / arg tables.
    TA_RETURNS_BOOL,
    TA_IMPLICIT_COMPUTE,
    TA_COMPUTE_ARGS,
    TA_IMPLICIT_COMPUTE_FULL,
    TA_IMPLICIT_APPEND,
    TA_TUPLE_FIELDS,
    # Type and reserved-name tables.
    PINE_TYPE_TO_CPP,
    # Skip / passthrough catalogs (used by support_checker).
    SKIP_FUNC_NAMES,
    SKIP_NAMESPACES,
    SKIP_VAR_TYPES,
    # Built-in dispatch tables (consumed by visitors + support_checker).
    SYMINFO_MEMBER_MAP,
    COLOR_CONST_MAP,
    ARRAY_METHODS,
    MAP_METHODS,
    MATRIX_METHODS,
    MATRIX_METHOD_KWARGS,
    MATRIX_RETURNING_METHODS,
    MATH_FUNC_MAP,
    STR_FUNC_MAP,
)

__all__ = [
    "CodeGen",
    "BAR_FIELDS",
    "BAR_BUILTINS",
    "BAR_SERIES_PUSH",
    "SECURITY_OHLC_BAR_FIELDS",
    "TA_RETURNS_BOOL",
    "TA_IMPLICIT_COMPUTE",
    "TA_COMPUTE_ARGS",
    "TA_IMPLICIT_COMPUTE_FULL",
    "TA_IMPLICIT_APPEND",
    "TA_TUPLE_FIELDS",
    "PINE_TYPE_TO_CPP",
    "CPP_RESERVED",
    "SKIP_FUNC_NAMES",
    "SKIP_NAMESPACES",
    "SKIP_VAR_TYPES",
    "SYMINFO_MEMBER_MAP",
    "COLOR_CONST_MAP",
    "ARRAY_METHODS",
    "MAP_METHODS",
    "MATRIX_METHODS",
    "MATRIX_METHOD_KWARGS",
    "MATRIX_RETURNING_METHODS",
    "MATH_FUNC_MAP",
    "STR_FUNC_MAP",
]
