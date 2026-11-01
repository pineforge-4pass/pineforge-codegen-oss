#!/usr/bin/env python3
"""Compile a private Pine probe population without publishing its source bytes.

Run from a source checkout with the dev extra installed. The native compile
implementation and flags are imported from the same helper as the PR sweep.
CLI only, not re-entrant: it sets process environment variables, sys.path and
sys.dont_write_bytecode and does not restore them.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG = sys.stderr
VERDICTS = (
    "translated_compiled", "front_end_refusal", "compile_failed", "internal_error",
)
# GCC normally quotes identifiers and types. Retain only closed diagnostic
# prefixes, even when an unquoted #error, filename or assertion contains data.
COMPILER_PREFIXES = (
    "no matching function for call to", "invalid initialization of reference of type",
    "invalid conversion from", "cannot convert", "could not convert",
    "invalid use of", "invalid operands of types", "request for member",
    "too few arguments to function", "too many arguments to function",
    "static assertion failed", "expected", "stray", "#error",
)


class RefusedPath(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class DriverError(RuntimeError):
    """An invocation or environment error; never contains private diagnostics."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's normal error includes arbitrary argument values.
        raise DriverError()


def _sources(root: Path) -> list[Path]:
    if not root.is_dir():
        raise RefusedPath("sources must be a directory")
    found = []

    def onerror(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            # Check all entries, including non-Pine files and directory links.
            if path.is_symlink():
                raise RefusedPath("sources tree contains a symlink")
        found.extend(
            Path(directory) / name for name in files if name.endswith(".pine")
        )
    for path in found:
        if not path.is_file():
            raise RefusedPath("source must be a regular file")
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def _ids(paths: list[Path], root: Path, index: Path | None) -> list[str]:
    labels = [path.relative_to(root).as_posix() for path in paths]
    if index is None:
        return labels
    index = index.expanduser().resolve()
    rows = json.loads(index.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RefusedPath("index must cover sources exactly")
    by_path = {}
    seen_ids = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "path"}:
            raise RefusedPath("index must cover sources exactly")
        identifier, relative = row["id"], row["path"]
        if not isinstance(identifier, str) or not identifier or not isinstance(relative, str):
            raise RefusedPath("index must cover sources exactly")
        path = Path(relative)
        if (
            path.is_absolute() or ".." in path.parts or path.as_posix() != relative
            or relative in by_path or identifier in seen_ids
        ):
            raise RefusedPath("index must cover sources exactly")
        by_path[relative] = identifier
        seen_ids.add(identifier)
    # An index names the entire discovered population; it cannot filter failures.
    if set(by_path) != set(labels):
        raise RefusedPath("index must cover sources exactly")
    return [by_path[label] for label in labels]


def _git_sha(tree: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    sha = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise DriverError()
    return sha.lower()


def _engine_sha(tree: Path) -> str:
    git_path = tree / ".git"
    if git_path.exists():
        return _git_sha(tree)
    pin = ROOT / "ci/engine-pin"
    value = pin.read_text(encoding="ascii")
    if not re.fullmatch(r"[0-9a-f]{40}\n", value):
        raise DriverError()
    return value.strip()


def _native(engine: Path | None):
    compiler = shutil.which("g++")
    if compiler is None:
        raise RefusedPath("g++ not found")
    os.environ["CXX"] = str(compiler)
    os.environ["PINEFORGE_REQUIRE_COMPILE"] = "1"
    os.environ["LC_ALL"] = "C"
    os.environ["GCC_COLORS"] = ""

    if engine is not None:
        engine = engine.expanduser().resolve()
        include = engine / "include"
        header = include / "pineforge/engine.hpp"
        if not header.is_file():
            raise RefusedPath("engine headers not found")
        os.environ["PINEFORGE_ENGINE_INCLUDE"] = str(include)

    sys.path.insert(0, str(ROOT))
    from tests import _compile

    # Check before calling the pytest helper: this CLI never skips, even when
    # its parent process has not set PINEFORGE_REQUIRE_COMPILE.
    if _compile._ENGINE_INC is None:
        raise RefusedPath("engine headers not found")
    if _compile._EIGEN_INC is None:
        raise RefusedPath("Eigen headers not found")
    if _compile._COMPILER is None:
        raise RefusedPath("g++ not found")
    include = _compile._ENGINE_INC
    generated = _compile._GENERATED_INC or include
    version = generated / "pineforge/version.h"
    if not version.is_file():
        raise RefusedPath("version header not found")

    # Use the same minimal strategy preflight as the PR sweep, rather than
    # relying on a handwritten approximation of the generated include order.
    from pineforge_codegen import transpile

    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            preflight = transpile('//@version=6\nstrategy("probe compile preflight")\n')
        # A broken native environment is a driver failure (2), before it can be
        # mistaken for a population full of generated-C++ defects (1).
        _compile.compile_cpp(
            preflight, label="probe-preflight", extra_flags=_compile.STRATEGY_FP_FLAGS,
        )
    except Exception:
        raise RefusedPath("native preflight failed") from None
    return _compile, engine if engine is not None else include.parent


def _compiler_first_line(diagnostics: str) -> str:
    for line in diagnostics.splitlines():
        match = re.search(r"\b((?:fatal )?error):\s*(.*)", line)
        if match is None:
            continue
        severity, message = match.groups()
        # Never include the diagnostic's source path, location, quote contents,
        # or the continuation that may contain a generated source excerpt.
        prefix = re.split(r"""['"`‘’“”]""", message, maxsplit=1)[0].strip()
        safe = next(
            (item for item in COMPILER_PREFIXES
             if prefix == item or prefix.startswith(item + " ")),
            "",
        )
        return f"{severity}: {safe + ' ' if safe else ''}<src>"[:300]
    return "error: <src>"


def _refusal(error) -> dict:
    from pineforge_codegen.diagnostic_codes import render_diagnostic
    from pineforge_codegen.errors import Level

    diagnostic = next(
        (item for item in error.diagnostics if item.level == Level.ERROR),
        error.diagnostics[0] if error.diagnostics else None,
    )
    if diagnostic is None:
        return {"verdict": "front_end_refusal", "code": "PF-E0000", "first_line": "<src>"}
    code = diagnostic.code
    # Render the real diagnostic's catalog template, with *all* argument kinds
    # redacted, including unquoted identifiers, numbers and free-form messages.
    message, _ = render_diagnostic(code, dict.fromkeys(diagnostic.args, "<src>"))
    return {
        "verdict": "front_end_refusal", "code": code,
        "first_line": (message or "<src>").splitlines()[0][:300],
    }


def _internal(error: BaseException) -> dict:
    return {"verdict": "internal_error", "first_line": type(error).__name__}


def _compile_one(cpp: str, identifier: str, native) -> dict:
    result = {}
    try:
        result["cpp_sha256"] = hashlib.sha256(cpp.encode("utf-8")).hexdigest()
        native.compile_cpp(cpp, label=identifier, extra_flags=native.STRATEGY_FP_FLAGS)
    except native.CppCompileError as error:
        result.update(
            verdict="compile_failed", first_line=_compiler_first_line(error.diagnostics),
        )
    except Exception as error:
        result.update(_internal(error))
    else:
        result["verdict"] = "translated_compiled"
    return result


def _sweep(paths: list[Path], entries: list[dict], native, jobs: int) -> None:
    from pineforge_codegen import transpile
    from pineforge_codegen.errors import CompileError

    pending = deque()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for path, entry in zip(paths, entries):
            if entry.get("first_line") is not None:
                continue
            try:
                if path.is_symlink():
                    raise RefusedPath("sources tree contains a symlink")
                payload = path.read_bytes()
                entry["source_sha256"] = hashlib.sha256(payload).hexdigest()
                # Transpile serially, as #282 does. Do not let imported code
                # publish a warning, source-bearing traceback or debug print.
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    cpp = transpile(payload.decode("utf-8"), filename=entry["id"])
            except CompileError as error:
                entry.update(_refusal(error))
            except Exception as error:
                entry.update(_internal(error))
            else:
                entry.pop("first_line", None)
                pending.append((entry, pool.submit(_compile_one, cpp, entry["id"], native)))
            # Bound both resident generated C++ and concurrent compilers.
            if len(pending) >= jobs:
                previous, future = pending.popleft()
                previous.update(future.result())
        while pending:
            previous, future = pending.popleft()
            previous.update(future.result())


def _parser() -> Parser:
    parser = Parser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--jobs", default=str(os.cpu_count() or 1))
    parser.add_argument("--index", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    report = {
        "schema": 1, "engine_sha": None, "codegen_sha": None,
        "source_count": 0, "counts": dict.fromkeys(VERDICTS, 0), "entries": [],
    }
    destination = None
    status = 2
    args = None
    failure = None
    sys.dont_write_bytecode = True
    try:
        args = _parser().parse_args(argv)
        # Validate output before accepting it as a report destination.
        lexical_root = args.sources.expanduser().absolute()
        root = lexical_root.resolve()
        candidate = args.out.expanduser().resolve()
        if candidate.is_relative_to(root):
            raise RefusedPath("output is inside sources tree")
        destination = candidate
        if lexical_root.is_symlink():
            raise RefusedPath("sources tree contains a symlink")
        jobs = int(args.jobs)
        if jobs < 1:
            raise RefusedPath("jobs must be at least 1")
        paths = _sources(root)
        if not paths:
            raise RefusedPath("source selection is empty")
        identifiers = _ids(paths, root, args.index)
        entries = report["entries"] = [
            {"id": identifier, "source_sha256": None, "verdict": "internal_error"}
            for identifier in identifiers
        ]
        # Hash the population before preflight so even an environment crash
        # yields a complete, identified population. Rehash the bytes actually
        # translated in _sweep; files are not assumed immutable between reads.
        for path, entry in zip(paths, entries):
            try:
                if path.is_symlink():
                    raise RefusedPath("sources tree contains a symlink")
                entry["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception as error:
                entry.update(_internal(error))

        # The imported helper uses tempfile for both C++ and synthesized
        # version headers. Contain *all* of those writes in this owned directory.
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if (
            temporary_root.is_relative_to(root)
            or temporary_root.is_relative_to(destination.parent)
        ):
            raise RefusedPath("temporary directory is inside sources or report directory")
        with tempfile.TemporaryDirectory(
            prefix="pineforge-probe-compile-", dir=temporary_root,
        ) as work:
            old_tempdir = tempfile.tempdir
            old_tmpdir = os.environ.get("TMPDIR")
            tempfile.tempdir = work
            os.environ["TMPDIR"] = work
            try:
                native, engine = _native(args.engine)
                report["codegen_sha"] = _git_sha(ROOT)
                report["engine_sha"] = _engine_sha(engine)
                _sweep(paths, entries, native, jobs)
            finally:
                tempfile.tempdir = old_tempdir
                if old_tmpdir is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = old_tmpdir
        status = int(any(row["verdict"] != "translated_compiled" for row in entries))
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 0 and args is None:
            # Only argparse's --help can exit successfully without compiling.
            status = 0
        else:
            for entry in report["entries"]:
                if entry["verdict"] == "internal_error" and "first_line" not in entry:
                    entry.update(_internal(error))
            if isinstance(error, RefusedPath):
                failure = f"compile_probes: refused: {error.reason}"
            else:
                failure = f"compile_probes: internal error: {type(error).__name__}"
            status = 2
    finally:
        report["source_count"] = len(report["entries"])
        for row in report["entries"]:
            report["counts"][row["verdict"]] += 1
        if destination is not None:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8",
                )
            except BaseException as error:
                failure = f"compile_probes: internal error: {type(error).__name__}"
                status = 2
    if failure is not None:
        print(failure, file=LOG)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
