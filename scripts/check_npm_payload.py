#!/usr/bin/env python3
"""Release guard: the npm package must ship the archive names this repo builds.

scripts/build-npm-package.mjs writes `tunaxis_codegen-<VERSION>.tar.gz` and a
`tunaxis_codegen/` source dir into npm/. npm/package.json ("files") and
npm/index.mjs (archivePath, codegenSourceDir) decide what is packed and what
consumers resolve. If they still name `pineforge_codegen-*` the published npm
package would ship without its Pyodide payload (npm pack drops files no pattern
matches) or point consumers at a file that does not exist.

Run from release.yml before anything is built or published (not from pytest:
the TX-NPM change lands in a separate PR and this check is red until it does).

  check_npm_payload.py [REPO_ROOT]

Prints one line per problem to stderr and exits 1; exits 0 when consistent.
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import List

ARCHIVE_TEMPLATE = "tunaxis_codegen-{version}.tar.gz"
SOURCE_DIR = "tunaxis_codegen"

_ARCHIVE_PATH_RE = re.compile(r"export const archivePath\s*=\s*join\(HERE,\s*`([^`]*)`\)")
_SOURCE_DIR_RE = re.compile(r'export const codegenSourceDir\s*=\s*join\(HERE,\s*"([^"]*)"\)')


def check(root: Path) -> List[str]:
    problems: List[str] = []
    version = (root / "VERSION").read_text(encoding="utf8").strip()
    archive = ARCHIVE_TEMPLATE.format(version=version)

    pkg = json.loads((root / "npm" / "package.json").read_text(encoding="utf8"))
    files = pkg.get("files")
    if not isinstance(files, list):
        problems.append("npm/package.json has no `files` list")
        files = []
    if not any(isinstance(p, str) and fnmatch.fnmatchcase(archive, p) for p in files):
        problems.append(
            f"npm/package.json `files` has no pattern matching {archive} "
            f"(patterns: {files}); the Pyodide archive would be left out of the package"
        )
    if not any(isinstance(p, str) and p.startswith(SOURCE_DIR + "/") for p in files):
        problems.append(f"npm/package.json `files` has no `{SOURCE_DIR}/...` pattern")

    index = (root / "npm" / "index.mjs").read_text(encoding="utf8")
    m = _ARCHIVE_PATH_RE.search(index)
    if not m:
        problems.append("npm/index.mjs: cannot find the archivePath definition")
    else:
        resolved = m.group(1).replace("${release.codegen}", version)
        if resolved != archive:
            problems.append(f"npm/index.mjs archivePath resolves to {resolved}, build writes {archive}")
    m = _SOURCE_DIR_RE.search(index)
    if not m:
        problems.append("npm/index.mjs: cannot find the codegenSourceDir definition")
    elif m.group(1) != SOURCE_DIR:
        problems.append(f"npm/index.mjs codegenSourceDir is {m.group(1)!r}, build writes {SOURCE_DIR!r}")
    return problems


def main(argv: List[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    for p in problems:
        print(f"check_npm_payload: {p}", file=sys.stderr)
    if problems:
        print(
            "check_npm_payload: release refused: the npm package (TX-NPM) must land in the same "
            "release window as the tunaxis_codegen rename",
            file=sys.stderr,
        )
        return 1
    print("check_npm_payload: npm package ships tunaxis_codegen-* as built")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
