#!/usr/bin/env python3
"""Derive the host members the generated C++ can read or write.

The generated strategy class derives from the engine host the emitter names
(``class GeneratedStrategy : public pineforge::source::PineStrategyHost``).
Inside it an unqualified name finds the class's own members first, so a script
identifier spelled like a host member the emitter reads -- a variable named
``session_isfirstbar_``, ``syminfo_`` or ``current_bar_`` -- would hide that
member from the generated code. This script writes
``pineforge_codegen/codegen/host_members.py``, the set ``_safe_name`` renames:

* the host: every public or protected member of that class and its bases,
  from clang's JSON AST of the ``#include`` lines the emitted C++ starts with;
* the emitter: every identifier one of ``pineforge_codegen/codegen``'s string
  constants spells outside a ``.`` or ``::`` access, or two such constants
  join (``"closed_trade_" + "profit"``); docstrings, the reserved-name lists
  of ``codegen/helpers.py``, dict keys and comparison operands are names the
  emitter looks up or avoids, not code it writes;

the set is their intersection. ``tests/test_host_member_names.py`` regenerates
it and checks every host member a transpiled battery names against it.

usage: python scripts/gen_host_members.py [--check] [--cxx CLANG] [-I DIR ...]

``-I`` defaults to $PINEFORGE_ENGINE_INCLUDE, $PINEFORGE_EIGEN_INCLUDE and
$PINEFORGE_GENERATED_INCLUDE; the compiler to $CXX when it is clang, else
clang++. ``--check`` writes nothing and exits 1 when the committed module is
not the derived one.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EMITTER = Path("pineforge_codegen") / "codegen"
OUTPUT = EMITTER / "host_members.py"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# An identifier and the qualifier that keeps it off the generated class, if
# any: a member of another object (``syminfo_.session``) or of a named scope
# (``PineStrategyHost::attach_pine_execution_adapter``). ``strat->run`` and
# ``this->x`` look the name up in the generated class first, as a bare name
# does.
_TOKEN = re.compile(r"(\.|::)?\s*\b([A-Za-z_][A-Za-z0-9_]*)")
# codegen/helpers.py's reserved-name lists: names the emitter avoids, not
# code it writes.
_NAME_LISTS = {"LEGACY_CPP_RESERVED", "CPP_KEYWORDS", "CPP_CONTEXTUAL", "CPP_STANDARD_MACROS",
               "CPP_EMITTER_NAMES", "BUILTIN_ACCESSOR_NAMES"}
_MEMBER_KINDS = {
    "FieldDecl", "VarDecl", "CXXMethodDecl", "FunctionTemplateDecl", "TypedefDecl",
    "TypeAliasDecl", "TypeAliasTemplateDecl", "CXXRecordDecl", "ClassTemplateDecl",
    "EnumDecl", "UsingDecl",
}


@dataclass(frozen=True)
class Derived:
    host_class: str
    includes: tuple[str, ...]
    host: frozenset[str]     # every accessible member of the host and its bases
    names: frozenset[str]    # the ones the emitter can spell


def find_clang(preferred: str | None = None) -> str | None:
    """A C++ compiler that is clang (its JSON AST dump), or None."""
    for candidate in (preferred, "clang++", "c++"):
        if not candidate:
            continue
        path = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if path is None:
            continue
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True,
                                     timeout=60).stdout
        except OSError:
            continue
        if "clang" in version.lower():
            return path
    return None


def emitter_target(repo: Path) -> tuple[tuple[str, ...], str]:
    """The ``#include`` lines the emitted C++ starts with and the class its
    strategy derives from, from the emitter itself."""
    probe = '//@version=6\nstrategy("host")\n'
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; import pineforge_codegen as pc; "
         "sys.stdout.write(pc.transpile(sys.stdin.read()))"],
        input=probe, capture_output=True, text=True, timeout=300,
        env=dict(os.environ, PYTHONPATH=str(repo)), cwd=repo)
    if proc.returncode != 0:
        raise RuntimeError(f"transpile failed:\n{proc.stderr}")
    cpp = proc.stdout
    includes = tuple(line for line in cpp.splitlines() if line.startswith("#include"))
    match = re.search(r"class\s+GeneratedStrategy\s*:\s*public\s+([\w:]+)", cpp)
    if match is None or not includes:
        raise RuntimeError("the emitted C++ names no host class")
    return includes, match.group(1)


def _dump(qualified: str, tu: Path, cxx: str, include_dirs: list[str]) -> list[dict]:
    cmd = [cxx, "-std=c++17", "-fsyntax-only", "-w"]
    for inc in include_dirs:
        cmd += ["-I", inc]
    cmd += ["-Xclang", "-ast-dump=json", "-Xclang", f"-ast-dump-filter={qualified}", str(tu)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"clang could not read the host header:\n{proc.stderr[-2000:]}")
    decoder, text, pos, out = json.JSONDecoder(), proc.stdout, 0, []
    while pos < len(text):
        brace = text.find("{", pos)
        if brace < 0:
            break
        obj, pos = decoder.raw_decode(text, brace)
        out.append(obj)
    return out


def host_members(host_class: str, includes: tuple[str, ...], cxx: str,
                 include_dirs: list[str]) -> frozenset[str]:
    """Every public or protected member name of ``host_class`` and its bases."""
    names: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        tu = Path(tmp) / "host.cpp"
        tu.write_text("\n".join(includes) + "\n")
        pending, seen = [host_class], set()
        while pending:
            qualified = pending.pop()
            if qualified in seen:
                continue
            seen.add(qualified)
            short = qualified.rsplit("::", 1)[-1]
            records = [d for d in _dump(qualified, tu, cxx, include_dirs)
                       if d.get("kind") == "CXXRecordDecl" and d.get("name") == short
                       and d.get("completeDefinition")]
            if not records:
                raise RuntimeError(f"no definition of {qualified} in the host headers")
            record = records[0]
            for base in record.get("bases", []):
                spelled = base["type"].get("desugaredQualType") or base["type"]["qualType"]
                pending.append(re.sub(r"<.*", "", spelled))
            # clang's JSON dump gives a member no access of its own: the
            # AccessSpecDecl before it does, and a class starts private.
            access = "private" if record.get("tagUsed") == "class" else "public"
            for decl in record.get("inner", []):
                if decl.get("kind") == "AccessSpecDecl":
                    access = decl.get("access", access)
                    continue
                if decl.get("isImplicit") or access == "private":
                    continue
                kind, name = decl.get("kind"), decl.get("name")
                if kind in _MEMBER_KINDS and name and _IDENT.fullmatch(name):
                    names.add(name)
                if kind == "EnumDecl" and not decl.get("scopedEnumTag"):
                    names.update(e["name"] for e in decl.get("inner", [])
                                 if e.get("kind") == "EnumConstantDecl")
    return frozenset(names)


def _names(tree: ast.Module) -> tuple[set[int], set[int]]:
    """The string constants that are not code the emitter writes: (docstrings
    and the reserved-name lists; the Pine names it looks up or matches on, a
    dict literal's keys and a comparison's operands -- ``func_name ==
    "cancel"``, ``node.member in ("ismarket", ...)``). A matched name can
    still end a spelling the emitter joins (``"pine_session_" + member``)."""
    prose, matched = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                prose.add(id(body[0].value))
        elif isinstance(node, ast.Dict):
            matched.update(id(k) for k in node.keys if isinstance(k, ast.Constant))
        elif isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                items = (operand.elts if isinstance(operand, (ast.Tuple, ast.List, ast.Set))
                         else [operand])
                matched.update(id(i) for i in items if isinstance(i, ast.Constant))
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in _NAME_LISTS for t in stmt.targets)):
            prose.update(id(n) for n in ast.walk(stmt.value) if isinstance(n, ast.Constant))
    return prose, matched


def emitter_vocabulary(repo: Path) -> tuple[frozenset[str], frozenset[str]]:
    """(the identifiers the emitter's string constants spell unqualified,
    the constants that are one identifier, which a spelling can join)."""
    spelled: set[str] = set()
    whole: set[str] = set()
    for path in sorted((repo / EMITTER).rglob("*.py")):
        if path == repo / OUTPUT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose, matched = _names(tree)
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Constant) or not isinstance(node.value, str)
                    or id(node) in prose):
                continue
            if _IDENT.fullmatch(node.value):
                whole.add(node.value)
            if id(node) not in matched:
                spelled.update(m.group(2) for m in _TOKEN.finditer(node.value)
                               if not m.group(1))
    return frozenset(spelled), frozenset(whole)


def _joined(name: str, whole: frozenset[str]) -> bool:
    """Whether two identifier constants join into ``name``: a prefix ending
    in ``_`` (``"closed_trade_"``) and the rest (``"profit"``)."""
    for cut in range(2, len(name)):
        head, tail = name[:cut], name[cut:]
        if (head.endswith("_") and head.strip("_") and not tail.startswith("_")
                and head in whole and tail in whole):
            return True
    return False


def derive(repo: Path, cxx: str, include_dirs: list[str]) -> Derived:
    includes, host_class = emitter_target(repo)
    host = host_members(host_class, includes, cxx, include_dirs)
    spelled, whole = emitter_vocabulary(repo)
    names = frozenset(n for n in host if n in spelled or _joined(n, whole))
    return Derived(host_class, includes, host, names)


def unqualified_identifiers(cpp: str) -> set[str]:
    """The identifiers C++ source names without a ``.`` or ``::`` qualifier
    (``strat->run`` counts: it looks ``run`` up in the generated class), outside
    comments, string and character literals and preprocessor lines."""
    text = re.sub(r"/\*.*?\*/", " ", cpp, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", text)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return {m.group(2) for m in _TOKEN.finditer(text) if not m.group(1)}


def render(derived: Derived) -> str:
    lines = [
        '"""Host members the generated C++ can read or write. GENERATED: do not',
        "edit; run ``scripts/gen_host_members.py``.",
        "",
        f"Every public or protected member of ``{derived.host_class}`` and its",
        "bases that the emitter's string constants spell. A script identifier",
        "with one of these names would hide the host's member from the generated",
        "strategy class, so ``_safe_name`` renames it.",
        '"""',
        "",
        "HOST_MEMBER_NAMES = frozenset({",
        *(f'    "{name}",' for name in sorted(derived.names)),
        "})",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when the committed module is not the derived one")
    parser.add_argument("--cxx", help="clang++ to dump the host header's AST with")
    parser.add_argument("-I", dest="include_dirs", action="append",
                        help="include directory (engine, Eigen, generated version.h)")
    args = parser.parse_args(argv)
    cxx = find_clang(args.cxx or os.environ.get("CXX"))
    if cxx is None:
        print("gen_host_members: needs clang (its JSON AST dump); set --cxx", file=sys.stderr)
        return 2
    include_dirs = args.include_dirs or [
        os.environ[key] for key in ("PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_EIGEN_INCLUDE",
                                    "PINEFORGE_GENERATED_INCLUDE") if os.environ.get(key)]
    derived = derive(REPO, cxx, include_dirs)
    text = render(derived)
    target = REPO / OUTPUT
    if args.check:
        current = target.read_text() if target.exists() else ""
        if current != text:
            print(f"gen_host_members: {OUTPUT} is not the derived set; run "
                  "scripts/gen_host_members.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT}: {len(derived.names)} names, up to date")
        return 0
    target.write_text(text)
    print(f"wrote {OUTPUT}: {len(derived.names)} of the {len(derived.host)} accessible "
          f"members of {derived.host_class}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
