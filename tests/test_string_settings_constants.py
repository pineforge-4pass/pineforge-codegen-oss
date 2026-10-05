"""String settings never reinterpret inert builtin codes as char pointers."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import Identifier, MemberAccess, StringLiteral
from pineforge_codegen.codegen.checked_settings import _string_setting_arg
from pineforge_codegen.codegen.tables import (
    DRAWING_STYLE_NS,
    NAME_ECHO_STRING_MEMBERS,
    SKIP_NAMESPACES,
)
from pineforge_codegen.codegen.visit_expr import _BUILTIN_NAMESPACE_NAMES
from pineforge_codegen.support_checker import (
    UNSUPPORTED_CONST_NAMESPACES,
    UNSUPPORTED_NAMESPACE_VARS,
)
from tests._compile import run_emitted_tu


CONSTANT_FAMILIES = {
    "alert": ("freq_all", "all"),
    "currency": ("USD", "USD"),
    "format": ("mintick", "mintick"),
    "order": ("ascending", "ascending"),
    "session": ("regular", "regular"),
    "position": ("top_right", None),
    "size": ("small", None),
    "line": ("style_solid", None),
    "box": ("style_dashed", None),
    "label": ("style_label_up", None),
    "extend": ("right", None),
    "font": ("family_monospace", None),
    "hline": ("style_dotted", None),
    "location": ("abovebar", None),
    "plot": ("style_line", None),
    "scale": ("right", None),
    "shape": ("triangleup", None),
    "text": ("align_left", None),
    "xloc": ("bar_index", None),
    "yloc": ("price", None),
    "barmerge": ("gaps_on", None),
    "display": ("all", None),
    "dayofweek": ("monday", None),
    "adjustment": ("none", None),
    "backadjustment": ("on", None),
    "settlement_as_close": ("off", None),
    "strategy": ("oca.none", None),
    "color": ("red", None),
    "earnings": ("actual", None),
    "dividends": ("gross", None),
    "splits": ("denominator", None),
}

EXPECTED_NAME_ECHO_MEMBERS = {
    "currency": frozenset({
        "AED", "ARS", "AUD", "BDT", "BHD", "BRL", "BTC", "CAD", "CHF", "CLP",
        "CNY", "COP", "CZK", "DKK", "EGP", "ETH", "EUR", "GBP", "HKD", "HUF",
        "IDR", "ILS", "INR", "ISK", "JPY", "KES", "KRW", "KWD", "LKR", "MAD",
        "MXN", "MYR", "NGN", "NOK", "NONE", "NZD", "PEN", "PHP", "PKR", "PLN",
        "QAR", "RON", "RSD", "RUB", "SAR", "SEK", "SGD", "THB", "TND", "TRY",
        "TWD", "USD", "USDT", "VES", "VND", "ZAR",
    }),
    "format": frozenset({"inherit", "price", "volume", "percent", "mintick"}),
}

SOURCE_INPUTS = {
    "21173c20d257": ["Label Size 2", "Label Size 3", "Label Size 4",
                     "Label Size 5", "Wyckoff Label Size"],
    "2604d3bddc43": ["Position"],
    "65838a026c15": ["Position"],
    "a8f9d204f2e1": ["Line style (default solid)"],
    "ab97273519c6": ["Position"],
    "ddf1d5ad96ba": ["Position"],
}

RECEIPT_DRIVER = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[512]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required,
                                           error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    assert(required > 1);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                           error, sizeof(error)) == PF_SETTINGS_OK);
    assert(error[0] == '\0' && receipt.back() == '\0');
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''


def _metadata(cpp, name):
    start = cpp.index("std::vector<::pineforge::checked_settings::Setting> _pf_settings_inputs()")
    return next(line.strip() for line in cpp[start:].splitlines()
                if line.strip().startswith(f'{{"{name}", "string", '))


def test_constant_family_inventory_tracks_builtin_tables():
    visual = DRAWING_STYLE_NS | (SKIP_NAMESPACES - {"table", "polyline", "chart"})
    families = visual | set(UNSUPPORTED_CONST_NAMESPACES) | set(UNSUPPORTED_NAMESPACE_VARS) | {
        "currency", "format", "order", "session", "dayofweek", "adjustment",
        "backadjustment", "settlement_as_close", "strategy", "color",
    }
    assert set(CONSTANT_FAMILIES) == families
    assert families <= _BUILTIN_NAMESPACE_NAMES


def test_name_echo_member_inventory_is_finite_and_complete():
    assert NAME_ECHO_STRING_MEMBERS == EXPECTED_NAME_ECHO_MEMBERS


@pytest.mark.parametrize("namespace,member", [
    (namespace, member)
    for namespace, members in EXPECTED_NAME_ECHO_MEMBERS.items()
    for member in sorted(members)
])
@pytest.mark.parametrize("placement", ["default", "options"])
def test_inventoried_name_echo_members_remain_supported(namespace, member, placement):
    constant = f"{namespace}.{member}"
    arguments = (f'{constant}, "Choice"' if placement == "default" else
                 f'"literal", "Choice", options=["literal", {constant}]')
    cpp = transpile(f'//@version=6\nstrategy("known constants")\n'
                    f'choice = input.string({arguments})\n')
    metadata = _metadata(cpp, "Choice")
    assert ", true, {}," in metadata
    assert f'std::string("{member}")' in metadata
    if placement == "default":
        assert f'"string", std::string("{member}"), {{}}' in metadata
    else:
        assert f'{{std::string("literal"), std::string("{member}")}}' in metadata


@pytest.mark.parametrize("namespace,member", [("format", "foo"), ("currency", "XYZ")])
@pytest.mark.parametrize("placement", ["default", "options"])
def test_unknown_name_echo_members_are_unsupported(namespace, member, placement):
    constant = f"{namespace}.{member}"
    arguments = (f'{constant}, "Choice"' if placement == "default" else
                 f'"literal", "Choice", options=["literal", {constant}]')
    cpp = transpile(f'//@version=6\nstrategy("unknown constants")\n'
                    f'choice = input.string({arguments})\n', check_support=False)
    metadata = _metadata(cpp, "Choice")
    default = 'std::string("")' if placement == "default" else 'std::string("literal")'
    assert f'"string", {default}, {{}}' in metadata
    assert ", false, {}," in metadata
    assert f'std::string("{member}")' not in metadata


@pytest.mark.parametrize("namespace", CONSTANT_FAMILIES)
@pytest.mark.parametrize("placement", ["default", "options"])
def test_each_builtin_constant_family_is_string_or_unsupported(namespace, placement):
    member, expected = CONSTANT_FAMILIES[namespace]
    constant = f"{namespace}.{member}"
    arguments = (f'{constant}, "Choice"' if placement == "default" else
                 f'"literal", "Choice", options=["literal", {constant}]')
    cpp = transpile(f'//@version=6\nstrategy("constants")\n'
                    f'choice = input.string({arguments})\nplot(close)\n', check_support=False)
    metadata = _metadata(cpp, "Choice")
    assert ", true, {}," in metadata if expected is not None else ", false, {}," in metadata
    if expected is not None:
        assert f'std::string("{expected}")' in metadata
    else:
        default = 'std::string("")' if placement == "default" else 'std::string("literal")'
        assert f'"string", {default}, {{}}' in metadata
    assert not re.search(r'"string", (?:0|nullptr|NULL|[0-9]+),', metadata)
    assert not re.search(r'\{(?:0|nullptr|NULL)(?:,|\})', metadata)


@pytest.mark.parametrize("lowered", ["0", "1", "-1", "nullptr", "NULL", "false",
                                     "std::string(0)", "std::string(nullptr)",
                                     "std::string(\"x\") + 0", "unresolved",
                                     "std::string(\"x\"", '"x")'])
def test_non_string_literals_never_enter_string_metadata(lowered):
    assert _string_setting_arg(StringLiteral(value="x"), lowered) is None


@pytest.mark.parametrize("namespace", ["position", "size", "line", "shape", "text",
                                       "alert", "order", "session", "format", "currency"])
def test_unknown_builtin_members_do_not_fabricate_pine_strings(namespace):
    expression = MemberAccess(object=Identifier(name=namespace), member="unknown")
    assert _string_setting_arg(expression, 'std::string("unknown")') is None


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_unknown_name_echo_receipts_preserve_runtime_values(optimization):
    source = '''//@version=6
strategy("unknown name echo")
formatDefault = input.string(format.foo, "Format default")
currencyDefault = input.string(currency.XYZ, "Currency default")
formatOptions = input.string("literal", "Format options", options=["literal", format.foo])
currencyOptions = input.string("literal", "Currency options", options=["literal", currency.XYZ])
'''
    driver = RECEIPT_DRIVER.replace(
        "    std::cout << receipt.data();",
        '''    const std::string before(receipt.data());
    for (const char* name : {"Format default", "Currency default", "Format options", "Currency options"}) {
        assert(strategy_set_input_checked(strategy, name, "foo", error,
                                          sizeof(error)) == PF_SETTINGS_UNSUPPORTED);
    }
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                           error, sizeof(error)) == PF_SETTINGS_OK);
    assert(before == receipt.data());
    std::cout << receipt.data();''',
    )
    receipt = json.loads(run_emitted_tu(transpile(source, check_support=False), driver,
                                       opt=optimization, label="unknown name echo receipt"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    for name, effective in [("Format default", "foo"), ("Currency default", "XYZ"),
                            ("Format options", "literal"), ("Currency options", "literal")]:
        entry = inputs[name]
        assert entry["supported"] is False
        assert entry["options"] == []
        assert entry["default"] == ("" if name.endswith("default") else "literal")
        assert entry["effective_value"] == effective


@pytest.mark.parametrize("value", ["", "plain", 'quote"slash\\', "first\nsecond"])
def test_literal_strings_keep_exact_metadata(value):
    literal = json.dumps(value)
    cpp = transpile(f'//@version=6\nstrategy("literal")\n'
                    f'choice = input.string({literal}, "Choice", options=[{literal}])\n')
    metadata = _metadata(cpp, "Choice")
    assert ", true, {}," in metadata
    assert "std::string(" in metadata


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_known_constant_options_match_getters_and_legacy_values_are_unchanged(optimization):
    source = '''//@version=6
strategy("constant settings")
frequency = input.string(alert.freq_all, "Frequency", options=[alert.freq_all, alert.freq_once_per_bar])
direction = input.string(order.ascending, "Direction", options=[order.ascending, order.descending])
currencyChoice = input.string(currency.USD, "Currency", options=[currency.USD, currency.EUR])
sessionChoice = input.string(session.regular, "Session", options=[session.regular, session.extended])
numberFormat = input.string(format.mintick, "Format", options=[format.mintick, format.percent])
labelSize = input.string(size.small, "Size", options=[size.small, size.tiny])
if frequency == alert.freq_all
    strategy.entry("all", strategy.long)
if labelSize == "small"
    strategy.close("all")
'''
    driver = r'''
#include <cassert>
#include <iostream>
class InputProbe : public GeneratedStrategy {
public:
    std::string read(const char* name, const char* fallback) {
        return get_input_string(name, std::string(fallback));
    }
};
int main() {
    InputProbe strategy;
    char error[256]{};
    assert(strategy.read("Frequency", "all") == std::string("all"));
    assert(strategy.read("Size", "") == std::string(""));
    assert(strategy.read("Size", "") != std::string("small"));
    assert(strategy_set_input_checked(&strategy, "Frequency", "once_per_bar", error,
                                      sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy.read("Frequency", "all") == std::string("once_per_bar"));
    assert(strategy_set_input_checked(&strategy, "Direction", "descending", error,
                                      sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy.read("Direction", "ascending") == std::string("descending"));
    assert(strategy_set_input_checked(&strategy, "Currency", "EUR", error,
                                      sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy.read("Currency", "USD") == std::string("EUR"));
    assert(strategy_set_input_checked(&strategy, "Session", "extended", error,
                                      sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy.read("Session", "regular") == std::string("extended"));
    assert(strategy_set_input_checked(&strategy, "Format", "percent", error,
                                      sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy.read("Format", "mintick") == std::string("percent"));
    assert(strategy.read("Frequency", "all") != std::string("all"));
    assert(strategy_set_input_checked(&strategy, "Frequency", "freq_all", error,
                                      sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy.read("Frequency", "all") == std::string("once_per_bar"));
    assert(strategy_set_input_checked(&strategy, "Size", "small", error,
                                      sizeof(error)) == PF_SETTINGS_UNSUPPORTED);
    assert(strategy.read("Size", "") == std::string(""));
    strategy_set_input(&strategy, "Size", "small");
    assert(strategy.read("Size", "") == std::string("small"));
    assert(strategy_set_input_checked(&strategy, "Size", "tiny", error,
                                      sizeof(error)) == PF_SETTINGS_UNSUPPORTED);
    assert(strategy.read("Size", "") == std::string("small"));
    std::cout << strategy._pf_settings_receipt();
}
'''
    receipt = json.loads(run_emitted_tu(transpile(source), driver, opt=optimization,
                                       label="string constant getters"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    for name, default, options in [
        ("Frequency", "all", ["all", "once_per_bar"]),
        ("Direction", "ascending", ["ascending", "descending"]),
        ("Currency", "USD", ["USD", "EUR"]),
        ("Session", "regular", ["regular", "extended"]),
        ("Format", "mintick", ["mintick", "percent"]),
    ]:
        assert inputs[name]["supported"] is True
        assert inputs[name]["default"] == default
        assert inputs[name]["options"] == options
    assert inputs["Frequency"]["effective_value"] == "once_per_bar"
    assert inputs["Size"]["supported"] is False
    assert inputs["Size"]["default"] == "" and inputs["Size"]["options"] == []
    assert inputs["Size"]["effective_value"] == "small"


@pytest.mark.parametrize("sha12", SOURCE_INPUTS)
def test_population_source_settings_receipt(sha12):
    directory = os.environ.get("PINEFORGE_STRING_SETTINGS_SOURCES")
    if not directory:
        pytest.skip("population sources require PINEFORGE_STRING_SETTINGS_SOURCES")
    matches = list(Path(directory).glob(f"{sha12}*.pine"))
    assert len(matches) == 1
    source = matches[0].read_bytes()
    assert hashlib.sha256(source).hexdigest().startswith(sha12)
    cpp = transpile(source.decode())
    receipt = json.loads(run_emitted_tu(cpp, RECEIPT_DRIVER, opt="-O0",
                                       label=f"population settings {sha12}"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    for entry in inputs.values():
        assert isinstance(entry["default"], str)
        assert isinstance(entry["effective_value"], str)
        assert all(isinstance(option, str) for option in entry["options"])
    for name in SOURCE_INPUTS[sha12]:
        entry = inputs[name]
        assert entry["supported"] is False
        assert entry["default"] == entry["effective_value"] == ""
        assert entry["options"] == []
        assert '"string", std::string(""), {}' in _metadata(cpp, name)
