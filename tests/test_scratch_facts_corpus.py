"""Lane V corpus census: every validation probe has a literal id vocabulary.

The static census (core-cr150-corpcls-a/b/c, Appendix E of the o1roots report)
classifies all 312 pineforge-engine validation probes as literal vocabulary
(MINTS=No). This test is the producer-side cross-check: it runs the real
emitter over the same probe population and requires every probe the census
lists as literal vocabulary to classify IN-SUBSET, and every OUT probe to name
at least one reason code. It asserts no count of its own; the population is
the corpus tree, and the OUT list is printed so the supervisor can read it.

The corpus is a sibling checkout; the resolver matches
tests/test_compile_corpus.py. Without it the module skips.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get("PINEFORGE_ENGINE_CORPUS", "")
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser())
    engine_include = os.environ.get("PINEFORGE_ENGINE_INCLUDE", "")
    if engine_include:
        include = Path(engine_include).expanduser().resolve()
        candidates.append(include.parent / "corpus")
        candidates.append(include / "corpus")
    repo_root = Path(__file__).resolve().parent.parent
    candidates.append(repo_root.parent / "pineforge-engine" / "corpus")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "data").is_dir():
            return candidate.resolve()
    return None


CORPUS_ROOT = _resolve_corpus_root()
pytestmark = pytest.mark.skipif(CORPUS_ROOT is None,
                                reason="pineforge-engine corpus is not available")

RECEIPT = re.compile(r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);')
HEX = re.compile(r"(?:[0-9a-f]{2})+")


def _probes() -> list[Path]:
    return sorted((CORPUS_ROOT / "validation").glob("*/strategy.pine"))


def test_every_validation_probe_is_classified():
    probes = _probes()
    assert probes, "the validation corpus is empty"
    outside = []
    for path in probes:
        source = path.read_text(encoding="utf-8")
        cpp = transpile(source, filename=str(path))
        documents = RECEIPT.findall(cpp)
        assert len(documents) == 3, path
        facts = json.loads(json.loads(documents[2]))["scratch_facts"]
        assert facts["version"] == 1 and HEX.fullmatch(facts["certificate"]), path
        if facts["classification"] == "out_of_subset":
            # Every OUT classification names why: no silent conservatism.
            assert facts["reasons"], path
            outside.append((path.parent.name, facts["reasons"]))
        else:
            assert facts["classification"] == "in_subset", path
            assert facts["reasons"] == [], path
    if outside:
        print("out_of_subset probes:")
        for name, reasons in outside:
            print(f"  {name}: {', '.join(reasons)}")
    assert outside == [], f"{len(outside)} literal-vocabulary probes classified OUT"
