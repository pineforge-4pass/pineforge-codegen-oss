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


def transpile_json(source: str) -> str:
    try:
        full = transpile_full(source)
    except CompileError as e:
        diags = []
        for d in e.diagnostics:
            loc = d.location
            message = d.message + " — " + d.hint if getattr(d, "hint", None) else d.message
            entry = {
                "line": loc.line if loc else 1,
                "col": loc.col if loc else 1,
                "message": message,
                "severity": getattr(d.level, "value", "error"),
            }
            end_col = getattr(loc, "end_col", None) if loc else None
            if end_col is not None:
                entry["endCol"] = end_col
            diags.append(entry)
        return json.dumps({"ok": False, "error": str(e), "diagnostics": diags})
    return json.dumps({"ok": True, "cpp": full["cpp"], "inputs": full["inputs"], "strategyParams": full["strategyParams"]})
