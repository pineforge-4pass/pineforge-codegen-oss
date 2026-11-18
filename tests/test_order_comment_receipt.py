"""A strategy.order comment stays out of the version-1 order-shapes receipt.

``strategy_order_shapes_receipt`` is a published version-1 document
(docs/PUBLIC_CONTRACT.md). Forwarding an order's comment to the engine adds no
field to it: with a comment or without, the document this build emits is the
one main emits for the same script, byte for byte.

There is one exception, and it is named. The generated code evaluates a
forwarded comment, so what the comment's expression reads from the host
appears in ``host_reads``. Of these scripts only the one whose comment is
``str.tostring(close)`` reads a host member that nothing else in it reads: the
mintick a formatted number takes. For that script the document is main's with
exactly that one name added to ``host_reads``, and nothing else changed.

Both sides are the raw C++ string literal a build writes into its generated
``checked_settings::receipt(...)`` call, and they are compared as text. The
reference is produced by main's own codegen at the pinned commit, never by
this build, and this build's literal is never parsed, re-encoded, sorted or
filtered. The one expected name is written into main's document, after
checking that writing that document again reproduces main's literal exactly.
"""

from __future__ import annotations

import json
import re

import pytest

from tests._e2e import reference_codegen, transpile_json


# main, where an order's comment is accepted and not forwarded.
RECEIPT_REFERENCE = "b95add0b7ed777227ef5551d94e1bcb8c5311c50"
RECEIPT_PATTERN = r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);'
HEADER = '//@version=6\nstrategy("order comment receipt", process_orders_on_close=true)\n'
LONG_COMMENT = "c" * 256
# The one comment expression that reads a host member of its own, and that member.
EXPRESSION_COMMENT = "comment = str.tostring(close)"
EXPRESSION_COMMENT_HOST_READ = "syminfo_mintick_"


def _order_shapes_literal(cpp: str) -> str:
    """The one emitted receipt literal that is the order-shapes document."""
    documents = [document for document in re.findall(RECEIPT_PATTERN, cpp)
                 if r'\"entry_ids\"' in document]
    assert len(documents) == 1, documents
    return documents[0]


def _canonical(receipt: dict) -> str:
    """A receipt in the document's own encoding (``order_shapes_document``)."""
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _with_one_more_host_read(published: str, name: str) -> str:
    """Main's literal with ``name`` added to ``host_reads`` and nothing else changed.

    The literal is a JSON string that holds the canonical JSON document. Both
    encodings must reproduce main's bytes exactly before the name is added, so
    writing the document again cannot hide another difference.
    """
    document = json.loads(published)
    assert json.dumps(document) == published
    receipt = json.loads(document)
    assert _canonical(receipt) == document
    reads = receipt["host_reads"]
    assert reads == sorted(reads) and name not in reads, reads
    receipt["host_reads"] = sorted([*reads, name])
    return json.dumps(_canonical(receipt))


def _expected_literal(body: str, published: str) -> str:
    """The literal this build must emit for ``body``, given main's ``published``."""
    if EXPRESSION_COMMENT not in body:
        return published
    return _with_one_more_host_read(published, EXPRESSION_COMMENT_HOST_READ)


@pytest.mark.parametrize("body", (
    pytest.param('strategy.order("O", strategy.long, qty = 1)\n', id="without-comment"),
    pytest.param('strategy.order("O", strategy.long, qty = 1, comment = "")\n',
                 id="empty-literal"),
    pytest.param('strategy.order("O", strategy.long, qty = 1, comment = "c")\n',
                 id="short-literal"),
    pytest.param(f'strategy.order("O", strategy.long, qty = 1, comment = "{LONG_COMMENT}")\n',
                 id="long-literal"),
    pytest.param('tag = close > open ? "up" : "down"\n'
                 'strategy.order("O", strategy.long, qty = 1, comment = tag)\n',
                 id="series-string-variable"),
    pytest.param('strategy.order("O", strategy.long, qty = 1, limit = close, '
                 'comment = str.tostring(close))\n',
                 id="formatted-number"),
    pytest.param('strategy.entry("L", strategy.long, comment = "entry")\n'
                 'strategy.order("O", strategy.short, qty = 1, oca_name = "group", '
                 'oca_type = strategy.oca.reduce, comment = "order")\n'
                 'strategy.order("P", strategy.long, qty = 2)\n'
                 'strategy.exit("X", "O", limit = high, comment = "exit")\n'
                 'strategy.close("L", comment = "close")\n',
                 id="mixed-calls"),
))
def test_order_comment_keeps_the_order_shapes_receipt_bytes(tmp_path, body):
    reference = reference_codegen(RECEIPT_REFERENCE)
    assert reference is not None, "the pinned main codegen is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + body, encoding="utf-8")
    current = transpile_json(pine)
    previous = transpile_json(pine, reference)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    published = _order_shapes_literal(previous["cpp"])
    # The reference must be a real receipt of this script: it names its order call.
    assert r'\"call\":\"order\"' in published, published
    assert _order_shapes_literal(current["cpp"]) == _expected_literal(body, published)
