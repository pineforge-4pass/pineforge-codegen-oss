"""Generate pinned casing tables offline from the three Unicode 14.0.0 inputs.

Only explicitly named files are read. Input hashes and sizes are mandatory.
No host Unicode database, locale, network, environment or package import
supplies data. The output is an ASCII Python module included in the package.
Run this generator on an approved build/test venue, not the authoring host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


DATA_VERSION = "14.0.0"
DATA_FORMAT = 1
INPUT_PINS = (
    ("UnicodeData.txt", 1897793,
     "36018e68657fdcb3485f636630ffe8c8532e01c977703d2803f5b89d6c5feafb"),
    ("SpecialCasing.txt", 16830,
     "c667b45908fd269af25fd55d2fc5bbc157fb1b77675936e25c513ce32e080334"),
    ("DerivedCoreProperties.txt", 1040794,
     "e3eddd7d469cd1b0feed7528defad1a1cc7c6a9ceb0ae4446a6d10921ed2e7bc"),
)
_CODE = re.compile(r"[0-9A-F]{4,6}", re.ASCII)
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:[-_][A-Za-z0-9]+)*", re.ASCII)
_DOMAIN = 0x110000


class DataError(ValueError):
    """An input fails its pinned identity or the supported UCD format."""


@dataclass(frozen=True)
class CaseData:
    lower: tuple[tuple[int, ...], ...]
    upper: tuple[tuple[int, ...], ...]
    lower_final_sigma: tuple[tuple[int, ...], ...]
    context: tuple[tuple[int, int, int], ...]
    excluded_locale_rules: tuple[str, ...]


def _code(value: str) -> int:
    if not _CODE.fullmatch(value):
        raise DataError(f"invalid UCD code point: {value!r}")
    result = int(value, 16)
    if result >= _DOMAIN:
        raise DataError(f"UCD code point outside Unicode: {value}")
    return result


def _sequence(value: str) -> tuple[int, ...]:
    return tuple(_code(part) for part in value.split())


def _records(text: str):
    for line_number, line in enumerate(text.split("\n"), 1):
        body = line.split("#", 1)[0].strip(" \t\r")
        if body:
            if not body.isascii():
                raise DataError(f"non-ASCII UCD data field at line {line_number}")
            yield line_number, [field.strip(" \t\r") for field in body.split(";")]


def _unicode_data(text: str, flags: bytearray):
    lower: dict[int, tuple[int, ...]] = {}
    upper: dict[int, tuple[int, ...]] = {}
    previous = -1
    pending = None

    def apply(first: int, last: int, fields: list[str]) -> None:
        if first != last and (fields[12] or fields[13]):
            raise DataError("a UnicodeData range carries a simple case mapping")
        if fields[2] == "Nd":
            for point in range(first, last + 1):
                flags[point] |= 4
        if fields[12]:
            upper[first] = (_code(fields[12]),)
        if fields[13]:
            lower[first] = (_code(fields[13]),)

    for line, fields in _records(text):
        if len(fields) != 15:
            raise DataError(f"UnicodeData line {line}: expected 15 fields")
        point = _code(fields[0])
        if point <= previous:
            raise DataError(f"UnicodeData line {line}: non-increasing code points")
        previous = point
        name = fields[1]
        if name.endswith(", First>"):
            if pending is not None:
                raise DataError(f"UnicodeData line {line}: nested range")
            pending = (point, fields)
        elif name.endswith(", Last>"):
            if pending is None:
                raise DataError(f"UnicodeData line {line}: range has no first row")
            first, first_fields = pending
            if (first_fields[1].removesuffix(", First>")
                    != name.removesuffix(", Last>")
                    or first_fields[2:] != fields[2:]):
                raise DataError(f"UnicodeData line {line}: inconsistent range")
            apply(first, point, first_fields)
            pending = None
        else:
            if pending is not None:
                raise DataError(f"UnicodeData line {line}: unterminated range")
            apply(point, point, fields)
    if pending is not None:
        raise DataError("UnicodeData: unterminated range at end of input")
    return lower, upper


def _special_casing(text: str, lower: dict, upper: dict):
    final_lower: dict[int, tuple[int, ...]] = {}
    unconditional_seen: set[int] = set()
    excluded: list[str] = []
    for line, fields in _records(text):
        if len(fields) == 6 and fields[-1] == "":
            fields.pop()
        if len(fields) != 5:
            raise DataError(f"SpecialCasing line {line}: expected five fields")
        point = _code(fields[0])
        lo, title, up = (_sequence(field) for field in fields[1:4])
        conditions = tuple(fields[4].split())
        if not conditions:
            if point in unconditional_seen:
                raise DataError(f"SpecialCasing line {line}: duplicate unconditional row")
            unconditional_seen.add(point)
            lower[point], upper[point] = lo, up
        elif any(_LANGUAGE.fullmatch(token) for token in conditions):
            # Root casing deliberately excludes every language-tailored row,
            # including deletions. Keep an inventory in the generated module.
            excluded.append(f"{point:04X}: {' '.join(conditions)}")
        elif conditions == ("Final_Sigma",):
            if point in final_lower:
                raise DataError(f"SpecialCasing line {line}: duplicate contextual row")
            if up != (point,) or title != (point,):
                raise DataError("Final_Sigma has a nonidentity upper/title mapping")
            final_lower[point] = lo
        else:
            raise DataError(f"SpecialCasing line {line}: unsupported root condition")
    return final_lower, tuple(sorted(excluded))


def _properties(text: str, flags: bytearray) -> None:
    bits = {"Cased": 1, "Case_Ignorable": 2}
    previous: dict[str, int] = {}
    for line, fields in _records(text):
        if len(fields) != 2:
            raise DataError(f"DerivedCoreProperties line {line}: expected two fields")
        prop = fields[1]
        if prop not in bits:
            continue
        interval = fields[0].split("..")
        if len(interval) not in (1, 2):
            raise DataError(f"DerivedCoreProperties line {line}: invalid interval")
        first, last = _code(interval[0]), _code(interval[-1])
        if first > last or first <= previous.get(prop, -1):
            raise DataError(f"DerivedCoreProperties line {line}: unordered interval")
        previous[prop] = last
        for point in range(first, last + 1):
            flags[point] |= bits[prop]
    if set(previous) != set(bits):
        raise DataError("DerivedCoreProperties: a required casing property is missing")


def _mapping_rows(mapping: dict[int, tuple[int, ...]]):
    return tuple(
        (point, len(converted), *converted)
        for point, converted in sorted(mapping.items())
        if converted != (point,)
    )


def _context_ranges(flags: bytearray):
    result = []
    first = 0
    while first < len(flags):
        value = flags[first]
        last = first
        while last + 1 < len(flags) and flags[last + 1] == value:
            last += 1
        if value:
            result.append((first, last, value))
        first = last + 1
    return tuple(result)


def parse_ucd(unicode_data: str, special_casing: str, derived_properties: str) -> CaseData:
    """Pure parser; production CLI calls this only after all three hash checks."""
    flags = bytearray(_DOMAIN)
    lower, upper = _unicode_data(unicode_data, flags)
    final_lower, excluded = _special_casing(special_casing, lower, upper)
    _properties(derived_properties, flags)
    data = CaseData(
        _mapping_rows(lower), _mapping_rows(upper),
        _mapping_rows(final_lower), _context_ranges(flags), excluded,
    )
    if not all((data.lower, data.upper, data.lower_final_sigma, data.context)):
        raise DataError("a required casing table is empty")
    return data


def _verified_text(name: str, payload: bytes) -> str:
    pin = next((item for item in INPUT_PINS if item[0] == name), None)
    if pin is None:
        raise DataError(f"input is not in the pinned UCD set: {name}")
    _, size, digest = pin
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise DataError(f"pinned size/SHA-256 mismatch: {name}")
    text = payload.decode("utf-8")
    if name != "UnicodeData.txt":
        stem = name.removesuffix(".txt")
        if not text.startswith(f"# {stem}-{DATA_VERSION}.txt\n"):
            raise DataError(f"pinned version header mismatch: {name}")
    return text


def render_module(data: CaseData) -> bytes:
    """Deterministic ASCII source: no dates, paths, host versions or locale."""
    lines = [
        '"""Generated Unicode 14.0.0 casing data; do not edit table entries.',
        "",
        "Source: https://www.unicode.org/Public/14.0.0/ucd/",
        "Copyright 2021 Unicode, Inc.",
        "Unicode data terms: https://www.unicode.org/terms_of_use.html",
        'Regenerate with scripts/gen_unicode_case_data.py using pinned inputs."""',
        "",
        f"DATA_FORMAT = {DATA_FORMAT}",
        f'DATA_VERSION = "{DATA_VERSION}"',
        "SOURCE_SHA256 = (",
    ]
    lines.extend(f'    ("{name}", "{digest}"),' for name, _, digest in INPUT_PINS)
    lines.extend((")", "SOURCE_BYTE_COUNTS = ("))
    lines.extend(f'    ("{name}", {size}),' for name, size, _ in INPUT_PINS)
    lines.extend((")", "EXCLUDED_LOCALE_RULES = ("))
    lines.extend(f'    "{rule}",' for rule in data.excluded_locale_rules)
    lines.append(")")
    for name, rows in (
        ("LOWER_ROWS", data.lower),
        ("UPPER_ROWS", data.upper),
        ("LOWER_FINAL_SIGMA_ROWS", data.lower_final_sigma),
        ("CONTEXT_RANGES", data.context),
    ):
        lines.extend(("", f"{name} = ("))
        lines.extend("    (" + ", ".join(f"0x{value:X}" for value in row) + "),"
                     for row in rows)
        lines.append(")")
    return ("\n".join(lines) + "\n").encode("ascii")


def _checked_path(value: str, filename: str) -> Path:
    """Reject sensitive names before any filesystem query; reject symlinks."""
    path = Path(value)
    parts = path.parts
    if any((part.startswith(".") and part not in {".", ".."})
           or part.endswith(".env") for part in parts):
        raise DataError("refused protected file path by name")
    if not path.is_absolute() or ".." in parts or path.name != filename:
        raise DataError(f"expected an absolute path ending in {filename}")
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise DataError(f"refused symlink path: {component}")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unicode-data")
    parser.add_argument("--special-casing")
    parser.add_argument("--derived-properties")
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true", help="compare an existing output")
    parser.add_argument("--crash-control", action="store_true")
    args = parser.parse_args(argv)
    if args.crash_control:
        print('{"read_files": [], "write_files": []}', flush=True)
        raise RuntimeError("deliberate Unicode generator crash control")
    values = (args.unicode_data, args.special_casing, args.derived_properties)
    if not all(values) or not args.output:
        parser.error("all three explicit UCD inputs and --output are required")
    # Announce the exact follow set before checking paths or opening a file.
    print(json.dumps({
        "read_files": list(values) + ([args.output] if args.check else []),
        "write_files": [] if args.check else [args.output],
    }, sort_keys=True), flush=True)
    paths = [_checked_path(value, pin[0]) for value, pin in zip(values, INPUT_PINS)]
    output = _checked_path(args.output, "_unicode_case_data.py")
    texts = []
    for path, (name, _, _) in zip(paths, INPUT_PINS):
        texts.append(_verified_text(name, path.read_bytes()))
    data = parse_ucd(*texts)
    encoded = render_module(data)
    if args.check:
        if output.read_bytes() != encoded:
            raise DataError("generated casing module differs from pinned inputs")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            if handle.write(encoded) != len(encoded):
                raise OSError("short casing data write")
    print(json.dumps({
        "output": str(output),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "lower_rows": len(data.lower),
        "upper_rows": len(data.upper),
        "context_ranges": len(data.context),
        "final_sigma_rows": len(data.lower_final_sigma),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
