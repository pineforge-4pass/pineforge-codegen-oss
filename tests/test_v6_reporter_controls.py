"""Previously accepted control forms keep their native compilation contract."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests.test_i372_form_1 import BUILTIN_CONTROL, BUILTIN_SCRIPT, LOCAL_SCRIPT
from tests.test_input_string_var_default import (
    NONDEFAULT_BRANCH, NONDEFAULT_CONTROL, OPTIONS_CONTROL, VARIABLE_OPTIONS,
)
from tests.test_v6_declaration_request_compilation import COLOR_DECLARATION, UNUSED_FORWARDER


def test_conditional_history_call_control_keeps_both_written_sites():
    source = LOCAL_SCRIPT.replace(
        "wrapper(x) =>\n    s = x\n    change(s)\nv = wrapper(close)",
        "v = close > open ? change(close) : change(open)",
        1,
    )
    compile_cpp(transpile(source, check_support=True), label="conditional-history-call-control")


@pytest.mark.parametrize("source", [BUILTIN_SCRIPT, BUILTIN_CONTROL])
def test_builtin_derived_history_control_has_native_types(source):
    compile_cpp(transpile(source, check_support=True), label="builtin-derived-history-control")


@pytest.mark.parametrize(
    "source",
    [NONDEFAULT_BRANCH, NONDEFAULT_CONTROL, VARIABLE_OPTIONS, OPTIONS_CONTROL],
    ids=["default-var", "default-control", "options-var", "options-control"],
)
def test_reported_string_inputs_keep_native_compilation(source):
    compile_cpp(transpile(source, check_support=True), label="string-input-var-control")


def test_persistent_declaration_without_qualifier_keeps_its_storage():
    source = COLOR_DECLARATION.replace("\nvar const color ", "\nvar color ", 1)
    compile_cpp(transpile(source, check_support=True), label="persistent-color-control")


def test_uncalled_direct_ta_length_control_keeps_compiling():
    source = UNUSED_FORWARDER.replace(
        "smoothTwice(src, len) => smooth(smooth(src, len), len)",
        "unused(src, len) => ta.rma(src, len) * 2",
        1,
    )
    compile_cpp(transpile(source, check_support=True), label="unused-direct-length-control")
