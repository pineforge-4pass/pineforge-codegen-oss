"""Recursive-descent parser for PineScript v6 tokens.

Rewritten parser (Tasks 5 & 6) that:
- Uses the new Lexer (pineforge_codegen.lexer) with TokenType enum and Token dataclass
- Produces AST nodes from pineforge_codegen.ast_nodes with ASTNode base class
- Sets SourceLocation (loc) on every node
- Handles all PineScript v6 constructs: expressions, declarations, control flow,
  function definitions, strategy/indicator declarations
- Implements proper operator precedence climbing
"""

from __future__ import annotations

import re

from .lexer import Token, TokenType
from .errors import SourceLocation
from .ast_nodes import (
    ASTNode,
    Program, StrategyDecl, ImportStmt,
    VarDecl, Assignment, TupleAssign,
    IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt, BreakStmt, ContinueStmt,
    FuncDef, ExprStmt,
    BinOp, UnaryOp, Ternary, FuncCall, Subscript,
    Identifier, MemberAccess, TypeAnnotation,
    NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral,
    TupleLiteral,
    TypeField, TypeDecl, EnumDecl, MethodDef,
)


class ParseError(Exception):
    pass


# Type annotation keywords
TYPE_KEYWORDS = {
    TokenType.TYPE_INT, TokenType.TYPE_FLOAT,
    TokenType.TYPE_BOOL, TokenType.TYPE_STRING,
}

# Compound assignment token types and their corresponding operator strings
COMPOUND_ASSIGN_OPS = {
    TokenType.COLON_EQUALS: ":=",
    TokenType.PLUS_EQUALS: "+=",
    TokenType.MINUS_EQUALS: "-=",
    TokenType.STAR_EQUALS: "*=",
    TokenType.SLASH_EQUALS: "/=",
    TokenType.PERCENT_EQUALS: "%=",
}


class Parser:
    def __init__(self, tokens: list[Token], *, source: str = "", filename: str = "<input>") -> None:
        self.tokens = tokens
        self.pos = 0
        self._source = source
        self._filename = filename

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.EOF_TOKEN, "", 0, 0)

    def _peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return Token(TokenType.EOF_TOKEN, "", 0, 0)

    def _at_end(self) -> bool:
        return self._current().type == TokenType.EOF_TOKEN

    def _check(self, tt: TokenType) -> bool:
        return self._current().type == tt

    def _match(self, *types: TokenType) -> Token | None:
        if self._current().type in types:
            return self._advance()
        return None

    def _advance(self) -> Token:
        tok = self._current()
        self.pos += 1
        return tok

    def _consume(self, tt: TokenType, msg: str = "") -> Token:
        if self._current().type == tt:
            return self._advance()
        cur = self._current()
        raise ParseError(
            f"Expected {tt.name} got {cur.type.name}({cur.value!r}) "
            f"L{cur.line}:{cur.col}. {msg}"
        )

    def _skip_newlines(self) -> None:
        while self._check(TokenType.NEWLINE):
            self._advance()

    def _skip_expr_continuation(self) -> None:
        """Skip NEWLINE tokens for expression continuation.

        After a binary operator, skip NEWLINE tokens so the expression
        parser sees the next operand. INDENT/DEDENT are NOT skipped here
        — they are handled by the lexer's line continuation logic.
        """
        while self._check(TokenType.NEWLINE):
            self._advance()

    def _loc(self, tok: Token) -> SourceLocation:
        """Build a SourceLocation from a token."""
        return SourceLocation(file=self._filename, line=tok.line, col=tok.col, end_col=tok.end_col)

    def _set_loc(self, node: ASTNode, tok: Token) -> ASTNode:
        """Set loc on a node from a token and return the node."""
        node.loc = self._loc(tok)
        return node

    # ------------------------------------------------------------------
    # Top-level
    # ------------------------------------------------------------------

    def parse(self) -> Program:
        """Parse the full program, returning a Program node."""
        version = self._extract_version()
        prog = Program(version=version)
        self._skip_newlines()

        while not self._at_end():
            try:
                stmt = self._parse_statement()
                if stmt is not None:
                    if isinstance(stmt, list):
                        prog.body.extend(stmt)
                    else:
                        prog.body.append(stmt)
            except ParseError:
                # Error recovery: skip to next newline and continue
                self._recover()
            self._skip_newlines()

        return prog

    def _extract_version(self) -> int | None:
        """Extract version number from //@version=N annotation in source."""
        if not self._source:
            return None
        m = re.search(r'//@version=(\d+)', self._source)
        if m:
            return int(m.group(1))
        return None

    def _recover(self) -> None:
        """Skip tokens until next NEWLINE or EOF for error recovery."""
        while not self._at_end() and not self._check(TokenType.NEWLINE):
            self._advance()
        if self._check(TokenType.NEWLINE):
            self._advance()

    # ------------------------------------------------------------------
    # Statement parsing
    # ------------------------------------------------------------------

    def _parse_statement(self):
        stmt = self._parse_single_statement()
        if not self._check(TokenType.COMMA):
            return stmt

        stmts: list = []
        self._extend_statement_list(stmts, stmt)
        while self._match(TokenType.COMMA):
            if self._check(TokenType.NEWLINE) or self._check(TokenType.DEDENT) or self._at_end():
                break
            self._extend_statement_list(stmts, self._parse_single_statement())
        return stmts

    @staticmethod
    def _extend_statement_list(stmts: list, stmt) -> None:
        if stmt is None:
            return
        if isinstance(stmt, list):
            stmts.extend(stmt)
        else:
            stmts.append(stmt)

    def _parse_single_statement(self):
        cur = self._current()

        # Control flow keywords
        if cur.type == TokenType.IF:
            return self._parse_if_stmt()
        if cur.type == TokenType.FOR:
            return self._parse_for_stmt()
        if cur.type == TokenType.WHILE:
            return self._parse_while_stmt()
        if cur.type == TokenType.SWITCH:
            return self._parse_switch_stmt()
        if cur.type == TokenType.BREAK:
            tok = self._advance()
            return self._set_loc(BreakStmt(), tok)
        if cur.type == TokenType.CONTINUE:
            tok = self._advance()
            return self._set_loc(ContinueStmt(), tok)

        # import statement
        if cur.type == TokenType.IMPORT:
            return self._parse_import_stmt()

        # var / varip declaration
        if cur.type in (TokenType.VAR, TokenType.VARIP):
            return self._parse_var_keyword_decl()

        # Type-annotated declaration: float x = ..., int x = ...
        if cur.type in TYPE_KEYWORDS and self._peek().type == TokenType.IDENT:
            # Check that the IDENT is followed by = (not == ) to confirm declaration
            if self._peek(2).type == TokenType.EQUALS:
                return self._parse_typed_decl()
        # Postfix-array type-annotated declaration: float[] x = ..., int[] x = ...
        if (cur.type in TYPE_KEYWORDS
                and self._peek().type == TokenType.LBRACKET
                and self._peek(2).type == TokenType.RBRACKET
                and self._peek(3).type == TokenType.IDENT
                and self._peek(4).type == TokenType.EQUALS):
            return self._parse_typed_decl()

        # IDENT-prefixed type-annotated declaration: ``Sample s = ...``,
        # ``array<Sample> arr = ...``, ``matrix<float> m = ...`` — when the
        # user does not prefix with ``var`` / ``varip``. Without this branch
        # the parser splits ``Sample s = ...`` into an orphan
        # ``ExprStmt(Sample)`` plus a bare ``s = ...`` VarDecl that drops the
        # UDT type annotation, so codegen ends up with ``double s = 0.0`` and
        # ``s.score()`` lowers to namespace dispatch on a primitive. Probe:
        # data/validation/udt-method-probe-19-array-of-udt-method.
        if cur.type == TokenType.IDENT and self._is_ident_typed_var_decl():
            return self._parse_typed_decl()

        # Tuple assignment: [a, b] = expr  vs  tuple literal: [a, b]
        if cur.type == TokenType.LBRACKET:
            # Look ahead past matching bracket to see if '=' follows
            if self._is_tuple_assign():
                return self._parse_tuple_assign()
            # Otherwise it's an expression statement (e.g., [a, b] as return value)
            return self._parse_expr_or_assign_stmt()

        # method declaration: method name(TypeName self, ...) =>
        if cur.type == TokenType.METHOD:
            return self._parse_method_def()

        if cur.type == TokenType.IDENT:
            # type/enum block declarations
            if cur.value in ("enum", "type") and self._peek().type == TokenType.IDENT:
                # Check if this is followed by a NEWLINE + INDENT block
                if self._peek(2).type == TokenType.NEWLINE:
                    return self._parse_type_or_enum_decl()

            # strategy() / indicator() declaration
            if cur.value in ("strategy", "indicator") and self._peek().type == TokenType.LPAREN:
                return self._parse_strategy_decl()

            # Check for function definition: name(params) =>
            if self._is_func_def():
                return self._parse_func_def()

            # Variable declaration: IDENT = expr (but not IDENT == expr)
            if self._peek().type == TokenType.EQUALS and self._peek(2).type != TokenType.EQUALS:
                return self._parse_var_decl()

            # Reassignment / compound assignment: IDENT :=  +=  -=  *=  /=  %=
            if self._peek().type in COMPOUND_ASSIGN_OPS:
                return self._parse_assignment()

            # Check for member.member := or member.member += etc.
            # We need to parse an expression first, then check if it's followed by an assignment op
            return self._parse_expr_or_assign_stmt()

        # Fallback: expression statement
        return self._parse_expr_or_assign_stmt()

    def _parse_expr_or_assign_stmt(self):
        """Parse an expression, then check if it's actually the target of an assignment."""
        start_tok = self._current()
        expr = self._parse_expression()

        # After parsing the expression, check if we have an assignment op
        if self._current().type in COMPOUND_ASSIGN_OPS:
            op = COMPOUND_ASSIGN_OPS[self._current().type]
            self._advance()
            value = self._parse_expression()
            node = Assignment(target=expr, op=op, value=value)
            return self._set_loc(node, start_tok)

        return self._set_loc(ExprStmt(expr=expr), start_tok)

    def _is_ident_typed_var_decl(self) -> bool:
        """Look ahead for ``IDENT [<...>] IDENT '='`` (UDT-typed declaration).

        Triggered by ``Sample s = expr`` / ``array<Sample> w = expr`` /
        ``matrix<float> m = expr`` at statement start. Excludes ``IDENT '='``
        (a plain assignment / VarDecl), ``IDENT '=='`` (comparison), keyword
        names (``enum``, ``type``, ``strategy``, ``indicator``, ``na``,
        ``true``, ``false``), and function-definition shapes
        (``IDENT '(' ... ')' '=>'``).

        Probe: data/validation/udt-method-probe-19-array-of-udt-method.
        """
        cur = self._current()
        if cur.type != TokenType.IDENT:
            return False
        if cur.value in ("enum", "type", "strategy", "indicator", "na", "true", "false"):
            return False
        # Skip past optional generic args after the type ident: IDENT [< ... >]
        i = self.pos + 1
        if i < len(self.tokens) and self.tokens[i].type == TokenType.LT:
            depth = 1
            i += 1
            while i < len(self.tokens) and depth > 0:
                tt = self.tokens[i].type
                if tt == TokenType.LT:
                    depth += 1
                elif tt == TokenType.GT:
                    depth -= 1
                elif tt in (TokenType.NEWLINE, TokenType.EOF_TOKEN):
                    return False
                i += 1
        # Now expect an IDENT (variable name).
        if i >= len(self.tokens) or self.tokens[i].type != TokenType.IDENT:
            return False
        # Followed by '=' (and not '==').
        if i + 1 >= len(self.tokens) or self.tokens[i + 1].type != TokenType.EQUALS:
            return False
        if i + 2 < len(self.tokens) and self.tokens[i + 2].type == TokenType.EQUALS:
            return False
        return True

    def _is_func_def(self) -> bool:
        """Look ahead to check if this is a function definition: name(args) =>"""
        if self._current().type != TokenType.IDENT or self._peek().type != TokenType.LPAREN:
            return False
        # Scan forward past matching parens to see if => follows
        depth = 0
        i = self.pos + 1
        while i < len(self.tokens):
            tt = self.tokens[i].type
            if tt == TokenType.LPAREN:
                depth += 1
            elif tt == TokenType.RPAREN:
                depth -= 1
                if depth == 0:
                    # Check if next non-newline token is =>
                    j = i + 1
                    while j < len(self.tokens) and self.tokens[j].type == TokenType.NEWLINE:
                        j += 1
                    return j < len(self.tokens) and self.tokens[j].type == TokenType.FAT_ARROW
            elif tt in (TokenType.EOF_TOKEN, TokenType.NEWLINE):
                if depth == 0:
                    return False
            i += 1
        return False

    # -- Declarations --

    def _parse_strategy_decl(self) -> StrategyDecl:
        start_tok = self._advance()  # consume 'strategy' or 'indicator'
        # Parse arguments as a function call, then convert to StrategyDecl
        self._consume(TokenType.LPAREN)
        args, kwargs = self._parse_call_args()
        self._consume(TokenType.RPAREN)
        node = StrategyDecl(args=args, kwargs=kwargs)
        node.annotations = {"decl_kind": start_tok.value}
        return self._set_loc(node, start_tok)

    def _parse_import_stmt(self) -> ImportStmt:
        """Parse: import path/to/library/version"""
        start_tok = self._current()
        self._consume(TokenType.IMPORT)
        # Consume the rest of the line as the import path
        parts: list[str] = []
        while (not self._at_end()
               and not self._check(TokenType.NEWLINE)
               and not self._check(TokenType.EOF_TOKEN)):
            parts.append(self._advance().value)
        path = "".join(parts)
        node = ImportStmt(path=path)
        return self._set_loc(node, start_tok)

    def _parse_var_decl(self) -> VarDecl | list:
        """Parse var declaration(s). Returns a single VarDecl or a list for comma-separated."""
        start_tok = self._current()
        name_tok = self._consume(TokenType.IDENT)
        self._consume(TokenType.EQUALS)
        value = self._parse_expression()
        first = VarDecl(name=name_tok.value, value=value)
        self._set_loc(first, start_tok)

        # Check for comma-separated additional declarations: x=1, y=2, z=3.
        # Other comma-separated simple statements (``a := 1, b := 2`` or
        # ``array.fill(a, na), array.set(a, 0, 1)``) are handled by the
        # statement wrapper above, so do not greedily consume their comma.
        if not (
            self._check(TokenType.COMMA)
            and self._peek().type == TokenType.IDENT
            and self._peek(2).type == TokenType.EQUALS
        ):
            return first

        decls = [first]
        while (
            self._check(TokenType.COMMA)
            and self._peek().type == TokenType.IDENT
            and self._peek(2).type == TokenType.EQUALS
        ):
            self._advance()
            st = self._current()
            n = self._consume(TokenType.IDENT)
            self._consume(TokenType.EQUALS)
            v = self._parse_expression()
            d = VarDecl(name=n.value, value=v)
            decls.append(self._set_loc(d, st))
        return decls

    def _parse_assignment(self) -> Assignment:
        start_tok = self._current()
        name_tok = self._consume(TokenType.IDENT)
        op_tok = self._advance()  # consume :=, +=, -=, etc.
        op = COMPOUND_ASSIGN_OPS[op_tok.type]
        value = self._parse_expression()
        target = Identifier(name=name_tok.value)
        self._set_loc(target, name_tok)
        node = Assignment(target=target, op=op, value=value)
        return self._set_loc(node, start_tok)

    def _parse_type_hint_string(self) -> str:
        """Parse primitive, UDT, array<T>, map<K,V>, or postfix-array (``T[]``) hints."""
        base = self._advance().value
        if self._check(TokenType.LT):
            parts: list[str] = []
            depth = 0
            self._advance()  # <
            while not self._at_end():
                tok = self._current()
                if tok.type == TokenType.LT:
                    depth += 1
                    parts.append("<")
                    self._advance()
                    continue
                if tok.type == TokenType.GT:
                    if depth == 0:
                        self._advance()
                        break
                    depth -= 1
                    parts.append(">")
                    self._advance()
                    continue
                if tok.type == TokenType.COMMA:
                    parts.append(",")
                else:
                    parts.append(str(tok.value))
                self._advance()
            base = f"{base}<{''.join(parts)}>"

        # Pine postfix-array shorthand: `float[]` == `array<float>`, `T[]` == `array<T>`.
        # Without this the trailing `[ ]` is left unconsumed, the following name
        # fails to parse, and the whole declaration is silently dropped.
        while self._check(TokenType.LBRACKET) and self._peek().type == TokenType.RBRACKET:
            self._advance()  # [
            self._advance()  # ]
            base = f"array<{base}>"
        return base

    def _parse_template_args(self) -> list[str]:
        """Parse and return generic args after a member name, e.g. new<K,V>()."""
        args: list[str] = []
        if not self._check(TokenType.LT):
            return args
        self._advance()  # <
        current: list[str] = []
        depth = 0
        while not self._at_end():
            tok = self._current()
            if tok.type == TokenType.LT:
                depth += 1
                current.append("<")
            elif tok.type == TokenType.GT:
                if depth == 0:
                    arg = "".join(current).strip()
                    if arg:
                        args.append(arg)
                    self._advance()
                    break
                depth -= 1
                current.append(">")
            elif tok.type == TokenType.COMMA and depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(str(tok.value))
            self._advance()
        return args

    def _looks_like_call_template_args(self) -> bool:
        """True when current '<' starts generic args immediately followed by '('."""
        if not self._check(TokenType.LT):
            return False
        depth = 0
        i = self.pos
        while i < len(self.tokens):
            tt = self.tokens[i].type
            if tt == TokenType.LT:
                depth += 1
            elif tt == TokenType.GT:
                depth -= 1
                if depth == 0:
                    return i + 1 < len(self.tokens) and self.tokens[i + 1].type == TokenType.LPAREN
            elif tt in (TokenType.NEWLINE, TokenType.EOF_TOKEN) and depth > 0:
                return False
            i += 1
        return False

    def _parse_var_keyword_decl(self) -> VarDecl:
        """Parse: var [type] name = expr  or  varip [type] name = expr"""
        start_tok = self._current()
        is_var = self._current().type == TokenType.VAR
        is_varip = self._current().type == TokenType.VARIP
        self._advance()

        type_hint = None
        if self._current().type in TYPE_KEYWORDS:
            type_hint = self._parse_type_hint_string()
        elif (self._current().type == TokenType.IDENT
              and self._peek().type in (TokenType.LT, TokenType.IDENT)
              and self._current().value not in ("na",)):
            # Complex type: array<float>, table, etc.
            type_hint = self._parse_type_hint_string()
        elif (self._current().type == TokenType.IDENT
              and self._peek().type == TokenType.LBRACKET
              and self._peek(2).type == TokenType.RBRACKET):
            # Postfix-array of a non-primitive / UDT element type, e.g.
            # ``var line[] lines = ...`` or ``var store[] xs = ...``. The
            # empty ``[]`` can only form a type here (a subscript index would
            # be non-empty), so this is unambiguously ``array<T>``. Without
            # this branch the ``[]`` is left unconsumed, the name fails to
            # parse, and the whole declaration is silently dropped.
            type_hint = self._parse_type_hint_string()

        name_tok = self._consume(TokenType.IDENT)
        self._consume(TokenType.EQUALS)
        value = self._parse_expression()
        node = VarDecl(
            name=name_tok.value, value=value,
            is_var=is_var, is_varip=is_varip, type_hint=type_hint,
        )
        return self._set_loc(node, start_tok)

    def _parse_typed_decl(self) -> VarDecl:
        """Parse: float x = expr"""
        start_tok = self._current()
        type_hint = self._parse_type_hint_string()
        name_tok = self._consume(TokenType.IDENT)
        self._consume(TokenType.EQUALS)
        value = self._parse_expression()
        node = VarDecl(name=name_tok.value, value=value, type_hint=type_hint)
        return self._set_loc(node, start_tok)

    def _is_tuple_assign(self) -> bool:
        """Look ahead to check if [a, b, ...] is followed by '=' (tuple assignment)."""
        depth = 0
        i = self.pos
        while i < len(self.tokens):
            tt = self.tokens[i].type
            if tt == TokenType.LBRACKET:
                depth += 1
            elif tt == TokenType.RBRACKET:
                depth -= 1
                if depth == 0:
                    # Check if next token is '=' (but not '==')
                    j = i + 1
                    if j < len(self.tokens) and self.tokens[j].type == TokenType.EQUALS:
                        # Make sure it's not ==
                        k = j + 1
                        if k >= len(self.tokens) or self.tokens[k].type != TokenType.EQUALS:
                            return True
                    return False
            elif tt in (TokenType.EOF_TOKEN,):
                return False
            i += 1
        return False

    def _parse_tuple_assign(self) -> TupleAssign:
        """Parse: [a, b, c] = expr"""
        start_tok = self._current()
        self._consume(TokenType.LBRACKET)
        names = []
        while not self._check(TokenType.RBRACKET):
            # Allow underscore as discard placeholder
            if self._check(TokenType.IDENT):
                names.append(self._consume(TokenType.IDENT).value)
            else:
                # Handle _ for tuple discard — lexer produces IDENT for _
                names.append(self._advance().value)
            self._match(TokenType.COMMA)
        self._consume(TokenType.RBRACKET)
        self._consume(TokenType.EQUALS)
        value = self._parse_expression()
        node = TupleAssign(names=names, value=value)
        return self._set_loc(node, start_tok)

    # -- Function definition --

    def _parse_param_type_annotation(self) -> str | None:
        """Consume an optional function-parameter type annotation and return it
        as a canonical hint string ('float', 'string', 'array<line>', 'pivot',
        'chart.point', ...), or ``None`` for a bare untyped param.

        Leaves the parser positioned at the parameter name. Supports the Pine
        qualifier prefixes (``series`` / ``simple`` / ``const`` — consumed but
        not part of the C++ type), builtin types, user-defined / drawing type
        names, and the ``T[]`` postfix-array shorthand (normalised to
        ``array<T>`` by ``_parse_type_hint_string``).
        """
        TYPE_TOKENS = {TokenType.TYPE_INT, TokenType.TYPE_FLOAT,
                       TokenType.TYPE_BOOL, TokenType.TYPE_STRING}
        # Optional qualifiers — they do not affect the C++ param type.
        while self._check(TokenType.IDENT) and self._current().value in (
            "series", "simple", "const",
        ):
            self._advance()
        # Is there a type annotation before the parameter name? A builtin type
        # token always is; an IDENT is a type only if followed by another IDENT
        # (``line ln``), by ``[`` (``line[] arr``), or by ``<`` (the generic
        # collection syntax ``array<float> xs`` / ``matrix<float> m`` /
        # ``map<string,float> mp``). Without the ``<`` case the generic type is
        # mis-consumed as the parameter name and the whole function definition
        # silently fails to parse (its body leaks to top-level scope).
        has_type = False
        if self._current().type in TYPE_TOKENS:
            has_type = True
        elif self._check(TokenType.IDENT):
            nxt = self._peek().type
            if nxt in (TokenType.IDENT, TokenType.LBRACKET, TokenType.LT):
                has_type = True
        if not has_type:
            return None
        return self._parse_type_hint_string()

    def _parse_func_def(self) -> FuncDef:
        """Parse: name(param1, param2) => expr_or_block"""
        start_tok = self._current()
        name = self._consume(TokenType.IDENT).value
        self._consume(TokenType.LPAREN)
        params = []
        param_type_hints: list = []
        param_defaults: list = []
        while not self._check(TokenType.RPAREN):
            # Consume the optional type annotation (builtin / user / drawing /
            # ``T[]``), returning the canonical hint string. Handles ``float[] arr``,
            # ``line[] ln``, ``color c``, ``SDZone z``, ``string tf``, as well as
            # the untyped bare-name case.
            hint = self._parse_param_type_annotation()
            param_name = self._consume(TokenType.IDENT).value
            pdefault = None
            if self._check(TokenType.EQUALS):
                self._advance()  # consume '='
                pdefault = self._parse_expression()  # default value
            params.append(param_name)
            param_type_hints.append(hint)
            param_defaults.append(pdefault)
            self._match(TokenType.COMMA)
        self._consume(TokenType.RPAREN)
        self._skip_newlines()
        self._consume(TokenType.FAT_ARROW)

        # Single expression or indented block
        if self._check(TokenType.NEWLINE):
            self._advance()
            self._consume(TokenType.INDENT)
            body = self._parse_block()
            self._consume(TokenType.DEDENT)
            node = FuncDef(name=name, params=params, body=body, is_single_expr=False)
        else:
            expr = self._parse_expression()
            node = FuncDef(name=name, params=params, body=[ExprStmt(expr=expr)], is_single_expr=True)

        # Record per-param type hints + defaults (mirrors _parse_method_def) so
        # the analyzer can type UDT/string/array params and the codegen can emit
        # them with the correct C++ type (``pivot hi``, ``std::string s``).
        node.annotations = {
            "param_type_hints": param_type_hints,
            "param_defaults": param_defaults,
        }
        return self._set_loc(node, start_tok)

    def _parse_type_or_enum_decl(self):
        """Parse type or enum block declarations."""
        start_tok = self._current()
        kind = self._advance().value  # 'type' or 'enum'
        if kind == "enum":
            return self._parse_enum_decl(start_tok)
        return self._parse_type_decl(start_tok)

    def _parse_type_decl(self, start_tok):
        """Parse: type Name\\n    float field = default"""
        name = self._consume(TokenType.IDENT).value
        self._skip_newlines()
        fields = []
        if self._check(TokenType.INDENT):
            self._advance()  # INDENT
            self._skip_newlines()
            while not self._check(TokenType.DEDENT) and not self._at_end():
                # Parse field: type_name field_name [= default]
                type_name = self._parse_type_hint_string()

                field_name = self._consume(TokenType.IDENT).value
                default = None
                if self._check(TokenType.EQUALS) and self._peek().type != TokenType.EQUALS:
                    self._advance()  # =
                    default = self._parse_expression()
                fields.append(TypeField(type_name=type_name, name=field_name, default=default))
                self._skip_newlines()
            self._consume(TokenType.DEDENT)
        node = TypeDecl(name=name, fields=fields)
        return self._set_loc(node, start_tok)

    def _parse_enum_decl(self, start_tok):
        """Parse: enum Name\\n    Member1 [= expr]\\n    Member2"""
        name = self._consume(TokenType.IDENT).value
        self._skip_newlines()
        members = []
        member_values: dict = {}
        if self._check(TokenType.INDENT):
            self._advance()  # INDENT
            self._skip_newlines()
            while not self._check(TokenType.DEDENT) and not self._at_end():
                if self._check(TokenType.IDENT):
                    mname = self._consume(TokenType.IDENT).value
                    members.append(mname)
                    if (self._check(TokenType.EQUALS)
                            and self._peek().type != TokenType.EQUALS):
                        self._advance()  # =
                        member_values[mname] = self._parse_expression()
                else:
                    self._advance()  # skip unexpected tokens
                self._skip_newlines()
            self._consume(TokenType.DEDENT)
        node = EnumDecl(name=name, members=members, member_values=member_values)
        return self._set_loc(node, start_tok)

    def _parse_method_def(self):
        """Parse: method name(TypeName self, params...) => body"""
        start_tok = self._advance()  # consume 'method'
        name = self._consume(TokenType.IDENT).value
        self._consume(TokenType.LPAREN)
        # First param is the type + self: TypeName self
        type_name = self._consume(TokenType.IDENT).value
        params = [self._consume(TokenType.IDENT).value]  # 'self' or user's name
        param_type_hints = [type_name]
        # Preserve per-param default expressions so codegen can substitute
        # them at the UDT-method call site when a caller omits trailing
        # args. See data/validation/udt-method-probe-04-default-param.
        param_defaults: list = [None]
        while self._match(TokenType.COMMA):
            # Skip optional type annotations
            param_type = None
            if self._current().type in TYPE_KEYWORDS:
                param_type = self._parse_type_hint_string()
            elif (self._current().type == TokenType.IDENT
                  and self._peek().type in (TokenType.IDENT, TokenType.LBRACKET, TokenType.LT)):
                # ``line ln`` / ``float[] arr`` / ``array<float> xs`` typed param.
                param_type = self._parse_type_hint_string()
            p = self._consume(TokenType.IDENT).value
            pdefault = None
            if self._check(TokenType.EQUALS):
                self._advance()
                pdefault = self._parse_expression()
            params.append(p)
            param_type_hints.append(param_type)
            param_defaults.append(pdefault)
        self._consume(TokenType.RPAREN)
        self._skip_newlines()
        self._consume(TokenType.FAT_ARROW)

        if self._check(TokenType.NEWLINE):
            self._advance()
            self._consume(TokenType.INDENT)
            body = self._parse_block()
            self._consume(TokenType.DEDENT)
            node = MethodDef(name=name, type_name=type_name, params=params, body=body)
        else:
            expr = self._parse_expression()
            node = MethodDef(name=name, type_name=type_name, params=params,
                            body=[ExprStmt(expr=expr)], is_single_expr=True)
        node.annotations = {
            "param_type_hints": param_type_hints,
            "param_defaults": param_defaults,
        }
        return self._set_loc(node, start_tok)

    # -- Control flow --

    def _parse_if_stmt(self) -> IfStmt:
        start_tok = self._current()
        self._consume(TokenType.IF)
        condition = self._parse_expression()

        self._consume(TokenType.NEWLINE)
        self._consume(TokenType.INDENT)
        body = self._parse_block()
        self._consume(TokenType.DEDENT)

        else_body: list = []
        if self._check(TokenType.ELSE):
            self._advance()
            if self._check(TokenType.IF):
                # else if -> nested IfStmt in else_body
                else_body = [self._parse_if_stmt()]
            else:
                self._consume(TokenType.NEWLINE)
                self._consume(TokenType.INDENT)
                else_body = self._parse_block()
                self._consume(TokenType.DEDENT)

        node = IfStmt(condition=condition, body=body, else_body=else_body)
        return self._set_loc(node, start_tok)

    def _parse_for_stmt(self):
        start_tok = self._current()
        self._consume(TokenType.FOR)

        # Check for for...in: for [a, b] in arr (destructured)
        if self._check(TokenType.LBRACKET):
            self._advance()  # [
            vars_list = []
            while not self._check(TokenType.RBRACKET):
                vars_list.append(self._consume(TokenType.IDENT).value)
                self._match(TokenType.COMMA)
            self._consume(TokenType.RBRACKET)
            self._consume(TokenType.IN)
            iterable = self._parse_expression()
            self._consume(TokenType.NEWLINE)
            self._consume(TokenType.INDENT)
            body = self._parse_block()
            self._consume(TokenType.DEDENT)
            node = ForInStmt(vars=vars_list, iterable=iterable, body=body)
            return self._set_loc(node, start_tok)

        var_name = self._consume(TokenType.IDENT).value

        # for x in arr
        if self._check(TokenType.IN):
            self._advance()  # consume 'in'
            iterable = self._parse_expression()
            self._consume(TokenType.NEWLINE)
            self._consume(TokenType.INDENT)
            body = self._parse_block()
            self._consume(TokenType.DEDENT)
            node = ForInStmt(var=var_name, iterable=iterable, body=body)
            return self._set_loc(node, start_tok)

        # Traditional: for var = start to end [by step]
        self._consume(TokenType.EQUALS)
        start = self._parse_expression()
        self._consume(TokenType.TO)
        end = self._parse_expression()
        step = None
        if self._match(TokenType.BY):
            step = self._parse_expression()
        self._consume(TokenType.NEWLINE)
        self._consume(TokenType.INDENT)
        body = self._parse_block()
        self._consume(TokenType.DEDENT)
        node = ForStmt(var=var_name, start=start, end=end, step=step, body=body)
        return self._set_loc(node, start_tok)

    def _parse_while_stmt(self) -> WhileStmt:
        start_tok = self._current()
        self._consume(TokenType.WHILE)
        condition = self._parse_expression()
        self._consume(TokenType.NEWLINE)
        self._consume(TokenType.INDENT)
        body = self._parse_block()
        self._consume(TokenType.DEDENT)
        node = WhileStmt(condition=condition, body=body)
        return self._set_loc(node, start_tok)

    def _parse_switch_stmt(self) -> SwitchStmt:
        start_tok = self._current()
        self._consume(TokenType.SWITCH)

        # Optional expression after switch
        expr = None
        if not self._check(TokenType.NEWLINE):
            expr = self._parse_expression()

        self._consume(TokenType.NEWLINE)
        self._consume(TokenType.INDENT)

        cases = []
        default_body = []
        self._skip_newlines()
        while not self._check(TokenType.DEDENT) and not self._at_end():
            # Default case: => body
            if self._check(TokenType.FAT_ARROW):
                self._advance()
                if self._check(TokenType.NEWLINE):
                    self._advance()
                    self._consume(TokenType.INDENT)
                    default_body = self._parse_block()
                    self._consume(TokenType.DEDENT)
                else:
                    default_body = [ExprStmt(expr=self._parse_expression())]
            else:
                # case_expr => body
                case_expr = self._parse_expression()
                self._consume(TokenType.FAT_ARROW)
                if self._check(TokenType.NEWLINE):
                    self._advance()
                    self._consume(TokenType.INDENT)
                    case_body = self._parse_block()
                    self._consume(TokenType.DEDENT)
                else:
                    case_body = [ExprStmt(expr=self._parse_expression())]
                cases.append((case_expr, case_body))
            self._skip_newlines()

        self._consume(TokenType.DEDENT)
        node = SwitchStmt(expr=expr, cases=cases, default_body=default_body)
        return self._set_loc(node, start_tok)

    # -- Block parsing --

    def _parse_block(self) -> list:
        stmts: list = []
        self._skip_newlines()
        while not self._check(TokenType.DEDENT) and not self._at_end():
            try:
                stmt = self._parse_statement()
                if stmt is not None:
                    if isinstance(stmt, list):
                        stmts.extend(stmt)
                    else:
                        stmts.append(stmt)
            except ParseError:
                self._recover()
            self._skip_newlines()
        return stmts

    # ------------------------------------------------------------------
    # Expression parsing (precedence climbing)
    # ------------------------------------------------------------------
    #
    # Precedence (lowest to highest):
    #   1. Ternary: ? :
    #   2. Logical OR: or
    #   3. Logical AND: and
    #   4. Logical NOT: not (unary)
    #   5. Comparison: == != > < >= <=
    #   6. Addition: + -
    #   7. Multiplication: * / %
    #   8. Unary: - +
    #   9. Postfix: [n] .member (args)
    #  10. Primary: literals, identifiers, (expr)

    # Tokens that can appear as member names (after dot)
    _MEMBER_NAME_TOKENS = {
        TokenType.IDENT,
        TokenType.TYPE_INT, TokenType.TYPE_FLOAT,
        TokenType.TYPE_BOOL, TokenType.TYPE_STRING,
    }

    def _parse_expression(self):
        # if/switch can be used as expressions (RHS of assignments)
        if self._check(TokenType.IF):
            return self._parse_if_expr()
        if self._check(TokenType.SWITCH):
            return self._parse_switch_expr()
        return self._parse_ternary()

    def _parse_if_expr(self):
        """Parse if/else as an expression (returns IfStmt, codegen handles it)."""
        return self._parse_if_stmt()

    def _parse_switch_expr(self):
        """Parse switch as an expression (returns SwitchStmt, codegen handles it)."""
        return self._parse_switch_stmt()

    def _parse_ternary(self):
        start_tok = self._current()
        expr = self._parse_or()
        if self._match(TokenType.QUESTION):
            self._skip_newlines()
            true_val = self._parse_expression()
            self._skip_newlines()
            self._consume(TokenType.COLON, "Expected ':' in ternary")
            self._skip_newlines()
            false_val = self._parse_expression()
            node = Ternary(condition=expr, true_val=true_val, false_val=false_val)
            return self._set_loc(node, start_tok)
        return expr

    def _try_line_continuation(self, *op_types: TokenType) -> bool:
        """Check if NEWLINE+INDENT+op is a line continuation, and consume if so.
        Returns True if continuation was found and NEWLINE+INDENT consumed."""
        saved = self.pos
        if self._check(TokenType.NEWLINE):
            self._advance()
            if self._check(TokenType.INDENT):
                self._advance()
                if self._current().type in op_types:
                    return True
            # Not a continuation — restore position
            self.pos = saved
        return False

    def _parse_or(self):
        start_tok = self._current()
        left = self._parse_and()
        in_continuation = False
        while True:
            self._skip_newlines_in_continuation(in_continuation)
            if self._match(TokenType.OR):
                right = self._parse_and()
                left = BinOp(left=left, op="or", right=right)
                self._set_loc(left, start_tok)
            elif not in_continuation and self._try_line_continuation(TokenType.OR):
                in_continuation = True
                self._advance()  # consume OR
                right = self._parse_and()
                left = BinOp(left=left, op="or", right=right)
                self._set_loc(left, start_tok)
            else:
                break
        if in_continuation:
            self._match(TokenType.DEDENT)
        return left

    def _parse_and(self):
        start_tok = self._current()
        left = self._parse_not()
        in_continuation = False
        while True:
            self._skip_newlines_in_continuation(in_continuation)
            if self._match(TokenType.AND):
                right = self._parse_not()
                left = BinOp(left=left, op="and", right=right)
                self._set_loc(left, start_tok)
            elif not in_continuation and self._try_line_continuation(TokenType.AND):
                in_continuation = True
                self._advance()  # consume AND
                right = self._parse_not()
                left = BinOp(left=left, op="and", right=right)
                self._set_loc(left, start_tok)
            else:
                break
        if in_continuation:
            self._match(TokenType.DEDENT)
        return left

    def _skip_newlines_in_continuation(self, in_continuation: bool) -> None:
        """Inside a continuation block, skip NEWLINE tokens."""
        if in_continuation:
            while self._check(TokenType.NEWLINE):
                self._advance()

    def _parse_not(self):
        if self._check(TokenType.NOT):
            start_tok = self._current()
            self._advance()
            operand = self._parse_not()
            node = UnaryOp(op="not", operand=operand)
            return self._set_loc(node, start_tok)
        return self._parse_comparison()

    def _parse_comparison(self):
        start_tok = self._current()
        left = self._parse_addition()
        comp_ops = {
            TokenType.EQEQ: "==", TokenType.NOTEQ: "!=",
            TokenType.GT: ">", TokenType.LT: "<",
            TokenType.GE: ">=", TokenType.LE: "<=",
        }
        while self._current().type in comp_ops:
            op = comp_ops[self._advance().type]
            right = self._parse_addition()
            left = BinOp(left=left, op=op, right=right)
            self._set_loc(left, start_tok)
        return left

    def _parse_addition(self):
        start_tok = self._current()
        left = self._parse_multiplication()
        while self._current().type in (TokenType.PLUS, TokenType.MINUS):
            op = "+" if self._advance().type == TokenType.PLUS else "-"
            right = self._parse_multiplication()
            left = BinOp(left=left, op=op, right=right)
            self._set_loc(left, start_tok)
        return left

    def _parse_multiplication(self):
        start_tok = self._current()
        left = self._parse_unary()
        mul_ops = {TokenType.STAR: "*", TokenType.SLASH: "/", TokenType.PERCENT: "%"}
        while self._current().type in mul_ops:
            op = mul_ops[self._advance().type]
            right = self._parse_unary()
            left = BinOp(left=left, op=op, right=right)
            self._set_loc(left, start_tok)
        return left

    def _parse_unary(self):
        if self._check(TokenType.MINUS):
            start_tok = self._current()
            self._advance()
            operand = self._parse_unary()
            node = UnaryOp(op="-", operand=operand)
            return self._set_loc(node, start_tok)
        if self._check(TokenType.PLUS):
            start_tok = self._current()
            self._advance()
            operand = self._parse_unary()
            node = UnaryOp(op="+", operand=operand)
            return self._set_loc(node, start_tok)
        return self._parse_postfix()

    def _parse_postfix(self):
        expr = self._parse_primary()
        while True:
            # Subscript: expr[index]
            if self._check(TokenType.LBRACKET):
                start_tok = self._current()
                self._advance()
                index = self._parse_expression()
                self._consume(TokenType.RBRACKET)
                expr = Subscript(object=expr, index=index)
                self._set_loc(expr, start_tok)

            # Member access: expr.member  or  expr.member(args)
            elif self._check(TokenType.DOT):
                self._advance()
                member_tok = self._consume_member_name()

                template_args = []
                if self._looks_like_call_template_args():
                    template_args = self._parse_template_args()

                if self._check(TokenType.LPAREN):
                    # Build callee as MemberAccess, then parse call
                    callee = MemberAccess(object=expr, member=member_tok.value)
                    self._set_loc(callee, member_tok)
                    if template_args:
                        callee.annotations = {"template_args": template_args}
                    expr = self._parse_call_with_callee(callee)
                else:
                    expr = MemberAccess(object=expr, member=member_tok.value)
                    self._set_loc(expr, member_tok)
                    if template_args:
                        expr.annotations = {"template_args": template_args}

            # Direct call: expr(args) — needed for identifiers followed by (
            elif self._check(TokenType.LPAREN) and self._is_call_position(expr):
                expr = self._parse_call_with_callee(expr)
            else:
                break
        return expr

    def _is_call_position(self, expr) -> bool:
        """Check if the current LPAREN should be treated as a function call."""
        return isinstance(expr, (Identifier, MemberAccess))

    def _consume_member_name(self) -> Token:
        """Consume an identifier or type keyword as a member name."""
        if self._current().type in self._MEMBER_NAME_TOKENS:
            return self._advance()
        return self._consume(TokenType.IDENT, "Expected member name")

    def _parse_call_with_callee(self, callee) -> FuncCall:
        """Parse (args, kwargs) after callee expression."""
        start_tok = self._current()
        self._consume(TokenType.LPAREN)
        args, kwargs = self._parse_call_args()
        self._consume(TokenType.RPAREN)
        node = FuncCall(callee=callee, args=args, kwargs=kwargs)
        return self._set_loc(node, start_tok)

    def _parse_call_args(self) -> tuple[list, dict]:
        """Parse function call arguments and keyword arguments."""
        args: list = []
        kwargs: dict = {}

        while not self._check(TokenType.RPAREN) and not self._at_end():
            # Detect kwargs: IDENT = value (but not IDENT == value)
            if (self._current().type == TokenType.IDENT
                    and self._peek().type == TokenType.EQUALS
                    and self._peek(2).type != TokenType.EQUALS):
                key_tok = self._advance()
                self._advance()  # consume =
                val = self._parse_expression()
                kwargs[key_tok.value] = val
            else:
                args.append(self._parse_expression())

            self._match(TokenType.COMMA)

        return args, kwargs

    # -- Primary expressions --

    def _parse_primary(self):
        cur = self._current()

        # Array/tuple literal: [expr, expr, ...]
        # Produces a TupleLiteral node with all elements preserved.
        if cur.type == TokenType.LBRACKET:
            self._advance()
            elements = []
            while not self._check(TokenType.RBRACKET) and not self._at_end():
                elements.append(self._parse_expression())
                self._match(TokenType.COMMA)
            self._consume(TokenType.RBRACKET)
            node = TupleLiteral(elements=elements)
            return self._set_loc(node, cur)

        # Parenthesized expression
        if cur.type == TokenType.LPAREN:
            self._advance()
            expr = self._parse_expression()
            self._consume(TokenType.RPAREN)
            return expr

        # Number literal
        if cur.type == TokenType.NUMBER:
            self._advance()
            if "." in cur.value or "e" in cur.value or "E" in cur.value:
                val = float(cur.value)
            else:
                val = int(cur.value)
            node = NumberLiteral(value=val)
            return self._set_loc(node, cur)

        # String literal
        if cur.type == TokenType.STRING:
            self._advance()
            node = StringLiteral(value=cur.value)
            return self._set_loc(node, cur)

        # Boolean literals
        if cur.type == TokenType.TRUE:
            self._advance()
            node = BoolLiteral(value=True)
            return self._set_loc(node, cur)
        if cur.type == TokenType.FALSE:
            self._advance()
            node = BoolLiteral(value=False)
            return self._set_loc(node, cur)

        # Color literal
        if cur.type == TokenType.COLOR:
            self._advance()
            node = ColorLiteral(value=cur.value)
            return self._set_loc(node, cur)

        # na literal (can also be used as function: na(x))
        if cur.type == TokenType.NA:
            self._advance()
            if self._check(TokenType.LPAREN):
                # na used as function call
                callee = Identifier(name="na")
                self._set_loc(callee, cur)
                return self._parse_call_with_callee(callee)
            node = NaLiteral()
            return self._set_loc(node, cur)

        # Type keywords used as values (e.g., type=float in input kwargs)
        if cur.type in TYPE_KEYWORDS:
            self._advance()
            node = Identifier(name=cur.value)
            return self._set_loc(node, cur)

        # Identifier (may be followed by function call via postfix)
        if cur.type == TokenType.IDENT:
            self._advance()
            node = Identifier(name=cur.value)
            return self._set_loc(node, cur)

        raise ParseError(
            f"Unexpected token {cur.type.name}({cur.value!r}) at L{cur.line}:{cur.col}"
        )
