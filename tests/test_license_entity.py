"""Pin the registered licensor's exact spelling in the current documents."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTITY = "PINEFORGE LLC"
DOCUMENT_ENTITY_COUNTS = {
    "LICENSE": 3,
    "LEGAL.md": 3,
    "README.md": 1,
    "CONTRIBUTING.md": 1,
}


def test_license_entity():
    for filename, expected_count in DOCUMENT_ENTITY_COUNTS.items():
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert ENTITY in text, f"{filename}: missing the exact licensor spelling"
        assert re.search(r"pineforge,\s*LLC", text, re.IGNORECASE) is None, (
            f"{filename}: the old comma form remains"
        )
        entities = re.findall(r"\bpineforge,?\s*llc\b", text, re.IGNORECASE)
        assert entities == [ENTITY] * expected_count, (
            f"{filename}: expected exactly {expected_count} canonical licensor mentions; "
            f"found {entities!r}"
        )
