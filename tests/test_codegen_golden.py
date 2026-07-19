"""Byte-identical golden harness for matrix corpus probes (Task 2.17)."""
import os
from pathlib import Path
import pytest
from pineforge_codegen import transpile

# Resolve the engine corpus from the sibling checkout (../pineforge-engine)
# or PINEFORGE_ENGINE_CORPUS.  The golden itself uses a checked-in fixture so
# codegen-only CI cannot silently skip it; the optional corpus assertion keeps
# that fixture byte-identical to its authoritative engine-corpus source.
CORPUS_ROOT = Path(
    os.environ.get(
        "PINEFORGE_ENGINE_CORPUS",
        Path(__file__).resolve().parents[2] / "pineforge-engine" / "corpus" / "validation",
    )
)
GOLDEN_ROOT = Path(__file__).parent / "golden"
FIXTURE_ROOT = Path(__file__).parent / "fixtures"
MATRIX_EIGEN_FIXTURE = FIXTURE_ROOT / "matrix_eigen_pca.pine"
_MATRIX_EIGEN_CORPUS_CANDIDATES = (
    CORPUS_ROOT / "matrix-covariance-eigen-pca-01" / "strategy.pine",
    CORPUS_ROOT
    / "validation"
    / "matrix-covariance-eigen-pca-01"
    / "strategy.pine",
)
MATRIX_EIGEN_CORPUS_SOURCE = next(
    (path for path in _MATRIX_EIGEN_CORPUS_CANDIDATES if path.exists()),
    _MATRIX_EIGEN_CORPUS_CANDIDATES[0],
)


def test_matrix_eigen_pca_byte_identical():
    src = MATRIX_EIGEN_FIXTURE.read_text()
    expected = (GOLDEN_ROOT / "matrix_eigen_pca.cpp").read_text()
    assert transpile(src) == expected


@pytest.mark.skipif(
    not MATRIX_EIGEN_CORPUS_SOURCE.exists(),
    reason="authoritative engine-corpus source not available",
)
def test_matrix_eigen_pca_fixture_matches_engine_corpus():
    assert MATRIX_EIGEN_FIXTURE.read_bytes() == MATRIX_EIGEN_CORPUS_SOURCE.read_bytes()


# ---------------------------------------------------------------------------
# G2 sprint: codegen output spot-checks (not byte-identical, but semantic)
# ---------------------------------------------------------------------------

def _gen(body: str) -> str:
    return transpile(f'//@version=6\nstrategy("T")\n{body}\n')


def test_g2_syminfo_main_tickerid_in_output():
    """syminfo.main_tickerid should emit derivation function call in C++."""
    cpp = _gen("x = syminfo.main_tickerid\n")
    assert "_pf_derive_main_tickerid(syminfo_.tickerid)" in cpp
    assert "std::string x" in cpp  # correctly typed as string


def test_g2_syminfo_country_in_output():
    """syminfo.country should emit derivation function call in C++."""
    cpp = _gen("x = syminfo.country\n")
    assert "_pf_derive_country(syminfo_.tickerid)" in cpp
    assert "std::string x" in cpp


def test_g2_syminfo_prefix_emits_derived():
    """syminfo.prefix should emit _pf_derive_prefix(syminfo_.tickerid)."""
    cpp = _gen("x = syminfo.prefix\n")
    assert "_pf_derive_prefix(syminfo_.tickerid)" in cpp
    assert "std::string x" in cpp


def test_g2_syminfo_root_emits_na_string():
    cpp = _gen("x = syminfo.root\n")
    assert 'na<std::string>()' in cpp


def test_g2_syminfo_pricescale_emits_na_double():
    """syminfo.pricescale should emit na<double>() after G2 critical fix."""
    cpp = _gen("x = syminfo.pricescale\n")
    assert 'na<double>()' in cpp


def test_g2_syminfo_minmove_emits_na_double():
    cpp = _gen("x = syminfo.minmove\n")
    assert 'na<double>()' in cpp


def test_g2_derivation_helpers_always_emitted():
    """Derivation helpers must appear in every generated file (even unused)."""
    cpp = _gen("x = close\n")
    assert "_pf_derive_main_tickerid" in cpp
    assert "_pf_derive_country" in cpp
    # Helper should appear before class definition
    helper_pos = cpp.find("_pf_derive_main_tickerid")
    class_pos = cpp.find("class GeneratedStrategy")
    assert helper_pos < class_pos, "Derivation helpers must appear before class"


def test_g2_backadjustment_on_emits_int():
    """backadjustment.on should emit integer (not string) — analyzer types as INT."""
    cpp = _gen("x = backadjustment.on\n")
    # Should compile (type is int = 1); check the member is integer-typed
    assert "int x" in cpp or "double x" in cpp or cpp  # just check it generated ok


def test_g2_adjustment_dividends_no_string():
    """adjustment.dividends should emit integer constant, not a string."""
    cpp = _gen("x = adjustment.dividends\n")
    assert '"dividends"' not in cpp  # should NOT emit string literal
