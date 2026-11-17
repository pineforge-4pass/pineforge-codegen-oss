"""Byte-identical golden harness for matrix corpus probes (Task 2.17).

Goldens compare legacy_cpp-normalised lowering, excluding the additive settings
scaffold and exception wrappers. test_checked_settings exercises actual output.
"""
import os
from pathlib import Path
import pytest
from tests._emitted_member_assertions import assert_int_member, assert_member_type
from tests._legacy_cpp import legacy_cpp
from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

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
    # The golden keeps coded call sites, excluding additive run-failure scaffolds.
    src = MATRIX_EIGEN_FIXTURE.read_text()
    expected = (GOLDEN_ROOT / "matrix_eigen_pca.cpp").read_text()
    assert legacy_cpp(transpile(src)) == expected


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
    """backadjustment.on uses the analyzer's INT member-storage contract."""
    cpp = _gen("x = backadjustment.on\n")
    assert_int_member(cpp, "x")


def test_g2_backadjustment_on_wrong_decl_type_control():
    cpp = _gen("other = 7\nx = backadjustment.on\n")
    assert_int_member(cpp, "other")
    declaration = assert_int_member(cpp, "x")
    assert cpp.count(declaration) == 1
    wrong_declaration = declaration.replace("    int ", "    double ", 1)
    mutated = cpp.replace(declaration, wrong_declaration, 1)
    assert mutated != cpp
    assert mutated.count(wrong_declaration) == 1
    with pytest.raises(AssertionError, match="Expected int x, got double x"):
        assert_int_member(mutated, "x")


def _tuple_bound_holder(name: str) -> str:
    """The body that binds a ``Holder`` record to ``name`` through a tuple and
    reads its float field into ``v``."""
    return (
        "type Holder\n"
        "    float factor = 0.0\n"
        "pair() =>\n"
        "    [Holder.new(1.5), 1]\n"
        f"[{name}, k] = pair()\n"
        f"v = {name}.factor\n"
    )


# TradingView has answered (relayed 2026-11-17 16:40): it refuses a record
# that a tuple declaration names ``backadjustment`` or ``settlement_as_close``
# with CE10093, at the name, through its last column 15 and 20. The name sits
# at the same line and column here as in the shape TradingView was shown (the
# first target of a tuple on line 7). These two rows pinned the acceptance of
# this program until that answer.
@pytest.mark.parametrize(
    "name, last_col",
    [("backadjustment", 15), ("settlement_as_close", 20)],
    ids=["backadjustment", "settlement_as_close"],
)
def test_g2_authored_instance_named_like_an_adjustment_namespace(name, last_col):
    """A record that a tuple declaration names like a data-adjustment namespace
    is refused with PF-E2065 at the name itself, as TradingView refuses it."""
    body = _tuple_bound_holder(name)
    with pytest.raises(CompileError) as caught:
        _gen(body)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2065"
    assert diagnostic.message == (
        f"Invalid object name: {name}. Namespaces of built-ins cannot be used."
    )
    location = diagnostic.location
    # TradingView counts the last column of the name; a location here ends one
    # column after it. _gen puts two header lines before the body.
    assert (location.line, location.col, location.end_col - 1) == (7, 2, last_col)
    declaration = body.splitlines()[location.line - 3]
    assert declaration == f"[{name}, k] = pair()"
    assert declaration[location.col - 1:location.end_col - 1] == name


def test_g2_authored_instance_named_adjustment_is_refused():
    """TradingView refuses a record that a tuple declaration names
    ``adjustment`` (CE10093, measured on the scripts pinned in
    test_namespace_named_receivers.py). This program has that declaration
    form, so its translation is refused with PF-E2065 at the name itself."""
    body = _tuple_bound_holder("adjustment")
    with pytest.raises(CompileError) as caught:
        _gen(body)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2065"
    assert diagnostic.message == (
        "Invalid object name: adjustment. Namespaces of built-ins cannot be used."
    )
    location = diagnostic.location
    # The identifier as the script wrote it, in its declaration: neither the
    # bracket before it nor the read on the next line. _gen puts two header
    # lines before the body.
    assert (location.line, location.col, location.end_col) == (7, 2, 12)
    declaration = body.splitlines()[location.line - 3]
    assert declaration == "[adjustment, k] = pair()"
    assert declaration[location.col - 1:location.end_col - 1] == "adjustment"


def test_g2_authored_instance_under_an_ordinary_name_keeps_float_field():
    """The refused program with its record under a name that spells no built-in
    namespace: reading the record's float field keeps floating storage."""
    cpp = _gen(_tuple_bound_holder("holder"))
    assert_member_type(cpp, "v", "double")


def test_g2_authored_instance_named_time_keeps_float_field():
    """``time`` is a built-in value that qualifies no member, not a namespace:
    TradingView accepts a record that a tuple declaration names ``time``
    (relayed 2026-11-17 16:40; it warns that the name shadows the built-in
    variable). Reading the record's float field keeps floating storage."""
    cpp = _gen(_tuple_bound_holder("time"))
    assert_member_type(cpp, "v", "double")


def test_g2_adjustment_dividends_no_string():
    """adjustment.dividends should emit integer constant, not a string."""
    cpp = _gen("x = adjustment.dividends\n")
    assert '"dividends"' not in cpp  # should NOT emit string literal
