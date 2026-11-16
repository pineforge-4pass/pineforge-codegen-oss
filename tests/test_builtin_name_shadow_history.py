"""A declaration owns its history even when its name shadows a builtin."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


@pytest.mark.parametrize(
    ("name", "shadows_builtin"),
    [("second", True), ("minute", True), ("second", False)],
    ids=("second-shadowed", "minute-shadowed", "second-builtin"),
)
def test_history_feed_follows_declaration(name, shadows_builtin):
    timestamps = [1_704_068_617_000 + index * 73_000 for index in range(10)]
    bar_rows = ",\n".join(
        f"        {{10.0, 11.0, 9.0, 10.5, 100.0, {timestamp}LL}}"
        for timestamp in timestamps
    )
    declaration = f"int {name} = bar_index % 7\n" if shadows_builtin else ""
    source = (
        '//@version=6\nstrategy("builtin name shadow history")\n'
        + declaration
        + f"previous = {name}[1]\nplot(previous)\n"
    )
    driver = r"""
#include <iostream>
#include <vector>

struct ObservedStrategy : GeneratedStrategy {
    ObservedStrategy() { syminfo_.timezone = "Etc/UTC"; }
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        if (is_na(previous)) std::cout << "na\n";
        else std::cout << previous << '\n';
    }
};

int main() {
    std::vector<Bar> bars = {
BAR_ROWS
    };
    ObservedStrategy strategy;
    strategy.run(bars.data(), static_cast<int>(bars.size()));
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 1;
    }
}
""".replace("BAR_ROWS", bar_rows)
    values = [
        str(index % 7 if shadows_builtin else timestamp // 1000 % 60)
        for index, timestamp in enumerate(timestamps)
    ]
    actual = run_emitted_tu(
        transpile(source), driver, opt="-O2", label=f"{name} history ownership",
    ).splitlines()
    assert actual == ["na", *values[:-1]]
