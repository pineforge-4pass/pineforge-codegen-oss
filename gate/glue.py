# CANONICAL GLUE — runtime-equivalent to the body of PY_GLUE in
# pineforge-app/apps/web/lib/pyodide-transpiler/glue.ts (produces identical
# transpile_json output). The browser worker and this gate run the same logic,
# so the gate's parity guarantee reflects shipped behavior.
# (Phase 3: ship this from the npm package so there is one source of truth.)
import json
import sys

if "/codegen" not in sys.path:
    sys.path.insert(0, "/codegen")

from pineforge_codegen import transpile_full
from pineforge_codegen.errors import CompileError


def _diagnostic_entries(diagnostics) -> list:
    diags = []
    for d in diagnostics:
        loc = d.location
        message = d.message + " — " + d.hint if getattr(d, "hint", None) else d.message
        entry = {
            "line": loc.line if loc else 1,
            "col": loc.col if loc else 1,
            "message": message,
            "severity": getattr(d.level, "value", "error"),
            # The stable code and named arguments of the catalog templates the
            # message (and the hint after " — ") render from.
            "code": d.code,
            "args": d.args,
            # A template for translation/fallback, not pre-rendered English.
            "user_message": d.user_message,
        }
        end_col = getattr(loc, "end_col", None) if loc else None
        if end_col is not None:
            entry["endCol"] = end_col
        diags.append(entry)
    return diags


def transpile_json(source: str) -> str:
    try:
        full = transpile_full(source)
    except CompileError as e:
        return json.dumps({"ok": False, "error": str(e),
                           "diagnostics": _diagnostic_entries(e.diagnostics)})
    # A script that transpiled carries warnings and notes in the same format.
    return json.dumps({"ok": True, "cpp": full["cpp"], "inputs": full["inputs"],
                       "strategyParams": full["strategyParams"],
                       "diagnostics": _diagnostic_entries(full["diagnostics"]),
                       "requests": full["requests"]})
