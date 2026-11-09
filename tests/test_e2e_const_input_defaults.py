"""Read constant color defaults through native settings and order quantities."""

import json
from decimal import Decimal

import pytest

from pineforge_codegen import transpile_full
from tests._compile import run_emitted_tu
from tests._e2e import (
    build_strategy_library, chart_feed_head, closed_trades, skip_unless_e2e_env,
)
from tests.test_const_input_defaults import DEFAULT_CASES, direct_value, source
from tests.test_string_settings_constants import RECEIPT_DRIVER


def readout(pine):
    return pine + "".join(
        f'if bar_index == {index}\n'
        f'    strategy.entry("channel_{channel}", strategy.long, '
        f'qty=1 + color.{channel}(shade) / 1000)\n'
        f'if bar_index == {index + 1}\n'
        f'    strategy.close("channel_{channel}")\n'
        for index, channel in zip((9, 19, 29, 39), ("r", "g", "b", "t"))
    )


@pytest.mark.parametrize("label,declarations,default,direct", DEFAULT_CASES,
                         ids=[row[0] for row in DEFAULT_CASES])
def test_native_receipt_resolves_the_default_before_on_bar(label, declarations, default, direct):
    full = transpile_full(source(declarations, default))
    expected = direct_value(direct)
    # Settings inspection alone never executes the static input preamble.
    # Run a bar without overrides and inspect the value the script actually
    # reads, as well as the receipt obtained before the bar.
    driver = RECEIPT_DRIVER.replace("    strategy_free(strategy);", r'''
    Bar bars[]{{100, 101, 99, 100, 1000, 1577836800000LL}};
    ReportC report{};
    assert(run_backtest_full_checked(strategy, bars, 1, "1", "1", 0, 4, 0,
                                     &report, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(error[0] == '\0');
    assert(static_cast<GeneratedStrategy*>(strategy)->shade == EXPECTED_DEFAULT);
    report_free(&report);
    strategy_free(strategy);
''').replace("EXPECTED_DEFAULT", f"{expected}LL")
    receipt = json.loads(run_emitted_tu(full["cpp"], driver, opt="-O0",
                                       label="constant color input receipt and execution"))
    assert int(receipt["inputs"][0]["default"]) == expected
    assert full["inputs"][0]["default"] == expected


@pytest.fixture(scope="module")
def native_color_feed(tmp_path_factory):
    root = skip_unless_e2e_env()
    feed = chart_feed_head(root, tmp_path_factory.mktemp("constant_color_feed"), 48)
    return root, feed


@pytest.mark.parametrize("named,context", [
    (False, "global"), (True, "global"), (False, "function"),
    (False, "function_local"), (False, "switch"), (False, "if"), (False, "loop"),
])
@pytest.mark.parametrize("member", ["blue", "red"])
def test_native_channel_trades_and_overrides_match_the_direct_default(
        tmp_path, native_color_feed, named, context, member):
    root, feed = native_color_feed
    candidate = transpile_full(readout(source(
        f"const color seed = color.{member}", named=named, context=context)))
    control = transpile_full(readout(source("", f"color.{member}")))
    results = []
    override = direct_value("color.red" if member == "blue" else "color.blue")
    for name, full in (("carrier", candidate), ("direct", control)):
        work = tmp_path / name
        work.mkdir()
        build_strategy_library(full["cpp"], work)
        metadata = {"mincontract": 0.00001}
        defaults = closed_trades(root, work, feed, syminfo_metadata=metadata)
        assert len(defaults) == 4
        # Function/switch inputs have no exported settings descriptor on the
        # existing path. Check overrides on the exported global input.
        overridden = None
        if context == "global":
            overridden = closed_trades(root, work, feed, {"Tint": str(override)},
                                       syminfo_metadata=metadata)
            assert len(overridden) == 4
            assert any(Decimal(str(a["qty"])) != Decimal(str(b["qty"]))
                       for a, b in zip(defaults, overridden))
        results.append((defaults, overridden))
    assert results[0] == results[1]
