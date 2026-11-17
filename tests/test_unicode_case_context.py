"""Unit classification of casing context; Pine values belong to TV probes."""

import pytest

from pineforge_codegen.codegen.unicode_case import _context_flags
from pineforge_codegen.codegen import unicode_case


@pytest.mark.parametrize("char, flags", [
    ("A", 1),          # Cased
    ("\u0301", 2),     # Case_Ignorable
    ("\u0345", 3),     # Cased and Case_Ignorable; skipping takes precedence
    (".", 2),
    (",", 0),
    ("1", 4),          # Decimal_Number, not Case_Ignorable or Cased
    ("\u0664", 4),
    ("\uff16", 4),
    ("\U0001d7d8", 4),
    ("中", 0),
])
def test_context_flags_keep_decimal_runs_separate_from_case_properties(char, flags):
    assert _context_flags(char) == flags


@pytest.mark.parametrize("field, invalid", [
    ("DATA_FORMAT", 0),
    ("DATA_VERSION", "0.0.0"),
    ("SOURCE_SHA256", ()),
    ("SOURCE_BYTE_COUNTS", ()),
])
def test_casing_data_requires_the_pinned_identity_before_use(monkeypatch, field, invalid):
    data = unicode_case._case_data()
    unicode_case._case_data.cache_clear()
    try:
        monkeypatch.setattr(data, field, invalid)
        with pytest.raises(RuntimeError, match="pinned UCD inputs"):
            unicode_case._case_data()
    finally:
        unicode_case._case_data.cache_clear()
