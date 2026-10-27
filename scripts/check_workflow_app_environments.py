#!/usr/bin/env python3
"""CR304 workflow preflight; standard library only, no scripts/actions followed.

Read block mappings and single-line flow mappings, decoding quoted keys. YAML
aliases, tags, duplicate keys and unchecked metadata shapes fail closed for
human review. actionlint remains responsible for the full workflow schema.
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


QUOTED = r'"(?:\\.|[^"\\\n])*"' r"|'(?:''|[^'\n])*'"
PAIR = re.compile(rf"^({QUOTED}|[A-Za-z_][\w-]*|<<):[ \t]*(.*)$")
ACTION = re.compile(r"\bactions/create-github-app-token@", re.I)
SECRET = re.compile(r"\bsecrets\s*(?:\.\s*\w+|\[[^\]\n]+\])", re.I)
PRIVATE_KEY = re.compile(r"private[-_]?key", re.I)
SCRIPT_MINT = re.compile(
    r"@octokit/auth-app|\bcreateAppAuth\b|\bGitHubIntegration\b|"
    r"\bjwt\.(?:encode|sign)\b|\bopenssl\s+dgst\b|"
    r"\bapp/installations/[^\s\"']*/access_tokens\b|\binstallation_access_token\b",
    re.I,
)


class MetadataError(ValueError):
    def __init__(self, line, message):
        super().__init__(message)
        self.line = line


@dataclass
class Field:
    value: str
    body: str = ""
    line: int = 1

    @property
    def raw(self):
        return self.value + "\n" + self.body


def uncomment(value):
    return re.sub(
        rf"({QUOTED})|(?:[ \t]+#.*$|^#.*$)",
        lambda match: match[1] or "", value,
    ).strip()


def decode(value, line):
    if value.startswith("'"):
        if not re.fullmatch(QUOTED, value):
            raise MetadataError(line, "unchecked quoted scalar")
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError as error:
            raise MetadataError(line, "unchecked YAML string escape or continuation") from error
    return value


def split_flow(value, separator, line):
    """Split only outside strings and nested flow collections/expressions."""
    parts, stack, quote = [], [], ""
    start = index = 0
    while index < len(value):
        char = value[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if quote == "'" and value[index:index + 2] == "''":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != ("{" if char == "}" else "["):
                raise MetadataError(line, "unbalanced flow collection")
        elif char == separator and not stack:
            parts.append(value[start:index].strip())
            start = index + 1
        index += 1
    if quote or stack:
        raise MetadataError(line, "multiline or unbalanced flow collection")
    parts.append(value[start:].strip())
    return parts


def add_field(fields, key, field):
    if key == "<<" or key in fields:
        raise MetadataError(field.line, f"duplicate or merged key {key!r}")
    fields[key] = field


def mapping(node):
    value = uncomment(node.value)
    fields = {}
    if value:
        raw = "\n".join(uncomment(line) for line in node.raw.splitlines()).strip()
        if not raw.startswith("{") or not raw.endswith("}"):
            raise MetadataError(node.line, "expected a block or flow mapping")
        for entry in split_flow(raw[1:-1], ",", node.line):
            if not entry:
                continue
            pair = split_flow(entry, ":", node.line)
            if len(pair) < 2:
                raise MetadataError(node.line, "unchecked flow mapping entry")
            key = decode(pair[0], node.line)
            if not isinstance(key, str):
                raise MetadataError(node.line, "mapping key is not a string")
            add_field(fields, key, Field(":".join(pair[1:]).strip(), line=node.line))
        return fields
    lines = node.body.splitlines(keepends=True)
    headers, indent = [], None
    for index, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        padding = len(raw) - len(raw.lstrip(" "))
        if indent is None:
            indent = padding
        if padding < indent:
            raise MetadataError(node.line + index + 1, "unexpected mapping indentation")
        if padding == indent:
            headers.append(index)
    for position, index in enumerate(headers):
        line = node.line + index + 1
        pair = PAIR.fullmatch(uncomment(lines[index].lstrip(" ").rstrip("\r\n")))
        if pair is None:
            raise MetadataError(line, "unchecked block mapping entry")
        end = headers[position + 1] if position + 1 < len(headers) else len(lines)
        key = decode(pair[1], line)
        add_field(fields, key, Field(pair[2], "".join(lines[index + 1:end]), line))
    return fields


def check_structure(text):
    """Refuse YAML features that could hide a mint outside the parsed subset."""
    scalar_indent = None
    for line, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if scalar_indent is not None and indent > scalar_indent:
            continue
        scalar_indent = None
        content = uncomment(raw.lstrip(" "))
        if content.startswith("- "):
            content = content[2:]
            # Plain block-sequence items (e.g. workflow_dispatch choices).
            if ":" not in content and not content.startswith(("&", "*", "!")):
                continue
        pair = PAIR.fullmatch(content)
        if pair is None:
            raise MetadataError(line, "unchecked YAML structure or scalar continuation")
        value = pair[2]
        unquoted = re.sub(QUOTED, '""', content)
        if pair[1] == "<<" or re.search(
            r"(?:^|:\s*|-\s*|[\[,{]\s*)[&*][\w.-]+|:\s*![\w!]", unquoted,
        ):
            raise MetadataError(line, "YAML anchors, aliases, merges and tags need review")
        # Reject unhandled escapes/continuations even in uses/with/steps keys.
        for quoted in re.finditer(QUOTED, content):
            decode(quoted[0], line)
        if value.startswith(("'", '"')) and not re.fullmatch(QUOTED, value):
            raise MetadataError(line, "multiline quoted scalar needs review")
        if value.startswith(("{", "[")):
            split_flow(value, "\0", line)
        if re.fullmatch(r"[|>][1-9+-]*", value):
            scalar_indent = indent


def searchable(raw):
    text = "\n".join(uncomment(line) for line in raw.splitlines())

    def decoded(match):
        try:
            return decode(match[0], 1)
        except MetadataError:
            # Structure validation already refuses unhandled metadata strings.
            # A shell string inside a run block is also searched as written.
            return match[0]

    return text + "\n" + re.sub(QUOTED, decoded, text)


def mint_reasons(raw):
    text = searchable(raw)
    reasons = []
    if ACTION.search(text):
        reasons.append("actions/create-github-app-token")
    secrets = SECRET.findall(text)
    if any(PRIVATE_KEY.search(secret) or "[" in secret for secret in secrets) or (
        secrets and PRIVATE_KEY.search(text)
    ):
        reasons.append("private key secret (including unresolved secret indexing)")
    if SCRIPT_MINT.search(text):
        reasons.append("possible scripted App token mint")
    return reasons


def literal_environment(field):
    if field is None:
        return False
    body = "\n".join(uncomment(line) for line in field.body.splitlines()).strip()
    if field.value.startswith("{") or (not field.value and body):
        field = mapping(field).get("name")
        if field is None:
            return False
        body = "\n".join(uncomment(line) for line in field.body.splitlines()).strip()
    raw = uncomment(field.value)
    if not raw or body or raw.startswith(("&", "*", "!", "|", ">", "{", "[")):
        return False
    value = decode(raw, field.line)
    if not raw.startswith(("'", '"')) and (
        raw.lower() in {"null", "~", "true", "false", "yes", "no", "on", "off"}
        or re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw)
    ):
        return False
    return isinstance(value, str) and bool(value.strip()) and "${{" not in value and not any(
        ord(char) < 32 for char in value
    )


def inspect_workflow(text, filename):
    check_structure(text)
    root = mapping(Field("", text, 0))
    jobs = mapping(root.get("jobs", Field("")))
    if not jobs:
        raise MetadataError(1, "no workflow jobs found")
    inherited = mint_reasons(root["env"].raw) if "env" in root else []
    errors, reviews, count = [], [], 0
    for job, node in jobs.items():
        fields = mapping(node)
        reasons = list(dict.fromkeys([*inherited, *mint_reasons(node.raw)]))
        if not reasons:
            continue
        count += 1
        location = f"{filename}:{node.line}:jobs.{job}"
        if not literal_environment(fields.get("environment")):
            errors.append(
                f"{location}: CR304: App token minting requires a literal, non-empty "
                f"environment ({'; '.join(reasons)})"
            )
        if "possible scripted App token mint" in reasons:
            reviews.append(
                f"{location}: CR304-REVIEW: possible scripted App token mint; human review required"
            )
    return errors, reviews, count


def safe_path(path):
    """Never follow symlinks or credential paths, even in a test checkout."""
    candidate = path.absolute()
    parts = candidate.parts
    if (
        any(part in {".ssh", ".aws", ".codex"} for part in parts)
        or "/.config/zsh/keys" in candidate.as_posix()
        or "/.config/pineforge" in candidate.as_posix()
        or candidate.name.endswith(".env")
    ):
        raise ValueError(f"refusing credential path by name: {candidate}")
    # Check ancestors from the root down, without resolving any symlink.
    for parent in (*reversed(candidate.parents), candidate):
        if parent.is_symlink():
            raise ValueError(f"refusing symlink by name: {parent}")
    return candidate


def main():
    directory = safe_path(Path(__file__).absolute().parent.parent / ".github/workflows")
    paths = sorted(
        safe_path(path) for path in directory.iterdir() if path.suffix in {".yml", ".yaml"}
    )
    if not paths:
        print("CR304: no workflow files found", file=sys.stderr)
        return 1
    print("Workflow files to read (no actions or scripts followed):", flush=True)
    for path in paths:
        print(path, flush=True)
    errors, reviews, minting_jobs = [], [], 0
    for path in paths:
        try:
            found, notices, count = inspect_workflow(path.read_text(encoding="utf-8"), path.name)
            errors.extend(found)
            reviews.extend(notices)
            minting_jobs += count
        except MetadataError as error:
            errors.append(
                f"{path.name}:{error.line}: CR304: cannot resolve workflow metadata; "
                f"human review required: {error}"
            )
    for review in reviews:
        print(review)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"App-token environment check: PASS ({len(paths)} workflow files; {minting_jobs} minting jobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
