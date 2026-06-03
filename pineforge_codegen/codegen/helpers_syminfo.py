"""Syminfo derivation helpers for PineForge codegen.

Emits C++ inline helper functions for syminfo fields that can be derived
at codegen/runtime from the existing SymInfo struct without requiring
engine changes:

- ``_pf_derive_main_tickerid(tickerid)``  — strip futures suffix from tickerid
  e.g., ``"CME_MINI:ES1!"`` → ``"CME_MINI:ES"``, ``"NASDAQ:AAPL"`` → ``"NASDAQ:AAPL"``
- ``_pf_derive_country(tickerid)``         — lookup country by exchange prefix
  e.g., ``"NASDAQ:AAPL"`` → ``"US"``, ``"LSE:BP"`` → ``"UK"``

These are emitted as ``static inline`` free functions before the
``GeneratedStrategy`` class definition. They depend only on ``<string>``
and ``<regex>`` (both already pulled in by the standard includes block).
"""

# Prefix → country lookup table used for ``syminfo.country`` derivation.
# Mirrors Pine v6 semantics best-effort; not an exhaustive list.
PREFIX_TO_COUNTRY: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "NYMEX": "US",
    "AMEX": "US",
    "ARCA": "US",
    "CBOE": "US",
    "CME": "US",
    "CME_MINI": "US",
    "CBOT": "US",
    "COMEX": "US",
    "OTC": "US",
    "LSE": "UK",
    "AQUIS": "UK",
    "TSE": "JP",
    "OSE": "JP",
    "HKEX": "HK",
    "SGX": "SG",
    "ASX": "AU",
    "EURONEXT": "EU",
    "XETRA": "DE",
    "BSE": "IN",
    "NSE": "IN",
    "BINANCE": "GLOBAL",
    "COINBASE": "US",
    "KRAKEN": "GLOBAL",
    "BYBIT": "GLOBAL",
    "OKX": "GLOBAL",
    "BITMEX": "GLOBAL",
    "DERIBIT": "GLOBAL",
    "UPBIT": "KR",
    "KRX": "KR",
    "KOSPI": "KR",
    "SSE": "CN",
    "SZSE": "CN",
    "JSE": "ZA",
    "BMF": "BR",
    "BMFBOVESPA": "BR",
    "B3": "BR",
    "MOEX": "RU",
    "TSX": "CA",
    "VENTURE": "CA",
    "SIX": "CH",
}

# ---------------------------------------------------------------------------
# C++ code generation
# ---------------------------------------------------------------------------

# The futures-suffix regex: strips one or more digits at the end, optionally
# followed by a ``!``.  Examples:
#   CME_MINI:ES1!  → CME_MINI:ES
#   NYMEX:CL2!     → NYMEX:CL
#   NASDAQ:AAPL    → NASDAQ:AAPL  (no suffix, unchanged)
_MAIN_TICKERID_CPP = r"""
static inline std::string _pf_derive_prefix(const std::string& tickerid) {
    std::size_t colon = tickerid.find(':');
    return (colon == std::string::npos) ? tickerid : tickerid.substr(0, colon);
}

static inline std::string _pf_derive_main_tickerid(const std::string& tickerid) {
    // Strip trailing digits (optionally followed by '!') from the symbol part.
    // e.g. "CME_MINI:ES1!" -> "CME_MINI:ES", "NYMEX:CL2!" -> "NYMEX:CL"
    std::string result = tickerid;
    std::size_t colon = result.find(':');
    std::size_t start = (colon == std::string::npos) ? 0 : colon + 1;
    // Find end of base symbol (strip trailing digits + optional '!')
    std::size_t end = result.size();
    if (end > start && result[end - 1] == '!') {
        --end;
    }
    while (end > start && std::isdigit((unsigned char)result[end - 1])) {
        --end;
    }
    return result.substr(0, end);
}
""".strip()


def _build_country_lookup_cpp() -> str:
    """Build the C++ prefix-to-country lookup table from PREFIX_TO_COUNTRY."""
    entries = []
    for prefix, country in sorted(PREFIX_TO_COUNTRY.items()):
        entries.append(f'        {{"{prefix}", "{country}"}}')
    table = ",\n".join(entries)
    return (
        "static inline std::string _pf_derive_country(const std::string& tickerid) {\n"
        "    // Lookup country by exchange prefix (text before ':').\n"
        "    std::size_t colon = tickerid.find(':');\n"
        "    std::string prefix = (colon == std::string::npos)\n"
        "        ? tickerid : tickerid.substr(0, colon);\n"
        "    static const std::unordered_map<std::string, std::string> _tbl = {\n"
        f"{table}\n"
        "    };\n"
        "    auto it = _tbl.find(prefix);\n"
        '    return (it != _tbl.end()) ? it->second : na<std::string>();\n'
        "}\n"
    )


def emit_syminfo_helpers() -> list[str]:
    """Return list of C++ lines for the syminfo derivation helper functions.

    Call this from ``_emit_includes`` (after the ``using namespace pineforge;``
    line) to inject the helpers before the ``GeneratedStrategy`` class.
    """
    lines: list[str] = []
    lines.append("// --- syminfo derivation helpers (PineForge G2) ---")
    for cpp_line in _MAIN_TICKERID_CPP.splitlines():
        lines.append(cpp_line)
    lines.append("")
    for cpp_line in _build_country_lookup_cpp().splitlines():
        lines.append(cpp_line)
    lines.append("// --- end syminfo derivation helpers ---")
    lines.append("")
    return lines
