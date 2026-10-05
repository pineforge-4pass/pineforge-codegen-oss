"""Immutable execution metadata; it does not alter generated computation."""

import json
import math

from ..ast_nodes import BoolLiteral, FuncCall, Identifier, MemberAccess, NumberLiteral, StrategyDecl, StringLiteral, Subscript, UnaryOp, VarDecl
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

MODELED_ORDER_ARGUMENTS = {
    "strategy.entry": frozenset(("id", "direction", "limit", "stop")),
    "strategy.exit": frozenset(("id", "from_entry", "limit", "stop")),
    "strategy.close": frozenset(("id", "immediately")),
    "strategy.close_all": frozenset(("immediately",)),
}
READ_ONLY_STRATEGY_CALLS = frozenset(
    f"strategy.{namespace}.{accessor}"
    for namespace in ("opentrades", "closedtrades")
    for accessor in ("entry_id", "entry_price", "entry_time", "entry_bar_index", "size",
                     "profit", "profit_percent", "commission", "max_runup", "max_runup_percent",
                     "max_drawdown", "max_drawdown_percent", "exit_id", "exit_price",
                     "exit_time", "exit_bar_index", "entry_comment", "exit_comment")
) | frozenset(("strategy.convert_to_account", "strategy.convert_to_symbol"))
ORDER_SETTINGS = frozenset(("pyramiding", "default_qty_type", "default_qty_value", "initial_capital",
                           "slippage", "commission_type", "commission_value", "close_entries_rule",
                           "margin_long", "margin_short", "risk_free_rate"))
MODELED_SETTING_DEFAULTS = {"default_qty_type": "strategy.fixed", "default_qty_value": 1,
                            "slippage": 0, "commission_type": "strategy.commission.percent",
                            "commission_value": 0}


def _expression(node):
    if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
        return node.value
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        return f"{_expression(node.object)}.{node.member}"
    if isinstance(node, FuncCall):
        return f"{_expression(node.callee)}({','.join(str(_expression(arg)) for arg in node.args)})"
    if isinstance(node, Subscript):
        return f"{_expression(node.object)}[{_expression(node.index)}]"
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


def _order_arguments(node):
    name = _expression(node.callee)
    positional = {
        "strategy.entry": ("id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "alert_message", "disable_alert"),
        "strategy.exit": ("id", "from_entry", "qty", "qty_percent", "profit", "limit", "loss", "stop", "trail_price", "trail_points", "trail_offset", "oca_name", "comment", "alert_message", "alert_profit", "alert_loss", "alert_trailing", "disable_alert"),
        "strategy.close": ("id", "comment", "qty", "qty_percent", "alert_message", "immediately", "disable_alert"),
        "strategy.close_all": ("comment", "alert_message", "immediately", "disable_alert"),
    }.get(name, ())
    arguments = {positional[index] if index < len(positional) else f"positional_{index}": argument
                 for index, argument in enumerate(node.args)}
    if arguments.keys() & node.kwargs.keys():
        arguments["duplicate_argument"] = None
    arguments.update(node.kwargs)
    return arguments


def _order_shape(node, short_ids):
    name = _expression(node.callee)
    arguments = _order_arguments(node)
    if name not in MODELED_ORDER_ARGUMENTS or not set(arguments) <= MODELED_ORDER_ARGUMENTS[name]:
        return name + (" (unproven exit terms)" if name == "strategy.exit" else " (unproven order shape)")
    if name != "strategy.close_all" and not isinstance(arguments.get("id"), StringLiteral):
        return name + " (unproven order id)"
    if name == "strategy.entry" and _expression(arguments.get("direction")) in ("strategy.long", "strategy.short"):
        priced = {key for key in ("limit", "stop") if key in arguments}
        if len(priced) < 2 and not (set(arguments) & {"oca_name", "oca_type"}):
            return "entry:" + (next(iter(priced)) if priced else "market")
    if name == "strategy.exit":
        if (set(arguments) & {"profit", "loss", "trail_price", "trail_points", "trail_offset", "qty", "qty_percent"}):
            return name + " (unproven exit terms)"
        if ("limit" in arguments and "stop" in arguments
                and isinstance(arguments.get("from_entry"), StringLiteral)
                and _expression(arguments["from_entry"]) in short_ids):
            return "exit:short_bracket"
    if name in ("strategy.close", "strategy.close_all"):
        immediate = arguments.get("immediately")
        if immediate is None or isinstance(immediate, BoolLiteral) and not immediate.value:
            if not (set(arguments) & {"qty", "qty_percent"}):
                return "close:market"
    return name + " (unproven order shape)"


def capabilities_documents(emitter) -> tuple[str, str]:
    declarations = dict(DECLARATION_DEFAULTS)
    unresolved = []
    requests = []
    lower_symbols = {}
    intrabar = False
    recorded = False
    order_nodes = []
    order_settings = {}
    for node, _depth in iter_ast_nodes(emitter.ctx.ast):
        if isinstance(node, StrategyDecl):
            arguments = {STRATEGY_PARAMETERS[index] if index < len(STRATEGY_PARAMETERS)
                         else f"strategy() positional argument {index + 1}": argument
                         for index, argument in enumerate(node.args)}
            unresolved.extend(arguments.keys() & node.kwargs.keys())
            arguments.update(node.kwargs)
            order_settings.update({name: _literal(argument)[0] for name, argument in arguments.items()
                                   if name in ORDER_SETTINGS})
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
            qualified = _expression(node.callee) or ""
            if qualified.startswith("strategy.") and qualified not in READ_ONLY_STRATEGY_CALLS:
                order_nodes.append(node)
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
    confirmed_requests = []
    lowering_sites = {site["sec_id"]: site for site in emitter._security_eval_info}
    for site in emitter._security_calls:
        lowering = lowering_sites[site["sec_id"]]
        symbol_node = site.get("symbol_node")
        if site.get("is_lower_tf_array"):
            symbol_node = lower_symbols.get(id(site.get("expr_node")), symbol_node)
        if isinstance(symbol_node, FuncCall) and site.get("heikinashi") and symbol_node.args:
            symbol_node = symbol_node.args[0]
        symbol = _expression(symbol_node) if symbol_node is not None else "syminfo.tickerid"
        timeframe = lowering.get("tf")
        if timeframe is None:
            timeframe = "timeframe.period" if lowering.get("tf_expr") == "script_tf_" else _expression(site.get("tf_node"))
        lookahead = _expression(site.get("lookahead_node")) or "barmerge.lookahead_off"
        gaps = _expression(site.get("gaps_node")) or "barmerge.gaps_off"
        if isinstance(symbol_node, Identifier) and symbol_node.name not in emitter._global_mutable_infos:
            symbol = _expression((getattr(emitter.ctx, "global_expr_map", {}) or {}).get(
                symbol_node.name, symbol_node))
        feed = "auxiliary" if lowering.get("foreign") else "chart"
        auxiliary |= feed == "auxiliary"
        if lowering.get("tf") is None and timeframe != "timeframe.period":
            unresolved.append(f"request.security[{site['sec_id']}].timeframe")
        if lookahead not in ("barmerge.lookahead_off", "barmerge.lookahead_on"):
            unresolved.append(f"request.security[{site['sec_id']}].lookahead")
        requests.append({
            "function": "request.security_lower_tf" if site.get("is_lower_tf_array") else "request.security",
            "symbol": symbol,
            "timeframe": timeframe,
            "lookahead": lookahead,
            "gaps": gaps,
            "heikinashi": bool(lowering.get("heikinashi")),
            "feed": feed,
        })
        expression = _expression(site.get("expr_node"))
        if expression in ("close", "close[1]", "ta.sma(close,4)", "ta.ema(close,3)") and "close" in (
                getattr(emitter.ctx, "global_expr_map", {}) or {}):
            unresolved.append("request.security.expression (user-bound close)")
        confirmed_requests.append({**requests[-1], "expression": expression})
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
    short_ids = set()
    conflicting_ids = set()
    dynamic_entry_id = False
    for node in order_nodes:
        if _expression(node.callee) != "strategy.entry":
            continue
        arguments = _order_arguments(node)
        identifier = arguments.get("id")
        if not isinstance(identifier, StringLiteral):
            dynamic_entry_id = True
        elif _expression(arguments.get("direction")) == "strategy.short":
            short_ids.add(identifier.value)
        else:
            conflicting_ids.add(identifier.value)
    if dynamic_entry_id or conflicting_ids:
        short_ids.clear()
    orders = set(_order_shape(node, short_ids - conflicting_ids) for node in order_nodes)
    if declarations["process_orders_on_close"]:
        settings = {**MODELED_SETTING_DEFAULTS, **order_settings}
        slipped = {**MODELED_SETTING_DEFAULTS, "default_qty_type": "strategy.percent_of_equity",
                   "default_qty_value": 100, "slippage": 15}
        if settings != MODELED_SETTING_DEFAULTS and not (orders == {"entry:market"} and settings == slipped
                                                        and not confirmed_requests and not intrabar):
            orders.add("strategy() (unproven POOC sizing, slippage or account settings)")
    confirmed = {"version": 1, "requests": confirmed_requests,
                 "orders": sorted(orders),
                 "intrabar_persistence": intrabar}
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (encode({"version": 1, "declarations": declarations, "requests": requests,
                    "requirements": requirements, "unresolved": sorted(set(unresolved))}), encode(confirmed))


def capabilities_document(emitter) -> str:
    return capabilities_documents(emitter)[0]


def emit_capabilities_exports(emitter, lines: list[str]) -> None:
    legacy, confirmed = capabilities_documents(emitter)
    literal = json.dumps(legacy)
    lines.extend([
        "#ifdef PF_SETTINGS_API_VERSION",
        "#ifdef PF_CAPABILITIES_API_VERSION",
        "    uint32_t strategy_capabilities_api_version(void) { return PF_CAPABILITIES_API_VERSION; }",
        "    int strategy_capabilities_receipt(void* s, char* json, size_t capacity, size_t* required, char* error, size_t error_capacity) {",
        "        if (required) *required = 0;",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(s != nullptr, "null strategy");',
        f"            ::pineforge::checked_settings::receipt({literal}, json, capacity, required);",
        "        });", "    }",
        "    uint32_t strategy_confirmed_bar_api_version(void) { return 1u; }",
        "    int strategy_confirmed_bar_receipt(void* s, char* json, size_t capacity, size_t* required, char* error, size_t error_capacity) {",
        "        if (required) *required = 0;",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(s != nullptr, "null strategy");',
        f"            ::pineforge::checked_settings::receipt({json.dumps(confirmed)}, json, capacity, required);",
        "        });", "    }", "#endif", "#endif",
    ])
