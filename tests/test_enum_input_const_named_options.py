"""Enum aliases retain the checked descriptor's choices and ordinal values."""

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.checked_settings import emit_settings_members
from pineforge_codegen.codegen.input import InputHelper
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


DIRECT_SOURCE = '''//@version=6
strategy("enum input with direct member options", default_qty_type=strategy.fixed, default_qty_value=1,
         initial_capital=10000, currency=currency.NONE)
enum Mode
    fast = "Fast"
    slow = "Slow"
    off = "Off"
mode = input.enum(Mode.fast, "Mode", options = [Mode.fast, Mode.slow])
step = mode == Mode.fast ? 10 : 20
if bar_index % step == 3
    strategy.entry("L", strategy.long)
if bar_index % step == 8
    strategy.close("L")
'''

NAMED_SOURCE = '''//@version=6
strategy("enum input with const-named member options", default_qty_type=strategy.fixed, default_qty_value=1,
         initial_capital=10000, currency=currency.NONE)
enum Mode
    fast = "Fast"
    slow = "Slow"
    off = "Off"
const Mode OPT_FAST = Mode.fast
const Mode OPT_SLOW = Mode.slow
mode = input.enum(OPT_FAST, "Mode", options = [OPT_FAST, OPT_SLOW])
step = mode == Mode.fast ? 10 : 20
if bar_index % step == 3
    strategy.entry("L", strategy.long)
if bar_index % step == 8
    strategy.close("L")
'''

# Derived from the base emit_settings_members enum branch and row serializer,
# not from executing the transpiler or recording a generated snapshot.
DIRECT_DESCRIPTOR = (
    '{"Mode", "enum", ::pineforge::checked_settings::number(Mode_fast), '
    '{"Mode.fast", "Mode.slow"}, '
    'std::numeric_limits<double>::quiet_NaN(), '
    'std::numeric_limits<double>::quiet_NaN(), '
    'std::numeric_limits<double>::quiet_NaN(), 32, true, {"0", "1"}, "enum"},'
)
UNSUPPORTED_DESCRIPTOR = DIRECT_DESCRIPTOR.replace(
    '{"Mode.fast", "Mode.slow"}', '{}'
).replace('32, true, {"0", "1"}', '32, false, {}')

DIRECT_CALL = 'input.enum(Mode.fast, "Mode", options = [Mode.fast, Mode.slow])'
NAMED_CALL = 'input.enum(OPT_FAST, "Mode", options = [OPT_FAST, OPT_SLOW])'


def _descriptor(cpp):
    # Same descriptor-row harness as test_checked_settings.
    return next(line.strip() for line in cpp.splitlines() if '{"Mode", "enum"' in line)


def _without_option_folding(monkeypatch, source):
    # For direct defaults, passing options through unchanged reproduces the
    # base path. raising=False lets this control also run on the base, which
    # has no shared member helper. Compare every byte, including scaffolding.
    with monkeypatch.context() as patch:
        patch.setattr(InputHelper, "_fold_enum_input_member",
                      lambda self, node, member: member, raising=False)
        return transpile(source)


def _assert_supported(full, options=None):
    expected = ["Mode.fast", "Mode.slow"] if options is None else options
    assert full["inputs"] == [{
        "title": "Mode", "type": "enum", "default": "Mode.fast",
        "options": expected, "supported": True,
    }]
    assert 'get_input_int("Mode", Mode_fast)' in full["cpp"]


def test_const_named_probe_preserves_descriptor_and_execution():
    direct = transpile_full(DIRECT_SOURCE)
    named = transpile_full(NAMED_SOURCE)
    _assert_supported(named)
    assert named["inputs"] == direct["inputs"]
    assert _descriptor(named["cpp"]) == _descriptor(direct["cpp"]) == DIRECT_DESCRIPTOR
    # Compare ALL emitted lines with a direct-member control that retains the
    # named probe's authored title and two declarations. This isolates the
    # call change without masking declaration/checkpoint or title differences.
    control = NAMED_SOURCE.replace(NAMED_CALL, DIRECT_CALL)
    assert named["cpp"] == transpile(control)
    # The getter lines are identical even against the verbatim direct probe.
    getter = 'get_input_int("Mode", Mode_fast)'
    assert [line for line in named["cpp"].splitlines() if getter in line] == [
        line for line in direct["cpp"].splitlines() if getter in line
    ]


def test_direct_members_keep_base_descriptor_and_complete_cpp(monkeypatch):
    full = transpile_full(DIRECT_SOURCE)
    _assert_supported(full)
    assert _descriptor(full["cpp"]) == DIRECT_DESCRIPTOR
    assert full["cpp"] == _without_option_folding(monkeypatch, DIRECT_SOURCE)


def test_named_default_with_direct_options_remains_supported():
    # This default-only form already works on the base: a compatibility control.
    source = NAMED_SOURCE.replace(
        'options = [OPT_FAST, OPT_SLOW]', 'options = [Mode.fast, Mode.slow]')
    full = transpile_full(source)
    _assert_supported(full)
    assert _descriptor(full["cpp"]) == DIRECT_DESCRIPTOR


def test_direct_default_with_named_options_is_supported():
    source = NAMED_SOURCE.replace(
        'input.enum(OPT_FAST,', 'input.enum(Mode.fast,')
    full = transpile_full(source)
    _assert_supported(full)
    assert _descriptor(full["cpp"]) == DIRECT_DESCRIPTOR


def test_option_alias_chains_preserve_written_order_and_ordinals():
    source = NAMED_SOURCE.replace(
        'mode = input.enum', 'const Mode FIRST = OPT_FAST\nconst Mode SECOND = OPT_SLOW\n'
        'mode = input.enum').replace(
            'options = [OPT_FAST, OPT_SLOW]', 'options = [SECOND, FIRST]')
    full = transpile_full(source)
    _assert_supported(full, ["Mode.slow", "Mode.fast"])
    assert _descriptor(full["cpp"]) == DIRECT_DESCRIPTOR.replace(
        '{"Mode.fast", "Mode.slow"}', '{"Mode.slow", "Mode.fast"}'
    ).replace('{"0", "1"}', '{"1", "0"}')


@pytest.mark.parametrize("declarations,option,tail", [
    ('var Mode CANDIDATE = Mode.slow\n', 'CANDIDATE', ''),
    ('varip Mode CANDIDATE = Mode.slow\n', 'CANDIDATE', ''),
    ('Mode CANDIDATE = Mode.slow\nCANDIDATE := Mode.fast\n', 'CANDIDATE', ''),
    ('Mode CANDIDATE = Mode.slow\n', 'CANDIDATE', 'CANDIDATE := Mode.fast\n'),
    ('enum Other\n    slow\n', 'Other.slow', ''),
    ('enum Other\n    slow\nconst Other CANDIDATE = Other.slow\n', 'CANDIDATE', ''),
    ('const int CANDIDATE = 1\n', 'CANDIDATE', ''),
    ('var Mode MUTABLE = Mode.slow\nconst Mode CANDIDATE = MUTABLE\n', 'CANDIDATE', ''),
], ids=["var", "varip", "reassigned-before", "reassigned-after", "mixed-enums",
        "mixed-enums-alias", "non-enum", "alias-of-var"])
def test_unsupported_options_keep_base_descriptor_and_cpp(
        monkeypatch, declarations, option, tail):
    source = DIRECT_SOURCE.replace('mode = input.enum', declarations + 'mode = input.enum')
    source = source.replace('options = [Mode.fast, Mode.slow]',
                            f'options = [Mode.fast, {option}]') + tail
    full = transpile_full(source)
    assert full["inputs"][0]["options"] == []
    assert full["inputs"][0]["supported"] is False
    assert _descriptor(full["cpp"]) == UNSUPPORTED_DESCRIPTOR
    assert full["cpp"] == _without_option_folding(monkeypatch, source)


def test_later_option_declaration_stays_unavailable_and_descriptor_unsupported():
    declaration = 'const Mode LATER = Mode.slow\n'
    source = DIRECT_SOURCE.replace('options = [Mode.fast, Mode.slow]',
                                   'options = [Mode.fast, LATER]')
    with pytest.raises(CompileError) as caught:
        transpile_full(source + declaration)
    assert any(diagnostic.code == "PF-E2075" for diagnostic in caught.value.diagnostics)

    # Analysis rejects the authored forward read before a descriptor exists.
    # Independently exercise the emitter's ordering guard: analyze a legal
    # declaration first, then move that AST declaration past the input call.
    legal_source = source.replace('mode = input.enum', declaration + 'mode = input.enum')
    program = Parser(Lexer(legal_source).tokenize(), source=legal_source).parse()
    emitter = CodeGen(Analyzer(program).analyze())
    emitter.generate()
    body = emitter.ctx.ast.body
    later = next(statement for statement in body
                 if isinstance(statement, VarDecl) and statement.name == "LATER")
    body.remove(later)
    body.append(later)
    lines = []
    emit_settings_members(emitter, lines, [])
    assert _descriptor("\n".join(lines)) == UNSUPPORTED_DESCRIPTOR
    metadata = next(iter(emitter._input_settings_metadata.values()))
    assert metadata == {"options": [], "supported": False}
