"""Immutable execution metadata; it does not alter generated computation."""

import json
import math

from ..ast_nodes import BoolLiteral, FuncCall, Identifier, MemberAccess, NumberLiteral, StrategyDecl, StringLiteral, UnaryOp, VarDecl
from ..external_requests import CAPABILITY_UNPINNED_ANNOTATION
from ..limits import iter_ast_nodes


STRATEGY_PARAMETERS = (
    "title", "shorttitle", "overlay", "format", "precision", "scale", "pyramiding",
    "calc_on_order_fills", "calc_on_every_tick", "max_bars_back",
    "backtest_fill_limits_assumption", "default_qty_type", "default_qty_value",
    "initial_capital", "currency", "slippage", "commission_type", "commission_value",
    "process_orders_on_close", "close_entries_rule", "margin_long", "margin_short",
    "explicit_plot_zorder", "max_lines_count", "max_labels_count", "max_boxes_count",
    "calc_bars_count", "risk_free_rate", "use_bar_magnifier", "fill_orders_on_standard_ohlc",
    "max_polylines_count", "dynamic_requests", "behind_chart", "calc_on_every_history_tick",
)


DECLARATION_DEFAULTS = {
    "calc_on_every_tick": False,
    "calc_on_order_fills": False,
    "process_orders_on_close": False,
    "use_bar_magnifier": False,
    "fill_orders_on_standard_ohlc": False,
    "backtest_fill_limits_assumption": 0,
    "currency": "currency.NONE",
    "timeframe": "",
    "timeframe_gaps": True,
    "dynamic_requests": True,
    "calc_on_every_history_tick": False,
}


def _expression(node):
    if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
        return node.value
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        return f"{_expression(node.object)}.{node.member}"
    if isinstance(node, FuncCall):
        return f"{_expression(node.callee)}({','.join(str(_expression(arg)) for arg in node.args)})"
    return None


def _literal(node):
    if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
        return node.value, True
    if isinstance(node, UnaryOp) and node.op in ("+", "-"):
        value, valid = _literal(node.operand)
        if valid and type(value) in (int, float):
            return value if node.op == "+" else -value, True
    if isinstance(node, MemberAccess):
        value = _expression(node)
        if value and value.split(".")[0] in ("currency", "format", "scale", "strategy"):
            return value, True
    return _expression(node), False


def capabilities_document(emitter) -> str:
    declarations = dict(DECLARATION_DEFAULTS)
    unresolved = []
    requests = []
    lower_symbols = {}
    intrabar = False
    recorded = False
    for node, _depth in iter_ast_nodes(emitter.ctx.ast):
        if isinstance(node, StrategyDecl):
            arguments = {STRATEGY_PARAMETERS[index] if index < len(STRATEGY_PARAMETERS)
                         else f"strategy() positional argument {index + 1}": argument
                         for index, argument in enumerate(node.args)}
            unresolved.extend(arguments.keys() & node.kwargs.keys())
            arguments.update(node.kwargs)
            for name, argument in arguments.items():
                value, valid = _literal(argument)
                default = DECLARATION_DEFAULTS.get(name)
                if name in DECLARATION_DEFAULTS:
                    valid &= type(value) is type(default)
                    if name == "backtest_fill_limits_assumption":
                        valid = (type(value) is int and value >= 0) or (
                            type(value) is float and math.isfinite(value) and value >= 0 and value == int(value))
                        if valid:
                            value = int(value)
                    declarations[name] = value
                if not valid or name not in STRATEGY_PARAMETERS and name not in DECLARATION_DEFAULTS:
                    unresolved.append(name)
        elif isinstance(node, VarDecl):
            intrabar |= node.is_varip
        elif isinstance(node, FuncCall):
            name, namespace = emitter._resolve_callee(node.callee)
            if namespace == "request" and name == "security_lower_tf":
                expression = node.args[2] if len(node.args) > 2 else node.kwargs.get("expression")
                lower_symbols[id(expression)] = node.args[0] if node.args else node.kwargs.get("symbol")
            parts = (node.annotations or {}).get("pf_recorded_key")
            if parts is not None:
                recorded = True
                requests.append({
                    "function": f"{namespace}.{name}",
                    "symbol": _expression(node.args[0]) if node.args else None,
                    "timeframe": "",
                    "lookahead": f"barmerge.lookahead_{parts.get('lookahead', 'off')}",
                    "gaps": f"barmerge.gaps_{parts.get('gaps', 'off')}",
                    "heikinashi": False,
                    "feed": "recorded",
                })
        if isinstance(node, Identifier) and node.name == "timenow":
            unresolved.append("timenow (bar timestamp approximation)")
        if isinstance(node, Identifier) and node.name in ("last_bar_index", "last_bar_time"):
            unresolved.append(f"{node.name} (stream endpoint is not batch-equivalent)")
        if isinstance(node, MemberAccess) and _expression(node) == "barstate.isrealtime":
            unresolved.append("barstate.isrealtime (compiled historical-only approximation)")
        if isinstance(node, MemberAccess) and _expression(node) in (
                "barstate.islast", "barstate.islastconfirmedhistory"):
            unresolved.append(f"{_expression(node)} (stream endpoint is not batch-equivalent)")

    auxiliary = False
    for site in emitter._security_calls:
        symbol_node = site.get("symbol_node")
        if site.get("is_lower_tf_array"):
            symbol_node = lower_symbols.get(id(site.get("expr_node")), symbol_node)
        if isinstance(symbol_node, FuncCall) and site.get("heikinashi") and symbol_node.args:
            symbol_node = symbol_node.args[0]
        symbol = _expression(symbol_node) if symbol_node is not None else "syminfo.tickerid"
        timeframe = _expression(site.get("tf_node"))
        lookahead = _expression(site.get("lookahead_node")) or "barmerge.lookahead_off"
        gaps = _expression(site.get("gaps_node")) or "barmerge.gaps_off"
        if isinstance(symbol_node, Identifier) and symbol_node.name not in emitter._global_mutable_infos:
            symbol = _expression((getattr(emitter.ctx, "global_expr_map", {}) or {}).get(
                symbol_node.name, symbol_node))
        feed = "chart" if symbol in ("syminfo.tickerid", "syminfo.ticker", "") else "auxiliary"
        auxiliary |= feed == "auxiliary"
        if not isinstance(site.get("tf_node"), StringLiteral) and timeframe != "timeframe.period":
            unresolved.append(f"request.security[{site['sec_id']}].timeframe")
        if lookahead not in ("barmerge.lookahead_off", "barmerge.lookahead_on"):
            unresolved.append(f"request.security[{site['sec_id']}].lookahead")
        requests.append({
            "function": "request.security_lower_tf" if site.get("is_lower_tf_array") else "request.security",
            "symbol": symbol,
            "timeframe": timeframe,
            "lookahead": lookahead,
            "gaps": gaps,
            "heikinashi": bool(site.get("heikinashi")),
            "feed": feed,
        })
    for index, site in enumerate((emitter.ctx.ast.annotations or {}).get(CAPABILITY_UNPINNED_ANNOTATION, ())):
        requests.append({
            "function": site["function"],
            "symbol": _expression(site["symbol_node"]),
            "timeframe": _expression(site["tf_node"]) if site["function"] in (
                "request.security", "request.security_lower_tf") else "",
            "lookahead": _expression(site["lookahead_node"]) or "barmerge.lookahead_off",
            "gaps": _expression(site["gaps_node"]) or "barmerge.gaps_off",
            "heikinashi": False,
            "feed": "unpinned",
        })
        unresolved.append(f"{site['function']}[unpinned:{index}]")
    requirements = {
        "auxiliary_security_feeds": auxiliary,
        "native_security_feeds": False,
        "fx_curve": declarations["currency"] != "currency.NONE",
        "recorded_series": recorded,
        "historical_probe_overrides": False,
        "intrabar_persistence": intrabar,
    }
    return json.dumps({"version": 1, "declarations": declarations, "requests": requests,
                       "requirements": requirements, "unresolved": sorted(set(unresolved))},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def emit_capabilities_exports(emitter, lines: list[str]) -> None:
    literal = json.dumps(capabilities_document(emitter))
    lines.extend([
        "#ifdef PF_SETTINGS_API_VERSION",
        "#ifdef PF_CAPABILITIES_API_VERSION",
        "    uint32_t strategy_capabilities_api_version(void) { return PF_CAPABILITIES_API_VERSION; }",
        "    int strategy_capabilities_receipt(void* s, char* json, size_t capacity, size_t* required, char* error, size_t error_capacity) {",
        "        if (required) *required = 0;",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(s != nullptr, "null strategy");',
        f"            ::pineforge::checked_settings::receipt({literal}, json, capacity, required);",
        "        });", "    }", "#endif", "#endif",
    ])
