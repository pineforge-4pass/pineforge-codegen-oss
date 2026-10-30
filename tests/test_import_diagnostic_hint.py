from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._pine_libraries import Layout, library_sources


PRELUDE = '//@version=6\nstrategy("T")\n'
# This exact sentence is the guidance shown for every unresolved import.
HINT = (
    "Libraries are supported only when the library source is supplied: "
    "remove the import, or paste the library code into the script."
)


@pytest.mark.parametrize(
    ("line", "body", "message"),
    [
        (
            "import user/lib/1",
            "",
            # Existing refusal text for a path without an alias.
            "Import is not supported: 'user/lib/1'",
        ),
        (
            "import user/lib/1 as x",
            "",
            # Existing refusal text preserves the written alias.
            "Import is not supported: 'user/lib/1 as x'",
        ),
        (
            "import someone/Logger/1 as log",
            'log.info("x")\n',
            # Existing refusal text preserves a non-no-op built-in alias.
            "Import is not supported: 'someone/Logger/1 as log'",
        ),
    ],
)
@pytest.mark.parametrize("check_support", [True, False])
def test_unresolved_imports_keep_message_and_add_hint(
    line: str, body: str, message: str, check_support: bool
):
    source = PRELUDE + line + "\n" + body
    with pytest.raises(CompileError) as caught:
        transpile(source, check_support=check_support)

    (diagnostic,) = [
        item for item in caught.value.diagnostics if item.message == message
    ]
    assert diagnostic.message == message
    assert diagnostic.hint == HINT
    # The import is the third source line and starts at column 1.
    assert (diagnostic.location.line, diagnostic.location.col) == (3, 1)


@pytest.mark.parametrize("check_support", [True, False])
def test_configured_but_unpinned_import_keeps_reason_and_hint(
    tmp_path, monkeypatch, check_support: bool
):
    source = PRELUDE + "import pftest/Signals/1 as S\n"
    layout = Layout(tmp_path)
    digest = layout.add_library("pftest/Signals/1")
    layout.add_probe(
        "neighbour",
        b'//@version=6\nstrategy("N")\n',
        {"pftest/Signals/1": digest},
    )
    for key, value in layout.env.items():
        monkeypatch.setenv(key, value)

    # The resolver appends this existing reason when no manifest matches.
    expected = (
        "Import is not supported: 'pftest/Signals/1 as S': no requests "
        "manifest under $PINEFORGE_REQUESTS_ROOT pins this script's source"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, check_support=check_support)

    (diagnostic,) = [
        item for item in caught.value.diagnostics if item.message == expected
    ]
    assert diagnostic.message == expected
    assert diagnostic.hint == HINT
    # The import is the third source line and starts at column 1.
    assert (diagnostic.location.line, diagnostic.location.col) == (3, 1)


def test_resolved_library_import_is_still_accepted():
    source = (
        PRELUDE
        + "import pftest/Signals/1 as S\n"
        + "x = S.scaled(close)\n"
        + "plot(x)\n"
    )
    transpile(source, libraries=library_sources("pftest/Signals/1"))


def test_builtin_namespace_noop_import_is_still_accepted():
    source = (
        PRELUDE
        + "import TradingView/ta/7 as ta\n"
        + "x = ta.sma(close, 2)\n"
        + "plot(x)\n"
    )
    transpile(source)


def test_analyzer_import_visitor_keeps_the_hint():
    source = PRELUDE + "import user/lib/1 as x\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()

    with pytest.raises(CompileError) as caught:
        Analyzer(ast).analyze()

    (diagnostic,) = caught.value.diagnostics
    # The analyzer path sees the same source line and the same literal message.
    assert diagnostic.message == "Import is not supported: 'user/lib/1 as x'"
    assert diagnostic.hint == HINT
    assert (diagnostic.location.line, diagnostic.location.col) == (3, 1)
