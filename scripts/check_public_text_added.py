#!/usr/bin/env python3
"""Check added public text, new paths, and commit messages from explicit refs.

The private path enables E4 by supplying an external list with --digests.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FROZEN_EXEMPTIONS: dict[str, set[int]] = {}
ROLE_WORD_RE = re.compile(
    r"\b(?:[A-Z]+-TOP|[A-Z0-9]+-CX|CR-[0-9]{3}|LB-[0-9]+|rj-20[0-9]{6}[a-z0-9]*(?:-[a-z0-9]+)*|CX-\w+-\w+)\b|R[0-9]+\x20lane|PERF-D[0-9]+|\bINT[0-9]{2}\b|TRIAGE[0-9]+|AUDIT[0-9]+|supervisor\x20call|owner\x20call|lane\x20brief|OWNER\x20DECISION|Taipe[i]"
)
PATTERNS = (
    ("E1", re.compile(r"\b[A-Z][A-Z0-9]*-(?:CX|TOP|NEXT)\b")),
    ("E2", re.compile(
        r"\b(?:(?:CR|HO|LB|SMP|CG|TX|LIVE)-[0-9]+|CGINT[0-9]+|S-[0-9]{3,}|K-[0-9]+)\b"
    )),
    ("E3", re.compile(
        r"\b(?:rj-20[0-9]{6}[a-z0-9]*(?:-[a-z0-9]+)*|tick-[0-9]{4})\b"
    )),
)
SEGMENT = "." + "la" + "ne"
OID_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
RAW_RE = re.compile(
    rb":(100644|100755|120000|160000|000000) "
    rb"(100644|100755|120000|160000|000000) "
    rb"([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) ([ADMRT])([0-9]*)"
)
HUNK_RE = re.compile(rb"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?:.*)")


class CheckFailure(Exception):
    """A named failure that cannot establish coverage."""


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise CheckFailure("arguments")


def git(root: Path, *args: str, operation: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            capture_output=True,
        )
    except OSError:
        raise CheckFailure("git_" + operation) from None
    if result.returncode:
        raise CheckFailure("git_" + operation)
    return result.stdout


def resolve(root: Path, ref: str, name: str) -> str:
    data = git(
        root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}",
        operation=name + "_unresolved",
    ).strip()
    if not OID_RE.fullmatch(data):
        raise CheckFailure(name + "_identity")
    return data.decode("ascii")


def decode(data: bytes, name: str) -> str:
    try:
        return data.decode("latin-1" if b"\0" in data else "utf-8")
    except UnicodeDecodeError:
        raise CheckFailure(name + "_decode") from None


def load_digests(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CheckFailure("digest_read") from None
    digests = []
    for line in lines:
        if line.startswith("#") and not digests:
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", line):
            raise CheckFailure("digest_format")
        digests.append(line)
    if not digests:
        raise CheckFailure("digest_empty")
    if digests != sorted(set(digests)):
        raise CheckFailure("digest_order")
    return set(digests)


def classes(text: str, digests: set[str] | None, *, path: bool = False):
    for class_id, pattern in PATTERNS:
        for _ in pattern.finditer(text):
            yield class_id
    if digests is not None:
        for word in ([text] if path else text.split()):
            parts = [part.lower() for part in re.split(r"[^0-9A-Za-z]+", word) if part]
            for start in range(len(parts)):
                for size in range(1, min(3, len(parts) - start) + 1):
                    term = "-".join(parts[start:start + size])
                    if hashlib.sha256(term.encode("utf-8")).hexdigest() in digests:
                        yield "E4"
    for match in ROLE_WORD_RE.finditer(text):
        if text[max(0, match.start() - 5):match.end()] == 'Asia/Taipei':
            continue
        yield "E5"
    for word in ([text] if path else text.split()):
        value = word if path else word.strip("`\"'()[]{}<>,;:")
        segments = value.split("/") if path else re.split(r"[/\\]", value)
        for segment in segments:
            if segment == SEGMENT:
                yield "E6"


def records(data: bytes):
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise CheckFailure("inventory_terminator")
    return data[:-1].split(b"\0")


def path_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise CheckFailure("path_decode") from None


def blob_lines(root: Path, oid: str) -> list[str]:
    text = decode(git(root, "cat-file", "blob", oid, operation="blob_read"), "blob")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def added_numbers(root: Path, old: str, new: str, total: int) -> list[int]:
    if old == new:
        return []
    patch = git(
        root, "diff", "--text", "--no-ext-diff", "--no-textconv", "--no-color",
        "--unified=0", "--inter-hunk-context=0", "--diff-algorithm=myers",
        "--no-indent-heuristic", old, new, "--", operation="blob_diff",
    )
    if not patch:
        raise CheckFailure("empty_blob_diff")
    numbers = []
    old_left = new_left = current = 0
    seen = False
    for line in patch.split(b"\n"):
        hunk = HUNK_RE.fullmatch(line)
        if hunk:
            if old_left or new_left:
                raise CheckFailure("diff_hunk_counts")
            seen = True
            old_left = int(hunk[2]) if hunk[2] is not None else 1
            current = int(hunk[3])
            new_left = int(hunk[4]) if hunk[4] is not None else 1
        elif old_left or new_left:
            if line.startswith(b"+"):
                numbers.append(current)
                current += 1
                new_left -= 1
            elif line.startswith(b"-"):
                old_left -= 1
            elif line.startswith(b" "):
                old_left -= 1
                new_left -= 1
                current += 1
            elif not line.startswith(b"\\ "):
                raise CheckFailure("diff_hunk_body")
            if old_left < 0 or new_left < 0:
                raise CheckFailure("diff_hunk_counts")
        elif line.startswith(b"@@"):
            raise CheckFailure("diff_hunk_header")
    if not seen or old_left or new_left:
        raise CheckFailure("diff_hunk_counts")
    if len(numbers) != len(set(numbers)) or any(n < 1 or n > total for n in numbers):
        raise CheckFailure("diff_line_numbers")
    return numbers


class Scan:
    def __init__(self, root: Path, digests: set[str] | None):
        self.root = root
        self.digests = digests
        self.files = self.lines = self.paths = self.commits = self.hits = 0

    def text(self, text: str, location: str, *, path: bool = False):
        for class_id in classes(text, self.digests, path=path):
            print(f"{location} {class_id}")
            self.hits += 1

    def file(self, path: str, oid: str, old: str | None, *, new_path: bool):
        location = path.encode("unicode_escape").decode("ascii")
        if new_path:
            self.paths += 1
            self.text(path, location, path=True)
        lines = blob_lines(self.root, oid)
        self.files += 1
        if old is None:
            numbers = range(1, len(lines) + 1)
        else:
            numbers = added_numbers(self.root, old, oid, len(lines))
        for number in numbers:
            self.text(lines[number - 1], f"{location}:{number}")
            self.lines += 1

    def tree(self, head: str):
        entries = records(git(
            self.root, "ls-tree", "-r", "-z", "--full-tree", head,
            operation="tree_inventory",
        ))
        if not entries:
            raise CheckFailure("empty_tree")
        for entry in entries:
            metadata, separator, path = entry.partition(b"\t")
            fields = metadata.split()
            if (not separator or len(fields) != 3 or fields[1] != b"blob"
                    or fields[0] not in (b"100644", b"100755", b"120000")
                    or not OID_RE.fullmatch(fields[2])):
                raise CheckFailure("tree_entry")
            self.file(path_text(path), fields[2].decode("ascii"), None, new_path=True)

    def diff(self, base: str, head: str) -> str:
        commits = git(
            self.root, "log", "--format=%H", "--reverse", "--no-patch",
            "--no-notes", "--no-show-signature", "--no-color", "--no-decorate",
            base + ".." + head, "--",
            operation="commit_inventory",
        ).splitlines()
        if not commits:
            raise CheckFailure("empty_range")
        if any(not OID_RE.fullmatch(commit) for commit in commits):
            raise CheckFailure("commit_inventory")
        merge_base = git(
            self.root, "merge-base", base, head, operation="merge_base",
        ).strip()
        if not OID_RE.fullmatch(merge_base):
            raise CheckFailure("merge_base_identity")
        start = merge_base.decode("ascii")
        entries = records(git(
            self.root, "diff", "--raw", "--no-abbrev", "-z", "--no-ext-diff",
            "--no-textconv", "--no-color", "--no-relative", "--ignore-submodules=none",
            "--find-renames=50%", "-l0", start, head, "--",
            operation="diff_inventory",
        ))
        index = 0
        while index < len(entries):
            match = RAW_RE.fullmatch(entries[index])
            if not match or index + 1 >= len(entries):
                raise CheckFailure("diff_entry")
            path = path_text(entries[index + 1])
            index += 2
            status = match[5]
            if status == b"R":
                if index >= len(entries):
                    raise CheckFailure("rename_entry")
                path = path_text(entries[index])
                index += 1
            if status == b"D":
                continue
            if match[2] == b"160000" or match[2] == b"000000":
                raise CheckFailure("diff_entry_mode")
            old = None if status == b"A" else match[3].decode("ascii")
            self.file(path, match[4].decode("ascii"), old, new_path=status in (b"A", b"R"))
        for commit in commits:
            oid = commit.decode("ascii")
            raw = git(self.root, "cat-file", "commit", oid, operation="commit_read")
            _, separator, message = raw.partition(b"\n\n")
            if not separator:
                raise CheckFailure("commit_message")
            self.text(decode(message, "commit"), oid[:8])
            self.commits += 1
        return start


def main(argv: list[str] | None = None) -> int:
    parser = Arguments(description=__doc__, add_help=False)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--tree", action="store_true")
    parser.add_argument("--digests", type=Path)
    try:
        args = parser.parse_args(argv)
        if not args.tree and not args.base:
            raise CheckFailure("base_required")
        if args.base and re.fullmatch(r"0{40}|0{64}", args.base):
            raise CheckFailure("zero_base")
        if FROZEN_EXEMPTIONS:
            raise CheckFailure("frozen_exemptions")
        digests = load_digests(args.digests) if args.digests is not None else None
        shallow = git(args.root, "rev-parse", "--is-shallow-repository", operation="repository")
        if shallow.strip() != b"false":
            raise CheckFailure("shallow_repository")
        head = resolve(args.root, args.head, "head")
        base = resolve(args.root, args.base, "base") if args.base else "tree"
        scan = Scan(args.root, digests)
        start = head if args.tree else scan.diff(base, head)
        if args.tree:
            scan.tree(head)
        if scan.hits:
            print(f"FAIL findings count={scan.hits}")
            return 1
        e4 = "run" if digests is not None else "not-run(no-private-list)"
        print(
            f"PASS coverage base={base} head={head} start={start} "
            f"files={scan.files} added_lines={scan.lines} paths={scan.paths} commits={scan.commits} "
            f"e4={e4}"
        )
        return 0
    except CheckFailure as error:
        print(f"FAIL {error} count=1")
        return 1
    except (OSError, UnicodeError):
        print("FAIL input_read count=1")
        return 1


if __name__ == "__main__":
    sys.exit(main())
