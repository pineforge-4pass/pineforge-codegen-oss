#!/usr/bin/env python3
"""Hold the reference-engine job's JUnit XML to its pinned test ids.

The reference-engine job of .github/workflows/test.yml runs four end-to-end
modules with PINEFORGE_REFERENCE_ENGINE naming a built reference engine. On a
runner without the private fixtures, the cases that need them skip; every
other case must run and pass. The check fails unless:

(a) the report holds no failure and no error;
(b) every skipped case's message starts with "private fixtures not present";
(c) the cases that did not skip are exactly the ids of the public list, and
    no case is reported twice;
(d) every id of the reference list is in the public list and passed.

Each list holds one full pytest node id per line, sorted and unique. A JUnit
case maps to its node id the way the lists are written: classname
"tests.test_x" and name "t[p]" give "tests/test_x.py::t[p]".

Exit status: 0 when every check passes; 1 when a check fails, with one FAIL
line per problem; 2 when the inputs are NOT COMPARED because one is missing,
unreadable, empty or malformed. Standard library only. The three files named
on the command line are listed before anything is read, and credential paths
and symlinks are refused by name.
"""
import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

PRIVATE_SKIP = "private fixtures not present"


class NotCompared(Exception):
    """An input cannot be compared: missing, unreadable, empty or malformed."""


def safe_path(path):
    """Never follow a symlink or a credential path, even in a test checkout."""
    candidate = path.absolute()
    text = candidate.as_posix()
    if (
        any(part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
            for part in candidate.parts)
        or "/.config/zsh/keys" in text
        or "/.config/pineforge" in text
    ):
        raise NotCompared(f"refusing credential path by name: {candidate}")
    # Check ancestors from the root down, without resolving any symlink.
    for parent in (*reversed(candidate.parents), candidate):
        if parent.is_symlink():
            raise NotCompared(f"refusing symlink by name: {parent}")
    return candidate


def read_ids(path, label):
    """The node ids of a list file: one per line, sorted and unique."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise NotCompared(f"the {label} {path} cannot be read: {error}") from None
    ids = text.splitlines()
    if not ids:
        raise NotCompared(f"the {label} {path} holds no id (0 ids)")
    for number, node_id in enumerate(ids, 1):
        if node_id != node_id.strip() or "::" not in node_id:
            raise NotCompared(
                f"the {label} {path}:{number} is not a full node id: {node_id!r}")
    for number, (before, after) in enumerate(zip(ids, ids[1:]), 2):
        if before >= after:
            raise NotCompared(
                f"the {label} {path}:{number} is not sorted and unique: "
                f"{after!r} follows {before!r}")
    return ids


def case_id(case):
    """The node id of a JUnit testcase, spelled the way the lists spell it."""
    classname, name = case.get("classname"), case.get("name")
    if classname is None or not name:
        raise NotCompared("a JUnit testcase has no classname or no name")
    # A collection error has an empty classname and the module as its name.
    return classname.replace(".", "/") + ".py::" + name if classname else name


def read_report(path):
    """The suite totals of a JUnit XML and its testcases, by node id."""
    try:
        root = ET.fromstring(path.read_bytes())
    except OSError as error:
        raise NotCompared(f"the JUnit XML {path} cannot be read: {error}") from None
    except ET.ParseError as error:
        raise NotCompared(f"the JUnit XML {path} is not well-formed: {error}") from None
    if root.tag not in {"testsuites", "testsuite"}:
        raise NotCompared(
            f"the JUnit XML {path} has the root element {root.tag!r}, "
            "not 'testsuites' or 'testsuite'")
    suites = list(root.iter("testsuite"))
    totals = {}
    for key in ("tests", "failures", "errors", "skipped"):
        try:
            totals[key] = sum(int(suite.get(key, "0")) for suite in suites)
        except ValueError:
            raise NotCompared(
                f"the JUnit XML {path} has a testsuite whose {key} count "
                "is not a number") from None
    cases = {}
    for case in root.iter("testcase"):
        cases.setdefault(case_id(case), []).append(case)
    if not cases:
        raise NotCompared(
            f"the JUnit XML {path} holds no testcase (0 testcases; "
            f"its suites count tests={totals['tests']})")
    return totals, cases


def problems(totals, cases, public, reference):
    """One line per problem, in the order of the rules (a), (b), (c), (d)."""
    found = []
    if totals["failures"] or totals["errors"]:
        found.append(f"(a) the report counts failures={totals['failures']} "
                     f"errors={totals['errors']}")
    failed, skipped = set(), set()
    for node_id, elements in sorted(cases.items()):
        for element in elements:
            for kind in ("failure", "error"):
                for child in element.findall(kind):
                    failed.add(node_id)
                    found.append(f"(a) {kind}: {node_id}: {child.get('message', '')}")
    for node_id, elements in sorted(cases.items()):
        for element in elements:
            for child in element.findall("skipped"):
                skipped.add(node_id)
                message = child.get("message", "")
                if not message.startswith(PRIVATE_SKIP):
                    found.append(f"(b) skipped for another reason: {node_id}: {message}")
    ran = set(cases) - skipped
    listed = set(public)
    for node_id, elements in sorted(cases.items()):
        if len(elements) > 1:
            found.append(f"(c) reported {len(elements)} times: {node_id}")
    for node_id in sorted(ran - listed):
        found.append(f"(c) ran but is not in the public list: {node_id}")
    for node_id in public:
        if node_id not in ran:
            where = "skipped" if node_id in skipped else "absent from the report"
            found.append(f"(c) in the public list but {where}: {node_id}")
    for node_id in reference:
        if node_id not in listed:
            found.append(f"(d) in the reference list but not in the public list: {node_id}")
        elif node_id not in ran or node_id in failed:
            found.append(f"(d) in the reference list but did not pass: {node_id}")
    return found, ran, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hold the reference-engine job's JUnit XML to its pinned test ids.")
    parser.add_argument("--junit", required=True, type=Path,
                        help="the JUnit XML that pytest --junitxml wrote")
    parser.add_argument("--public", required=True, type=Path,
                        help="the node ids that must run and pass, one per line")
    parser.add_argument("--reference", required=True, type=Path,
                        help="the node ids that need the reference engine, one per line")
    args = parser.parse_args(argv)
    print("Files to read: " + json.dumps([str(args.junit), str(args.public),
                                          str(args.reference)]), flush=True)
    try:
        junit = safe_path(args.junit)
        public_path = safe_path(args.public)
        reference_path = safe_path(args.reference)
        public = read_ids(public_path, "public list")
        reference = read_ids(reference_path, "reference list")
        totals, cases = read_report(junit)
    except NotCompared as error:
        print(f"NOT COMPARED: {error}", flush=True)
        return 2
    print(f"JUnit totals: tests={totals['tests']} failures={totals['failures']} "
          f"errors={totals['errors']} skipped={totals['skipped']}; "
          f"{len(cases)} distinct case ids", flush=True)
    found, ran, skipped = problems(totals, cases, public, reference)
    for line in found:
        print(f"FAIL: {line}")
    if found:
        print(f"Reference-engine JUnit check: FAIL ({len(found)} problems; "
              f"public list {len(public)} ids, {len(ran)} cases not skipped, "
              f"{len(skipped)} skipped)")
        return 1
    print(f"Reference-engine JUnit check: PASS (the {len(public)} public cases ran and "
          f"passed, {len(reference)} of them need the reference engine; {len(skipped)} "
          "cases skipped because private fixtures are not present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
