"""Hand-written receipt addition to the pinned 1e6eb83354b7 C++ scaffold."""


# Both identity probes have one literal strategy.entry("L", strategy.long).
# All rosters are complete. In unsigned-byte order:
# S = {L, __close__, __close__L, __intraday_cap_close__, __intraday_loss__,
#      __margin_call__, __margin_preopen__L, __pine_close_all}
# F = {"", L}; E = {}; H = S union {""}; J = {L}.
# With 109 string bytes in S, wire v1 costs:
# 1 + (9+8*8+109) + (9+8*2+1) + 9 + (9+8*9+109) + (9+8+1) = 426 bytes.
# These are literal expected bytes, never obtained from the current emitter.
# Adjacent literals follow version, then S,F,E,H,J; lengths/counts are u64 LE.
_LITERAL_ENTRY_FIELD = (
    r'\"scratch_facts\":{\"certificate\":\"'
    "01"
    "01" "0800000000000000"
    "0100000000000000" "4c"
    "0900000000000000" "5f5f636c6f73655f5f"
    "0a00000000000000" "5f5f636c6f73655f5f4c"
    "1600000000000000" "5f5f696e7472616461795f6361705f636c6f73655f5f"
    "1100000000000000" "5f5f696e7472616461795f6c6f73735f5f"
    "0f00000000000000" "5f5f6d617267696e5f63616c6c5f5f"
    "1300000000000000" "5f5f6d617267696e5f7072656f70656e5f5f4c"
    "1000000000000000" "5f5f70696e655f636c6f73655f616c6c"
    "01" "0200000000000000"
    "0000000000000000"
    "0100000000000000" "4c"
    "01" "0000000000000000"
    "01" "0900000000000000"
    "0000000000000000"
    "0100000000000000" "4c"
    "0900000000000000" "5f5f636c6f73655f5f"
    "0a00000000000000" "5f5f636c6f73655f5f4c"
    "1600000000000000" "5f5f696e7472616461795f6361705f636c6f73655f5f"
    "1100000000000000" "5f5f696e7472616461795f6c6f73735f5f"
    "0f00000000000000" "5f5f6d617267696e5f63616c6c5f5f"
    "1300000000000000" "5f5f6d617267696e5f7072656f70656e5f5f4c"
    "1000000000000000" "5f5f70696e655f636c6f73655f616c6c"
    "01" "0100000000000000"
    "0100000000000000" "4c"
    r'\",\"classification\":\"in_subset\",\"reasons\":[],\"version\":1},'
)


def reference_with_literal_entry_scratch_facts(cpp: str) -> str:
    """Add only the fixed receipt field to historical expected C++, in place.

    Keep its reference commit, every other byte and the full-string assertion.
    The actual C++ is never passed here or normalized.
    """
    old = r'\"process_orders_on_close\":false,\"settings\":'
    new = (r'\"process_orders_on_close\":false,'
           + _LITERAL_ENTRY_FIELD + r'\"settings\":')
    assert cpp.count(old) == 1, "expected one pre-scratch order-shapes receipt"
    return cpp.replace(old, new, 1)
