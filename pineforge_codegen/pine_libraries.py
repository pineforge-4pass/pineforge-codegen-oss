"""Where the sources of a script's imported Pine libraries come from.

``transpile(source, libraries=...)`` takes them from the caller: a mapping of
import path (``user/name/version``) to library source text. With
``libraries=None`` they come from the environment the campaign's case runner
sets (pineforge-workflow ``docs/xsym-requests.md``, "The environment
contract"):

- ``PINEFORGE_PINE_LIBRARIES=<dir>``: ``<dir>/libraries.json``
  (``pineforge-pine-libraries/v1``) and ``<dir>/<user>/<name>/<version>.pine``,
  the bytes pine-facade served. The directory is case-wide: it holds every
  library any probe of the case pins.
- ``PINEFORGE_REQUESTS_ROOT=<dir>``: one ``<slug>/requests.json``
  (``pineforge-probe-requests/v1``) per probe of the case that pins data, with
  every file it references under ``<slug>/files/<sha256>``.

A script's imports resolve only through its own manifest, never through the
case-wide directory alone: otherwise a probe that pins no library would
transpile on a shard neighbour's pin, and measure differently by case
composition. ``transpile()`` is handed the source text, not a slug (the frozen
verifier, pineforge-lab 3bac0b7b ``scripts/verify-engine-local.py:1698``, calls
``transpile((d / "strategy.pine").read_text())``), so the manifest is found by
content: the one ``requests.json`` whose ``probe.strategySha256`` is the sha256
of the script. That sha is of the strategy file's bytes, and ``read_text()``
folds ``\\r\\n`` and ``\\r`` line ends to ``\\n``: the text matches a manifest
when its UTF-8 bytes do, or the same bytes with every ``\\n`` spelled
``\\r\\n``, or ``\\r``. A file mixing line ends matches none. No manifest, or
more than one, resolves no library: every import keeps its refusal. An import
the manifest does not pin is refused by name whatever ``libraries.json`` lists,
and each source is verified against the manifest entry's ``sha256``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

LIBRARIES_ENV = "PINEFORGE_PINE_LIBRARIES"
REQUESTS_ENV = "PINEFORGE_REQUESTS_ROOT"
LIBRARIES_SCHEMA = "pineforge-pine-libraries/v1"
REQUESTS_SCHEMA = "pineforge-probe-requests/v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class LibraryResolveError(Exception):
    """An import's library source cannot be used; the message names it."""


@dataclass(frozen=True)
class LibrarySource:
    path: str
    text: str
    sha256: str | None


def source_digests(text: str) -> set[str]:
    """sha256 of every byte form ``text`` can have been read from with
    ``Path.read_text()``: its own UTF-8 bytes and, for a text holding no
    ``\\r``, the same bytes with CRLF or CR line ends."""
    forms = [text]
    if "\r" not in text and "\n" in text:
        forms += [text.replace("\n", "\r\n"), text.replace("\n", "\r")]
    digests = set()
    for form in forms:
        try:
            data = form.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:
            continue
        digests.add(hashlib.sha256(data).hexdigest())
    return digests


class LibraryResolver:
    """The library sources one transpile may read.

    ``configured`` is False when neither ``libraries=`` nor a requests
    manifest of the script applies; every import then keeps today's
    refusal, with ``reason`` (if any) saying why none applied.
    """

    configured = True
    reason: str | None = None

    def pinned(self, path: str) -> bool:
        raise NotImplementedError

    def load(self, path: str) -> LibrarySource:
        raise NotImplementedError


class _Unconfigured(LibraryResolver):
    configured = False

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason

    def pinned(self, path: str) -> bool:
        return False

    def load(self, path: str) -> LibrarySource:
        raise LibraryResolveError(self.reason or "no library sources are configured")


class _Provided(LibraryResolver):
    """``transpile(..., libraries={path: source})``: the caller's sources."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._sources: dict[str, str] = {}
        for path, text in sources.items():
            if isinstance(text, (bytes, bytearray)):
                text = bytes(text).decode("utf-8")
            if not isinstance(text, str):
                raise TypeError(
                    f"libraries[{path!r}] must be the library's source text")
            self._sources[str(path)] = text

    def pinned(self, path: str) -> bool:
        return path in self._sources

    def load(self, path: str) -> LibrarySource:
        if path not in self._sources:
            raise LibraryResolveError(
                f"library '{path}' is not among the libraries passed to transpile()")
        text = self._sources[path]
        return LibrarySource(path, text, None)


class _Pinned(LibraryResolver):
    """The libraries one probe's requests manifest pins."""

    def __init__(self, library_dir: Path, requests_root: Path, slug_dir: Path,
                 manifest: dict) -> None:
        self._library_dir = library_dir
        self._requests_root = requests_root
        self._slug_dir = slug_dir
        self._where = f"{slug_dir.name}/requests.json"
        self._pins: dict[str, dict] = {}
        for entry in manifest.get("libraries") or []:
            if isinstance(entry, dict) and isinstance(entry.get("import"), str):
                self._pins.setdefault(entry["import"], entry)
        self._index: dict | None = None

    def pinned(self, path: str) -> bool:
        return path in self._pins

    def _library_index(self) -> dict:
        if self._index is None:
            self._index = {}
            try:
                doc = json.loads((self._library_dir / "libraries.json")
                                 .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
            if (isinstance(doc, dict)
                    and doc.get("schemaVersion", LIBRARIES_SCHEMA) == LIBRARIES_SCHEMA
                    and isinstance(doc.get("libraries"), dict)):
                self._index = {k: v for k, v in doc["libraries"].items()
                               if isinstance(v, dict)}
        return self._index

    def load(self, path: str) -> LibrarySource:
        entry = self._pins.get(path)
        if entry is None:
            raise LibraryResolveError(
                f"library '{path}' is not pinned by this script's requests "
                f"manifest ({self._where})")
        access = entry.get("access")
        if not (isinstance(access, str) and access.startswith("open")):
            raise LibraryResolveError(
                f"library '{path}' is not open-source (access {access!r}); "
                "PineForge inlines open libraries only")
        sha = entry.get("sha256")
        if not (isinstance(sha, str) and _SHA256_RE.fullmatch(sha)):
            raise LibraryResolveError(
                f"library '{path}' has no valid sha256 in {self._where}")
        indexed = self._library_index().get(path) or {}
        indexed_access = indexed.get("access")
        if indexed_access is not None and not (
                isinstance(indexed_access, str) and indexed_access.startswith("open")):
            raise LibraryResolveError(
                f"library '{path}' is not open-source (access {indexed_access!r} "
                "in libraries.json); PineForge inlines open libraries only")
        candidates = [self._slug_dir / "files" / sha]
        rel = indexed.get("file")
        if isinstance(rel, str) and rel and not Path(rel).is_absolute() \
                and ".." not in Path(rel).parts:
            candidates.append(self._library_dir / rel)
        candidates.append(self._library_dir / f"{path}.pine")
        seen: list[str] = []
        for candidate in dict.fromkeys(candidates):
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            got = hashlib.sha256(data).hexdigest()
            if got != sha:
                seen.append(got)
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LibraryResolveError(
                    f"library '{path}' source is not UTF-8 text") from exc
            return LibrarySource(path, text, sha)
        if seen:
            raise LibraryResolveError(
                f"library '{path}' source does not match its pinned sha256 "
                f"{sha[:12]}... (found {seen[0][:12]}...)")
        raise LibraryResolveError(
            f"library '{path}' source is missing (sha256 {sha[:12]}... under "
            f"${REQUESTS_ENV}/{self._slug_dir.name}/files and ${LIBRARIES_ENV})")


def _manifest_for(text: str, requests_root: Path) -> tuple[Path | None, dict | None, str]:
    """The one requests manifest under ``requests_root`` pinning ``text``."""
    digests = source_digests(text)
    matches: list[tuple[Path, dict]] = []
    try:
        candidates = sorted(requests_root.glob("*/requests.json"))
    except OSError:
        candidates = []
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("schemaVersion") != REQUESTS_SCHEMA:
            continue
        probe = doc.get("probe")
        sha = probe.get("strategySha256") if isinstance(probe, dict) else None
        if isinstance(sha, str) and sha.lower() in digests:
            matches.append((path.parent, doc))
    if len(matches) == 1:
        return matches[0][0], matches[0][1], ""
    if not matches:
        return None, None, (
            f"no requests manifest under ${REQUESTS_ENV} pins this script's "
            "source")
    slugs = ", ".join(sorted(p.name for p, _ in matches))
    return None, None, (
        f"{len(matches)} requests manifests under ${REQUESTS_ENV} pin this "
        f"script's source ({slugs})")


def library_resolver(source: str, libraries: Mapping[str, str] | None) -> LibraryResolver:
    """The library sources ``transpile(source, libraries=libraries)`` reads."""
    if libraries is not None:
        return _Provided(libraries)
    library_dir = os.environ.get(LIBRARIES_ENV)
    if not library_dir:
        return _Unconfigured()
    requests_root = os.environ.get(REQUESTS_ENV)
    if not requests_root:
        return _Unconfigured(
            f"${LIBRARIES_ENV} is set but ${REQUESTS_ENV} is not, so no "
            "requests manifest pins this script's libraries")
    slug_dir, manifest, reason = _manifest_for(source, Path(requests_root))
    if manifest is None:
        return _Unconfigured(reason)
    return _Pinned(Path(library_dir), Path(requests_root), slug_dir, manifest)
