"""Generated names and fallback comments protect their own text boundaries.

Name cases use the public transpiler with identifier token substitutions.
Fallback comment units call the emitter with names outside Pine's grammar.
Controls repeat the same assertion body with one protection weakened.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import inspect
import re
import textwrap

import pytest

from pineforge_codegen import transpile
from pineforge_codegen import library_inline
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import FuncCall, Identifier, MemberAccess
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen import emit_top, types
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.codegen.security import SecurityEmitter
from pineforge_codegen.codegen.tables import TIME_FIELD_EXPRS
from pineforge_codegen.codegen.visit_call import CallVisitor
from pineforge_codegen.codegen.visit_expr import ExprVisitor
from pineforge_codegen.cpp_allowlist import check_generated_cpp
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer, TokenType
from pineforge_codegen.library_modules import LibraryModule
from pineforge_codegen.parser import Parser

from tests._cpp_tokens import code_only, is_allowlist_refusal, regions


PRELUDE = '//@version=6\nstrategy("Text channels")\n'
TRADE = 'if close > open\n    strategy.entry("Long", strategy.long)\n'
TOKENIZE = Lexer.tokenize
SAFE_NAME = NamingHelper._safe_name
SECURITY_MATH = SecurityEmitter._build_security_math_call


def _assert_clean(cpp: str, name: str) -> None:
    assert not [region for region in regions(cpp) if region[0] == "broken"], (
        f"{name}: emitted a broken literal"
    )
    findings = check_generated_cpp(cpp)
    assert findings == [], f"{name}: generated C++ has allowlist findings: {findings}"


def _paired_control(monkeypatch, owner, method, weakened, case_body, name):
    case_body()
    with monkeypatch.context() as patch:
        patch.setattr(owner, method, weakened)
        # Only the named output assertion can kill a control; exceptions and
        # unrelated internal assertions must fail the test. The one exception
        # is the translation pipeline refusing the weakened output for
        # allowlist findings (every diagnostic one of the allowlist's fixed
        # reasons): the same defence firing earlier.
        try:
            case_body()
        except CompileError as error:
            assert is_allowlist_refusal(error), f"{name}: unrelated compile error {error}"
            return
        except AssertionError as error:
            assert re.search(re.escape(name) + r": (emitted|generated C\+\+)", str(error)), (
                f"{name}: unrelated assertion {error}"
            )
            return
        pytest.fail(f"{name}: the weakened control was not killed")


def _substitute_token(monkeypatch, authored: str) -> None:
    """Supply an identifier spelling after lexing, with the rest of the
    public parser/support/analyzer/codegen path left active."""
    def tokenize(self):
        tokens = TOKENIZE(self)
        for token in tokens:
            if token.type == TokenType.IDENT and token.value == "Boundary":
                token.value = authored
        return tokens

    monkeypatch.setattr(Lexer, "tokenize", tokenize)


def _escape_standalone_bindings(monkeypatch) -> None:
    """Isolate backing names from the independent standalone-name boundary.

    UDT handles also emit the authored type as a complete identifier. Supply
    that separate protection generically for bound names only; generated
    composites remain untouched, so their own missing escape stays visible.
    This uses ASCII replacement rather than the fragment escaper under test.
    """
    def safe_name(self, name):
        safe = SAFE_NAME(self, name)
        if name in self._safe_name_bound:
            return safe.encode("ascii", "backslashreplace").decode("ascii").replace(
                "\\", "_"
            )
        return safe

    monkeypatch.setattr(NamingHelper, "_safe_name", safe_name)


def test_enum_string_lookup_uses_allocated_table_name(monkeypatch):
    name = "enum_lookup"

    def case_body():
        cpp = transpile(
            PRELUDE + 'enum class\n    member = "kept"\n'
            'choice = input.enum(class.member, "Choice")\n'
            'text = str.tostring(choice)\n' + TRADE
        )
        # The legacy class escape is _class_; appending the table suffix adds a second underscore.
        assert "pine_enum_str_at(_class__str_values, 1, choice)" in code_only(cpp), (
            f"{name}: emitted lookup does not use the allocated enum table"
        )
        assert "static const std::string _class__str_values[]" in cpp, (
            f"{name}: emitted enum table is missing"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, NamingHelper, "_safe_name", lambda self, value: value,
        case_body, name,
    )


@dataclass(frozen=True)
class NameChannel:
    name: str
    body: str
    authored: str
    expected: str
    owner: object
    standalone: bool = False
    library: str | None = None
    namespace_from_title: bool = False


NAME_CHANNELS = (
    # U+00E9 is one six-digit code point escape in the backing type component.
    NameChannel(
        "record_backing",
        "type Boundary\n    float value\nrecord = Boundary.new(close)\n",
        "Récord", "_PFUdtRecord_R_u0000e9_cord", types, standalone=True,
    ),
    # The receiver's U+00E9 is encoded inside the complete typed method name.
    NameChannel(
        "method_receiver",
        "type Boundary\n    float value\n"
        "method read(Boundary self) => self.value\n"
        "record = Boundary.new(close)\nvalue = record.read()\n",
        "Récord", "_udt_R_u0000e9_cord_read", emit_top, standalone=True,
    ),
    # Both block delimiters become four independent code point escapes.
    NameChannel(
        "method_name",
        "method Boundary(float self) => self + 1.0\nvalue = close.Boundary()\n",
        "Edge*/Guard/*",
        "_udt_float_Edge_u00002a__u00002f_Guard_u00002f__u00002a_",
        emit_top,
    ),
    # A title supplied at the namespace boundary has its leading digit and delimiters encoded.
    NameChannel(
        "library_namespace",
        "import texttest/TextModule/1 as TextModule\nvalue = TextModule.message(close)\n",
        '7Edge*/Guard/*',
        "_u000037_Edge_u00002a__u00002f_Guard_u00002f__u00002a__v1__message",
        library_inline,
        library='export message(float value) => value + 1.0\n',
        namespace_from_title=True,
    ),
    # Definition and local prefixes each retain the complete encoded function component.
    NameChannel(
        "library_definition",
        "import texttest/TextModule/1 as TextModule\nvalue = TextModule.Boundary(close)\n",
        "Edge*/Guard/*",
        "TextModule_v1__Edge_u00002a__u00002f_Guard_u00002f__u00002a_",
        library_inline,
        library="export Boundary(float value) => value + 1.0\n",
    ),
    # The function-scope separator precedes the parameter's encoded component.
    NameChannel(
        "library_parameter",
        "import texttest/TextModule/1 as TextModule\nvalue = TextModule.message(close)\n",
        "Edge*/Guard/*",
        "TextModule_v1__message__Edge_u00002a__u00002f_Guard_u00002f__u00002a_",
        library_inline,
        library="export message(float Boundary) => Boundary + 1.0\n",
    ),
    # A declaration and its read keep the same encoded local binding.
    NameChannel(
        "library_local",
        "import texttest/TextModule/1 as TextModule\nvalue = TextModule.message(close)\n",
        "Edge*/Guard/*",
        "TextModule_v1__message__Edge_u00002a__u00002f_Guard_u00002f__u00002a_",
        library_inline,
        library="export message(float value) =>\n    Boundary = value + 1.0\n    Boundary\n",
    ),
)


@pytest.mark.parametrize("row", NAME_CHANNELS, ids=lambda row: row.name)
def test_name_channel_inertness(monkeypatch, row):
    if row.namespace_from_title:
        # The module title supplies arbitrary authored bytes at this boundary,
        # while the resolver still uses its ordinary validated import path.
        monkeypatch.setattr(LibraryModule, "name", property(lambda module: module.title))
    else:
        _substitute_token(monkeypatch, row.authored)
    if row.standalone:
        _escape_standalone_bindings(monkeypatch)

    def case_body():
        libraries = None
        if row.library is not None:
            title = row.authored if row.namespace_from_title else "TextModule"
            libraries = {
                "texttest/TextModule/1": '//@version=6\nlibrary("' + title + '")\n'
                + row.library,
            }
        cpp = transpile(PRELUDE + row.body + TRADE, libraries=libraries)
        code = code_only(cpp)
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)
        assert row.expected in tokens, (
            f"{row.name}: emitted protected name is missing or merged"
        )
        if row.name.startswith("method_") or row.name in {
            "library_namespace", "library_definition",
        }:
            # Each stateless callable has one definition and one evaluated call.
            assert len(re.findall(r"\b" + re.escape(row.expected) + r"\s*\(", code)) == 2, (
                f"{row.name}: emitted definition and call do not share one name"
            )
        if row.name in {"library_parameter", "library_local"}:
            # The parameter/local declaration and its read are the only two occurrences.
            assert tokens.count(row.expected) == 2, (
                f"{row.name}: emitted declaration and read do not share one name"
            )
        _assert_clean(cpp, row.name)

    _paired_control(
        monkeypatch, row.owner, "cpp_identifier_fragment", lambda value: value,
        case_body, row.name,
    )


@pytest.mark.parametrize("reverse", (False, True), ids=("escaped_first", "ascii_first"))
def test_method_encoding_preserves_distinct_ascii_method(monkeypatch, reverse):
    name = "method_collision"
    _substitute_token(monkeypatch, "réad")
    methods = (
        "method Boundary(float self) => self + 1.0\n",
        "method r_u0000e9_ad(float self) => self + 2.0\n",
    )
    source = PRELUDE + "".join(reversed(methods) if reverse else methods)
    source += "value = close.Boundary() + close.r_u0000e9_ad()\n" + TRADE

    def case_body():
        cpp = transpile(source)
        code = code_only(cpp)
        # The authored ASCII method keeps its spelling; the encoded collision takes suffix 2.
        assert len(re.findall(r"\b_udt_float_r_u0000e9_ad__pf2\s*\(", code)) == 2, (
            f"{name}: emitted encoded method captured the ASCII method"
        )
        assert len(re.findall(r"\b_udt_float_r_u0000e9_ad\s*\(", code)) == 2, (
            f"{name}: emitted ASCII method changed spelling"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, emit_top, "cpp_identifier_fragment", lambda value: value,
        case_body, name,
    )


def test_library_encoding_preserves_authored_ascii_binding(monkeypatch):
    name = "library_collision"
    _substitute_token(monkeypatch, "réad")
    source = (
        PRELUDE + "import texttest/TextModule/1 as TextModule\n"
        "TextModule_v1__r_u0000e9_ad = open\n"
        "value = TextModule.Boundary(close) + TextModule_v1__r_u0000e9_ad\n" + TRADE
    )
    libraries = {
        "texttest/TextModule/1": '//@version=6\nlibrary("TextModule")\n'
        "export Boundary(float value) => value + 1.0\n",
    }

    def case_body():
        cpp = transpile(source, libraries=libraries)
        code = code_only(cpp)
        # The importing script occupies the first namespace prefix, so prefix number 2 is used.
        assert len(re.findall(r"\bTextModule_v1_2__r_u0000e9_ad\s*\(", code)) == 2, (
            f"{name}: emitted library function captured an authored binding"
        )
        assert "TextModule_v1__r_u0000e9_ad" in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", code,
        ), f"{name}: emitted authored binding changed spelling"
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, library_inline, "cpp_identifier_fragment", lambda value: value,
        case_body, name,
    )


def test_library_encoding_keeps_two_definition_bindings(monkeypatch):
    name = "library_definition_collision"
    _substitute_token(monkeypatch, "réad")
    source = (
        PRELUDE + "import texttest/TextModule/1 as TextModule\n"
        "value = TextModule.Boundary(close) + TextModule.r_u0000e9_ad(close)\n" + TRADE
    )
    libraries = {
        "texttest/TextModule/1": '//@version=6\nlibrary("TextModule")\n'
        "export r_u0000e9_ad(float value) => value + 2.0\n"
        "export Boundary(float value) => value + 1.0\n",
    }

    def case_body():
        cpp = transpile(source, libraries=libraries)
        code = code_only(cpp)
        # The first definition owns the encoded spelling; the second allocation takes _2.
        assert len(re.findall(r"\bTextModule_v1__r_u0000e9_ad_2\s*\(", code)) == 2, (
            f"{name}: emitted encoded definition captured the first definition"
        )
        assert len(re.findall(r"\bTextModule_v1__r_u0000e9_ad\s*\(", code)) == 2, (
            f"{name}: emitted first definition changed spelling"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, library_inline, "cpp_identifier_fragment", lambda value: value,
        case_body, name,
    )


def test_record_encoding_preserves_authored_support_name(monkeypatch):
    name = "record_collision"
    _substitute_token(monkeypatch, "Récord")
    _escape_standalone_bindings(monkeypatch)

    def case_body():
        cpp = transpile(
            PRELUDE + "_PFUdtRecord_R_u0000e9_cord = open\n"
            "type Boundary\n    float value\nrecord = Boundary.new(close)\n" + TRADE
        )
        code = code_only(cpp)
        # The authored member occupies the preferred support name, so the record takes suffix 2.
        assert "_PFUdtRecord_R_u0000e9_cord__pf2" in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", code,
        ), f"{name}: emitted backing record captured the authored member"
        assert "_PFUdtRecord_R_u0000e9_cord" in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", code,
        ), f"{name}: emitted authored support-shaped member changed spelling"
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, types, "cpp_identifier_fragment", lambda value: value,
        case_body, name,
    )


@dataclass(frozen=True)
class CommentChannel:
    name: str
    namespace: str
    expected: str
    security: bool = False


COMMENT_CHANNELS = (
    # Direct delimiters are split; the backslash and LF become three backslashes and n.
    CommentChannel(
        "ta_comment", "ta",
        r"na<double>() /* unsupported: ta.Edge* /Guard/ *\\\n/Guard/ * */",
    ),
    # The symbol-information fallback retains the same single-line escaped name.
    CommentChannel(
        "symbol_comment", "syminfo",
        r"na<double>() /* unsupported: syminfo.Edge* /Guard/ *\\\n/Guard/ * */",
    ),
    # The string fallback returns an empty string before its single escaped comment.
    CommentChannel(
        "string_comment", "str",
        r'std::string("") /* unsupported: str.Edge* /Guard/ *\\\n/Guard/ * */',
    ),
    # Chart-context math retains its zero result and one complete block comment.
    CommentChannel(
        "math_comment", "math",
        r"0.0 /* unsupported: math.Edge* /Guard/ *\\\n/Guard/ * */",
    ),
    # Requested-context math has its own fallback emitter and the same inert comment.
    CommentChannel(
        "security_math_comment", "math",
        r"0.0 /* unsupported: math.Edge* /Guard/ *\\\n/Guard/ * */", security=True,
    ),
)


@pytest.mark.parametrize("row", COMMENT_CHANNELS, ids=lambda row: row.name)
@pytest.mark.parametrize(
    "method", ("_cpp_comment_escape", "_cpp_string_escape"),
    ids=("comment_escape", "string_escape"),
)
def test_fallback_comment_interpolation_unit(monkeypatch, row, method):
    # Delimiters and line breaks cannot be part of a Pine function identifier.
    source = PRELUDE + TRADE
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    emitter = CodeGen(Analyzer(program).analyze())
    authored = "Edge*/Guard/*\\\n/Guard/*"
    node = FuncCall(callee=MemberAccess(
        object=Identifier(name=row.namespace), member=authored,
    ))

    def case_body():
        if row.security:
            cpp = emitter._build_security_math_call(
                0, authored, node, range(0), {}, set(), set(), (), None,
            )
        elif row.namespace == "str":
            cpp = emitter._visit_str_call(authored, node)
        elif row.namespace == "math":
            cpp = emitter._visit_math_call(authored, node)
        else:
            cpp = emitter._visit_func_call(node)
        assert "\n" not in cpp and "\r" not in cpp, (
            f"{row.name}: emitted fallback comment contains a physical line break"
        )
        assert "Guard" not in code_only(cpp), (
            f"{row.name}: emitted function text left its comment"
        )
        assert cpp == row.expected, (
            f"{row.name}: emitted fallback expression changed or split"
        )
        _assert_clean(cpp, row.name)

    _paired_control(
        monkeypatch, NamingHelper, method, staticmethod(lambda value: value),
        case_body, row.name,
    )


@pytest.mark.parametrize(
    "method", ("_cpp_comment_escape", "_cpp_string_escape"),
    ids=("comment_escape", "string_escape"),
)
def test_security_math_fallback_comment_inertness(monkeypatch, method):
    name = "security_math_comment"

    def security_math(self, sec_id, func_name, node, *args, **kwargs):
        # Supply the requested evaluator's function name after registration.
        return SECURITY_MATH(self, sec_id, "Edge*/Guard/*\\\n/Guard/*", node, *args, **kwargs)

    monkeypatch.setattr(SecurityEmitter, "_build_security_math_call", security_math)

    def case_body():
        cpp = transpile(
            PRELUDE
            + 'value = request.security(syminfo.tickerid, "60", math.abs(close))\n'
            + TRADE
        )
        comments = [
            value for kind, _start, _end, value in regions(cpp)
            if kind == "comment" and value.startswith("/* unsupported:")
        ]
        # Escaping the backslash then LF gives three backslashes and n; delimiters split.
        assert r"/* unsupported: math.Edge* /Guard/ *\\\n/Guard/ * */" in comments, (
            f"{name}: emitted fallback comment is missing or split"
        )
        assert "Guard" not in code_only(cpp), (
            f"{name}: emitted function text left its comment"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, NamingHelper, method, staticmethod(lambda value: value),
        case_body, name,
    )


@pytest.mark.parametrize(
    "body,expected",
    (
        # A reserved word is safe inside the historical backing-record prefix.
        ("type class\n    float value\nrecord = class.new(close)\n",
         "_PFUdtRecord_class"),
        # The existing typed-method prefix makes this keyword component inert.
        ("method class(float self) => self + 1.0\nvalue = close.class()\n",
         "_udt_float_class"),
    ),
    ids=("record_ascii", "method_ascii"),
)
def test_ascii_composite_names_keep_their_spelling(body, expected):
    cpp = transpile(PRELUDE + body + TRADE)
    assert expected in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code_only(cpp))
    _assert_clean(cpp, "ascii_composite")


def test_ascii_library_namespace_and_locals_keep_their_spelling():
    cpp = transpile(
        PRELUDE + "import texttest/namespace/1 as TextModule\n"
        "value = TextModule.message(close)\n" + TRADE,
        libraries={
            "texttest/namespace/1": '//@version=6\nlibrary("TextModule")\n'
            "export message(float system) => system + 1.0\n",
        },
    )
    code = code_only(cpp)
    # Keyword and libc components remain inert within the existing qualified names.
    assert len(re.findall(r"\bnamespace_v1__message\s*\(", code)) == 2
    assert re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code).count(
        "namespace_v1__message__system"
    ) == 2
    _assert_clean(cpp, "ascii_library")


def _interpolation_guards(method, field: str, prefix: str, returns_only: bool = False):
    """Read the actual enclosing membership guards of a raw interpolation."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    guards = []

    def memberships(test):
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            return [item for value in test.values for item in memberships(value)]
        if (isinstance(test, ast.Compare) and ast.unparse(test.left) == field
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.In)):
            return [test.comparators[0]]
        return []

    def walk(node, stack=(), parent=None):
        if isinstance(node, ast.If):
            walk(node.test, stack, node)
            for child in node.body:
                walk(child, stack + (node.test,), node)
            for child in node.orelse:
                walk(child, stack, node)
            return
        if isinstance(node, ast.JoinedStr) and (
            not returns_only or isinstance(parent, ast.Return)
        ):
            for index, value in enumerate(node.values):
                if (isinstance(value, ast.FormattedValue)
                        and ast.unparse(value.value) == field
                        and index > 0
                        and isinstance(node.values[index - 1], ast.Constant)
                        and node.values[index - 1].value.endswith(prefix)):
                    choices = []
                    for test in stack:
                        for rhs in memberships(test):
                            if isinstance(rhs, ast.Name) and rhs.id == "TIME_FIELD_EXPRS":
                                choices.append(tuple(TIME_FIELD_EXPRS))
                            elif isinstance(rhs, (ast.Tuple, ast.Set)):
                                choices.append(tuple(ast.literal_eval(rhs)))
                    guards.append(choices)
        for child in ast.iter_child_nodes(node):
            walk(child, stack, node)

    walk(tree)
    return guards


@pytest.mark.parametrize(
    "method,field,prefix,returns_only,expected",
    (
        # The calendar table has exactly eight fixed keys, in declaration order.
        (CallVisitor._visit_func_call, "func_name", "pine_", False,
         [[("year", "month", "dayofmonth", "dayofweek", "hour", "minute", "second",
            "weekofyear")]]),
        # A color channel can select exactly r, g, b or t.
        (CallVisitor._visit_color_call, "func_name", "pine_color::", False,
         [[("r", "g", "b", "t")]]),
        # Session predicate interpolation occurs inside its three-member guard.
        (ExprVisitor._visit_member_access, "node.member", "pine_session_", False,
         [[("ismarket", "ispremarket", "ispostmarket")]]),
        # Identifier and expression receivers each expose the same three point fields.
        (ExprVisitor._visit_member_access, "node.member", ".", True,
         [[("time", "index", "price")], [("time", "index", "price")]]),
    ),
    ids=("calendar", "color", "session", "point"),
)
def test_raw_dispatch_interpolations_have_closed_membership(
    method, field, prefix, returns_only, expected,
):
    assert _interpolation_guards(method, field, prefix, returns_only) == expected
