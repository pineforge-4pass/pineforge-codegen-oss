#!/usr/bin/env python3
"""One VERSION value, every release channel's spelling (run by the release workflows).

VERSION holds semver: X.Y.Z or X.Y.Z-{alpha,beta,rc}.N, the subset with exactly
one PEP 440 spelling. From 1.0.0-rc.1 it derives:

  pypi          1.0.0rc1      what hatchling builds from VERSION and PyPI serves
  npm           1.0.0-rc.1    @pineforge/codegen-pyodide's version
  npm_dist_tag  next          `latest` for a stable release only
  git_tag       v1.0.0-rc.1   also the GitHub release, marked prerelease
  prerelease    true

  next --current CUR --bump {patch,minor,major} [--override VERSION]
  channels VERSION

print key=value lines for $GITHUB_OUTPUT; errors go to stderr and exit 1.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional

_VERSION_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(alpha|beta|rc)\.(0|[1-9][0-9]*))?"
)
_PEP440_PRE = {"alpha": "a", "beta": "b", "rc": "rc"}


def _parse(version: str):
    m = _VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if not m:
        raise ValueError(f"rejected version {version!r}: VERSION takes X.Y.Z or "
                         "X.Y.Z-{alpha,beta,rc}.N (for example 1.0.0-rc.1)")
    major, minor, patch, pre, num = m.groups()
    return int(major), int(minor), int(patch), pre, num


def channels(version: str) -> Dict[str, str]:
    major, minor, patch, pre, num = _parse(version)
    core = f"{major}.{minor}.{patch}"
    return {
        "version": version,
        "pypi": core if pre is None else f"{core}{_PEP440_PRE[pre]}{num}",
        "npm": version,
        "npm_dist_tag": "latest" if pre is None else "next",
        "git_tag": f"v{version}",
        "prerelease": "false" if pre is None else "true",
    }


def next_version(current: str, bump: str, override: str) -> str:
    if override:
        new = override[1:] if override.startswith("v") else override
        _parse(new)
        return new
    major, minor, patch, pre, _ = _parse(current)
    if pre is not None:
        raise ValueError(f"VERSION {current} is a prerelease: set override to its release "
                         f"({major}.{minor}.{patch}) or to the next prerelease")
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "major":
        return f"{major + 1}.0.0"
    raise ValueError(f"bad bump {bump!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_next = sub.add_parser("next")
    p_next.add_argument("--current", required=True)
    p_next.add_argument("--bump", required=True)
    p_next.add_argument("--override", default="")
    p_channels = sub.add_parser("channels")
    p_channels.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "next":
            new = next_version(args.current.strip(), args.bump, args.override.strip())
            out = {"cur": args.current.strip(), "new": new, **channels(new)}
            note = f"Release {out['cur']} -> {new}"
        else:
            out = channels(args.version)
            note = f"{args.version}: {out['pypi']} on PyPI, npm dist-tag {out['npm_dist_tag']}"
    except ValueError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    for key, value in out.items():
        print(f"{key}={value}")
    print(note, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
