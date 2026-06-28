"""Lexer for PineScript v6 source code.

Improvements over tokens.py:
- Token carries end_col for span tracking
- Scientific notation support (1.5e-3)
- Leading-dot float support (.5 -> 0.5)
- PERCENT_EQUALS operator (%=)
- IMPORT and METHOD keywords
- Uses Diagnostic/CompileError for malformed tokens instead of silent skips
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from pineforge_codegen.errors import (
    CompileError,
    Diagnostic,
    Level,
    Phase,
    SourceLocation,
)


class TokenType(Enum):
    # Literals
    NUMBER = auto()
    STRING = auto()
    IDENT = auto()

    # Structure
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    EOF_TOKEN = auto()

    # Delimiters
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COMMA = auto()
    DOT = auto()

    # Assignment
    EQUALS = auto()
    COLON_EQUALS = auto()
    PLUS_EQUALS = auto()
    MINUS_EQUALS = auto()
    STAR_EQUALS = auto()
    SLASH_EQUALS = auto()
    PERCENT_EQUALS = auto()

    # Arithmetic
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()

    # Comparison
    EQEQ = auto()       # ==
    NOTEQ = auto()      # !=
    GT = auto()         # >
    LT = auto()         # <
    GE = auto()         # >=
    LE = auto()         # <=

    # Logical (keywords)
    AND = auto()
    OR = auto()
    NOT = auto()

    # Ternary
    QUESTION = auto()
    COLON = auto()

    # Arrow
    FAT_ARROW = auto()  # =>

    # Keywords
    IF = auto()
    ELSE = auto()
    FOR = auto()
    WHILE = auto()
    SWITCH = auto()
    BREAK = auto()
    CONTINUE = auto()
    VAR = auto()
    VARIP = auto()
    TO = auto()
    BY = auto()
    TRUE = auto()
    FALSE = auto()
    NA = auto()
    IMPORT = auto()
    METHOD = auto()
    IN = auto()

    # Color literal (#rrggbb / #rrggbbaa)
    COLOR = auto()

    # Type keywords
    TYPE_INT = auto()
    TYPE_FLOAT = auto()
    TYPE_BOOL = auto()
    TYPE_STRING = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    col: int
    end_col: int = 0

    def __post_init__(self) -> None:
        # Default end_col to col + len(value) if not explicitly set
        if self.end_col == 0:
            self.end_col = self.col + len(self.value)

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:{self.col}-{self.end_col})"


KEYWORDS: dict[str, TokenType] = {
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "while": TokenType.WHILE,
    "switch": TokenType.SWITCH,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,
    "var": TokenType.VAR,
    "varip": TokenType.VARIP,
    "to": TokenType.TO,
    "by": TokenType.BY,
    "not": TokenType.NOT,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "na": TokenType.NA,
    "import": TokenType.IMPORT,
    "method": TokenType.METHOD,
    "in": TokenType.IN,
    "int": TokenType.TYPE_INT,
    "float": TokenType.TYPE_FLOAT,
    "bool": TokenType.TYPE_BOOL,
    "string": TokenType.TYPE_STRING,
}


class Lexer:
    """Converts PineScript v6 source string into a list of tokens."""

    # Token types that indicate line continuation when they end a line.
    # If a line ends with one of these, the next line is a continuation
    # and its INDENT/DEDENT should be suppressed.
    CONTINUATION_TOKENS = {
        TokenType.AND, TokenType.OR,
        TokenType.PLUS, TokenType.MINUS, TokenType.STAR, TokenType.SLASH,
        TokenType.PERCENT,
        TokenType.GT, TokenType.LT, TokenType.GE, TokenType.LE,
        TokenType.EQEQ, TokenType.NOTEQ,
        TokenType.QUESTION, TokenType.COLON,
        TokenType.COMMA, TokenType.DOT,
        TokenType.EQUALS, TokenType.COLON_EQUALS,
        TokenType.PLUS_EQUALS, TokenType.MINUS_EQUALS,
        TokenType.STAR_EQUALS, TokenType.SLASH_EQUALS,
        TokenType.PERCENT_EQUALS,
    }

    def __init__(self, source: str, filename: str = "<input>") -> None:
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        self.indent_stack: list[int] = [0]
        self.paren_depth = 0  # Track () and [] nesting to suppress NEWLINE/INDENT/DEDENT
        self._in_continuation = False  # True when current line is a continuation
        self._diagnostics: list[Diagnostic] = []

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.source[idx] if idx < len(self.source) else "\0"

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _at_end(self) -> bool:
        return self.pos >= len(self.source)

    def _emit(self, tt: TokenType, value: str, line: int, col: int, end_col: int | None = None) -> None:
        if end_col is None:
            end_col = col + len(value)
        self.tokens.append(Token(tt, value, line, col, end_col))

    def _emit_diagnostic(self, message: str, line: int, col: int, end_col: int, hint: str | None = None) -> None:
        loc = SourceLocation(file=self.filename, line=line, col=col, end_col=end_col)
        diag = Diagnostic(level=Level.ERROR, phase=Phase.LEXER, location=loc, message=message, hint=hint)
        self._diagnostics.append(diag)

    def _skip_line(self) -> None:
        while not self._at_end() and self.source[self.pos] != "\n":
            self._advance()
        if not self._at_end():
            self._advance()

    def _skip_comment(self) -> None:
        while not self._at_end() and self.source[self.pos] != "\n":
            self._advance()

    def tokenize(self) -> list[Token]:
        while not self._at_end():
            self._tokenize_line()

        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self._emit(TokenType.DEDENT, "", self.line, self.col)

        self._emit(TokenType.EOF_TOKEN, "", self.line, self.col)

        if self._diagnostics:
            raise CompileError(self._diagnostics)

        return self.tokens

    def _tokenize_line(self) -> None:
        if self._at_end():
            return

        line_start = self.pos

        # Peek at line content to detect blank/comment-only lines
        temp = self.pos
        while temp < len(self.source) and self.source[temp] in (" ", "\t"):
            temp += 1
        if temp >= len(self.source) or self.source[temp] == "\n":
            self._advance_to(min(temp + 1, len(self.source)))
            return
        if self.source[temp: temp + 3] == "//@":
            self._skip_line()
            return
        if self.source[temp: temp + 2] == "//":
            self._skip_line()
            return

        # Inside parens/brackets: skip indentation handling, treat as continuation
        if self.paren_depth > 0:
            while not self._at_end() and self.source[self.pos] in (" ", "\t"):
                self._advance()
            emitted_in_parens = False
            while not self._at_end() and self.source[self.pos] != "\n":
                self._skip_whitespace_inline()
                if self._at_end() or self.source[self.pos] == "\n":
                    break
                if self.source[self.pos: self.pos + 2] == "//":
                    self._skip_comment()
                    break
                emitted_in_parens = True
                self._read_token()
            if not self._at_end() and self.source[self.pos] == "\n":
                self._advance()
            # If parens closed on this line, end the statement UNLESS the line
            # ends with a continuation token (e.g. trailing operator), in which
            # case the next line continues the same logical line.
            if self.paren_depth == 0 and emitted_in_parens:
                last_token = self.tokens[-1] if self.tokens else None
                if last_token and last_token.type in self.CONTINUATION_TOKENS:
                    self._in_continuation = True
                else:
                    self._in_continuation = False
                    self._emit(TokenType.NEWLINE, "\\n", self.line - 1, self.col)
            return

        # Indentation handling
        indent_level = 0
        while not self._at_end() and self.source[self.pos] in (" ", "\t"):
            ch = self._advance()
            indent_level += 1

        raw = self.source[line_start: self.pos]
        if "\t" in raw:
            indent_level = raw.count("\t")
        else:
            indent_level = len(raw) // 4

        # Operator-first line continuation: when a line *begins* with a binary
        # / ternary operator that cannot start a statement (e.g. ``? x``,
        # ``: y``, ``+ z``, ``and w``), it continues the previous logical line
        # even though that line did not *end* with an operator (the break was
        # placed before the operator instead of after it). Suppress this line's
        # INDENT/DEDENT and drop the NEWLINE that ended the prior line so the
        # parser sees one contiguous expression. Only applies outside parens
        # and when not already in an end-of-line continuation.
        starts_with_cont_op = (
            not self._in_continuation
            and self._line_starts_with_continuation_op()
        )
        if starts_with_cont_op and self.tokens and self.tokens[-1].type == TokenType.NEWLINE:
            self.tokens.pop()

        # If we're in a continuation (previous line ended with an operator),
        # suppress INDENT/DEDENT — the indentation is cosmetic, not structural
        if not self._in_continuation and not starts_with_cont_op:
            current_indent = self.indent_stack[-1]
            if indent_level > current_indent:
                self.indent_stack.append(indent_level)
                self._emit(TokenType.INDENT, "", self.line, 1)
            elif indent_level < current_indent:
                while len(self.indent_stack) > 1 and self.indent_stack[-1] > indent_level:
                    self.indent_stack.pop()
                    self._emit(TokenType.DEDENT, "", self.line, 1)

        # Tokens on this line
        emitted_something = False
        while not self._at_end() and self.source[self.pos] != "\n":
            self._skip_whitespace_inline()
            if self._at_end() or self.source[self.pos] == "\n":
                break
            if self.source[self.pos: self.pos + 2] == "//":
                self._skip_comment()
                break
            emitted_something = True
            self._read_token()

        if not self._at_end() and self.source[self.pos] == "\n":
            self._advance()

        # Check if this line ends with a continuation token
        if emitted_something and self.paren_depth == 0:
            last_token = self.tokens[-1] if self.tokens else None
            if last_token and last_token.type in self.CONTINUATION_TOKENS:
                # Next line is a continuation — don't emit NEWLINE
                self._in_continuation = True
            else:
                self._in_continuation = False
                self._emit(TokenType.NEWLINE, "\\n", self.line - 1, self.col)
        else:
            self._in_continuation = False

    def _line_starts_with_continuation_op(self) -> bool:
        """True when the upcoming line content begins with a binary/ternary
        operator that can never begin a statement, so the line is a
        continuation of the previous logical line.

        Called with ``self.pos`` positioned at the first non-whitespace
        character of the line. Deliberately conservative: ``-`` is excluded
        (ambiguous leading unary minus) and ``.`` is excluded (``.5`` is a
        leading-dot number, not member access). The included operators
        (``? : + * / % == != > < >= <= and or``) cannot legally start a Pine
        statement, so suppressing the line break for them never merges two
        independent statements that previously parsed."""
        src = self.source
        p = self.pos
        n = len(src)
        if p >= n:
            return False
        c = src[p]
        c2 = src[p + 1] if p + 1 < n else ""
        # Two-character comparison operators.
        if c2 == "=" and c in ("=", "!", ">", "<"):
            return True
        # ':' ternary-else continuation, but not ':=' (reassignment).
        if c == ":" and c2 != "=":
            return True
        # Single-character operators that cannot start a statement.
        if c in "?+*%><":
            return True
        # '/' division continuation, but never '//' (comment).
        if c == "/" and c2 != "/":
            return True
        # 'and' / 'or' keyword continuation (require a word boundary so names
        # like ``android`` / ``organic`` are not misread).
        def _kw(word: str) -> bool:
            end = p + len(word)
            if src[p:end] != word:
                return False
            nxt = src[end] if end < n else ""
            return not (nxt.isalnum() or nxt == "_")
        if _kw("and") or _kw("or"):
            return True
        return False

    def _advance_to(self, target: int) -> None:
        while self.pos < target and self.pos < len(self.source):
            self._advance()

    def _skip_whitespace_inline(self) -> None:
        while not self._at_end() and self.source[self.pos] in (" ", "\t"):
            self._advance()

    def _read_token(self) -> None:
        ch = self.source[self.pos]
        start_line = self.line
        start_col = self.col

        # Numbers: digit-starting
        if ch.isdigit():
            self._read_number(start_line, start_col)
            return

        # Leading-dot float: .5, .123
        if ch == "." and self.pos + 1 < len(self.source) and self.source[self.pos + 1].isdigit():
            self._read_leading_dot_number(start_line, start_col)
            return

        # Strings
        if ch == '"':
            self._read_string(start_line, start_col)
            return
        if ch == "'":
            self._read_string_single(start_line, start_col)
            return

        # Color literals (#rrggbb or #rrggbbaa)
        if ch == "#":
            self._advance()  # consume #
            buf = []
            while not self._at_end() and self.source[self.pos] in "0123456789abcdefABCDEF":
                buf.append(self._advance())
            if not buf:
                self._emit_diagnostic(
                    "Invalid color literal: expected hex digits after '#'",
                    start_line, start_col, start_col + 1,
                    hint="Color literals must be #RRGGBB or #RRGGBBAA",
                )
            else:
                value = "#" + "".join(buf)
                self._emit(TokenType.COLOR, value, start_line, start_col, start_col + len(value))
            return

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            self._read_ident(start_line, start_col)
            return

        # Two-character operators (check before single-char)
        two = self.source[self.pos: self.pos + 2] if self.pos + 1 < len(self.source) else ""
        two_char_ops: dict[str, TokenType] = {
            ":=": TokenType.COLON_EQUALS,
            "==": TokenType.EQEQ,
            "!=": TokenType.NOTEQ,
            ">=": TokenType.GE,
            "<=": TokenType.LE,
            "=>": TokenType.FAT_ARROW,
            "+=": TokenType.PLUS_EQUALS,
            "-=": TokenType.MINUS_EQUALS,
            "*=": TokenType.STAR_EQUALS,
            "/=": TokenType.SLASH_EQUALS,
            "%=": TokenType.PERCENT_EQUALS,
        }
        if two in two_char_ops:
            self._advance()
            self._advance()
            self._emit(two_char_ops[two], two, start_line, start_col, start_col + 2)
            return

        # Single-character operators
        singles: dict[str, TokenType] = {
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            ",": TokenType.COMMA,
            ".": TokenType.DOT,
            "=": TokenType.EQUALS,
            "+": TokenType.PLUS,
            "-": TokenType.MINUS,
            "*": TokenType.STAR,
            "/": TokenType.SLASH,
            "%": TokenType.PERCENT,
            ">": TokenType.GT,
            "<": TokenType.LT,
            "?": TokenType.QUESTION,
            ":": TokenType.COLON,
        }
        if ch in singles:
            self._advance()
            tt = singles[ch]
            if tt in (TokenType.LPAREN, TokenType.LBRACKET):
                self.paren_depth += 1
            elif tt in (TokenType.RPAREN, TokenType.RBRACKET):
                self.paren_depth = max(0, self.paren_depth - 1)
            self._emit(tt, ch, start_line, start_col, start_col + 1)
            return

        # Unknown character — emit diagnostic instead of silently skipping
        self._emit_diagnostic(
            f"Unexpected character: {ch!r}",
            start_line, start_col, start_col + 1,
        )
        self._advance()

    def _read_number(self, start_line: int, start_col: int) -> None:
        """Read an integer or float, including optional scientific notation (e/E)."""
        buf: list[str] = []

        # Integer part
        while not self._at_end() and self.source[self.pos].isdigit():
            buf.append(self._advance())

        # Optional fractional part
        if not self._at_end() and self.source[self.pos] == ".":
            # Consume dot if followed by digit OR if followed by non-identifier char
            # (e.g., 0. is a valid float, but 0.member should not consume the dot)
            next_char = self.source[self.pos + 1] if self.pos + 1 < len(self.source) else ""
            if next_char.isdigit() or (not next_char.isalpha() and next_char != "_"):
                buf.append(self._advance())  # consume '.'
                while not self._at_end() and self.source[self.pos].isdigit():
                    buf.append(self._advance())

        # Optional exponent
        if not self._at_end() and self.source[self.pos] in ("e", "E"):
            buf.append(self._advance())  # consume 'e'/'E'
            if not self._at_end() and self.source[self.pos] in ("+", "-"):
                buf.append(self._advance())  # consume sign
            if not self._at_end() and self.source[self.pos].isdigit():
                while not self._at_end() and self.source[self.pos].isdigit():
                    buf.append(self._advance())
            else:
                # Malformed exponent — emit diagnostic
                self._emit_diagnostic(
                    "Malformed scientific notation: expected digits after exponent",
                    start_line, start_col, self.col,
                    hint="Example: 1.5e-3 or 2E10",
                )

        value = "".join(buf)
        self._emit(TokenType.NUMBER, value, start_line, start_col, start_col + len(value))

    def _read_leading_dot_number(self, start_line: int, start_col: int) -> None:
        """Read a leading-dot float like .5, normalising to '0.5'."""
        self._advance()  # consume '.'
        buf: list[str] = []
        while not self._at_end() and self.source[self.pos].isdigit():
            buf.append(self._advance())

        # Optional exponent
        if not self._at_end() and self.source[self.pos] in ("e", "E"):
            buf2: list[str] = [self._advance()]  # 'e'/'E'
            if not self._at_end() and self.source[self.pos] in ("+", "-"):
                buf2.append(self._advance())
            if not self._at_end() and self.source[self.pos].isdigit():
                while not self._at_end() and self.source[self.pos].isdigit():
                    buf2.append(self._advance())
                buf.extend(buf2)
            # If malformed exponent after leading-dot, just ignore the e part

        frac = "".join(buf)
        value = "0." + frac
        self._emit(TokenType.NUMBER, value, start_line, start_col, start_col + len(value))

    def _read_string(self, start_line: int, start_col: int) -> None:
        self._advance()  # consume opening "
        buf: list[str] = []
        while not self._at_end() and self.source[self.pos] != '"':
            if self.source[self.pos] == "\\" and self.pos + 1 < len(self.source):
                self._advance()  # skip backslash
            buf.append(self._advance())
        if not self._at_end():
            self._advance()  # consume closing "
        value = "".join(buf)
        self._emit(TokenType.STRING, value, start_line, start_col, self.col)

    def _read_string_single(self, start_line: int, start_col: int) -> None:
        self._advance()  # consume opening '
        buf: list[str] = []
        while not self._at_end() and self.source[self.pos] != "'":
            if self.source[self.pos] == "\\" and self.pos + 1 < len(self.source):
                self._advance()
            buf.append(self._advance())
        if not self._at_end():
            self._advance()  # consume closing '
        value = "".join(buf)
        self._emit(TokenType.STRING, value, start_line, start_col, self.col)

    def _read_ident(self, start_line: int, start_col: int) -> None:
        buf: list[str] = []
        while not self._at_end() and (self.source[self.pos].isalnum() or self.source[self.pos] == "_"):
            buf.append(self._advance())
        word = "".join(buf)
        tt = KEYWORDS.get(word, TokenType.IDENT)
        self._emit(tt, word, start_line, start_col, start_col + len(word))
