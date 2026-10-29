"""Allowlist checker for the C++ codegen emits.

``check_generated_cpp(text)`` tokenizes a generated translation unit and
returns a :class:`Finding` for every construct the generator may not emit.
The scan works on tokens -- comments, ordinary and raw string literals (and
their delimiters), character literals, identifiers, preprocessor lines,
digraphs, trigraphs, line splices and NUL -- never on raw text, so text a
Pine script lands inside a literal or a comment is inert.

The allowed ``#include`` set is read from ``codegen/emit_top.py`` at import
time; the allowed ``#define`` lines and fixed ``#error`` texts are read from
``codegen/emit_top.py`` and ``codegen/run_stops.py``.  They therefore cannot
silently drift from the emitter.

Two generated constructs are allowed by exact shape (see ASSESSMENT.md
section 1f): the ``timestamp(...)`` and time-field timezone lambdas in
``codegen/visit_call.py`` and ``codegen/tables.py``.  Their environment
calls (``getenv``/``setenv``/``unsetenv``/``tzset``) are accepted only when
the whole fixed statement run matches the emitted template token for token.
Every other environment access is refused.

CLI: ``pf-cpp-allowlist [FILE ...]`` reads standard input when no file is
named and exits 1 when any finding is reported.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_EMIT_TOP = _PACKAGE_DIR / "codegen" / "emit_top.py"
_RUN_STOPS = _PACKAGE_DIR / "codegen" / "run_stops.py"

#: Reasons are fixed per rule: no token, file name or script text is ever
#: interpolated into a finding's message (ASSESSMENT.md section 3b).
REASONS: dict[str, str] = {
    "asm": "inline assembly is not allowed in generated C++",
    "include": "#include is not in the generated-C++ include allowlist",
    "pragma": "preprocessor pragmas are not allowed in generated C++",
    "define": "#define is outside codegen's fixed emitted macro set",
    "undef": "#undef is outside codegen's fixed emitted macro set",
    "directive": "preprocessor directive is not part of codegen's fixed emitted set",
    "extern-c": "extern is only allowed as the generated C export block",
    "process": "process or system-call primitive is not allowed in generated C++",
    "namespace": "namespace or mount primitive is not allowed in generated C++",
    "thread": "thread creation is not allowed in generated C++",
    "dlopen": "dynamic loading is not allowed in generated C++",
    "file-io": "file I/O is not allowed in generated C++",
    "filesystem-stream": "standard file or filesystem streams are not allowed in generated C++",
    "socket": "socket primitives are not allowed in generated C++",
    "env": "environment access is not allowed outside the sealed timezone templates",
    "function-pointer": "a function pointer to a libc function is not allowed in generated C++",
    "reinterpret-cast": "reinterpret_cast is not allowed in generated C++",
    "trigraph": "trigraphs are not allowed in generated C++",
    "digraph": "digraph tokens are not allowed in generated C++",
    "line-splice": "line splices are not allowed in generated C++",
    "nul": "NUL bytes are not allowed in generated C++",
    "escape": "a backslash outside a literal, comment or directive is not allowed",
    "non-ascii-identifier": "identifiers must be ASCII",
    "numeric-udl": "user-defined numeric literals are not allowed in generated C++",
    "numeric-separator": "digit separators are not allowed in generated C++",
    "raw-string": "raw string literals are not allowed in generated C++",
    "malformed-literal": "string or character literal is not terminated",
}


@dataclass(frozen=True)
class Finding:
    """One refused construct.  ``line`` and ``column`` are 1-based."""

    line: int
    column: int
    rule: str
    reason: str


def _normalise_pp(line: str) -> str:
    return " ".join(line.strip().split())


def _string_constants(path: Path) -> list[tuple[str, int]]:
    """String constants of a Python source file with their first line."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    return [
        (node.value, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _directive_lines(path: Path) -> list[tuple[str, int]]:
    """Normalized preprocessor lines spelled by one emitter module."""
    out: list[tuple[str, int]] = []
    for value, lineno in _string_constants(path):
        for line in value.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                out.append((_normalise_pp(stripped), lineno))
    return out


_INCLUDE_DIRECTIVE_RE = re.compile(r"^#\s*include\s*<([^>]+)>$")
_DEFINE_NAME_RE = re.compile(r"^#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")


def _load_policy() -> tuple[
    frozenset[str], frozenset[str], frozenset[str], frozenset[str]
]:
    emit = _directive_lines(_EMIT_TOP)
    stops = _directive_lines(_RUN_STOPS)

    includes: set[str] = set()
    for line, _lineno in emit:
        match = _INCLUDE_DIRECTIVE_RE.match(line)
        if match:
            includes.add(match.group(1))

    define_lines: set[str] = set()
    for line, _lineno in emit + stops:
        if line.startswith("#define "):
            define_lines.add(line)
    define_names: set[str] = set()
    for line in define_lines:
        match = _DEFINE_NAME_RE.match(line)
        if match:
            define_names.add(match.group(1))

    error_lines = frozenset(
        line for line, _lineno in emit if line.startswith("#error ")
    )
    return (
        frozenset(includes),
        frozenset(define_lines),
        frozenset(define_names),
        error_lines,
    )


(
    ALLOWED_INCLUDES,
    _ALLOWED_DEFINE_LINES,
    _ALLOWED_DEFINE_NAMES,
    _ALLOWED_ERROR_LINES,
) = _load_policy()

# Handled as fixed shapes, never by a guessed allowlist.
_ALLOWED_CONTROL_DIRECTIVES = frozenset(
    {"if", "ifdef", "ifndef", "elif", "else", "endif"}
)

# Identifier-level refusals.  A member name (``bar.open``) is never a libc
# call, so member accesses are exempt below.
_ASM_NAMES = frozenset({"asm", "__asm", "__asm__"})
_PROCESS_NAMES = frozenset(
    {
        "system", "popen", "pclose", "fork", "vfork", "posix_spawn",
        "posix_spawnp", "syscall", "mmap", "mprotect", "ptrace",
    }
)
_EXEC_NAMES = frozenset(
    {
        "exec", "execl", "execle", "execlp", "execv", "execve", "execvp",
        "execvpe", "execveat",
    }
)
_NAMESPACE_NAMES = frozenset(
    {
        "unshare", "setns", "clone", "clone3", "mount", "umount", "umount2",
        "pivot_root", "chroot", "prctl", "seccomp",
    }
)
_THREAD_NAMES = frozenset(
    {"pthread_create", "thrd_create", "__clone", "thread", "jthread", "async"}
)
_DL_NAMES = frozenset({"dlopen", "dlsym", "dlclose"})
_FILE_IO_NAMES = frozenset(
    {"fopen", "freopen", "fdopen", "open", "openat"}
)
_SOCKET_NAMES = frozenset(
    {"socket", "connect", "bind", "listen", "accept", "getaddrinfo"}
)
_ENV_NAMES = frozenset(
    {
        "getenv", "setenv", "unsetenv", "tzset", "putenv",
        "secure_getenv", "__secure_getenv",
    }
)
_STREAM_NAMES = frozenset(
    {"filesystem", "fstream", "ifstream", "ofstream"}
)

# Taking the address of one of these is a function pointer to libc even when
# the bare name alone is not otherwise refused (``&malloc``).
_LIBC_ADDRESS_NAMES = frozenset(
    {
        "malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset",
        "tzset", "getenv", "setenv", "unsetenv", "putenv", "secure_getenv",
        "__secure_getenv", "system",
        "popen", "pclose", "fork", "vfork", "posix_spawn", "posix_spawnp",
        "exec", "execl", "execle", "execlp", "execv", "execve", "execvp",
        "execvpe", "execveat", "dlopen", "dlsym", "dlclose", "syscall",
        "socket", "connect", "bind", "listen", "accept", "getaddrinfo",
        "open", "openat", "fopen", "freopen", "fdopen", "fclose", "mmap",
        "mprotect", "ptrace",
        "unshare", "setns", "clone", "clone3", "mount", "umount", "umount2",
        "pivot_root", "chroot", "prctl", "seccomp",
        "pthread_create", "thrd_create", "__clone",
    }
)

# Bare occurrences of the memory primitives are a function pointer through
# C++'s function-to-pointer decay (``auto p = malloc;``); every other libc
# name above is already refused wherever it appears by its own rule.
_LIBC_DECAY_NAMES = frozenset(
    {"malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset"}
)

# The two sealed timezone statement runs (ASSESSMENT.md section 1f).  They
# start at the non-UTC ``else`` branch and are matched token for token
# against the strings in ``codegen/tables.py`` (``_TZ_TIME_FIELD_BODY`` and
# ``TIMESTAMP_CALENDAR_BODY``). Only identifiers inside a matched run may
# name the environment calls.
_SEALED_TZ_FIELD_RUN = (
    "else", "{", "static", "std", ":", ":", "mutex", "_pf_tz_mu", ";",
    "std", ":", ":", "lock_guard", "<", "std", ":", ":", "mutex", ">",
    "_pf_tz_lock", "(", "_pf_tz_mu", ")", ";",
    "const", "char", "*", "_old", "=", "std", ":", ":", "getenv", "(",
    '"TZ"', ")", ";",
    "std", ":", ":", "string", "_old_tz", "=", "_old", "?", "_old", ":",
    '""', ";",
    "bool", "_had_old", "=", "(", "_old", "!", "=", "nullptr", ")", ";",
    ":", ":", "setenv", "(", '"TZ"', ",", "_tz", ".", "c_str", "(",
    ")", ",", "1", ")", ";",
    ":", ":", "tzset", "(", ")", ";",
    "localtime_r", "(", "&", "_secs", ",", "&", "tm_buf", ")", ";",
    "if", "(", "_had_old", ")", "{",
    ":", ":", "setenv", "(", '"TZ"', ",", "_old_tz", ".", "c_str", "(",
    ")", ",", "1", ")", ";", "}",
    "else", "{", ":", ":", "unsetenv", "(", '"TZ"', ")", ";", "}",
    ":", ":", "tzset", "(", ")", ";",
    "}",
)

_SEALED_TZ_CALENDAR_RUN = (
    "else", "{", "static", "std", ":", ":", "mutex", "_pf_ts_mu", ";",
    "std", ":", ":", "lock_guard", "<", "std", ":", ":", "mutex", ">",
    "_pf_ts_mu_lock", "(", "_pf_ts_mu", ")", ";",
    "const", "char", "*", "_old", "=", "std", ":", ":", "getenv", "(",
    '"TZ"', ")", ";",
    "std", ":", ":", "string", "_old_tz", "=", "_old", "?", "_old", ":",
    '""', ";",
    "bool", "_had_old", "=", "(", "_old", "!", "=", "nullptr", ")", ";",
    ":", ":", "setenv", "(", '"TZ"', ",", "_tz", ".", "c_str", "(",
    ")", ",", "1", ")", ";",
    ":", ":", "tzset", "(", ")", ";",
    "_res", "=", "(", "int64_t", ")", "mktime", "(", "&", "t", ")", "*",
    "1000", ";",
    "if", "(", "_had_old", ")", "{",
    ":", ":", "setenv", "(", '"TZ"', ",", "_old_tz", ".", "c_str", "(",
    ")", ",", "1", ")", ";", "}",
    "else", "{", ":", ":", "unsetenv", "(", '"TZ"', ")", ";", "}",
    ":", ":", "tzset", "(", ")", ";",
    "}",
)

_SEALED_TZ_RUNS = (_SEALED_TZ_FIELD_RUN, _SEALED_TZ_CALENDAR_RUN)

_NUMERIC_UDL_RE = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)"
    r"(?:[eEpP][+-]?\d+)?)(_[A-Za-z_]\w*)$"
)

_RAW_PREFIXES = ("u8R\"", "uR\"", "UR\"", "LR\"", "R\"")
_QUOTED_PREFIXES = (
    ("u8\"", '"'), ("u\"", '"'), ("U\"", '"'), ("L\"", '"'), ('"', '"'),
    ("u8'", "'"), ("u'", "'"), ("U'", "'"), ("L'", "'"), ("'", "'"),
)

_TRIGRAPH_FOLLOWERS = frozenset("=/'()!<>-")
_DIGRAPHS = ("%:%:", "<:", ":>", "<%", "%>", "%:", "##")


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    line: int
    column: int


class _Tokenizer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        self.line = 1
        self.column = 1
        self.bol = True
        self.tokens: list[_Token] = []
        self.findings: list[Finding] = []

    # -- location helpers -------------------------------------------------
    def _advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self.i >= self.n:
                return
            ch = self.text[self.i]
            self.i += 1
            if ch == "\n":
                self.line += 1
                self.column = 1
                self.bol = True
            else:
                self.column += 1

    def _finding(self, rule: str, line: int, column: int) -> None:
        self.findings.append(Finding(line, column, rule, REASONS[rule]))

    def _splice_len(self, at: int) -> int:
        text = self.text
        if at >= self.n or text[at] != "\\":
            return 0
        if at + 1 < self.n and text[at + 1] == "\n":
            return 2
        if (at + 2 < self.n and text[at + 1] == "\r"
                and text[at + 2] == "\n"):
            return 3
        if at + 1 < self.n and text[at + 1] == "\r":
            return 2
        return 0

    def _skip_splice(self, length: int) -> None:
        self._finding("line-splice", self.line, self.column)
        # A splice joins two physical lines: the logical line continues, so
        # ``bol`` is deliberately left unchanged.
        self.i += length
        self.line += 1
        self.column = 1

    # -- character classes ------------------------------------------------
    @staticmethod
    def _is_ident_start(ch: str) -> bool:
        return ch == "_" or ch.isalpha() or ord(ch) >= 128

    @staticmethod
    def _is_ident_char(ch: str) -> bool:
        return ch == "_" or ch.isalnum() or ord(ch) >= 128

    # -- sub-scanners -----------------------------------------------------
    def _scan_line_comment(self) -> None:
        self._advance(2)
        while self.i < self.n:
            ch = self.text[self.i]
            if ch == "\n":
                return
            splice = self._splice_len(self.i)
            if splice:
                self._skip_splice(splice)
                continue
            if ch == "\x00":
                self._finding("nul", self.line, self.column)
                self._advance()
                continue
            self._advance()

    def _scan_block_comment(self) -> None:
        self._advance(2)
        while self.i < self.n:
            if self.text.startswith("*/", self.i):
                self._advance(2)
                return
            splice = self._splice_len(self.i)
            if splice:
                self._skip_splice(splice)
                continue
            ch = self.text[self.i]
            if ch == "\x00":
                self._finding("nul", self.line, self.column)
                self._advance()
                continue
            self._advance()

    def _scan_directive(self) -> None:
        start_line, start_column = self.line, self.column
        parts: list[str] = []
        while self.i < self.n:
            ch = self.text[self.i]
            if ch == "\n":
                break
            splice = self._splice_len(self.i)
            if splice:
                self._skip_splice(splice)
                parts.append(" ")
                continue
            if ch == "\x00":
                self._finding("nul", self.line, self.column)
                self._advance()
                continue
            parts.append(ch)
            self._advance()
        line_text = "".join(parts)
        self.tokens.append(
            _Token("directive", line_text, start_line, start_column)
        )
        self._check_directive(line_text, start_line, start_column)

    def _check_directive(self, line_text: str, line: int, column: int) -> None:
        normalised = _normalise_pp(line_text)
        match = re.match(
            r"#\s*([A-Za-z_][A-Za-z0-9_]*)(.*)$", line_text, re.S
        )
        if not match:
            self._finding("directive", line, column)
            return
        name = match.group(1)
        rest = match.group(2)

        if name == "include":
            stripped = rest.strip()
            if not stripped.startswith("<"):
                self._finding("include", line, column)
                return
            close = stripped.find(">")
            if close < 0:
                self._finding("include", line, column)
                return
            header = stripped[1:close]
            if header not in ALLOWED_INCLUDES:
                self._finding("include", line, column)
        elif name == "define":
            if normalised not in _ALLOWED_DEFINE_LINES:
                self._finding("define", line, column)
        elif name == "undef":
            undef = re.match(
                r"#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)", line_text
            )
            if undef is None or undef.group(1) not in _ALLOWED_DEFINE_NAMES:
                self._finding("undef", line, column)
        elif name == "error":
            if normalised not in _ALLOWED_ERROR_LINES:
                self._finding("directive", line, column)
        elif name == "pragma":
            self._finding("pragma", line, column)
        elif name in _ALLOWED_CONTROL_DIRECTIVES:
            pass
        else:
            self._finding("directive", line, column)

    def _string_prefix(self) -> tuple[int, str, bool] | None:
        for prefix in _RAW_PREFIXES:
            if self.text.startswith(prefix, self.i):
                if self.i == 0 or not self._is_ident_char(self.text[self.i - 1]):
                    return len(prefix), '"', True
        for prefix, quote in _QUOTED_PREFIXES:
            if self.text.startswith(prefix, self.i):
                if len(prefix) == 1:
                    return 1, quote, False
                if self.i == 0 or not self._is_ident_char(self.text[self.i - 1]):
                    return len(prefix), quote, False
        return None

    def _scan_literal(self, prefix: tuple[int, str, bool]) -> None:
        start = self.i
        start_line, start_column = self.line, self.column
        length, quote, is_raw = prefix
        self._advance(length)

        if is_raw:
            self._finding("raw-string", start_line, start_column)
            delimiter: list[str] = []
            valid = True
            while self.i < self.n:
                ch = self.text[self.i]
                if ch == "(":
                    self._advance()
                    break
                if len(delimiter) >= 16 or ch in " ()\\\t\r\n\v\f":
                    valid = False
                    break
                delimiter.append(ch)
                self._advance()
            else:
                valid = False
            if not valid:
                self._finding("malformed-literal", start_line, start_column)
                # A broken raw literal is refused already; consume to EOF so
                # its contents are not re-read as code.
                while self.i < self.n:
                    if self.text[self.i] == "\x00":
                        self._finding("nul", self.line, self.column)
                    self._advance()
            else:
                closing = ")" + "".join(delimiter) + '"'
                found = self.text.find(closing, self.i)
                if found < 0:
                    self._finding("malformed-literal", start_line, start_column)
                    while self.i < self.n:
                        if self.text[self.i] == "\x00":
                            self._finding("nul", self.line, self.column)
                        self._advance()
                else:
                    end = found + len(closing)
                    while self.i < end:
                        if self.text[self.i] == "\x00":
                            self._finding("nul", self.line, self.column)
                        self._advance()
            self.tokens.append(
                _Token("string", self.text[start:self.i],
                       start_line, start_column)
            )
            self.bol = False
            return

        kind = "string" if quote == '"' else "char"
        while self.i < self.n:
            ch = self.text[self.i]
            if ch == "\\":
                splice = self._splice_len(self.i)
                if splice:
                    self._skip_splice(splice)
                    continue
                if self.i + 1 < self.n and self.text[self.i + 1] == "\x00":
                    self._finding("nul", self.line, self.column + 1)
                self._advance(2)
                continue
            if ch == "\x00":
                self._finding("nul", self.line, self.column)
                self._advance()
                continue
            if ch == "\n":
                self._finding("malformed-literal", start_line, start_column)
                break
            self._advance()
            if ch == quote:
                break
        self.tokens.append(
            _Token(kind, self.text[start:self.i], start_line, start_column)
        )
        self.bol = False

    def _scan_identifier(self) -> None:
        start_line, start_column = self.line, self.column
        start = self.i
        while self.i < self.n and self._is_ident_char(self.text[self.i]):
            self._advance()
        word = self.text[start:self.i]
        for offset, ch in enumerate(word):
            if ord(ch) > 127:
                self._finding(
                    "non-ascii-identifier",
                    start_line,
                    start_column + offset,
                )
                break
        self.tokens.append(_Token("id", word, start_line, start_column))
        self.bol = False

    def _scan_number(self) -> None:
        start_line, start_column = self.line, self.column
        start = self.i
        while self.i < self.n:
            ch = self.text[self.i]
            if ch == "'":
                # C++14 digit separator ([lex.ppnumber]): consume it as part
                # of the pp-number, never as a character literal, so text
                # after it cannot be hidden from the identifier scan.
                following = (
                    self.text[self.i + 1] if self.i + 1 < self.n else ""
                )
                if (following and (
                    following.isalnum() or following == "_"
                    or ord(following) >= 128
                )):
                    self._finding("numeric-separator", self.line, self.column)
                    self._advance()
                    continue
                break
            if ch.isalnum() or ch in "_.":
                self._advance()
                continue
            if (ch in "+-" and self.i > start
                    and self.text[self.i - 1] in "eEpP"):
                self._advance()
                continue
            break
        word = self.text[start:self.i]
        if _NUMERIC_UDL_RE.match(word):
            self._finding("numeric-udl", start_line, start_column)
        self.tokens.append(_Token("number", word, start_line, start_column))
        self.bol = False

    def _digraph_length(self) -> int:
        # [lex.pptoken] max-munch exception: ``<::`` is an ordinary ``<``
        # followed by ``::`` unless the character after ``<::`` is another
        # ``:`` or a ``>``.  Codegen emits that sequence for qualified type
        # members (``std::vector<::pineforge::checked_settings::Setting>``).
        if self.text.startswith("<::", self.i):
            after = self.text[self.i + 3] if self.i + 3 < self.n else ""
            if after not in (":", ">"):
                return 0
        for digraph in _DIGRAPHS:
            if self.text.startswith(digraph, self.i):
                return len(digraph)
        return 0

    # -- main loop --------------------------------------------------------
    def tokenize(self) -> list[Finding]:
        text = self.text
        while self.i < self.n:
            start_line, start_column = self.line, self.column
            ch = text[self.i]

            if ch == "\x00":
                self._finding("nul", start_line, start_column)
                self._advance()
                self.bol = False
                continue

            splice = self._splice_len(self.i)
            if splice:
                self._skip_splice(splice)
                continue
            if ch == "\\":
                # A universal-character-name can spell a non-ASCII identifier
                # without a non-ASCII byte; no generated code holds a bare
                # backslash outside a literal, comment or directive.
                self._finding("escape", start_line, start_column)
                self._advance()
                self.bol = False
                continue

            if ch in " \t\v\f\r":
                self._advance()
                continue
            if ch == "\n":
                self._advance()
                continue

            if ch == "#" and self.bol:
                self._scan_directive()
                continue
            if text.startswith("//", self.i):
                self._scan_line_comment()
                continue
            if text.startswith("/*", self.i):
                self._scan_block_comment()
                continue

            prefix = self._string_prefix()
            if prefix is not None:
                self._scan_literal(prefix)
                continue

            if (ch == "?" and self.i + 2 < self.n
                    and text[self.i + 1] == "?"
                    and text[self.i + 2] in _TRIGRAPH_FOLLOWERS):
                self._finding("trigraph", start_line, start_column)
                self._advance(3)
                self.bol = False
                continue

            digraph_len = self._digraph_length()
            if digraph_len:
                self._finding("digraph", start_line, start_column)
                self._advance(digraph_len)
                self.bol = False
                continue

            if self._is_ident_start(ch):
                self._scan_identifier()
                continue
            if ch.isdigit() or (
                ch == "." and self.i + 1 < self.n
                and text[self.i + 1].isdigit()
            ):
                self._scan_number()
                continue

            self.tokens.append(_Token("punct", ch, start_line, start_column))
            self._advance()
            self.bol = False

        self._check_tokens()
        self.findings.sort(key=lambda f: (f.line, f.column, f.rule))
        return self.findings

    # -- whole-token checks ----------------------------------------------
    def _sealed_tz_tokens(self) -> set[int]:
        texts = [token.text for token in self.tokens]
        sealed: set[int] = set()
        for pattern in _SEALED_TZ_RUNS:
            length = len(pattern)
            for start in range(0, len(texts) - length + 1):
                if texts[start:start + length] == list(pattern):
                    sealed.update(range(start, start + length))
        return sealed

    @staticmethod
    def _takes_address(tokens: list[_Token], index: int) -> bool:
        """``&name``, ``&::name`` or ``&std::name`` (not a reference type)."""
        before = index - 1
        while before >= 0 and tokens[before].text == ":":
            before -= 1
        if before >= 0 and tokens[before].text == "std":
            before -= 1
        return before >= 0 and tokens[before].text == "&"

    def _check_tokens(self) -> None:
        sealed = self._sealed_tz_tokens()
        extern_block_seen = False
        tokens = self.tokens

        for index, token in enumerate(tokens):
            if token.kind != "id":
                continue
            name = token.text
            previous = tokens[index - 1].text if index else ""
            before_previous = tokens[index - 2].text if index >= 2 else ""

            if name == "extern":
                following = tokens[index + 1] if index + 1 < len(tokens) else None
                opening = tokens[index + 2] if index + 2 < len(tokens) else None
                if (not extern_block_seen
                        and following is not None
                        and following.kind == "string"
                        and following.text == '"C"'
                        and opening is not None
                        and opening.text == "{"):
                    extern_block_seen = True
                    continue
                self._finding("extern-c", token.line, token.column)
                continue

            # A member access (``bar.open``, ``ptr->open``) names an object's
            # member, never the libc function of the same spelling.
            if previous == "." or (previous == ">" and before_previous == "-"):
                continue

            following = tokens[index + 1].text if index + 1 < len(tokens) else ""
            if name in _LIBC_ADDRESS_NAMES and (
                self._takes_address(tokens, index)
                or (name in _LIBC_DECAY_NAMES and following != "(")
            ):
                self._finding("function-pointer", token.line, token.column)
                continue

            if name in _ASM_NAMES:
                self._finding("asm", token.line, token.column)
            elif name == "_Pragma":
                self._finding("pragma", token.line, token.column)
            elif name == "reinterpret_cast":
                self._finding("reinterpret-cast", token.line, token.column)
            elif name in _PROCESS_NAMES or name in _EXEC_NAMES:
                self._finding("process", token.line, token.column)
            elif name in _NAMESPACE_NAMES:
                self._finding("namespace", token.line, token.column)
            elif name in _THREAD_NAMES:
                self._finding("thread", token.line, token.column)
            elif name in _DL_NAMES:
                self._finding("dlopen", token.line, token.column)
            elif name in _FILE_IO_NAMES:
                self._finding("file-io", token.line, token.column)
            elif name in _SOCKET_NAMES:
                self._finding("socket", token.line, token.column)
            elif name in _STREAM_NAMES:
                self._finding("filesystem-stream", token.line, token.column)
            elif name in _ENV_NAMES and index not in sealed:
                self._finding("env", token.line, token.column)


def check_generated_cpp(text: str) -> list[Finding]:
    """Return every refused construct in generated C++ ``text``.

    An empty list means the translation unit contains only constructs the
    allowlist permits.
    """
    return _Tokenizer(text).tokenize()


def main(argv: list[str] | None = None) -> int:
    """``pf-cpp-allowlist [FILE ...]``; standard input when none is named."""
    parser = argparse.ArgumentParser(
        prog="pf-cpp-allowlist",
        description="Check generated C++ against the codegen allowlist.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="generated .cpp files (default: standard input)",
    )
    args = parser.parse_args(argv)
    paths = args.files or ["-"]
    failed = False
    for path in paths:
        if path == "-":
            text = sys.stdin.read()
            label = "<stdin>"
        else:
            text = Path(path).read_text(encoding="utf-8")
            label = path
        findings = check_generated_cpp(text)
        for finding in findings:
            print(
                f"{label}:{finding.line}:{finding.column}: "
                f"{finding.rule}: {finding.reason}"
            )
        if findings:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
