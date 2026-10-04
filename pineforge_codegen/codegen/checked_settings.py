"""Opt-in settings metadata, validation and exception-contained C exports."""

from ..ast_nodes import Identifier, MemberAccess
from ..errors import CompileError


def _visit_setting_arg(emitter, expr) -> str:
    """An input argument's C++ for the settings metadata. The metadata is
    emitted in the constructor, ahead of the script body: an error here is
    held until the body is generated (``CodeGen.generate``), so the script's
    first error in source order is the one raised."""
    try:
        return emitter._visit_expr(expr)
    except CompileError as error:
        emitter._defer_settings_error(error)
        return "0"


def emit_settings_members(emitter, lines: list[str], constructor: list[str]) -> None:
    lines.extend([
        "    bool _pf_setting_failed_ = false;",
        "    std::string _pf_setting_failure_;",
        "    void _pf_record_failure(const char* entrypoint, const char* message) noexcept {",
        "        try { last_error_ = entrypoint; last_error_ += \": \"; last_error_ += message; } catch (...) {}",
        "    }",
        "    void _pf_record_setting_failure(const char* entrypoint, const char* message) noexcept {",
        "        if (!_pf_setting_failed_) {",
        "            _pf_setting_failed_ = true;",
        '            try { _pf_setting_failure_ = entrypoint; _pf_setting_failure_ += ": "; _pf_setting_failure_ += message; } catch (...) {}',
        "        }",
        "        _pf_refuse_failed_setting(nullptr);",
        "    }",
        "    void _pf_require_settings_ok() const {",
        "#ifdef PF_SETTINGS_API_VERSION",
        '        if (_pf_setting_failed_) throw ::pineforge::checked_settings::LatchedSettingsFailure(_pf_setting_failure_.empty() ? "legacy strategy setter failed" : _pf_setting_failure_);',
        "#else",
        '        if (_pf_setting_failed_) throw std::runtime_error(_pf_setting_failure_.empty() ? "legacy strategy setter failed" : _pf_setting_failure_);',
        "#endif",
        "    }",
        "    bool _pf_refuse_failed_setting(ReportC* out) noexcept {",
        "        if (!_pf_setting_failed_) return false;",
        '        try { last_error_ = _pf_setting_failure_.empty() ? "legacy strategy setter failed" : _pf_setting_failure_; } catch (...) {}',
        "        if (out) *out = ReportC{};",
        "        return true;",
        "    }",
    ])
    inputs = []
    for node, binding in emitter._global_input_calls_with_names():
        func_name, namespace = emitter._resolve_callee(node.callee)
        name = emitter._get_input_title(node, var_name=binding)
        key = emitter._input_key_literal(name)
        default = emitter._get_input_default(node)
        getter = emitter._input_getter_for_call(node, func_name, namespace)
        default_cpp = _visit_setting_arg(emitter, default) if default is not None else "0"
        default_cpp = emitter._coerce_string_input_default(getter, default_cpp)
        value_type = {
            "get_input_int": "int", "get_input_int64": "int",
            "get_input_double": "float", "get_input_bool": "bool",
            "get_input_string": "string",
        }[getter]
        kind = emitter._FORM_TYPE.get(func_name, value_type) if namespace == "input" else value_type
        options = []
        option_values = []
        supported = "true"
        default_serialized = None
        names, merged = emitter._merged_args(node, func_name, namespace)
        arguments = dict(zip(names or [], merged))
        arguments.update(node.kwargs)
        if emitter._is_source_input(node):
            value_type = "source"
            source = emitter._source_defval_to_base_series(default)[5:-1]
            default_cpp = emitter._input_key_literal(source)
            effective = f'(inputs_.count({key}) ? inputs_.at({key}) : {default_cpp})'
            options = [emitter._input_key_literal(value)
                       for value in sorted(emitter._NATIVE_SOURCE_SERIES)]
        elif namespace == "input" and func_name == "enum":
            value_type = "enum"
            declared = getattr(arguments.get("options"), "elements", None)
            enum_members = ([default] if isinstance(default, MemberAccess) else []) + list(declared or [])
            enum_names = {member.object.name for member in enum_members
                          if isinstance(member, MemberAccess) and isinstance(member.object, Identifier)}
            if (len(enum_names) == 1 and enum_members
                    and all(isinstance(member, MemberAccess) and isinstance(member.object, Identifier)
                            for member in enum_members)):
                enum_name = next(iter(enum_names))
                members = emitter._enum_defs.get(enum_name, [])
                selected = [member.member for member in declared] if declared is not None else members
                options = [emitter._input_key_literal(f"{enum_name}.{member}")
                           for member in selected]
                option_values = [emitter._input_key_literal(str(members.index(member)))
                                 for member in selected if member in members]
                supported = "true" if options and len(options) == len(option_values) else "false"
            else:
                supported = "false"
            if not isinstance(default, MemberAccess):
                supported = "false"
                default_serialized = 'std::string("na")'
                effective = f'(inputs_.count({key}) ? inputs_.at({key}) : std::string("na"))'
            else:
                effective = f'::pineforge::checked_settings::number({getter}({key}, {default_cpp}))'
        else:
            declared = getattr(arguments.get("options"), "elements", None)
            if declared:
                for option in declared:
                    option_cpp = _visit_setting_arg(emitter, option)
                    options.append(option_cpp if getter == "get_input_string" else
                                   f'::pineforge::checked_settings::number({option_cpp})')
            expression = f'{getter}({key}, {default_cpp})'
            effective = (expression if getter == "get_input_string" else
                         f'::pineforge::checked_settings::number({expression})')
        if default_serialized is None:
            default_serialized = (default_cpp if value_type in ("string", "source") else
                                  f'::pineforge::checked_settings::number({default_cpp})')
        constraints = [_visit_setting_arg(emitter, arguments[name]) if arguments.get(name) is not None
                       else "std::numeric_limits<double>::quiet_NaN()"
                       for name in ("minval", "maxval", "step")]
        metadata = (f'{{{key}, "{value_type}", {default_serialized}, '
                    f'{{{", ".join(options)}}}, {", ".join(constraints)}, '
                    f'{64 if getter == "get_input_int64" else 32}, {supported}, '
                    f'{{{", ".join(option_values)}}}, "{kind}"}}')
        inputs.append((metadata, effective))

    overrides = [
        ("initial_capital", "float", "initial_capital", "initial_capital", "0.0"),
        ("commission_value", "float", "commission_value", "commission_value", "0.0"),
        ("default_qty_value", "float", "default_qty_value", "default_qty_value", "0.0"),
        ("pyramiding", "int", "pyramiding", "pyramiding", "0.0"),
        ("slippage", "int", "slippage", "slippage", "0.0"),
        ("process_orders_on_close", "bool", "process_orders_on_close", "process_orders_on_close", "nan"),
        ("calc_on_order_fills", "bool", "calc_on_order_fills", "calc_on_order_fills", "nan"),
        ("close_entries_rule", "string", "close_entries_rule_any", "close_entries_rule", "nan"),
        ("default_qty_type", "string", "default_qty_type", "default_qty_type", "nan"),
        ("commission_type", "string", "commission_type", "commission_type", "nan"),
    ]
    enum_options = {
        "close_entries_rule": ["FIFO", "ANY"],
        "default_qty_type": ["fixed", "percent_of_equity", "cash"],
        "commission_type": ["percent", "cash_per_order", "cash_per_contract"],
    }
    lines.extend([
        "#ifdef PF_SETTINGS_API_VERSION",
        "    static pineforge::source::PineStrategyConfig _pf_settings_declared_config() {",
        "        pineforge::source::PineStrategyConfig cfg{};",
    ])
    lines.extend(statement for statement in constructor if statement.startswith("        cfg."))
    lines.extend([
        "        return cfg;", "    }",
        "    std::vector<::pineforge::checked_settings::Setting> _pf_settings_inputs() const {",
        "        return {",
    ])
    lines.extend(f"            {metadata}," for metadata, _effective in inputs)
    lines.extend([
        "        };", "    }",
        "    std::vector<::pineforge::checked_settings::Setting> _pf_settings_overrides() const {",
        "        const double _pf_nan = std::numeric_limits<double>::quiet_NaN();",
        "        const auto _pf_defaults = _pf_settings_declared_config();",
        "        return {",
    ])
    override_effective = []
    for name, value_type, config_field, override_field, minimum in overrides:
        raw = f'config_.{config_field}'
        declared_default = f'_pf_defaults.{config_field}'
        overridden = f'override_.{override_field}'
        selected = (f'(std::isnan({overridden}) ? {raw} : {overridden})'
                    if value_type == "float" else
                    f'({overridden} < 0 ? {raw} : {overridden})')
        if name in enum_options:
            options = enum_options[name]
            default_value = f'_pf_{name}_word({declared_default})'
            effective = f'_pf_{name}_word({selected})'
        else:
            options = []
            default_value = f'::pineforge::checked_settings::number({declared_default})'
            effective = f'::pineforge::checked_settings::number({selected})'
            if value_type == "bool":
                effective = f'::pineforge::checked_settings::number(static_cast<bool>({selected}))'
        option_cpp = ", ".join(emitter._input_key_literal(option) for option in options)
        floor = "_pf_nan" if minimum == "nan" else minimum
        lines.append(f'            {{"{name}", "{value_type}", {default_value}, '
                     f'{{{option_cpp}}}, {floor}}},')
        override_effective.append(effective)
    lines.extend(["        };", "    }"])
    for name, options in enum_options.items():
        lines.append(f'    static std::string _pf_{name}_word(int _pf_value) {{')
        for index, option in enumerate(options):
            lines.append(f'        if (_pf_value == {index}) return "{option}";')
        lines.extend(['        return "invalid";', '    }'])
    lines.extend([
        "    void _pf_set_input_checked(const std::string& _pf_key, const std::string& _pf_value) {",
        "        if (_pf_refuse_failed_setting(nullptr)) throw ::pineforge::checked_settings::Error{PF_SETTINGS_RUN_FAILED, last_error_.c_str()};",
        '        ::pineforge::checked_settings::require(script_bars_processed() == 0 && stream_phase_ == StreamPhase::IDLE, "settings are frozen after execution begins", PF_SETTINGS_UNSUPPORTED);',
        "        const auto _pf_inputs = _pf_settings_inputs();",
        "        const ::pineforge::checked_settings::Setting* _pf_match = nullptr;",
        "        for (const auto& _pf_input : _pf_inputs) {",
        "            if (_pf_input.name != _pf_key) continue;",
        '            ::pineforge::checked_settings::require(_pf_match == nullptr, "ambiguous input key", PF_SETTINGS_UNSUPPORTED);',
        "            _pf_match = &_pf_input;",
        "        }",
        '        ::pineforge::checked_settings::require(_pf_match != nullptr, "unknown input key");',
        "        const auto _pf_canonical = ::pineforge::checked_settings::validate(*_pf_match, _pf_value);",
        "        set_input(_pf_key, _pf_canonical);",
        '        ::pineforge::checked_settings::require(inputs_.count(_pf_key) && inputs_.at(_pf_key) == _pf_canonical, "input was not installed", PF_SETTINGS_UNSUPPORTED);',
        "    }",
        "    void _pf_set_override_checked(const std::string& _pf_key, const std::string& _pf_value) {",
        "        if (_pf_refuse_failed_setting(nullptr)) throw ::pineforge::checked_settings::Error{PF_SETTINGS_RUN_FAILED, last_error_.c_str()};",
        '        ::pineforge::checked_settings::require(script_bars_processed() == 0 && stream_phase_ == StreamPhase::IDLE, "settings are frozen after execution begins", PF_SETTINGS_UNSUPPORTED);',
        "        for (const auto& _pf_override : _pf_settings_overrides()) {",
        "            if (_pf_override.name != _pf_key) continue;",
        "            auto _pf_alias = _pf_value;",
    ])
    for name, options in enum_options.items():
        lines.append(f'            if (_pf_key == "{name}") {{')
        for index, option in enumerate(options):
            aliases = [str(index)]
            if name == "close_entries_rule":
                aliases.append(option.lower())
            else:
                prefix = "strategy.commission." if name == "commission_type" else "strategy."
                aliases.append(prefix + option)
            condition = " || ".join(f'_pf_value == "{alias}"' for alias in aliases)
            lines.append(f'                if ({condition}) _pf_alias = "{option}";')
        lines.append("            }")
    lines.extend([
        "            const auto _pf_canonical = ::pineforge::checked_settings::validate(_pf_override, _pf_alias);",
        "            set_strategy_override(_pf_key, _pf_canonical);",
        "            return;",
        "        }",
        '        throw ::pineforge::checked_settings::Error{PF_SETTINGS_INVALID_ARGUMENT, "unknown override key"};',
        "    }",
        "    std::string _pf_settings_receipt() const {",
        '        ::pineforge::checked_settings::require(!_pf_setting_failed_, _pf_setting_failure_.empty() ? "legacy strategy setter failed" : _pf_setting_failure_.c_str(), PF_SETTINGS_RUN_FAILED);',
        '        std::string _pf_document = "{\\\"version\\\":1,\\\"inputs\\\":[";',
        "        const auto _pf_inputs = _pf_settings_inputs();",
    ])
    for index, (_metadata, effective) in enumerate(inputs):
        if index:
            lines.append("        _pf_document += ',';")
        lines.append(f"        _pf_document += ::pineforge::checked_settings::describe(_pf_inputs[{index}], {effective});")
    lines.extend([
        '        _pf_document += "],\\\"overrides\\\":[";',
        "        const auto _pf_overrides = _pf_settings_overrides();",
    ])
    for index, effective in enumerate(override_effective):
        if index:
            lines.append("        _pf_document += ',';")
        lines.append(f"        _pf_document += ::pineforge::checked_settings::describe(_pf_overrides[{index}], {effective});")
    lines.extend(['        return _pf_document + "]}";', "    }", "#endif"])


def emit_settings_exports(lines: list[str]) -> None:
    lines.extend([
        "#ifdef PF_SETTINGS_API_VERSION",
        "    uint32_t strategy_settings_api_version(void) { return PF_SETTINGS_API_VERSION; }",
        "    int strategy_create_checked(const char* params_json, void** out, char* error, size_t error_capacity) {",
        "        if (out) *out = nullptr;",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(out != nullptr, "strategy output pointer is null");',
        '            ::pineforge::checked_settings::require(!params_json || !*params_json, "params_json is reserved; use checked setters", PF_SETTINGS_UNSUPPORTED);',
        "            *out = new GeneratedStrategy();",
        "        });",
        "    }",
    ])
    for kind in ("input", "override"):
        lines.extend([
            f"    int strategy_set_{kind}_checked(void* s, const char* key, const char* value, char* error, size_t error_capacity) {{",
            "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
            '            ::pineforge::checked_settings::require(s && key && value, "null strategy, key or value");',
            f"            static_cast<GeneratedStrategy*>(s)->_pf_set_{kind}_checked(key, value);",
            "        });", "    }",
        ])
    lines.extend([
        "    int strategy_get_effective_settings(void* s, char* json, size_t capacity, size_t* required, char* error, size_t error_capacity) {",
        "        if (required) *required = 0;",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(s != nullptr, "null strategy");',
        "            ::pineforge::checked_settings::receipt(static_cast<GeneratedStrategy*>(s)->_pf_settings_receipt(), json, capacity, required);",
        "        });", "    }",
        "    int run_backtest_full_checked(void* s, Bar* bars, int n, const char* input_tf, const char* script_tf, int bar_magnifier, int magnifier_samples, int magnifier_dist, ReportC* out, char* error, size_t error_capacity) {",
        "        return ::pineforge::checked_settings::boundary(error, error_capacity, [&] {",
        '            ::pineforge::checked_settings::require(s && out && n >= 0 && (n == 0 || bars), "invalid batch arguments");',
        "            _pf_run_backtest_full_impl(s, bars, n, input_tf, script_tf, bar_magnifier, magnifier_samples, magnifier_dist, out);",
        "            const auto& _pf_error = static_cast<GeneratedStrategy*>(s)->last_error();",
        '            ::pineforge::checked_settings::require(_pf_error.empty(), _pf_error.c_str(), PF_SETTINGS_RUN_FAILED);',
        "        });", "    }", "#endif",
    ])
