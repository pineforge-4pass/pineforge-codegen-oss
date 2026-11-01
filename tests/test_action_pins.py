"""Require immutable action references throughout the GitHub YAML tree."""

from collections.abc import Iterator
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
# Recounted at c1cc532b: gate.yml 9, gke-pr.yml 11, promote-baseline.yml 6,
# publish-pyodide.yml 5, release.yml 7, test.yml 7; no .github/actions/ (45 total).
MIN_USES = 45

QUOTED = r'"(?:\\.|[^"\\])*"' + r"|'(?:''|[^'])*'"
# Skip quoted text and comments before looking for the next mapping key.
KEY_OR_TOKEN = re.compile(
    r"(?P<uses>(?:^[ \t]*(?:-[ \t]+)?|(?P<flow>(?<=[{,\[])[ \t]*))"
    r"""(?:uses|"uses"|'uses')[ \t]*:)"""
    rf"|{QUOTED}|(?P<comment>(?<!\S)#[^\r\n]*)"
)
# A hash starts a comment only after whitespace and outside a quoted scalar.
BLOCK_SCALAR = re.compile(rf"(?:{QUOTED}|" r"""[^#"']|(?<!\s)#)*""")
FLOW_SCALAR = re.compile(rf"(?:{QUOTED}|" r"""[^#,\]}"']|(?<!\s)#)*""")
REPOSITORY_PIN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+(?:/[^\s@]+)?@[0-9a-f]{40}"
)
LOCAL_ACTION = re.compile(r"\./[^\s@]+")
DOCKER_PIN = re.compile(r"docker://[^\s@]+@sha256:[0-9a-fA-F]{64}")


def uses_entries(text: str) -> Iterator[tuple[int, str]]:
    """Yield literal uses values, including empty ones, with one-based lines."""
    for number, line in enumerate(text.splitlines(), 1):
        position = 0
        while token := KEY_OR_TOKEN.search(line, position):
            position = token.end()
            if token.group("comment") is not None:
                break
            if token.group("uses") is None:
                continue
            pattern = FLOW_SCALAR if token.group("flow") is not None else BLOCK_SCALAR
            scalar = pattern.match(line, position)
            assert scalar is not None  # The scalar pattern also matches empty values.
            position = scalar.end()
            reference = scalar.group().strip()
            if re.fullmatch(QUOTED, reference):
                reference = reference[1:-1]
            yield number, reference


def scan_action_pins(name: str, text: str) -> tuple[int, list[str]]:
    """Pure text validator returning the number of uses entries and findings."""
    count = 0
    findings = []
    for number, reference in uses_entries(text):
        count += 1
        if reference.startswith("./"):
            pattern = LOCAL_ACTION
        elif reference.startswith("docker://"):
            pattern = DOCKER_PIN
        else:
            pattern = REPOSITORY_PIN
        if pattern.fullmatch(reference) is None:
            findings.append(f"{name}:{number}: unpinned uses: {reference!r}")
    return count, findings


def checked_path(path: Path) -> Path:
    """Refuse protected names and links before following a diagnostic input."""
    parts = tuple(part.casefold() for part in path.parts)
    if any(
        part in (".ssh", ".aws", ".codex") or part.endswith(".env")
        for part in parts
    ) or any(
        parts[index : index + 3] == (".config", "zsh", "keys")
        or parts[index : index + 2] == (".config", "pineforge")
        for index in range(len(parts))
    ):
        raise ValueError(f"refusing prohibited path: {path}")
    # Check parents first so even metadata checks cannot follow a linked parent.
    for candidate in reversed((path, *path.parents)):
        if candidate.is_symlink():
            raise ValueError(f"refusing symbolic link: {candidate}")
    return path


def github_yaml_files(directory: Path) -> list[Path]:
    """Walk every directory, including composite actions, without ignore rules."""
    checked_path(directory)
    paths = []
    for path in sorted(directory.iterdir()):
        checked_path(path)
        if path.is_dir():
            paths.extend(github_yaml_files(path))
        elif path.suffix in (".yml", ".yaml"):
            paths.append(path)
    return paths


CONTROL_SHA = "0123456789abcdef" * 2 + "01234567"
CONTROL_DIGEST = "0123456789abcdef" * 4


@pytest.mark.parametrize("text", [
    pytest.param("uses: example/action@v4", id="tag"),
    pytest.param("- uses: example/action@main", id="branch"),
    pytest.param("uses: example/action@abc1234", id="short-sha"),
    pytest.param(f"uses: example/action@{CONTROL_SHA.upper()}", id="uppercase-sha"),
    pytest.param(f"uses: example/action@{CONTROL_SHA[:-1]}", id="39-hex"),
    pytest.param(f"uses: example/action@{CONTROL_SHA}0", id="41-hex"),
    pytest.param("uses: 'example/action@v4' # version", id="single-quoted-tag"),
    pytest.param('uses: "example/action@v4"', id="double-quoted-tag"),
    pytest.param("- {uses: example/action@v4}", id="flow-tag"),
    pytest.param('- {"uses": "example/action@v4"}', id="quoted-flow-tag"),
    pytest.param("uses: docker://example/image:latest", id="docker-tag"),
    pytest.param("uses: docker://x@sha256:abc1234", id="docker-short-digest"),
    pytest.param(f"uses: ./local@{CONTROL_SHA}", id="local-with-ref"),
    pytest.param("uses: example/action", id="missing-ref"),
    pytest.param("uses: # missing reference", id="empty"),
    pytest.param(f"uses: example/action@{CONTROL_SHA}#version", id="attached-hash"),
    pytest.param(f'uses: "example/action@{CONTROL_SHA} #version"', id="quoted-hash"),
    pytest.param(f'uses: "example/action@{CONTROL_SHA}" extra', id="quoted-suffix"),
    pytest.param(f"uses: example/action@{CONTROL_SHA},tag", id="block-comma-suffix"),
    pytest.param(f"uses: example/action@{CONTROL_SHA}}}", id="block-brace-suffix"),
])
def test_invalid_reference_controls(text: str) -> None:
    name = ".github/actions/control/action.yaml"
    count, findings = scan_action_pins(name, "\n" + text + "\n")
    assert count == 1
    assert len(findings) == 1, findings
    assert findings[0].startswith(f"{name}:2:"), findings


@pytest.mark.parametrize("text", [
    pytest.param(f"uses: example/action@{CONTROL_SHA}", id="sha"),
    pytest.param(f"- uses: example/action@{CONTROL_SHA} # v4.1.0", id="comment"),
    pytest.param(f"uses: 'example/action@{CONTROL_SHA}' # v4", id="single-quoted"),
    pytest.param(f'uses: "example/action@{CONTROL_SHA}"', id="double-quoted"),
    pytest.param(f"- {{uses: example/action@{CONTROL_SHA}, name: pinned}}", id="flow"),
    pytest.param(f'- {{"uses": "example/action@{CONTROL_SHA}"}}', id="quoted-flow"),
    pytest.param(
        f"uses: example/repo/.github/workflows/reuse.yml@{CONTROL_SHA}", id="reusable",
    ),
    pytest.param("uses: ./local", id="local"),
    pytest.param(f"uses: docker://x@sha256:{CONTROL_DIGEST}", id="docker-digest"),
    pytest.param(
        f"uses: docker://x@sha256:{CONTROL_DIGEST.upper()}", id="docker-uppercase-digest",
    ),
])
def test_valid_reference_controls(text: str) -> None:
    assert scan_action_pins(".github/control.yml", "\n" + text + "\n") == (1, [])


def test_comments_and_quoted_text_are_not_entries() -> None:
    text = (
        "# uses: example/action@v4\n"
        'name: "uses: example/action@v4"\n'
        "name: 'uses: example/action@main'\n"
        "name: ordinary # uses: example/action@v4\n"
    )
    assert scan_action_pins(".github/control.yaml", text) == (0, [])
    assert scan_action_pins(".github/empty.yaml", "") == (0, [])


def test_every_flow_entry_is_checked() -> None:
    name = ".github/actions/nested/action.yaml"
    text = (
        "\nsteps: [{uses: example/first@v4}, "
        '{"uses": "example/second@main"}] # uses: ignored/action@v4\n'
    )
    assert scan_action_pins(name, text) == (
        2,
        [
            f"{name}:2: unpinned uses: 'example/first@v4'",
            f"{name}:2: unpinned uses: 'example/second@main'",
        ],
    )


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    paths = github_yaml_files(ROOT / ".github")
    print("Action pin input files:\n" + "\n".join(map(str, paths)), flush=True)
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }


def test_repository_action_pins(sources: dict[str, str]) -> None:
    count = 0
    findings = []
    for name, text in sources.items():
        seen, problems = scan_action_pins(name, text)
        count += seen
        findings.extend(problems)
    assert findings == [], "Unpinned action references:\n" + "\n".join(findings)
    assert count >= MIN_USES, (
        f"Only {count} uses entries found; expected at least {MIN_USES}"
    )


def test_real_workflow_tag_mutation_is_refused(sources: dict[str, str]) -> None:
    candidates = [
        (name, text, number, reference)
        for name, text in sources.items()
        if name.startswith(".github/workflows/")
        for number, reference in uses_entries(text)
        if REPOSITORY_PIN.fullmatch(reference)
    ]
    assert candidates, "No real workflow reference available for mutation"
    name, text, number, reference = candidates[0]
    original_count, original_findings = scan_action_pins(name, text)
    assert original_findings == []
    tagged = reference.rsplit("@", 1)[0] + "@v4"
    lines = text.splitlines(keepends=True)
    lines[number - 1] = lines[number - 1].replace(reference, tagged, 1)
    mutated = "".join(lines)
    assert mutated != text
    count, findings = scan_action_pins(name, mutated)
    assert count == original_count
    assert findings == [f"{name}:{number}: unpinned uses: {tagged!r}"]
