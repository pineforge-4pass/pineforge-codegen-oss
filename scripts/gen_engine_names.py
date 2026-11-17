#!/usr/bin/env python3
"""Derive the file-scope collision inventory from the paired public headers.

Run with the same engine/Eigen/generated include roots as native compilation.
--check is strict: missing, stale or differently scoped declarations fail.
No ordinary nested namespace or record member is implicitly imported.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.gen_host_members import emitter_target, find_clang

OUTPUT = REPO / "pineforge_codegen/codegen/engine_names.py"
HEADERS = (
    "pineforge/map.hpp", "pineforge/matrix.hpp",
    "pineforge/generic_matrix.hpp", "pineforge/drawing.hpp",
)
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
TYPE_KINDS = {
    "CXXRecordDecl", "RecordDecl", "EnumDecl", "TypedefDecl", "TypeAliasDecl",
    "ClassTemplateDecl", "ClassTemplateSpecializationDecl", "TypeAliasTemplateDecl",
}
VALUE_KINDS = {
    "FunctionDecl", "FunctionTemplateDecl", "VarDecl", "VarTemplateDecl",
    "EnumConstantDecl",
}
LINKAGE_KINDS = {"LinkageSpecDecl", "ExternCContextDecl", "ExportDecl"}


def visible_names(tree: dict) -> dict[str, set[str]]:
    """Global declarations plus declarations imported by using pineforge.

    Namespace re-openings share a qualified path. Using directives follow
    their nominated namespace; using declarations follow the shadow target.
    Inline/anonymous namespaces contribute members. A normal child namespace
    contributes its name, and a class contributes only its type name.
    """
    paths: dict[str, tuple] = {}
    namespaces: dict[tuple, list[dict]] = {}

    def index(nodes, path=()):
        for node in nodes:
            if node.get("kind") == "NamespaceDecl":
                name = node.get("name") or ("anonymous", node.get("id"))
                qualified = (*path, name)
                paths[node["id"]] = qualified
                namespaces.setdefault(qualified, []).append(node)
                index(node.get("inner", ()), qualified)
            elif node.get("kind") in LINKAGE_KINDS:
                index(node.get("inner", ()), path)

    index(tree.get("inner", ()))
    result = {kind: set() for kind in ("types", "namespaces", "values")}
    visiting: set[tuple] = set()

    def add(category, name):
        if name and IDENT.fullmatch(name):
            result[category].add(name)

    def namespace(path):
        if path in visiting:
            return
        visiting.add(path)
        for declaration in namespaces.get(path, ()):
            collect(declaration.get("inner", ()), path)

    def declaration(node):
        kind, name = node.get("kind"), node.get("name")
        if kind in TYPE_KINDS:
            add("types", name)
            if kind == "EnumDecl" and not node.get("scopedEnumTag"):
                for child in node.get("inner", ()):
                    if child.get("kind") == "EnumConstantDecl":
                        add("values", child.get("name"))
        elif kind in VALUE_KINDS:
            add("values", name)
        elif kind == "NamespaceAliasDecl":
            add("namespaces", name)

    def collect(nodes, path=()):
        for node in nodes:
            kind = node.get("kind")
            if kind == "NamespaceDecl":
                add("namespaces", node.get("name"))
                if node.get("isInline") or not node.get("name"):
                    namespace(paths[node["id"]])
            elif kind in LINKAGE_KINDS:
                collect(node.get("inner", ()), path)
            elif kind == "UsingDirectiveDecl":
                target = node.get("nominatedNamespace", {}).get("id")
                if target not in paths:
                    raise ValueError("using directive has no namespace definition")
                namespace(paths[target])
            elif kind == "UsingShadowDecl":
                declaration(node.get("target", {}))
            elif kind == "UsingDecl":
                # Clang emits the typed UsingShadowDecl beside this spelling.
                continue
            else:
                declaration(node)

    collect(tree.get("inner", ()))
    namespace(("pineforge",))
    return result


def header_inventory(includes, cxx, include_dirs):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "names.cpp"
        source.write_text("\n".join(includes) + "\n", encoding="utf-8")
        common = [cxx, "-std=c++17"]
        for include in include_dirs:
            common.extend(("-I", include))
        ast_run = subprocess.run(
            [*common, "-fsyntax-only", "-Xclang", "-ast-dump=json", str(source)],
            capture_output=True, text=True,
        )
        if ast_run.returncode:
            raise RuntimeError(ast_run.stderr)
        tree = json.loads(ast_run.stdout)
        if tree.get("kind") != "TranslationUnitDecl":
            raise ValueError("clang did not return the complete translation unit")
        names = visible_names(tree)
        macro_run = subprocess.run(
            [*common, "-E", "-dM", str(source)], capture_output=True, text=True,
        )
        if macro_run.returncode:
            raise RuntimeError(macro_run.stderr)
        names["macros"] = set(re.findall(
            r"^#define ([A-Za-z_][A-Za-z_0-9]*)", macro_run.stdout, re.M,
        ))
        return names


def derive(repo, cxx, include_dirs):
    includes, _host = emitter_target(repo)
    baseline = header_inventory(includes, cxx, include_dirs)
    occupied = set().union(*baseline.values())
    optional = {}
    for header in HEADERS:
        names = header_inventory(
            (*includes, f"#include <{header}>"), cxx, include_dirs,
        )
        optional[header] = set().union(*names.values()) - occupied
    return baseline, optional


def render(derived):
    baseline, optional = derived
    lines = [
        '"""File-scope names from the paired public headers. GENERATED:',
        'run scripts/gen_engine_names.py; do not edit the inventory by hand."""',
        "",
    ]

    def names(label, values, indent=""):
        lines.append(f'{indent}{label}frozenset("""')
        lines.extend(textwrap.wrap(
            " ".join(sorted(values)), width=88,
            initial_indent=indent + "    ", subsequent_indent=indent + "    ",
        ))
        lines.append(indent + '""".split())')

    for category, label in (
        ("types", "ENGINE_TYPE_NAMES"), ("namespaces", "ENGINE_NAMESPACE_NAMES"),
        ("values", "ENGINE_VALUE_NAMES"), ("macros", "ENGINE_MACRO_NAMES"),
    ):
        names(label + " = ", baseline[category])
        lines.append("")
    lines.extend((
        "ENGINE_IMPORTED_NAMES = (",
        "    ENGINE_TYPE_NAMES | ENGINE_NAMESPACE_NAMES | ENGINE_VALUE_NAMES",
        ")", "", "ENGINE_HEADER_NAMES = {",
    ))
    for header, values in optional.items():
        names(json.dumps(header) + ": ", values, "    ")
        lines[-1] += ","
    lines.extend(("}", ""))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--cxx")
    parser.add_argument("-I", dest="include_dirs", action="append")
    args = parser.parse_args(argv)
    cxx = find_clang(args.cxx or os.environ.get("CXX"))
    if cxx is None:
        raise RuntimeError("engine-name inventory requires clang")
    includes = args.include_dirs or [
        os.environ[key] for key in (
            "PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_EIGEN_INCLUDE",
            "PINEFORGE_GENERATED_INCLUDE",
        ) if os.environ.get(key)
    ]
    text = render(derive(REPO, cxx, includes))
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != text:
            print("engine_names.py differs from the paired header inventory; "
                  "run scripts/gen_engine_names.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
