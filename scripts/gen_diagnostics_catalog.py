#!/usr/bin/env python3
"""Find every diagnostic template the transpiler spells, and keep
``pineforge_codegen/diagnostics_catalog.json`` in step with them.

The emitters spell their English text in place (f-strings, string tables,
helper arguments). This script reads the package's source with ``ast`` and
turns each spelling into an ICU MessageFormat template:

* the message and hint arguments of every diagnostic emitter
  (``_err``/``_warn``/``_error``/``_codegen_error``/``_codegen_warning``/
  ``Diagnostic(...)``/the exception types the transpiler converts, ...);
* a placeholder that is a constant (``chr(34) * 3``) becomes its text;
* a placeholder that looks a key up in a module-level table
  (``HARD_REJECT_FUNC[full]``) becomes one template per distinct value;
* a placeholder (or a whole message) naming a local the function assigns
  string spellings to, or a parameter its callers pass string spellings for,
  becomes one template per spelling;
* every other placeholder becomes a named argument.

``--write`` adds a code for every template the catalog lacks (the next free
number of its area and severity) and never renumbers or removes one; the
default ``--check`` exits 1 naming each template without a code.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "pineforge_codegen"
sys.path.insert(0, str(ROOT))

from pineforge_codegen.diagnostic_codes import (  # noqa: E402
    CATALOG_PATH, CATALOG_SCHEMA, UNCATALOGUED, escape_literal,
)

ERR, WARN = "error", "warning"
MAX_VARIANTS = 48

# Area digit of a code, by the module that spells the template.
AREAS = [
    ("lexer.py", 0), ("parser.py", 0), ("limits.py", 0), ("pragmas.py", 0),
    ("support_checker.py", 1), ("external_requests.py", 1),
    ("analyzer/", 2), ("method_binding.py", 2), ("block_locals.py", 2),
    ("finite_ta_length.py", 7), ("security_contexts.py", 3), ("codegen/security.py", 3),
    ("library_inline.py", 4), ("library_modules.py", 4), ("library_v5.py", 4),
    ("pine_libraries.py", 4),
    ("collection_history.py", 6), ("codegen/collection_history.py", 6),
    ("codegen/ta.py", 7),
    ("codegen/", 5),
]
AREA_NAMES = {
    0: "source", 1: "support", 2: "analysis", 3: "request-security", 4: "libraries",
    5: "codegen", 6: "collection-history", 7: "ta",
}
# The support checker reports what it refuses inside a switch arm as a
# warning (``SupportChecker._err``): PF-W1nnn (nnn < 500) is PF-E1nnn there.
SWITCH_ARM_TWIN_LIMIT = 500


def area_of(path: str) -> int:
    rel = path.replace("\\", "/").split("pineforge_codegen/", 1)[-1]
    best = None
    for suffix, area in AREAS:
        if rel == suffix or rel.endswith("/" + suffix) or (suffix.endswith("/") and rel.startswith(suffix)):
            if best is None or len(suffix) > len(best[0]):
                best = (suffix, area)
        elif rel.endswith(suffix) and suffix.endswith(".py"):
            if best is None or len(suffix) > len(best[0]):
                best = (suffix, area)
    return best[1] if best else 5


# callee -> (severity, message position, hint position); a position is an
# index of a positional argument, its keyword being "message" / "hint".
def emitter(name: str, path: str):
    table = {
        "_err": (ERR, 1, 2), "_codegen_error": (ERR, 1, 2), "_codegen_warning": (WARN, 1, 2),
        "_codegen_error_diagnostic": (ERR, 1, 2), "_reject": (ERR, 1, 2),
        "_feed_warning": (WARN, 2, 3), "pass_warning": (WARN, 1, 2),
        "limit_error": (ERR, 0, None), "_module_error": (ERR, 1, None),
        "_emit_diagnostic": (ERR, 0, 4), "LibraryResolveError": (ERR, 0, None),
        "MethodBindError": (ERR, 0, None), "ParseError": (ERR, 0, None),
        "_refused_session_read": (ERR, 2, None),
    }
    if name in table:
        return table[name]
    if name == "_warn":
        return (WARN, 1, 2) if path.endswith("support_checker.py") else (WARN, 0, None)
    if name == "_error":
        if "analyzer/" in path or path.endswith("library_inline.py"):
            return (ERR, 0, None)
        return (ERR, 1, None)
    if name == "refuse" and path.endswith("security_contexts.py"):
        return (ERR, 1, None)
    if name == "Diagnostic":
        return ("level", None, None)
    if name == "Decision":
        return (ERR, None, None)
    if name == "_reject_if_in" and path.endswith("support_checker.py"):
        return (ERR, None, None)
    if name == "Use" and path.endswith("collection_history.py"):
        # A use the collection-history checker refuses carries its text;
        # ``Decision(REFUSED, message=...)`` raises it.
        return (ERR, 2, 3)
    return None


def _fixed_hint(mod, name: str, depth: int = 0):
    """The constant hint an emitter defined in ``mod`` always passes
    (``security_contexts._error``, and ``refuse`` through it)."""
    if depth > 2:
        return None
    for owner, func in FUNCS.get(name, ()):
        if owner is not mod:
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            callee = callee_name(node)
            if callee == "Diagnostic":
                hint = call_arg(node, None, "hint")
                if isinstance(hint, ast.Constant) and isinstance(hint.value, str):
                    return hint
            elif callee and callee != name and any(o is mod for o, _ in FUNCS.get(callee, ())):
                found = _fixed_hint(mod, callee, depth + 1)
                if found is not None:
                    return found
    return None


class _Substituted(ast.NodeTransformer):
    def __init__(self, mapping: dict):
        self.mapping = mapping

    def visit_Name(self, node):
        return self.mapping.get(node.id, node)

    def visit_Subscript(self, node):
        if isinstance(node.value, ast.Name) and node.value.id in self.mapping \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            target = self.mapping[node.value.id]
            if isinstance(target, ast.Tuple):
                return target.elts[node.slice.value]
        return self.generic_visit(node)


def _reject_if_in_message(mod, call):
    """``_reject_if_in(table, key, node, lambda k, v: f"...", hint=...)``: the
    lambda's text with ``k`` the key's spelling and ``v`` the table's value."""
    table, key, fmt = call.args[0], call.args[1], call.args[3]
    hint = call_arg(call, 4, "hint")
    k_name, v_name = fmt.args.args[0].arg, fmt.args.args[1].arg
    value = ast.Subscript(value=table, slice=key, ctx=ast.Load())
    body = _Substituted({k_name: key, v_name: value}).visit(
        ast.parse(ast.unparse(fmt.body), mode="eval").body)
    # Spell the substituted body back into the module so placeholders name
    # the key's own expression and the table lookup expands.
    src = ast.unparse(body)
    mod.src_extra = getattr(mod, "src_extra", {})
    expr = ast.parse(src, mode="eval").body
    for sub in ast.walk(expr):
        sub.lineno = getattr(call, "lineno", 1)
    _SYNTH_KEEP.append(expr)
    _SYNTH[id(expr)] = src
    for sub in ast.walk(expr):
        if sub is not expr and hasattr(sub, "col_offset"):
            _SYNTH[id(sub)] = ast.unparse(sub)
    return expr, hint


_SYNTH: dict = {}
_SYNTH_KEEP: list = []


class Arg:
    """A placeholder: an argument named from its expression."""

    def __init__(self, src: str, quoted: bool = False):
        self.src = src
        self.quoted = quoted

    def __repr__(self) -> str:
        return f"Arg({self.src!r})"


@dataclass
class Template:
    severity: str
    message: list
    hint: list | None
    path: str
    line: int
    sites: list = field(default_factory=list)


class Module:
    def __init__(self, path: Path):
        self.path = path
        self.rel = str(path.relative_to(ROOT))
        self.src = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)
        self.parents: dict = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        mod_name = self.rel[:-3].replace("/", ".")
        try:
            self.module = importlib.import_module(mod_name)
        except Exception:  # pragma: no cover - every module imports
            self.module = None

    def seg(self, node) -> str:
        if id(node) in _SYNTH:
            return _SYNTH[id(node)]
        return ast.get_source_segment(self.src, node) or ""

    def enclosing(self, node):
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return node
        return None


MODULES: dict[str, Module] = {}
FUNCS: dict[str, list] = {}   # function name -> [(module, FunctionDef)]
CALLS: dict[str, list] = {}   # callee name -> [(module, Call)]


def load_modules() -> None:
    for path in sorted(PKG.rglob("*.py")):
        mod = Module(path)
        MODULES[mod.rel] = mod
        for node in ast.walk(mod.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                FUNCS.setdefault(node.name, []).append((mod, node))
            elif isinstance(node, ast.Call):
                name = callee_name(node)
                if name:
                    CALLS.setdefault(name, []).append((mod, node))


def callee_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def call_arg(call: ast.Call, pos, kw):
    if isinstance(pos, int) and pos < len(call.args):
        return call.args[pos]
    for keyword in call.keywords:
        if keyword.arg == kw:
            return keyword.value
    return None


def _const_value(node):
    """The value of a constant spelling (``chr(34) * 3``), else raise."""
    allowed = (ast.Constant, ast.BinOp, ast.Call, ast.Name, ast.Load, ast.Mult, ast.Add)
    for sub in ast.walk(node):
        if not isinstance(sub, allowed):
            raise ValueError
        if isinstance(sub, ast.Name) and sub.id != "chr":
            raise ValueError
        if isinstance(sub, ast.Call) and not (isinstance(sub.func, ast.Name) and sub.func.id == "chr"):
            raise ValueError
    value = eval(compile(ast.Expression(node), "<const>", "eval"), {"chr": chr})  # noqa: S307
    if not isinstance(value, str):
        raise ValueError
    return value


def _assigned_values(func, name: str) -> list | None:
    values = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    values.append(node.value)
                elif isinstance(target, (ast.Tuple, ast.List)) and any(
                        isinstance(e, ast.Name) and e.id == name for e in target.elts):
                    return None
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name and node.value is not None:
            values.append(node.value)
        elif isinstance(node, (ast.AugAssign,)) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return None
        elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return None
        elif isinstance(node, ast.NamedExpr) and node.target.id == name:
            return None
    return values


def _param_index(func, name: str):
    args = func.args
    params = [a.arg for a in args.posonlyargs + args.args]
    if name in params:
        return params.index(name)
    if name in [a.arg for a in args.kwonlyargs]:
        return name
    return None


def _caller_values(mod: Module, func, name: str) -> list | None:
    """The spellings every call of ``func`` passes for parameter ``name``."""
    index = _param_index(func, name)
    if index is None:
        return None
    is_method = bool(func.args.args) and func.args.args[0].arg == "self"
    values = []
    found = False
    for other, node in CALLS.get(func.name, ()):
        if True:
            if func.name in ("refuse",) and other is not mod:
                continue
            found = True
            pos = index
            if isinstance(pos, int) and is_method and isinstance(node.func, ast.Attribute):
                pos -= 1
            value = call_arg(node, pos if isinstance(pos, int) else None, name)
            if value is None:
                default = _default(func, name)
                if default is None:
                    return None
                value = default
            values.append((other, value))
    return values if found else None


def _default(func, name: str):
    args = func.args
    positional = args.posonlyargs + args.args
    defaults = args.defaults
    offset = len(positional) - len(defaults)
    for i, a in enumerate(positional):
        if a.arg == name and i >= offset:
            return defaults[i - offset]
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if a.arg == name:
            return d
    return None


def _table_values(mod: Module, node: ast.Subscript) -> list | None:
    if not isinstance(node.value, ast.Name) or mod.module is None:
        return None
    table = getattr(mod.module, node.value.id, None)
    if not isinstance(table, dict):
        return None
    if not table:
        return []   # an empty table: the lookup never yields a text
    values = sorted({v for v in table.values()})
    if not all(isinstance(v, str) for v in values):
        return None
    return values


class Flattener:
    """Turn a string expression into variants: lists of str | Arg."""

    def __init__(self, mod: Module, origin, depth: int = 0):
        self.mod = mod
        self.origin = origin
        self.depth = depth

    def flatten(self, node) -> list[list]:
        mod = self.mod
        if node is None:
            return [None]
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return [[node.value]]
            if node.value is None:
                return [None]
            return [[Arg(mod.seg(node))]]
        if isinstance(node, ast.JoinedStr):
            variants = [[]]
            for value in node.values:
                if isinstance(value, ast.Constant):
                    options = [[value.value]]
                else:
                    options = self.placeholder(value)
                variants = [a + b for a in variants for b in options]
                variants = variants[:MAX_VARIANTS * 4]
            return variants
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.flatten(node.left), self.flatten(node.right)
            return [a + b for a in left for b in right if a is not None and b is not None]
        if isinstance(node, ast.IfExp):
            return self.flatten(node.body) + self.flatten(node.orelse)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            out = []
            for value in node.values:
                out += [v for v in self.flatten(value) if v is not None]
            return out
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "strip" and not node.args:
            return [_strip(v) for v in self.flatten(node.func.value)]
        resolved = self.resolve(node)
        if resolved is not None:
            return resolved
        return [[Arg(mod.seg(node))]]

    def placeholder(self, value: ast.FormattedValue) -> list[list]:
        mod = self.mod
        inner = value.value
        if value.format_spec is not None:
            return [[Arg(mod.seg(value))]]
        if value.conversion == ord("r"):
            resolved = self.resolve(inner, quoted=True)
            if resolved is not None:
                return resolved
            return [[Arg(mod.seg(inner), quoted=True)]]
        if isinstance(inner, ast.IfExp):
            options = self.flatten(inner.body) + self.flatten(inner.orelse)
            if all(o is not None for o in options):
                return options
        if isinstance(inner, (ast.JoinedStr, ast.BinOp)):
            options = self.flatten(inner)
            if all(o is not None for o in options):
                return options
        resolved = self.resolve(inner)
        if resolved is not None:
            return resolved
        return [[Arg(mod.seg(inner))]]

    def resolve(self, node, quoted: bool = False) -> list[list] | None:
        """Constant, table, local or caller spellings of ``node``, or None."""
        mod = self.mod
        if quoted:
            return None
        try:
            return [[_const_value(node)]]
        except (ValueError, SyntaxError, TypeError, NameError):
            pass
        if self.depth > 3:
            return None
        if isinstance(node, ast.Subscript):
            values = _table_values(mod, node)
            if values is not None and len(values) <= MAX_VARIANTS:
                return [[v] for v in values]
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" \
                and len(node.args) == 1:
            return self.resolve(node.args[0])
        if isinstance(node, ast.Call):
            returned = _returned_texts(callee_name(node), self.origin, self.depth)
            if returned is not None:
                return returned
            return None
        if isinstance(node, ast.Name):
            caught = _caught_exception_texts(mod, node, self.origin, self.depth)
            if caught is not None:
                return caught
            func = mod.enclosing(node)
            if func is None or isinstance(func, ast.Lambda):
                return None
            assigned = _assigned_values(func, node.id)
            if assigned is None:
                return None
            if assigned and _param_index(func, node.id) is None:
                out = []
                for value in assigned:
                    sub = Flattener(mod, self.origin, self.depth + 1).flatten(value)
                    if any(v is None for v in sub):
                        return None
                    if any(len(v) == 1 and isinstance(v[0], Arg) and v[0].src == node.id for v in sub):
                        return None
                    out += sub
                if 0 < len(out) <= MAX_VARIANTS and _is_textual(out):
                    return out
                return None
            if not assigned:
                key = (id(func), node.id)
                if key in _CALLER_TEXTS:
                    return _CALLER_TEXTS[key]
                _CALLER_TEXTS[key] = None
                _CALLER_TEXTS[key] = self._caller_texts(mod, func, node.id)
                return _CALLER_TEXTS[key]
        return None

    def _caller_texts(self, mod, func, name):
        """The spellings the callers of ``func`` pass for parameter ``name``."""
        callers = _caller_values(mod, func, name)
        if callers is None:
            return None
        out = []
        for other, value in callers:
            sub = Flattener(other, self.origin, self.depth + 1).flatten(value)
            out += [v for v in sub if v is not None] if all(v is not None for v in sub) else []
        if 0 < len(out) <= MAX_VARIANTS and _is_textual(out):
            return out
        return None


def _caught_exception_texts(mod, node: ast.Name, origin, depth: int):
    """``except LibraryResolveError as exc: ... f"...{exc}"``: the texts every
    raise of that exception type spells."""
    current = node
    while current in mod.parents:
        current = mod.parents[current]
        if isinstance(current, ast.ExceptHandler) and current.name == node.id:
            kind = current.type
            name = kind.id if isinstance(kind, ast.Name) else (
                kind.attr if isinstance(kind, ast.Attribute) else None)
            if name is None or name in ("Exception", "ValueError", "KeyError", "TypeError"):
                return None
            out = []
            for other, call in CALLS.get(name, ()):
                if not call.args:
                    continue
                sub = Flattener(other, origin, depth + 1).flatten(call.args[0])
                out += [v for v in sub if v is not None and not _is_catch_all(v)]
            return out if 0 < len(out) <= MAX_VARIANTS else None
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
    return None


_RETURNED: dict = {}
_CALLER_TEXTS: dict = {}


def _returned_texts(name: str | None, origin, depth: int):
    """A helper's text: every string its ``return`` statements spell."""
    if not name or depth > 3 or name in ("next", "join", "format", "get", "pop"):
        return None
    if name not in _RETURNED:
        _RETURNED[name] = None   # a recursive helper reads as no text
        _RETURNED[name] = _returned_texts_uncached(name, origin, depth)
    return _RETURNED[name]


def _returned_texts_uncached(name: str, origin, depth: int):
    defs = FUNCS.get(name, ())
    if len(defs) != 1:
        return None
    mod, func = defs[0]
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                continue
            sub = Flattener(mod, origin, depth + 1).flatten(node.value)
            out += [v for v in sub if v is not None]
    if not out or not _is_textual(out) or len(out) > MAX_VARIANTS:
        return None
    return out


def _is_textual(variants: list) -> bool:
    """Expansion pays off only when every variant spells some text."""
    return all(any(isinstance(p, str) and p.strip() for p in v) for v in variants)


def _strip(parts):
    if parts is None:
        return None
    parts = list(parts)
    if parts and isinstance(parts[0], str):
        parts[0] = parts[0].lstrip()
    if parts and isinstance(parts[-1], str):
        parts[-1] = parts[-1].rstrip()
    return parts


def normalize(parts):
    if parts is None:
        return None
    out: list = []
    for p in parts:
        if isinstance(p, str):
            if not p:
                continue
            if out and isinstance(out[-1], str):
                out[-1] += p
            else:
                out.append(p)
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Argument names
# ---------------------------------------------------------------------------

_NAME_OVERRIDES = {
    "spell_call(node)": "call", "spell_call(request)": "call",
    "self._expr_to_str(node)": "expr",
    "type(stmt).__name__": "statement",
    "_a(kind)": "kind",
    "import_spelling(node)": "import_path", "import_spelling(stmt)": "import_path",
    "self._ta_signature_text(name)": "signature",
    "info.node.params[index]": "param",
}


def arg_name(src: str) -> str:
    src = src.strip()
    if src in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[src]
    m = re.fullmatch(r"len\((.*)\)", src)
    if m:
        return _base(m.group(1)) + "_count"
    m = re.fullmatch(r"""(?:"[^"]*"|'[^']*')\.join\((.*)\)""", src, re.S)
    if m:
        inner = m.group(1)
        inner = re.sub(r"^sorted\((.*)\)$", r"\1", inner.strip(), flags=re.S)
        return _base(inner)
    m = re.fullmatch(r"str\((.*)\)", src)
    if m:
        return _base(m.group(1))
    return _base(src)


def _base(src: str) -> str:
    src = re.sub(r"\[[^\[\]]*\]", "", src)          # drop subscripts
    src = re.sub(r"\((?:[^()]|\([^()]*\))*\)", "", src)  # drop call arguments
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", src)
    tokens = [t for t in tokens if t not in ("self", "node", "value", "split", "get", "strip")] or tokens
    if not tokens:
        return "value"
    name = tokens[-1].lstrip("_")
    if name in ("name", "kind", "member") and len(tokens) >= 2:
        prev = tokens[-2].lstrip("_")
        if prev not in ("self", "node", "expr_node", "n", "info", "stmt", "site", "spec", "decl",
                        "target", "obj", "field", "mod", "lib", "tt", "error", "sig", "signature"):
            name = f"{prev}_{name}"
    name = re.sub(r"[^A-Za-z0-9_]", "_", name) or "value"
    if name[0].isdigit():
        name = "v" + name
    return name.lower() if name.isupper() else name


def to_template(message: list, hint: list | None) -> tuple[str, str | None, dict]:
    """ICU templates of a variant pair and the source of each argument."""
    names: dict[str, str] = {}   # expression source -> argument name
    used: set[str] = set()

    def name_for(arg: Arg) -> str:
        if arg.src in names:
            return names[arg.src]
        base = arg_name(arg.src)
        candidate, n = base, 2
        while candidate in used:
            candidate = f"{base}{n}"
            n += 1
        used.add(candidate)
        names[arg.src] = candidate
        return candidate

    def render(parts):
        if parts is None:
            return None
        out = []
        for p in parts:
            if isinstance(p, str):
                out.append(escape_literal(p))
            else:
                ph = "{" + name_for(p) + "}"
                out.append("''" + ph + "''" if p.quoted else ph)
        return "".join(out)

    msg = render(message)
    hnt = render(hint)
    sources = {v: k for k, v in names.items()}
    return msg, hnt, sources


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _level_of(mod: Module, call: ast.Call) -> list[str]:
    level = call_arg(call, 0, "level")
    src = mod.seg(level) if level is not None else ""
    out = []
    if "ERROR" in src:
        out.append(ERR)
    if "WARNING" in src:
        out.append(WARN)
    return out


def _is_catch_all(parts) -> bool:
    """A text that is one argument alone: its spellings are sites of their own."""
    return len(parts) == 1 and isinstance(parts[0], Arg)


def _is_passthrough(variants) -> bool:
    return all(v is not None and len(v) == 1 and isinstance(v[0], Arg) for v in variants)


def extract() -> list[dict]:
    """Every (severity, message, hint) template the source spells."""
    if not MODULES:
        load_modules()
    found: dict[tuple, dict] = {}
    for mod in MODULES.values():
        for call in ast.walk(mod.tree):
            if not isinstance(call, ast.Call):
                continue
            name = callee_name(call)
            spec = emitter(name, mod.rel) if name else None
            severity_choices = None
            if spec is None and isinstance(call.func, ast.Name):
                # ``emit = self._err if ... else self._warn``; ``emit(...)``.
                spec, severity_choices = _local_emitter(mod, call)
            if spec is None:
                continue
            severity, mpos, hpos = spec
            message = call_arg(call, mpos, "message")
            hint = call_arg(call, hpos, "hint")
            if hint is None:
                hint = _fixed_hint(mod, name)
            if name == "_reject_if_in":
                message, hint = _reject_if_in_message(mod, call)
            if message is None or _forwards_parameter(mod, message):
                continue
            severities = [severity] if severity in (ERR, WARN) else _level_of(mod, call)
            if severity_choices:
                severities = severity_choices
            if name == "ParseError" or name == "MethodBindError" or name == "LibraryResolveError":
                severities = [ERR]
            flattener = Flattener(mod, call)
            mvars = [normalize(v) for v in flattener.flatten(message)]
            mvars = [v for v in mvars if v is not None and not _is_catch_all(v)]
            if not mvars or _is_passthrough(mvars):
                continue
            hvars = [normalize(v) for v in flattener.flatten(hint)] if hint is not None else [None]
            if hint is not None and not (isinstance(hint, ast.Constant) and hint.value is None):
                # A hint that can be None at run time keeps a None variant.
                if _may_be_none(mod, hint):
                    hvars.append(None)
            for sev in severities:
                for m in mvars[:MAX_VARIANTS]:
                    for h in hvars[:MAX_VARIANTS]:
                        if h is not None and len(h) == 0:
                            h = None
                        msg, hnt, sources = to_template(m, h)
                        key = (sev, msg, hnt)
                        entry = found.setdefault(key, {
                            "severity": sev, "message": msg, "hint": hnt,
                            "sources": sources, "sites": []})
                        entry["sites"].append(f"{mod.rel}:{call.lineno}")
    for parse_site in _parser_consume_templates():
        key = (ERR, parse_site["message"], None)
        entry = found.setdefault(key, dict(parse_site, sites=[]))
        entry["sites"] += parse_site["sites"]
    return list(found.values())


def _local_emitter(mod: Module, call: ast.Call):
    """A call of a local bound to one emitter or another."""
    func = mod.enclosing(call)
    if func is None or isinstance(func, ast.Lambda):
        return None, None
    values = _assigned_values(func, call.func.id) or []
    specs = []
    for value in values:
        options = [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
        for option in options:
            name = option.attr if isinstance(option, ast.Attribute) else None
            spec = emitter(name, mod.rel) if name else None
            if spec is None:
                return None, None
            specs.append(spec)
    if not specs or len({(s[1], s[2]) for s in specs}) != 1:
        return None, None
    return specs[0], sorted({s[0] for s in specs})


def _forwards_parameter(mod: Module, message) -> bool:
    """An emitter's body handing on its own ``message`` parameter: the text
    is spelled where the emitter is called, which is a site of its own."""
    if not isinstance(message, ast.Name):
        return False
    func = mod.enclosing(message)
    return (func is not None and not isinstance(func, ast.Lambda)
            and _param_index(func, message.id) is not None)


def _may_be_none(mod: Module, hint) -> bool:
    if isinstance(hint, ast.Name):
        func = mod.enclosing(hint)
        if func is not None and not isinstance(func, ast.Lambda):
            if _param_index(func, hint.id) is not None:
                default = _default(func, hint.id)
                if isinstance(default, ast.Constant) and default.value is None:
                    return True
            for value in _assigned_values(func, hint.id) or []:
                if isinstance(value, ast.Constant) and value.value is None:
                    return True
    if isinstance(hint, ast.IfExp):
        return any(isinstance(b, ast.Constant) and b.value is None for b in (hint.body, hint.orelse))
    return False


def _parser_consume_templates() -> list[dict]:
    """``Parser._consume(tt, msg)``: "Expected {tt}, got {type}({value!r}). {msg}"."""
    mod = MODULES["pineforge_codegen/parser.py"]
    out = []
    for call in ast.walk(mod.tree):
        if not isinstance(call, ast.Call) or callee_name(call) != "_consume":
            continue
        tt = call_arg(call, 0, "tt")
        msg = call_arg(call, 1, "msg")
        expected = mod.seg(tt).split(".")[-1] if isinstance(tt, ast.Attribute) else None
        text = msg.value if isinstance(msg, ast.Constant) and isinstance(msg.value, str) else ""
        parts = ["Expected ", expected if expected else Arg("tt.name"), ", got ", Arg("cur.type.name"),
                 "(", Arg("cur.value", quoted=True), "). " + text]
        parts = normalize(_strip(parts))
        msg_t, _, sources = to_template(parts, None)
        out.append({"severity": ERR, "message": msg_t, "hint": None, "sources": sources,
                    "sites": [f"{mod.rel}:{call.lineno}"]})
    return out


# ---------------------------------------------------------------------------
# Catalog maintenance
# ---------------------------------------------------------------------------

def _guess_kind(name: str, src: str) -> str:
    low = name.lower()
    if low.endswith("_count") or low in ("count", "n", "cs_idx", "tuple_size", "max_family_variants",
                                         "max_nesting_depth", "max_source_chars",
                                         "max_transpile_seconds", "line") or src.startswith("len("):
        return "number"
    if "type" in low or low in ("t", "elem_str", "element_label", "inferred_types", "spec_name"):
        return "type"
    if low in ("op", "kw_name", "keyword", "statement"):
        return "keyword"
    if low in ("what", "why", "reason", "kind", "form", "problem", "found", "where", "reads",
               "opens", "daily", "listed", "extra", "exc", "err", "hint", "message", "detail",
               "allowed_hint", "chart_read", "via", "through", "label", "function", "code"):
        return "text"
    return "identifier"


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {"schema": CATALOG_SCHEMA, "codes": {}}


def _next_code(codes: dict, severity: str, area: int) -> str:
    letter = "E" if severity == ERR else "W"
    used = {int(c[4:]) for c in codes if c.startswith(f"PF-{letter}{area}")}
    start = area * 1000 + 1
    if area == 1 and severity == WARN:
        start = 1000 + SWITCH_ARM_TWIN_LIMIT
    if area == 1 and severity == ERR:
        limit = 1000 + SWITCH_ARM_TWIN_LIMIT
    else:
        limit = area * 1000 + 1000
    for number in range(start, limit):
        if number not in used:
            return f"PF-{letter}{number:04d}"
    raise SystemExit(f"area {area} {severity} codes exhausted")


def merge(catalog: dict, templates: list[dict]) -> list[str]:
    """Add codes for templates the catalog lacks; returns the new codes."""
    codes = catalog["codes"]
    by_key = {(e["severity"], e["message"], e.get("hint")): c for c, e in codes.items()}
    added = []
    ordered = sorted(templates, key=lambda t: (area_of(t["sites"][0].split(":")[0]),
                                               t["sites"][0].split(":")[0],
                                               int(t["sites"][0].split(":")[1]),
                                               t["severity"], t["message"], t["hint"] or ""))
    for t in ordered:
        key = (t["severity"], t["message"], t["hint"])
        if key in by_key:
            continue
        area = area_of(t["sites"][0].split(":")[0])
        code = _next_code(codes, t["severity"], area)
        codes[code] = {
            "severity": t["severity"], "area": AREA_NAMES[area],
            "message": t["message"], "hint": t["hint"],
            "explanation": "",
            "args": {name: {"kind": _guess_kind(name, src)} for name, src in t["sources"].items()},
        }
        by_key[key] = code
        added.append(code)
        if area == 1 and t["severity"] == ERR:
            twin = "PF-W" + code[4:]
            twin_key = (WARN, t["message"], t["hint"])
            if twin not in codes and twin_key not in by_key:
                codes[twin] = dict(codes[code], severity=WARN)
                codes[twin]["args"] = {k: dict(v) for k, v in codes[code]["args"].items()}
                by_key[twin_key] = twin
                added.append(twin)
    return added


PIN_PATH = ROOT / "tests" / "fixtures" / "diagnostic_codes_pin.json"


def pin_digest(entry: dict) -> str:
    """What a code means: its severity, templates and argument names
    (``tests/test_diagnostic_codes.py`` computes the same)."""
    import hashlib
    meaning = json.dumps([entry["severity"], entry["message"], entry.get("hint"),
                          sorted(entry.get("args", {}))], ensure_ascii=False)
    return hashlib.sha256(meaning.encode("utf-8")).hexdigest()


def load_pin() -> dict:
    if PIN_PATH.exists():
        return json.loads(PIN_PATH.read_text(encoding="utf-8"))["codes"]
    return {}


def write_pin(catalog: dict) -> None:
    """Pin every new code; a pinned code's digest never changes here."""
    pinned = load_pin()
    for code, entry in catalog["codes"].items():
        pinned.setdefault(code, pin_digest(entry))
    body = {"note": ("Each code's meaning, pinned: a code is never removed or repurposed "
                     "(tests/test_diagnostic_codes.py). scripts/gen_diagnostics_catalog.py "
                     "--write pins new codes; never edit a line by hand."),
            "codes": dict(sorted(pinned.items()))}
    PIN_PATH.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def prune(catalog: dict, templates: list[dict]) -> list[str]:
    """Drop codes no source spells any more, while they are not pinned."""
    pinned = load_pin()
    spelled = {(t["severity"], t["message"], t["hint"]) for t in templates}
    codes = catalog["codes"]
    gone = []
    for code, entry in list(codes.items()):
        if code in pinned or code in UNCATALOGUED.values():
            continue
        key = (entry["severity"], entry["message"], entry.get("hint"))
        if key in spelled:
            continue
        if code.startswith("PF-W1") and int(code[4:]) < 1000 + SWITCH_ARM_TWIN_LIMIT:
            twin = codes.get("PF-E" + code[4:])
            if twin is not None and (twin["severity"] == ERR) and \
                    (ERR, twin["message"], twin.get("hint")) in spelled:
                continue
        gone.append(code)
    for code in gone:
        del codes[code]
    return gone


def ensure_uncatalogued(catalog: dict) -> None:
    for severity, code in UNCATALOGUED.items():
        catalog["codes"].setdefault(code, {
            "severity": severity, "area": "uncatalogued",
            "message": "{message}", "hint": "{hint}",
            "explanation": ("A diagnostic whose text no catalog template renders; the "
                            "test suite refuses it, so a release never emits it."),
            "args": {"message": {"kind": "text"}, "hint": {"kind": "text"}},
        })


def dump(catalog: dict) -> str:
    """One code per line: compact for the Pyodide bundle, readable in a diff."""
    codes = sorted(catalog["codes"].items())
    lines = [f'{{"schema": {json.dumps(CATALOG_SCHEMA)},', ' "codes": {']
    for i, (code, entry) in enumerate(codes):
        ordered = {k: entry[k] for k in ("severity", "area", "message", "hint", "explanation", "args")
                   if k in entry}
        tail = "," if i < len(codes) - 1 else ""
        lines.append(f"  {json.dumps(code)}: {json.dumps(ordered, ensure_ascii=False)}{tail}")
    lines.append(" }}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="add codes for new templates")
    parser.add_argument("--list", action="store_true", help="print every extracted template")
    args = parser.parse_args(argv)
    templates = extract()
    if args.list:
        for t in templates:
            print(t["severity"], t["message"], "||", t["hint"], t["sites"][:2])
        return 0
    catalog = load_catalog()
    if args.write:
        ensure_uncatalogued(catalog)
        added = merge(catalog, templates)
        pruned = prune(catalog, templates)
        if pruned:
            print(f"{len(pruned)} unpinned code(s) no source spells removed: {', '.join(pruned)}")
        write_pin(catalog)
        CATALOG_PATH.write_text(dump(catalog), encoding="utf-8")
        print(f"{len(added)} code(s) added; {len(catalog['codes'])} in the catalog")
        return 0
    have = {(e["severity"], e["message"], e.get("hint")) for e in catalog["codes"].values()}
    missing = [t for t in templates if (t["severity"], t["message"], t["hint"]) not in have]
    for t in missing:
        print(f"no code: [{t['severity']}] {t['message']!r} hint={t['hint']!r} at {', '.join(t['sites'])}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
