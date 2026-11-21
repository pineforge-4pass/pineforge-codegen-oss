"""The changelog keeps one dated section per released version.

A release commit (its subject is ``release: vX.Y.Z`` and the tag ``vX.Y.Z`` points at it) must not leave the notes it ships under the
Unreleased heading: the notes of 1.13.0 and 1.14.0 stayed under it after both tags existed, so the Unreleased section listed shipped
changes as if they were still to come.  These rows read the release commits (the commits the tags point at; a checkout without the
tags still has them) and compare the changelog of each release with the changelog of the previous release.

A checkout that does not hold the release history (a shallow clone) skips the rows: the skip names the reason.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_SUBJECT = re.compile(r"^release: v(\d+)\.(\d+)\.(\d+)\b")
# 1.13.0 is the first release whose notes were left under the Unreleased heading; the sections of the earlier releases are as released.
FIRST_CHECKED = (1, 13, 0)


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip(f"git is not usable in this checkout: {done.stderr.strip()[:120]}")
    return done.stdout


def _releases() -> list[tuple[tuple[int, int, int], str, str]]:
    """Release commits found in the history, oldest version first: (version, commit, committer date)."""
    found = []
    for line in _git("log", "--format=%H%x09%cs%x09%s", "--grep=^release: v").splitlines():
        sha, date, subject = line.split("\t", 2)
        match = RELEASE_SUBJECT.match(subject)
        if match:
            found.append((tuple(int(part) for part in match.groups()), sha, date))
    found.sort()
    if len(found) < 2:
        pytest.skip("the release commits are not in this checkout (shallow history)")
    return found


def _version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _bullets(text: str) -> list[tuple[str, str]]:
    """Every bullet of a changelog as (heading, one normalized line).

    A bullet starts with ``- `` in column 1 and runs over the following lines that start with a space or a tab, until a blank line, a
    heading or the next bullet; sub-bullets are part of the bullet that holds them.
    """
    result: list[tuple[str, str]] = []
    heading = ""
    current: list[str] = []

    def flush() -> None:
        if current:
            result.append((heading, re.sub(r"[ \t]+", " ", " ".join(current))))
            current.clear()

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line
        elif line.startswith("- "):
            flush()
            current.append(line)
        elif line[:1] in (" ", "\t") and current:
            current.append(line)
        else:
            flush()
    flush()
    return result


def _file_at(commit: str) -> str:
    return _git("show", f"{commit}:CHANGELOG.md")


def _checked_pairs() -> list[tuple[tuple[int, int, int], str, str, str]]:
    releases = _releases()
    pairs = []
    for index, (version, sha, date) in enumerate(releases):
        if version >= FIRST_CHECKED and index > 0:
            pairs.append((version, sha, date, releases[index - 1][1]))
    if not pairs:
        pytest.skip("no release from 1.13.0 on is in this checkout")
    return pairs


def test_each_release_from_1_13_0_has_its_dated_section() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    missing = [
        f"## {_version(version)} — {date}"
        for version, _sha, date, _previous in _checked_pairs()
        if f"## {_version(version)} — {date}" not in text.splitlines()
    ]
    assert not missing, f"the changelog lacks these release headings: {missing}"


def test_notes_of_each_release_sit_under_its_own_section() -> None:
    current = _bullets(CHANGELOG.read_text(encoding="utf-8"))
    problems = []
    for version, sha, _date, previous in _checked_pairs():
        shipped_before = {text for _heading, text in _bullets(_file_at(previous))}
        released = [
            text
            for heading, text in _bullets(_file_at(sha))
            if text not in shipped_before
            and (heading.startswith("## Unreleased") or heading.startswith(f"## {_version(version)} — "))
        ]
        for text in released:
            homes = {heading for heading, other in current if other == text}
            if not any(home.startswith(f"## {_version(version)} — ") for home in homes):
                problems.append((_version(version), sorted(homes), text[:70]))
    assert not problems, f"notes shipped in a release are not under that release's section: {problems}"


def test_no_note_of_a_released_version_stays_under_unreleased() -> None:
    latest_version, latest_sha, _date = _releases()[-1]
    shipped = {text for _heading, text in _bullets(_file_at(latest_sha))}
    stale = [
        text[:70]
        for heading, text in _bullets(CHANGELOG.read_text(encoding="utf-8"))
        if heading.startswith("## Unreleased") and text in shipped
    ]
    assert not stale, f"notes of {_version(latest_version)} or earlier are still under the Unreleased heading: {stale}"
