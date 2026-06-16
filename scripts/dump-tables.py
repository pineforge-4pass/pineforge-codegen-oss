#!/usr/bin/env python3
"""Introspect the codegen modules and emit tables.json — the raw data the app's
analyzer needs to (re)generate tables.generated.ts WITHOUT a Python checkout.

This is the data half of pineforge-app/packages/analyzer/scripts/gen-tables.py:
identical introspection + normalizations, JSON instead of TS. The app's
Phase-3b TS transform knows each table's target shape; here we only emit values.

Run: PYTHONPATH=<repo-root> python3 scripts/dump-tables.py > <out>/tables.json
"""

from __future__ import annotations

import json
import os
import sys

from pineforge_codegen import signatures as sigs
from pineforge_codegen.analyzer import tables as atab
from pineforge_codegen.codegen import tables as ctab
from pineforge_codegen.codegen import visit_expr as cve
from pineforge_codegen import tv_input_choices as tvi
from pineforge_codegen import support_checker as sc
from pineforge_codegen.symbols import PineType


def _type_str(v: object) -> str:
    return v.value if isinstance(v, PineType) else str(v)


def set_or_keys(values) -> list:
    """sets/frozensets -> sorted; lists/dict_keys -> as-is (insertion order).

    Mirrors gen-tables.py emit_string_set: membership is the contract, so sets
    are sorted for determinism while insertion-ordered key lists are preserved.
    """
    if isinstance(values, (set, frozenset)):
        return sorted(values)
    return list(values)


def type_record(mapping: dict) -> dict:
    return {k: _type_str(v) for k, v in mapping.items()}


def build_param_names() -> dict:
    out: dict[str, list[str]] = {}
    for reg in (
        sigs.TA_FUNCTIONS, sigs.MATH_FUNCTIONS, sigs.STRATEGY_FUNCTIONS,
        sigs.STR_FUNCTIONS, sigs.INPUT_FUNCTIONS, sigs.MAP_FUNCTIONS,
        sigs.SYMINFO_FUNCTIONS, sigs.BUILTIN_FUNCTIONS,
    ):
        for fn in reg.values():
            out[fn.name] = list(fn.param_names)
    return out


def build_ta_implicit_noarg_members() -> list:
    return sorted(
        m for m in ctab.TA_IMPLICIT_COMPUTE_FULL
        if ctab.TA_COMPUTE_ARGS.get(m) == []
    )


def main() -> None:
    version = open(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
    ).read().strip()

    tables = {
        "CODEGEN_VERSION": version,

        # namespace member key sets (from codegen maps; keys = insertion order)
        "TA_CLASS_MAP_KEYS": set_or_keys(list(atab.TA_CLASS_MAP.keys())),
        "MATH_FUNC_MAP_KEYS": set_or_keys(list(ctab.MATH_FUNC_MAP.keys())),
        "STR_FUNC_MAP_KEYS": set_or_keys(list(ctab.STR_FUNC_MAP.keys())),
        "ARRAY_METHODS_KEYS": set_or_keys(list(ctab.ARRAY_METHODS.keys())),
        "MAP_METHODS_KEYS": set_or_keys(list(ctab.MAP_METHODS.keys())),
        "MATRIX_METHODS_KEYS": set_or_keys(list(ctab.MATRIX_METHODS.keys())),
        "SYMINFO_MEMBER_MAP_KEYS": set_or_keys(list(ctab.SYMINFO_MEMBER_MAP.keys())),
        "COLOR_CONST_MAP_KEYS": set_or_keys(list(ctab.COLOR_CONST_MAP.keys())),
        "BAR_FIELDS_KEYS": set_or_keys(list(ctab.BAR_FIELDS.keys())),
        "BAR_BUILTINS_KEYS": set_or_keys(list(ctab.BAR_BUILTINS.keys())),
        "SKIP_FUNC_NAMES": set_or_keys(ctab.SKIP_FUNC_NAMES),
        "SKIP_NAMESPACES": set_or_keys(ctab.SKIP_NAMESPACES),
        "SKIP_VAR_TYPES": set_or_keys(ctab.SKIP_VAR_TYPES),

        # codegen visit_expr.py unknown-symbol guard tables
        "BUILTIN_NAMESPACE_NAMES": set_or_keys(cve._BUILTIN_NAMESPACE_NAMES),
        "REALTIME_ONLY_VARS": set_or_keys(cve._REALTIME_ONLY_VARS),
        "TA_IMPLICIT_NOARG_COMPUTE_MEMBERS": build_ta_implicit_noarg_members(),

        # signatures.py registries (keys)
        "STRATEGY_FUNCTIONS": set_or_keys(list(sigs.STRATEGY_FUNCTIONS.keys())),
        "INPUT_FUNCTIONS": set_or_keys(list(sigs.INPUT_FUNCTIONS.keys())),
        "MATH_FUNCTIONS": set_or_keys(list(sigs.MATH_FUNCTIONS.keys())),
        "MATH_CONSTANTS": set_or_keys(list(sigs.MATH_CONSTANTS.keys())),
        "FUNC_PARAM_NAMES": build_param_names(),

        # signatures.py builtin variable maps (name -> type)
        "BUILTIN_VARIABLES": type_record(sigs.BUILTIN_VARIABLES),
        "STRATEGY_VARIABLES": type_record(sigs.STRATEGY_VARIABLES),
        "BARSTATE_VARIABLES": type_record(sigs.BARSTATE_VARIABLES),
        "SYMINFO_VARIABLES": type_record(sigs.SYMINFO_VARIABLES),
        "TIMEFRAME_VARIABLES": type_record(sigs.TIMEFRAME_VARIABLES),
        "DISPLAY_VARIABLES": type_record(sigs.DISPLAY_VARIABLES),

        # tv_input_choices.py
        "INPUT_SOURCE_SERIES_IDS": set_or_keys(tvi.INPUT_SOURCE_SERIES_IDS),
        "INPUT_TIMEFRAME_CHOICES": set_or_keys(tvi.INPUT_TIMEFRAME_CHOICES),
        "INPUT_SESSION_PRESETS": set_or_keys(tvi.INPUT_SESSION_PRESETS),

        # support_checker.py reject / message / hint tables
        "HARD_REJECT_FUNC": dict(sc.HARD_REJECT_FUNC),
        "HARD_REJECT_NAMESPACE": dict(sc.HARD_REJECT_NAMESPACE),
        "NOT_YET_FUNC": dict(sc.NOT_YET_FUNC),
        "DIVERGENT_VARS": dict(sc.DIVERGENT_VARS),
        "BARSTATE_APPROX_VARS": dict(sc.BARSTATE_APPROX_VARS),
        "STRATEGY_UNSUPPORTED_PARAMS": {
            k: sorted(v) for k, v in sc.STRATEGY_UNSUPPORTED_PARAMS.items()
        },
        "CLOSED_TRADE_ACCESSOR_METHODS": set_or_keys(sc.CLOSED_TRADE_ACCESSOR_METHODS),
        "OPEN_TRADE_ACCESSOR_METHODS": set_or_keys(sc.OPEN_TRADE_ACCESSOR_METHODS),
        "STRATEGY_EXIT_PRICE_PARAMS": set_or_keys(sc.STRATEGY_EXIT_PRICE_PARAMS),
        "UNSUPPORTED_BARE_FUNCS": dict(sc.UNSUPPORTED_BARE_FUNCS),
        "UNSUPPORTED_NAMESPACES": dict(sc.UNSUPPORTED_NAMESPACES),
        "UNSUPPORTED_CONST_NAMESPACES": dict(sc.UNSUPPORTED_CONST_NAMESPACES),
        "UNSUPPORTED_MEMBERS": {
            f"{k[0]}.{k[1]}": v for k, v in sc.UNSUPPORTED_MEMBERS.items()
        },
        "UNSUPPORTED_NAMESPACE_VARS": dict(sc.UNSUPPORTED_NAMESPACE_VARS),
        "TA_PROPERTY_VARIABLES": set_or_keys(sc.TA_PROPERTY_VARIABLES),
        "SECURITY_ALLOWED_PARAMS": set_or_keys(sc.SECURITY_ALLOWED_PARAMS),
        "SECURITY_ADJUSTMENT_ALLOWED_VALUES": {
            k: sorted(v) for k, v in sc.SECURITY_ADJUSTMENT_ALLOWED_VALUES.items()
        },
        "SECURITY_CURRENT_SYMBOL_NAMES": set_or_keys(sc.SECURITY_CURRENT_SYMBOL_NAMES),
        "SECURITY_MAX_POSITIONAL": sc.SECURITY_MAX_POSITIONAL,
        "SECURITY_PARAM_ORDER": list(sc.SECURITY_PARAM_ORDER),
        "SYMINFO_SILENT_GAP_FIELDS": set_or_keys(sc.SupportChecker._SYMINFO_SILENT_GAP_FIELDS),
    }

    json.dump(tables, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
