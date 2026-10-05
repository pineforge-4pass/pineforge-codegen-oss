"""Immutable facts about emitted order parameters and execution context."""

from collections import Counter
from dataclasses import dataclass
import json
import math
import re

from ..ast_nodes import (
    Assignment, BinOp, BoolLiteral, ForInStmt, ForStmt, FuncCall, FuncDef,
    Identifier, MemberAccess, MethodDef, NumberLiteral, StringLiteral, TupleAssign,
    UnaryOp, VarDecl, WhileStmt,
)
from ..limits import iter_ast_nodes, syntax_children
from .capabilities import READ_ONLY_STRATEGY_CALLS, _expression
from .host_members import HOST_MEMBER_NAMES


NUMERIC_PARAMETERS = frozenset((
    "limit", "stop", "qty", "qty_percent", "trail_points", "trail_offset",
    "trail_price", "profit_ticks", "loss_ticks",
))
FINITE_BAR_VALUES = frozenset(("open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"))
ENUM_VALUES = {
    "strategy.oca.none": 0, "strategy.oca.cancel": 1, "strategy.oca.reduce": 2,
    "strategy.fixed": 0, "strategy.percent_of_equity": 1, "strategy.cash": 2,
}
SETTING_DEFAULTS = {
    "initial_capital": 10000.0, "default_qty_type": "fixed", "default_qty_value": 1.0,
    "pyramiding": 1, "commission_type": "percent", "commission_value": 0.0,
    "slippage": 0, "margin_long": 100.0, "margin_short": 100.0,
    "close_entries_rule": "FIFO",
}
SETTING_ENUMS = {
    "QtyType::FIXED": "fixed", "QtyType::PERCENT_OF_EQUITY": "percent_of_equity",
    "QtyType::CASH": "cash", "CommissionType::PERCENT": "percent",
    "CommissionType::CASH_PER_ORDER": "cash_per_order",
    "CommissionType::CASH_PER_CONTRACT": "cash_per_contract",
}
CPP_TOKENS = re.compile(
    r'R"(?P<delimiter>[^ ()\\\t\r\n]{0,16})\(.*?\)(?P=delimiter)"'
    r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|//[^\n]*|/\*.*?(?:\*/|\Z)|[A-Za-z_][A-Za-z_0-9]*',
    re.DOTALL,
)


def scan_host_reads(cpp: str) -> set[str]:
    """Cross-check host tokens, excluding C++ strings and comments."""
    return {match.group() for match in CPP_TOKENS.finditer(cpp)
            if match.group() in HOST_MEMBER_NAMES}


class HostReadLines(list):
    """Record scaffold references when each fragment enters the emission buffer."""

    def __init__(self, reads: set[str]):
        super().__init__()
        self.reads = reads
        self.in_comment = False

    def _record(self, fragment: str) -> None:
        if self.in_comment:
            end = fragment.find("*/")
            if end < 0:
                return
            fragment = fragment[end + 2:]
            self.in_comment = False
        for match in CPP_TOKENS.finditer(fragment):
            token = match.group()
            if token in HOST_MEMBER_NAMES:
                self.reads.add(token)
            if token.startswith("/*") and not token.endswith("*/"):
                self.in_comment = True

    def append(self, fragment: str) -> None:
        self._record(fragment)
        super().append(fragment)

    def extend(self, fragments) -> None:
        for fragment in fragments:
            self.append(fragment)

    def insert(self, index: int, fragment: str) -> None:
        self.reads.update(scan_host_reads(fragment))
        super().insert(index, fragment)


@dataclass(frozen=True)
class LoweredParameter:
    text: str
    node: object
    default: str | None = None


@dataclass
class LoweredCall:
    node: FuncCall
    call: str
    parameters: dict[str, LoweredParameter]
    context: str
    form: str | None = None


def record_order_call(emitter, node, call, parameters, form=None) -> None:
    """Called only by a branch that lowers the corresponding order command."""
    emitter._order_shape_calls.setdefault(id(node), LoweredCall(
        node, call, {name: LoweredParameter(*value) for name, value in parameters.items()},
        "repeatable" if getattr(emitter, "_current_func_body", None) is not None else "straight", form,
    ))


def _literal(node, bindings):
    if isinstance(node, (NumberLiteral, StringLiteral, BoolLiteral)):
        return node.value
    if isinstance(node, Identifier):
        return bindings.get(node.name)
    if isinstance(node, UnaryOp) and node.op in ("+", "-"):
        value = _literal(node.operand, bindings)
        if type(value) in (int, float):
            return value if node.op == "+" else -value
    return None


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _never_na(node, bindings) -> bool:
    if _finite(_literal(node, bindings)):
        return True
    if isinstance(node, Identifier):
        return node.name in FINITE_BAR_VALUES and node.name not in bindings
    if isinstance(node, UnaryOp) and node.op in ("+", "-"):
        return _never_na(node.operand, bindings)
    if isinstance(node, BinOp):
        if node.op in ("+", "-"):
            return _never_na(node.left, bindings) and _never_na(node.right, bindings)
        if node.op == "*":
            return ((_finite(_literal(node.left, bindings)) and _never_na(node.right, bindings))
                    or (_finite(_literal(node.right, bindings)) and _never_na(node.left, bindings)))
    return False


def parameter_class(name, parameter, bindings):
    if parameter.text == parameter.default:
        return "absent"
    value = _literal(parameter.node, bindings)
    if name in ("id", "from_entry"):
        if parameter.node is None and parameter.text == '\"\"':
            value = ""
        if isinstance(value, str):
            return ("global" if not value else "named") if name == "from_entry" else (
                "literal" if value else "empty")
        return "dynamic"
    if name == "direction":
        if parameter.text == "true":
            return "long"
        if parameter.text == "false":
            return "short"
        return "dynamic"
    if name in NUMERIC_PARAMETERS:
        if _finite(value):
            return "literal"
        return "never_na" if _never_na(parameter.node, bindings) else "maybe_na"
    if name in ("comment", "oca_name"):
        if value == "":
            return "absent"
        return "literal" if isinstance(value, str) else "dynamic"
    if name in ("oca_type", "qty_type"):
        if isinstance(parameter.node, MemberAccess):
            value = ENUM_VALUES.get(_expression(parameter.node))
        if _finite(value) and str(int(value)) == parameter.default and value == int(value):
            return "absent"
        return f"literal:{int(value)}" if _finite(value) and value == int(value) else "dynamic"
    if name == "immediately":
        return "literal:true" if value is True else "dynamic"
    raise ValueError(f"unclassified lowered order parameter: {name}")


def _site_facts(ast):
    """Source ordering, lexical shadows, and repeatability for authored call sites."""
    mutated = {node.target.name for node, _depth in iter_ast_nodes(ast)
               if isinstance(node, Assignment) and isinstance(node.target, Identifier)}
    bindings = {}
    for node in ast.body:
        if isinstance(node, VarDecl):
            value = _literal(node.value, bindings)
            bindings[node.name] = (value if not node.is_var and not node.is_varip
                                   and node.name not in mutated else None)
    facts = {}
    stack = [(ast, False, bindings)]
    while stack:
        node, repeatable, visible = stack.pop()
        if isinstance(node, FuncCall):
            facts[id(node)] = (len(facts), "repeatable" if repeatable else "straight", visible)
        nested = repeatable or isinstance(node, (FuncDef, MethodDef, ForStmt, ForInStmt, WhileStmt))
        if isinstance(node, (FuncDef, MethodDef, ForStmt, ForInStmt, WhileStmt)) or hasattr(node, "body") and node is not ast:
            visible = dict(visible)
            if isinstance(node, (FuncDef, MethodDef)):
                visible.update({name if isinstance(name, str) else name[0]: None for name in node.params})
            for child, _depth in iter_ast_nodes(node):
                if child is not node and isinstance(child, FuncDef):
                    continue
                if isinstance(child, VarDecl):
                    visible[child.name] = None
                if isinstance(child, TupleAssign):
                    visible.update({name: None for name in child.names})
            if isinstance(node, (ForStmt, ForInStmt)):
                visible.update({name: None for name in ([node.var] if node.var else node.vars or [])})
        stack.extend((child, nested, visible) for child in reversed(list(syntax_children(node))))
    return facts


def settings_echo(constructor):
    settings = dict(SETTING_DEFAULTS)
    pooc = False
    for statement in constructor:
        match = re.fullmatch(r"        cfg\.(\w+) = (.*);", statement)
        if match is None:
            continue
        name, text = match.groups()
        if name == "process_orders_on_close":
            pooc = text == "true"
        elif name == "close_entries_rule_any":
            settings["close_entries_rule"] = "ANY" if text == "true" else "FIFO"
        elif name in settings:
            if text.startswith("static_cast<int>("):
                settings[name] = SETTING_ENUMS[text[len("static_cast<int>("):-1]]
            else:
                value = float(text)
                settings[name] = (int(value) if name in ("pyramiding", "slippage") else value) if math.isfinite(value) else None
    return settings, pooc


def order_shapes_document(emitter) -> str:
    facts = _site_facts(emitter.ctx.ast)
    records = sorted(emitter._order_shape_calls.values(), key=lambda record: (
        record.node.loc.line if record.node.loc else 0,
        record.node.loc.col if record.node.loc else 0,
        facts.get(id(record.node), (len(facts),))[0],
    ))
    calls = []
    ids = []
    for site, record in enumerate(records):
        _order, context, bindings = facts.get(id(record.node), (site, record.context, {}))
        descriptor = {"site": site, "call": record.call, "context": context}
        if record.form is not None:
            descriptor["form"] = record.form
        descriptor.update({name: parameter_class(name, parameter, bindings)
                           for name, parameter in record.parameters.items()})
        calls.append(descriptor)
        ids.append({name: _literal(parameter.node, bindings) if parameter.node is not None else ""
                    for name, parameter in record.parameters.items() if name in ("id", "from_entry")})
    entries = [(site, ids[site].get("id"), call.get("direction"))
               for site, call in enumerate(calls) if call["call"] == "entry"
               and call["id"] in ("literal", "empty")]
    long_ids = {name for _site, name, direction in entries if direction == "long"}
    short_ids = {name for _site, name, direction in entries if direction == "short"}
    for site, call in enumerate(calls):
        if call["call"] not in ("exit", "close", "cancel"):
            continue
        target = ids[site].get("from_entry" if call["call"] == "exit" else "id")
        matching = [(entry_site, direction) for entry_site, name, direction in entries
                    if target == "" or target is not None and target == name]
        directions = {direction for _entry_site, direction in matching}
        call["target"] = ("both" if "dynamic" in directions or directions >= {"long", "short"}
                          else "long" if "long" in directions else "short" if "short" in directions
                          else "dangling")
        if call["call"] == "exit":
            before = any(entry_site > site for entry_site, _direction in matching)
            after = any(entry_site < site for entry_site, _direction in matching)
            call["order"] = "mixed" if before and after or not matching else "before" if before else "after"
    counts = Counter(name for _site, name, _direction in entries)
    settings, pooc = settings_echo(emitter._order_shape_constructor)
    unmodeled = {_expression(node.callee) for node, _depth in iter_ast_nodes(emitter.ctx.ast)
                 if isinstance(node, FuncCall) and (_expression(node.callee) or "").startswith("strategy.")
                 and _expression(node.callee) not in READ_ONLY_STRATEGY_CALLS
                 and id(node) not in emitter._order_shape_calls}
    return json.dumps({
        "version": 1, "process_orders_on_close": pooc, "calls": calls,
        "entry_ids": {"long": len(long_ids), "short": len(short_ids),
                      "shared": len(long_ids & short_ids), "multi_site": sum(count > 1 for count in counts.values())},
        "host_reads": sorted(emitter._order_shape_host_reads), "settings": settings,
        "unmodeled": sorted(unmodeled),
    }, sort_keys=True, separators=(",", ":"), allow_nan=False)
