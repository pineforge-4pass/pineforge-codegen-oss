"""Native-CPython oracle for the conformance gate.

Reads a JSON array of {"name","src"} from stdin, runs the CANONICAL glue's
transpile_json on each, and writes a JSON object {name: result} to stdout.
Each result is {"json": <transpile_json string>} on a normal return, or
{"unexpected": "<ExcType>: <msg>"} if transpile_json raised something other
than CompileError (CompileError is already encoded inside the json string).
"""

from __future__ import annotations

import json
import os
import sys

GLUE = os.path.join(os.path.dirname(__file__), "glue.py")
exec(compile(open(GLUE).read(), GLUE, "exec"))  # defines transpile_json  # noqa: S102


def main() -> None:
    items = json.load(sys.stdin)
    out: dict[str, dict] = {}
    for item in items:
        name = item["name"]
        src = item["src"]
        try:
            out[name] = {"json": transpile_json(src)}  # noqa: F821 — from exec
        except Exception as exc:  # noqa: BLE001 — capture for parity, don't throw
            out[name] = {"unexpected": f"{type(exc).__name__}: {exc}"}
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
