"""Deterministic file slices for the compile job's remaining pytest suite."""

from __future__ import annotations

import argparse
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parent.parent
SUITE_PARTS = 4
SWEEP_FILES = frozenset({
    "tests/test_translate_implies_compile.py",
    "tests/test_require_compile.py",
    "tests/test_ci_compile_contract.py",
})


def collected_test_files(root: Path = ROOT) -> list[str]:
    """Return sorted repository-relative files selected by the pytest config."""
    options = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["pytest"]["ini_options"]
    return sorted({
        path.relative_to(root).as_posix()
        for testpath in options["testpaths"]
        for pattern in options["python_files"]
        for path in (root / testpath).rglob(pattern)
        if path.is_file()
    })


def suite_slice(index: int, root: Path = ROOT) -> list[str]:
    """Exclude sweep-run files, then take index K modulo four in path order."""
    if not 0 <= index < SUITE_PARTS:
        raise ValueError(f"suite index must be between 0 and {SUITE_PARTS - 1}")
    remaining = [path for path in collected_test_files(root) if path not in SWEEP_FILES]
    files = remaining[index::SUITE_PARTS]
    if not files:
        raise ValueError(f"suite slice {index} is empty")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=int, help="suite slice index (0 through 3)")
    args = parser.parse_args(argv)
    try:
        files = suite_slice(args.index)
    except ValueError as error:
        parser.error(str(error))
    for path in files:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
