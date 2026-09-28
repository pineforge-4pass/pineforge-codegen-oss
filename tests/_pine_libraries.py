"""Library sources for tests, laid out as the campaign's case runner lays
them out (pineforge-workflow ``docs/xsym-requests.md``, "The environment
contract"): a case-wide ``PINEFORGE_PINE_LIBRARIES`` directory and one
``PINEFORGE_REQUESTS_ROOT/<slug>/requests.json`` per probe that pins data.

Only the clean-room synthetic libraries of ``fixtures/pine_libraries`` are
laid out here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LIBS = Path(__file__).parent / "fixtures" / "pine_libraries"


def library_text(path: str) -> str:
    return (LIBS / f"{path}.pine").read_text(encoding="utf-8")


def library_sources(*paths: str) -> dict[str, str]:
    """``libraries=`` for transpile(): the fixtures, by import path."""
    return {path: library_text(path) for path in paths}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Layout:
    """A requests root and a library directory under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.libraries = root / "libraries"
        self.requests = root / "requests"
        self.libraries.mkdir(parents=True, exist_ok=True)
        self.requests.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = {}

    @property
    def env(self) -> dict[str, str]:
        return {"PINEFORGE_PINE_LIBRARIES": str(self.libraries),
                "PINEFORGE_REQUESTS_ROOT": str(self.requests)}

    def add_library(self, path: str, data: bytes | None = None,
                    access: str = "open_no_auth") -> str:
        """Put ``path`` in the case-wide directory; its sha256."""
        data = library_text(path).encode("utf-8") if data is None else data
        target = self.libraries / f"{path}.pine"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = sha256(data)
        self._index[path] = {"sha256": digest, "file": f"{path}.pine",
                             "id": "PUB;" + digest[:32], "access": access}
        (self.libraries / "libraries.json").write_text(json.dumps(
            {"schemaVersion": "pineforge-pine-libraries/v1",
             "libraries": self._index}, indent=2))
        return digest

    def add_probe(self, slug: str, strategy: bytes, pins: dict[str, str],
                  access: str = "open_no_auth", files: dict[str, bytes] | None = None
                  ) -> Path:
        """A probe's manifest pinning ``pins`` (import path -> sha256) for a
        strategy file of ``strategy`` bytes; ``files`` go to its ``files/``."""
        probe = self.requests / slug
        (probe / "files").mkdir(parents=True, exist_ok=True)
        for digest, data in (files or {}).items():
            (probe / "files" / digest).write_bytes(data)
        manifest = {
            "schemaVersion": "pineforge-probe-requests/v1",
            "probe": {"probeId": f"test:data/standard/{slug}", "slug": slug,
                      "symbol": "BINANCE:ETHUSDT.P", "timeframe": "15",
                      "strategySha256": sha256(strategy)},
            "window": {"fromMs": 1743465600000, "toMs": 1744092000000},
            "symbols": {}, "feeds": [], "recorded": [],
            "libraries": [
                {"import": path, "id": "PUB;" + digest[:32],
                 "version": path.rsplit("/", 1)[1] + ".0", "access": access,
                 "sha256": digest, "licenseLine": None, "requires": [],
                 "provenanceSha256": "0" * 64}
                for path, digest in pins.items()
            ],
        }
        (probe / "requests.json").write_text(json.dumps(manifest, indent=2))
        return probe

    def pin_all(self, slug: str, script: str, *paths: str) -> dict[str, str]:
        """The env under which ``script`` (LF bytes) resolves ``paths``."""
        pins = {path: self.add_library(path) for path in paths}
        self.add_probe(slug, script.encode("utf-8"), pins)
        return self.env
