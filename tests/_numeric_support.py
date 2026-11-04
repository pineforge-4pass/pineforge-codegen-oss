"""Process and identity helpers of the numeric inventory tests.

Recovered unchanged from tests/test_numeric_missing_value_rows.py at
27e0e8dc879db9e24b8a621a6110e9d39119a7ba (the component created at 15b182c2),
which is no longer in the tree: ``_sha`` hashes the bytes it is given, ``_json``
writes the fields it is given, ``_process`` runs the argv it is given and
retains the raw outputs and status before anything is interpreted, and
``_git_identity`` reads the real Git HEAD and tree of a checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _process(command, directory: Path, name: str, *, stdin: bytes | None = None):
    """Retain raw outputs before interpreting any return code."""
    _json(directory / f"{name}.command.json", {
        "argv": list(map(str, command)), "cwd": str(directory), "LC_ALL": "C",
        "stdin_sha256": None if stdin is None else _sha(stdin),
    })
    try:
        result = subprocess.run(
            list(map(str, command)), cwd=directory, input=stdin,
            capture_output=True, env={**os.environ, "LC_ALL": "C"},
        )
    except OSError as error:
        _json(directory / f"{name}.result.json", {
            "returncode": None, "setup_error": repr(error),
        })
        raise
    (directory / f"{name}.stdout").write_bytes(result.stdout)
    (directory / f"{name}.stderr").write_bytes(result.stderr)
    _json(directory / f"{name}.result.json", {"returncode": result.returncode})
    return result


def _git_identity(path: Path, directory: Path, label: str):
    result = _process(
        ["git", "-C", path, "rev-parse", "HEAD", "HEAD^{tree}"],
        directory, label,
    )
    assert result.returncode == 0, f"Missing Git identity: {directory}"
    fields = result.stdout.decode("ascii").splitlines()
    assert len(fields) == 2 and all(re.fullmatch(r"[0-9a-f]{40}", v) for v in fields)
    return {"head": fields[0], "tree": fields[1]}
