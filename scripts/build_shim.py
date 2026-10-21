#!/usr/bin/env python3
"""Build the old-name forwarding distribution from the root alias and VERSION.

Usage: python scripts/build_shim.py --outdir dist/shim

Only the staging tree contains a rendered pyproject and a copy of the alias.
The resulting sdist is self-contained, so PyPI can build its wheel without this
checkout. Hatchling is a build prerequisite; this script never installs it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from release_version import channels


ROOT = Path(__file__).resolve().parents[1]
_DYNAMIC = 'dynamic = ["version", "dependencies"]'


def stage_shim(destination: Path) -> None:
    """Render one build project using the repository's only version/alias."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pypi_version = channels(version)["pypi"]
    template = (ROOT / "shim" / "pyproject.toml").read_text(encoding="utf-8")
    if template.count(_DYNAMIC) != 1:
        raise ValueError("shim pyproject must declare dynamic version and dependencies once")
    rendered = template.replace(
        _DYNAMIC,
        'dynamic = ["version"]\n'
        f'dependencies = ["tunaxis-codegen=={pypi_version}"]',
    )
    # Validate the rendered TOML before building any artifact.
    project = tomllib.loads(rendered)["project"]
    if project["dependencies"] != [f"tunaxis-codegen=={pypi_version}"]:
        raise ValueError("shim dependency must exactly pin this VERSION")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "pyproject.toml").write_text(rendered, encoding="utf-8")
    shutil.copyfile(ROOT / "VERSION", destination / "VERSION")
    shutil.copyfile(ROOT / "LICENSE", destination / "LICENSE")
    shutil.copyfile(ROOT / "shim" / "README.md", destination / "README.md")
    shutil.copytree(
        ROOT / "pineforge_codegen", destination / "pineforge_codegen",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist" / "shim")
    args = parser.parse_args()
    outdir = args.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tunaxis-codegen-shim-") as temporary:
        staged = Path(temporary) / "project"
        stage_shim(staged)
        # The same PEP 517 backend calls that `python -m build` makes, without
        # an isolated build environment fetching anything from the network.
        subprocess.run(
            [sys.executable, "-c",
             "import sys; from hatchling.build import build_sdist, build_wheel; "
             "build_sdist(sys.argv[1]); build_wheel(sys.argv[1])",
             str(outdir)],
            cwd=staged, check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
