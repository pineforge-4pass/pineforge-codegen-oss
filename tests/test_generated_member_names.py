"""Authored bindings cannot share the emitter's fixed state or helper names."""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.helpers import (
    CPP_RESERVED, cpp_code_only, is_emitter_temporary,
)
from pineforge_codegen.lexer import KEYWORDS, Lexer
from pineforge_codegen.parser import Parser
from tests._e2e import reference_codegen, transpile_json
from tests.test_helper_series_words import helper_source


CPP_REFERENCE = "7d7681dcef6918f0cb304831320e5d8e5b545285"
_ROOT = Path(__file__).resolve().parents[1]
_FIELD = "INTERPOLATED"
_TYPE = (
    r"(?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*"
    r"(?:<[^;\n{}]+>)?(?:\s*[*&]+)?"
)
_NON_DECLARATOR = (
    r"(?:break|case|catch|class|continue|default|do|else|for|goto|if|"
    r"return|struct|switch|throw|try|using|while)\b"
)
_DECLARATION = re.compile(
    r"(?m)^(?P<indent> {4})?"
    rf"(?!{_NON_DECLARATOR})"
    r"(?:(?:mutable|static|inline|constexpr|const|virtual)\s+)*"
    + _TYPE + rf"\s+(?!{_NON_DECLARATOR})"
    r"(?P<name>[A-Za-z_]\w*)\s*(?=[;={(])"
)
_TYPE_DECLARATION = re.compile(
    r"(?m)^(?P<indent> {4})?(?:template\s*<[^;\n]+>\s*)?"
    r"(?:class|struct|using)\s+(?!namespace\b)(?P<name>[A-Za-z_]\w*)"
    r"[ \t]*(?=[;{<=:]|$)"
)

# Support implementations and validation lambdas have separate C++ scopes.
# Their fields and locals cannot meet authored bindings; their outer types
# and free functions still join the census.
_SUPPORT_BODIES = {
    "_emit_ta_compat_shims",
    "_emit_handle_checkpoint_traits",
    "_emit_lazy_source_clock_helper",
    "_build_country_lookup_cpp",
    "checkpoint_traits",
    "_security_series_tf_value",
}

# Generated namespaces that explicitly check authored spellings. They are
# excluded by allocation, rather than widening the allocator's reserved set.
_ALLOCATED_FAMILIES = (
    (r"__pf_map_(?:iter|key)_\d+",
     "visit_stmt.py checks statement bindings and active parameters"),
    (r"__pf_array_(?:iter|index)_\d+",
     "visit_stmt.py checks statement bindings and active parameters"),
    (r"__pf_(?:binop_lhs|map_param_arg|map_receiver|call_arg)_\d+",
     "expression/call visitors check the complete operand text"),
    (r"_pf_array_new_\w+_arg(?:_\d+)?",
     "visit_call.py checks every authored spelling before staging arguments"),
    (r"_pf_w_*",
     "types.py checks identifiers in the complete coerced operand"),
    (r"_pf_selected_tf_*",
     "security.py checks authored timeframe bindings"),
    (r"_pf_risk_direction_value(?:__pf\d+)?",
     "visit_stmt.py checks authored names and the lowered direction expression"),
    (r"_session_call_\d+",
     "base.py advances this history counter past authored spellings"),
    (r"_pf_session_hist_\w+",
     "base.py allocates session histories against authored spellings"),
    (r"_pf_hist_bar_\w+",
     "base.py allocates history clocks against authored spellings"),
    (r"_PFLazySourceClock(?:_\d+)?|_pf_lazy_src_(?:clock|hist)_\d+(?:_\d+)?",
     "ta.py allocates against the complete reserved member inventory"),
    (r"_pf_var_init_\w+|_fvinit_\w+",
     "base.py allocates declaration and legacy latches against member names"),
    (r"_pf_outer_\w+",
     "visit_stmt.py checks bindings, parameters and earlier aliases"),
    (r"_PF(?:UdtArena|UdtUndoCoordinator|CheckpointTraits|UdtRecord_\w+)"
     r"(?:__pf\d+)?|_pf_udt_\w+",
     "base.py allocates UDT support against authored and generated names"),
    (r"_PFSliceArray(?:__pf\d+)?|_pf_slice_(?:view|copy)(?:__pf\d+)?",
     "array_slice.py allocates support names against authored names"),
    (r"_PF(?:ArrayJournal|CachedCollectionHistory|HistoryVersionClock)"
     r"(?:__pf\d+)?|_pf_(?:array_journal|history_versions|next_generation)\w*",
     "base.py and history_sharing.py allocate against authored names"),
)


def _allocated(name):
    return any(re.fullmatch(pattern, name) for pattern, _reason in _ALLOCATED_FAMILIES)


def _literal_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else _FIELD
            for part in node.values
        )
    return None


def _emitted_fixed_names():
    """Read declarations, retaining locations independently of reserved sets.

    Interpolated types still expose a fixed declarator, including the split
    string-store declaration. Interpolated identifiers are covered separately
    by the numbered-family rows. Four-space declarations are strategy members;
    nested support-class fields are deliberately outside that namespace.
    """
    names = {}
    paths = sorted((_ROOT / "pineforge_codegen" / "codegen").glob("*.py"))
    # The collection emitter imports these C++ declaration literals.
    paths.append(_ROOT / "pineforge_codegen" / "collection_history.py")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            id(child): parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
        }
        strategy_start = next((
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith("class GeneratedStrategy :")
        ), 0)
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            parent = parents.get(id(node))
            if isinstance(parent, ast.JoinedStr):
                continue
            text = _literal_text(node)
            if text is None:
                continue
            # Diagnostic arguments are prose, including nested string expressions.
            owner = parent
            while owner is not None and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(owner, ast.keyword) and owner.arg in {"hint", "message"}:
                    break
                if (isinstance(owner, ast.Call)
                        and getattr(owner.func, "attr", getattr(owner.func, "id", None))
                        in {"_codegen_error", "_codegen_error_diagnostic",
                            "_codegen_warning", "Diagnostic"}):
                    break
                owner = parents.get(id(owner))
            if isinstance(owner, (ast.keyword, ast.Call)):
                continue
            member_scope = (
                (owner is not None and owner.name not in _SUPPORT_BODIES)
                or (owner is None and "\n" not in text)
            )
            if (path.name == "base.py" and owner is not None
                    and owner.name == "_generate_cpp" and node.lineno < strategy_start):
                member_scope = False
            code = cpp_code_only(text)
            for declaration in (_DECLARATION, _TYPE_DECLARATION):
                for match in declaration.finditer(code):
                    if match["indent"] and not member_scope:
                        continue
                    name = match["name"]
                    if _FIELD in name or _allocated(name):
                        continue
                    names.setdefault(name, set()).add(f"{path.name}:{node.lineno}")
    return names


_FIXED_NAMES = _emitted_fixed_names()

# Representatives of the unallocated families, including nested requested
# state, tuple results, TA variants and the legacy inline-history kinds.
# Locations name their declaration or the naming expression it interpolates.
_NUMBERED_NAMES = {
    "_req_sec_0": "base.py:request result declarations",
    "_req_sec_12_3": "base.py:tuple request result declarations",
    "_req_sec_lower_tf_12": "base.py:lower-timeframe result declarations",
    "_precalc__ta_ema_1": "base.py:precalculated TA vector declarations",
    "_sec0_hist_close": "security.py:_security_ohlc_hist_series_cpp",
    "_sec12_expr_hist_3": "security.py:_emit_security_expr_hist_members",
    "_sec12_arg_hist_3": "security.py:_security_sampled_argument",
    "_sec12_value_initialized": "security.py:_security_init_flag_name",
    "_sec12_bar_index_": "security.py:_security_bar_index_member",
    "_sec12__ta_ema_1_v3_hist": "security.py:_security_ta_hist_series_cpp",
    "_pf_sec_missing_12": "base.py:foreign request missing-data flags",
    "_pf_rec_missing_12": "base.py:recorded request missing-data flags",
    "_eval_security_12": "security.py:_emit_security_evaluator",
    "_RuntimeSec12": "security.py:_emit_runtime_security_templates",
    "_runtime_feed_12": "security.py:_runtime_security_evaluator_cpp",
    "_runtime_request_12": "security.py:_runtime_security_evaluator_cpp",
    "_ta_ema_1_cs2__ni3": "base.py:TA member declarations and clones",
    "_prev_fixnan_1_cs2_u3": "base.py:fixnan member declarations and clones",
    "_hist_call_12": "base.py:_prepare_inline_history_members",
    "_series_arg_12": "base.py:_prepare_inline_history_members",
    "_udf_series_arg_12": "base.py:_prepare_inline_history_members",
    "_fn_global_hist_12": "base.py:_prepare_inline_history_members",
    "_s_close": "base.py:bar-field history declarations",
    "_strat_position_size": "base.py:strategy history declarations",
    "_pf_ctf12_settled": "base.py:closed-trade fold state declarations",
    "_pf_ctf12_s3": "base.py:closed-trade fold accumulator declarations",
    "_for_step_12": "visit_stmt.py:_visit_for",
    "_for_down_12": "visit_stmt.py:_visit_for",
    "_result__ta_macd_1": "visit_stmt.py:_visit_tuple_assign",
    "_pf_selection__pf_var_init_value": "visit_stmt.py:Series selection temporary",
}


def test_every_fixed_generated_member_or_helper_name_is_reserved():
    assert _FIXED_NAMES, "No generated declarations found in the emitter sources"
    declarations = {**_FIXED_NAMES, **_NUMBERED_NAMES}
    missing = {
        name: locations for name, locations in sorted(declarations.items())
        if name not in CPP_RESERVED and not is_emitter_temporary(name)
    }
    assert missing == {}


_ACTIVATE_MEMBERS = (
    "signal = ta.ema(close, 3)\n"
    "mark = line.new(bar_index, close, bar_index + 1, close)\n"
)


def _source(name, position, string):
    source = helper_source("held", "prior", string=string)
    source = source.replace("n = input.int(3)\n", _ACTIVATE_MEMBERS + "n = input.int(3)\n")
    if position == "helper-local":
        return source.replace("held", name)
    value = "str.tostring(close)" if string else "close"
    if position == "global-var":
        kind = "string" if string else "float"
        declaration = f"var {kind} {name} = {value}\n{name} := {value}\n"
        observed = f"str.length({name}[1])" if string else f"nz({name}[1])"
        return source.replace("h() =>", declaration + "h() =>") + f"plot({observed})\n"
    kind = "string" if string else "float"
    result = f'{name} + "x"' if string else f"{name} + 1.0"
    observed = "str.length(observed)" if string else "observed"
    return (
        source + f"echo({kind} {name}) => {result}\n"
        f"observed = echo({value})\nplot({observed})\n"
    )


def _spelling(source, name):
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(program).analyze())
    return generator._safe_name(name)


# Pine keywords cannot be authored bindings, even when they appear in emitted C++.
@pytest.mark.parametrize("name", sorted(
    (_FIXED_NAMES.keys() | _NUMBERED_NAMES.keys()) - KEYWORDS.keys()
))
@pytest.mark.parametrize("position", ["global-var", "helper-local", "parameter"])
@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
def test_generated_name_bindings_preserve_complete_cpp(name, position, string):
    source = _source(name, position, string)
    spelling = _spelling(source, name)
    cpp = transpile(source)
    control = transpile(_source("aa", position, string))
    assert re.search(rf"\b{re.escape(spelling)}\b", cpp), spelling
    # Include declarations, all uses, history keys and checkpoint/reset code.
    # Names derived from a binding (state flags, store keys) hold its spelling
    # inside a longer identifier, so the replacement is by text.
    assert cpp.replace(spelling, "aa") == control


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


@pytest.mark.parametrize("position", ["global-var", "helper-local", "parameter"])
@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
def test_ordinary_names_keep_reference_cpp(position, string, reference, tmp_path):
    source = _source("aa", position, string)
    source += (
        "first = close\nhist = first[1]\nvalue = nz(hist)\n"
        "result = value + 1.0\nplot(result)\n"
    )
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    expected = before["cpp"]
    if string:
        from tests.test_string_local_history_concat import with_string_join_fixed

        # The reference joined a string local with its previous value through
        # std::to_string (issue #354); that one site is the whole difference.
        expected = with_string_join_fixed(expected)
    assert transpile(source) == expected
