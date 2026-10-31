"""unused fixture metadata fields"""

import hashlib
from pathlib import Path
import re
import subprocess


_DIGESTS = {
    "e161dd10207de8575bf1fbb02668fac718569f9608e822ba15874b08464d8610",
    "ac777db32ba9e754d5289c78dc4963cbc74da53b7743c52a9fbf9621ae67fd38",
    "1f1835ad45fab177bd7080b3e091a8a20e8400163ab60f6b68c8ba99d7b0aa7d",
    "2f2e271b7ecdb1039b6d97f664f3845440a4d4255a5e202f5675a3bc2d7c0a0d",
    "44ec77b8af0a1695673311afebfb03777b47a440b42f8ca645dccf6f7ec2840a",
    "c4f10090301877a3c66dab40886391f4b1a2c846589bc350cd7280d640665b05",
    "43aedf0da625b9fa4471ce529f1a90986b09381637edc666d1f46ffb0ec5b96f",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_JOINED_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_FILE_BYTES = 5 * 1024 * 1024


def _scan_tracked_text(root: Path, digests: set[str]) -> list[tuple[str, int]]:
    assert digests, "The digest set must not be empty."
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    paths = [path for path in result.stdout.split("\0") if path]
    assert paths, "git ls-files returned no tracked files."
    matches = []
    scanned = 0
    for relative_path in paths:
        path = root / relative_path
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        contents = path.read_bytes()
        if b"\0" in contents:
            continue
        scanned += 1
        text = contents.decode("utf-8", errors="surrogateescape")
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(
                hashlib.sha256(token.encode("ascii")).hexdigest() in digests
                for pattern in (_TOKEN_RE, _JOINED_TOKEN_RE)
                for token in pattern.findall(line)
            ):
                matches.append((relative_path, line_number))
    assert scanned, "No tracked text files were scanned."
    return matches


def test_fixture_metadata_keys():
    matches = _scan_tracked_text(Path(__file__).resolve().parents[1], _DIGESTS)
    assert not matches, "unused fixture metadata fields:\n" + "\n".join(
        f"{path}:{line_number}" for path, line_number in matches
    )


def test_tracked_text_scanner_self_check(tmp_path, monkeypatch):
    token = "fixture_guard_probe"
    hyphen_token = "fixture-guard-probe"
    digests = {
        hashlib.sha256(probe.encode("ascii")).hexdigest()
        for probe in (token, hyphen_token)
    }
    plain = tmp_path / "example.txt"
    quoted = tmp_path / ".github" / "example.json"
    clean = tmp_path / "clean.md"
    quoted.parent.mkdir()
    plain.write_text(f"clean\n{token}\n{hyphen_token}\n", encoding="utf-8")
    quoted.write_text(f'{{\n  "{token}": 1\n}}\n', encoding="utf-8")
    clean.write_text(
        f"prefix_{token}_suffix\nprefix_{hyphen_token}_suffix\n", encoding="utf-8"
    )

    def tracked_files(command, **kwargs):
        assert command == ["git", "ls-files", "-z"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="example.txt\0.github/example.json\0clean.md\0",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", tracked_files)
    assert _scan_tracked_text(tmp_path, digests) == [
        ("example.txt", 2),
        ("example.txt", 3),
        (".github/example.json", 2),
    ]
    plain.write_text("clean text\n", encoding="utf-8")
    quoted.write_text('{"clean": 1}\n', encoding="utf-8")
    assert _scan_tracked_text(tmp_path, digests) == []
