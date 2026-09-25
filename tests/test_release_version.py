# tests/test_release_version.py
"""scripts/release_version.py: one VERSION value, every channel's spelling.

VERSION holds semver (1.0.0 or 1.0.0-rc.1). PyPI gets the PEP 440 spelling
hatchling builds (1.0.0rc1), npm the semver one on dist-tag `next` for a
prerelease, git the tag v1.0.0-rc.1 and a GitHub prerelease.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_version.py"
_spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

# semver.org's reference pattern (what npm's version field must match).
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")


def cli(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, check=False)
    pairs = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return proc, pairs


def test_rc_gets_every_channel_its_spelling():
    assert rv.channels("1.0.0-rc.1") == {
        "version": "1.0.0-rc.1",
        "pypi": "1.0.0rc1",
        "npm": "1.0.0-rc.1",
        "npm_dist_tag": "next",
        "git_tag": "v1.0.0-rc.1",
        "prerelease": "true",
    }


def test_stable_release_keeps_one_spelling_and_latest():
    assert rv.channels("1.0.0") == {
        "version": "1.0.0",
        "pypi": "1.0.0",
        "npm": "1.0.0",
        "npm_dist_tag": "latest",
        "git_tag": "v1.0.0",
        "prerelease": "false",
    }


@pytest.mark.parametrize("semver,pypi", [
    ("1.0.0-alpha.2", "1.0.0a2"),
    ("1.0.0-beta.3", "1.0.0b3"),
    ("1.2.3-rc.10", "1.2.3rc10"),
    ("0.10.4", "0.10.4"),
])
def test_pypi_spelling(semver, pypi):
    assert rv.channels(semver)["pypi"] == pypi


@pytest.mark.parametrize("version", ["1.0.0", "1.0.0-rc.1", "1.0.0-beta.2", "1.0.0-alpha.1",
                                     "0.10.4", "2.3.4-rc.12"])
def test_pypi_spelling_is_what_hatchling_builds(version):
    # hatchling reads VERSION through packaging's normalization: the dist
    # files, PyPI and the hub's probe must all see the spelling we compute.
    packaging_version = pytest.importorskip("packaging.version")
    assert str(packaging_version.Version(version)) == rv.channels(version)["pypi"]


@pytest.mark.parametrize("version", ["1.0.0", "1.0.0-rc.1", "1.0.0-beta.2", "0.10.4"])
def test_npm_spelling_is_semver(version):
    assert SEMVER.match(rv.channels(version)["npm"])


@pytest.mark.parametrize("bad", ["1.0.0rc1", "1.0.0-rc1", "1.0.0-rc", "1.0.0-RC.1", "1.0.0-dev.1",
                                 "1.0", "01.0.0", "1.0.0-rc.01", "1.0.0+build.1", "v1.0.0", "",
                                 "1.0.0-rc.1\n"])
def test_rejects_versions_without_one_spelling_per_channel(bad):
    with pytest.raises(ValueError):
        rv.channels(bad)


def test_bump_from_a_stable_version():
    assert rv.next_version("0.10.4", "patch", "") == "0.10.5"
    assert rv.next_version("0.10.4", "minor", "") == "0.11.0"
    assert rv.next_version("0.10.4", "major", "") == "1.0.0"


def test_override_sets_a_prerelease_or_its_release():
    assert rv.next_version("0.10.4", "patch", "1.0.0-rc.1") == "1.0.0-rc.1"
    assert rv.next_version("1.0.0-rc.1", "patch", "1.0.0-rc.2") == "1.0.0-rc.2"
    assert rv.next_version("1.0.0-rc.1", "patch", "1.0.0") == "1.0.0"
    assert rv.next_version("0.10.4", "patch", "v1.0.0-rc.1") == "1.0.0-rc.1"


@pytest.mark.parametrize("current,override", [
    ("1.0.0", "1.0.0-rc.3"),   # a prerelease of an already released version
    ("1.0.0", "0.10.5"),       # a lower release
    ("1.0.0-rc.2", "1.0.0-rc.1"),
    ("1.0.0", "1.0.0"),        # PyPI would refuse the re-upload after tagging
])
def test_override_must_move_forward(current, override):
    # PyPI uploads are permanent and npm/PyPI/GitHub would all go backwards.
    with pytest.raises(ValueError, match="not above"):
        rv.next_version(current, "patch", override)


@pytest.mark.parametrize("current,override", [
    ("9.0.0", "10.0.0"),            # numeric, not string, order
    ("1.0.0-rc.9", "1.0.0-rc.10"),
    ("1.0.0-beta.2", "1.0.0-rc.1"),
    ("1.0.0-rc.1", "1.0.0"),
])
def test_override_forward_by_semver_precedence(current, override):
    assert rv.next_version(current, "patch", override) == override


def test_0x_prereleases_are_refused():
    # The hub pairs prereleases only from 1.0.0 on; a 0.x rc could never ship.
    with pytest.raises(ValueError, match="1.0.0"):
        rv.next_version("0.10.4", "patch", "0.11.0-rc.1")


def test_bump_from_a_prerelease_needs_an_explicit_override():
    with pytest.raises(ValueError, match="override"):
        rv.next_version("1.0.0-rc.1", "patch", "")


def test_bad_bump_and_bad_override_are_refused():
    with pytest.raises(ValueError):
        rv.next_version("0.10.4", "huge", "")
    with pytest.raises(ValueError):
        rv.next_version("0.10.4", "patch", "1.0.0rc1")


def test_cli_next_emits_github_outputs():
    proc, out = cli("next", "--current=0.10.4", "--bump=patch", "--override=1.0.0-rc.1")
    assert proc.returncode == 0, proc.stderr
    assert out == {"cur": "0.10.4", "new": "1.0.0-rc.1", "version": "1.0.0-rc.1",
                   "pypi": "1.0.0rc1", "npm": "1.0.0-rc.1", "npm_dist_tag": "next",
                   "git_tag": "v1.0.0-rc.1", "prerelease": "true"}
    assert "Release 0.10.4 -> 1.0.0-rc.1" in proc.stderr


def test_cli_channels_reads_one_version():
    proc, out = cli("channels", "1.0.0")
    assert proc.returncode == 0, proc.stderr
    assert (out["pypi"], out["npm_dist_tag"], out["prerelease"]) == ("1.0.0", "latest", "false")


def test_cli_names_both_versions_for_a_backwards_override():
    proc, out = cli("next", "--current=1.0.0", "--bump=patch", "--override=0.10.5")
    assert (proc.returncode, out) == (1, {})
    assert "0.10.5" in proc.stderr and "1.0.0" in proc.stderr


def test_cli_fails_loud_without_outputs():
    proc, out = cli("next", "--current=1.0.0-rc.1", "--bump=patch", "--override=")
    assert proc.returncode == 1
    assert out == {}
    assert proc.stderr.startswith("::error::")
    proc, out = cli("channels", "1.0.0rc1")
    assert (proc.returncode, out) == (1, {})
