"""Pin the confirmed-bar receipt of a request made inside the function that declares
a local spelled like a built-in variable.

The receipt renders each request payload from the analyzed syntax tree, where such a
local already carries its private spelling. Each row reads the whole receipt document
back out of the emitted C++ and compares it with the document of the same script written
with a neutral local name: the two documents must be equal except for the expression
text of the request, and the private spelling may occur only in that text. These checks
do not run the emitted code.
"""

import copy
import json

import pytest

from tests.test_builtin_named_locals_scope import _LOCAL_NAMES, _program
from tests.test_strategy_capabilities import emitted_confirmed_receipt


# The fields of a receipt request entry besides its expression.
_ENTRY_FIELDS = {
    "function", "symbol", "timeframe", "lookahead", "gaps", "heikinashi", "feed",
}

# Each payload reads the local with history; {name} is the local's spelling.
_PAYLOADS = [
    pytest.param("nz({name}[1])", id="nz-history"),
    pytest.param("{name}[1]", id="plain-history"),
]


def _receipt(local, payload):
    source = _program(
        f"f() =>\n    {local} = close\n"
        f'    request.security(syminfo.tickerid, "60", {payload.format(name=local)})\n',
        "plot(f())\n",
    )
    return emitted_confirmed_receipt(source)


def _without_expressions(receipt):
    """A deep copy of the receipt document without the request expressions."""
    stripped = copy.deepcopy(receipt)
    for entry in stripped["requests"]:
        del entry["expression"]
    return stripped


# The confirmed-bar receipt carries the private spelling of a function local that is
# spelled like a built-in variable. This is pinned on purpose: restoring the authored
# spelling is a contract decision that needs its own change.
@pytest.mark.parametrize("payload", _PAYLOADS)
@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_confirmed_bar_receipt_keeps_private_spelling_of_builtin_named_local(name, payload):
    control = _receipt("aa", payload)
    subject = _receipt(name, payload)
    assert len(control["requests"]) == len(subject["requests"]) == 1, (control, subject)
    control_entry, subject_entry = control["requests"][0], subject["requests"][0]
    # One declaration in the script: the allocator's counter is at its first value.
    assert control_entry["expression"] == payload.format(name="aa")
    assert subject_entry["expression"] == payload.format(name=f"{name}__pfhist1")
    assert _ENTRY_FIELDS <= set(control_entry)
    # Everything else the document holds is the control's, and the private spelling
    # occurs nowhere but in the expression text. The originals are left untouched.
    control_rest = _without_expressions(control)
    subject_rest = _without_expressions(subject)
    assert subject_rest == control_rest
    subject_text = json.dumps(subject_rest, sort_keys=True)
    control_text = json.dumps(control, sort_keys=True)
    assert "__pfhist" not in subject_text, subject_text
    assert "__pfhist" not in control_text, control_text
