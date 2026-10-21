"""Tokenizer for PineScript v6 source code."""

from dataclasses import dataclass
from enum import Enum, auto


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

    # Arithmetic
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()

    # Comparison
    EQEQ = auto()       # ==
    NOTEQ = auto()       # !=
    GT = auto()          # >
    LT = auto()          # <
    GE = auto()          # >=
    LE = auto()          # <=

    # Logical (keywords)
    AND = auto()
    OR = auto()
    NOT = auto()

    # Ternary
    QUESTION = auto()
    COLON = auto()

    # Arrow
    FAT_ARROW = auto()   # =>

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

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:{self.col})"


KEYWORDS = {
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
    "int": TokenType.TYPE_INT,
    "float": TokenType.TYPE_FLOAT,
    "bool": TokenType.TYPE_BOOL,
    "string": TokenType.TYPE_STRING,
}


class Tokenizer:
    """Converts PineScript v6 source string into a list of tokens."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        self.indent_stack: list[int] = [0]
        self.paren_depth = 0  # Track () and [] nesting to suppress NEWLINE/INDENT/DEDENT

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

    def _emit(self, tt: TokenType, value: str, line: int, col: int) -> None:
        self.tokens.append(Token(tt, value, line, col))

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
        return self.tokens

    def _tokenize_line(self) -> None:
        if self._at_end():
            return

        line_start = self.pos

        # Peek at line content to detect blank/comment lines
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
            # If parens closed on this line, emit NEWLINE so parser sees end of statement
            if self.paren_depth == 0 and emitted_in_parens:
                self._emit(TokenType.NEWLINE, "\\n", self.line - 1, self.col)
            return

        # Indentation
        indent_level = 0
        while not self._at_end() and self.source[self.pos] in (" ", "\t"):
            ch = self._advance()
            indent_level += 1

        raw = self.source[line_start: self.pos]
        if "\t" in raw:
            indent_level = raw.count("\t")
        else:
            indent_level = len(raw) // 4

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

        if emitted_something and self.paren_depth == 0:
            self._emit(TokenType.NEWLINE, "\\n", self.line - 1, self.col)

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

        # Numbers
        if ch.isdigit():
            self._read_number(start_line, start_col)
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
            while not self._at_end() and (self.source[self.pos] in "0123456789abcdefABCDEF"):
                buf.append(self._advance())
            self._emit(TokenType.COLOR, "#" + "".join(buf), start_line, start_col)
            return

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            self._read_ident(start_line, start_col)
            return

        # Two-character operators (check first)
        two = self.source[self.pos: self.pos + 2] if self.pos + 1 < len(self.source) else ""
        if two == ":=":
            self._advance(); self._advance()
            self._emit(TokenType.COLON_EQUALS, ":=", start_line, start_col)
            return
        if two == "==":
            self._advance(); self._advance()
            self._emit(TokenType.EQEQ, "==", start_line, start_col)
            return
        if two == "!=":
            self._advance(); self._advance()
            self._emit(TokenType.NOTEQ, "!=", start_line, start_col)
            return
        if two == ">=":
            self._advance(); self._advance()
            self._emit(TokenType.GE, ">=", start_line, start_col)
            return
        if two == "<=":
            self._advance(); self._advance()
            self._emit(TokenType.LE, "<=", start_line, start_col)
            return
        if two == "=>":
            self._advance(); self._advance()
            self._emit(TokenType.FAT_ARROW, "=>", start_line, start_col)
            return
        if two == "+=":
            self._advance(); self._advance()
            self._emit(TokenType.PLUS_EQUALS, "+=", start_line, start_col)
            return
        if two == "-=":
            self._advance(); self._advance()
            self._emit(TokenType.MINUS_EQUALS, "-=", start_line, start_col)
            return
        if two == "*=":
            self._advance(); self._advance()
            self._emit(TokenType.STAR_EQUALS, "*=", start_line, start_col)
            return
        if two == "/=":
            self._advance(); self._advance()
            self._emit(TokenType.SLASH_EQUALS, "/=", start_line, start_col)
            return

        # Single-character operators
        singles = {
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
            self._emit(tt, ch, start_line, start_col)
            return

        # Unknown — skip
        self._advance()

    def _read_number(self, start_line: int, start_col: int) -> None:
        buf = []
        while not self._at_end() and (self.source[self.pos].isdigit() or self.source[self.pos] == "."):
            buf.append(self._advance())
        self._emit(TokenType.NUMBER, "".join(buf), start_line, start_col)

    def _read_string(self, start_line: int, start_col: int) -> None:
        self._advance()  # consume "
        buf = []
        while not self._at_end() and self.source[self.pos] != '"':
            if self.source[self.pos] == "\\" and self.pos + 1 < len(self.source):
                self._advance()  # skip backslash
            buf.append(self._advance())
        if not self._at_end():
            self._advance()  # consume closing "
        self._emit(TokenType.STRING, "".join(buf), start_line, start_col)

    def _read_string_single(self, start_line: int, start_col: int) -> None:
        self._advance()  # consume '
        buf = []
        while not self._at_end() and self.source[self.pos] != "'":
            if self.source[self.pos] == "\\" and self.pos + 1 < len(self.source):
                self._advance()
            buf.append(self._advance())
        if not self._at_end():
            self._advance()
        self._emit(TokenType.STRING, "".join(buf), start_line, start_col)

    def _read_ident(self, start_line: int, start_col: int) -> None:
        buf = []
        while not self._at_end() and (self.source[self.pos].isalnum() or self.source[self.pos] == "_"):
            buf.append(self._advance())
        word = "".join(buf)
        tt = KEYWORDS.get(word, TokenType.IDENT)
        self._emit(tt, word, start_line, start_col)
