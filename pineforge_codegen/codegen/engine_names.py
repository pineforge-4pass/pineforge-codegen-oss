"""Names imported by the generated file's ``using namespace pineforge``.

The inventory follows the public source-host include closure, plus the
unconditional TA, math, series, string, color and session headers.
It includes direct namespace declarations and declarations in inline
namespaces. Members of ordinary nested namespaces and classes are excluded:
``source::PineRequestDelivery`` does not import ``PineRequestDelivery``.
Optional header contributions are recorded separately below. Refresh this
source-derived seed with scripts/gen_engine_names.py on the paired SDK; its
strict native check compares the complete declaration and macro inventory.

Keep types, namespace names and values separate from the value-name policy in
helpers.py: an imported type need not cause an otherwise harmless Pine value
to be renamed. All three categories can collide with a file-scope UDT.
"""

ENGINE_TYPE_NAMES = frozenset("""
    AggregatedBar BacktestEngine Bar BirthCursor BirthCursorDomain
    BirthCursorPosition BorrowedBarFeedReader BrokerStateHashProvider
    BrokerStateHashSink CacheCapacity CacheDefaults CalendarPeriod
    CancellationCause CancellationResult CancellationState CancellationTarget
    CloseClaimRelease ClosedLotExcursion ClosedLotExcursionFacts CommissionType
    DynamicRingBuffer ExitLegActivation ExitLegActivationBounds ExitLegLifecycle
    FeedBarRow FeedReader IExecutionConsumer INativeDriverSink InputsMap
    IntrabarPath IsOwnedAllocator KahanWindowSum LedgerCachePolicy LotExcursionHook
    MagnifierDistribution MarketAdmissionDraft
    MarketAdmissionJournal MetricsPlainDeclarationV1 MetricsPlainInputsV1
    NativeAbortReporting NativeAccountObservation NativeAnchoredLevelView
    NativeAnchoredTrigger NativeAuxiliaryAppendError NativeAuxiliaryAppendResult
    NativeAuxiliaryFeed NativeBeginArgs NativeCalculationReason
    NativeCalculationTrigger NativeCloseExecution NativeCompleted NativeCompletion
    NativeCompletionKind NativeCoordinate NativeCurrentExecution
    NativeCurrentExecutionPreview NativeCurrentExecutionResult
    NativeCurrentPointView NativeCurrentPriceRule NativeCurrentQuoteKind
    NativeCurrentRefusal NativeDayPartition NativeDayPartitionScope
    NativeDecisionContext NativeDriverPoint NativeDriverStatistics NativeEventKind
    NativeEventRetention NativeExecutionConsumer NativeExecutionTermsFacts
    NativeFailed NativeFailure NativeFailureCode NativeFailureContext
    NativeFailureContextKind NativeFailureOperation NativeFeeKind
    NativeFeedTolerance NativeFxCurve NativeFxCurveError NativeFxCurveSetupResult
    NativeFxCurveValidation NativeGridRounding NativeInRunCause NativeInRunCursor
    NativeInRunRecipient NativeInputContext NativeInputPolicy
    NativeInputPreflightError NativeInputPreflightResult NativeInstrumentColumn
    NativeInstrumentFeed NativeLegacyTolerance NativeLifecycle NativeLifecycleKind
    NativeLiquidationCheck NativeLiquidationLevelBase NativeLiquidationSizing
    NativeLossLimit NativeMarginCallView NativeMarginCheckKind
    NativeMarginCheckPoint NativeMarginDecision NativeMarginEquityBasis
    NativeMarginModel NativeMarginRequirementView NativeMarketEvent
    NativeOpenBarView NativeOpenDirections NativeOpenLot NativeOpenLotView
    NativePathOrder NativePathPhase NativePhysicalPosition NativePrecommitVerdict
    NativePrecommitView NativePriceGrid NativePriceProvenance NativeReady
    NativeReportPolicy NativeRequestField NativeRiskAction NativeRiskDay
    NativeRiskLimits NativeRiskState NativeRunPhase NativeRunSpec NativeRunSpecError
    NativeRunSpecField NativeRunSpecValidation NativeRunning NativeSeriesSource
    NativeSetupResult NativeSetupStatus NativeSlotLabelPolicy NativeStateView
    NativeStrategyHost NativeTickContext NativeTimeframeBarContext
    NativeTimeframeSubscription NativeTrailState NativeUnconfigured
    NativeWorkingRequest OpenReportRowSource OpenedLotFillPoint OrderBirth
    OrderBirthCause OrderCancellationReceipt OwnedAllocator OwnedDeque OwnedStorage
    OwnedStorageTraits OwnedStorageUnsupported OwnedStorageUsage OwnedUnorderedMap
    PineExitActivationPolicy PineHistoricalBirthReach PositionSide
    PyramidEntry QtyType QuantityIntent QuantityIntentKind QuantityRequest
    QuantityReservation ReportC ReportEnvelopeV1 ReportMode ReservationExpansion
    ReservationExpansionCapture ReservationGrowthSource RunFailureArg
    RunFailureArgKind RunFailureArgs RunFailureClass RunFailureCode RunFailureInfo
    RunFailureValidation RunFailureValidationStatus RunFailureValue
    RuntimeBatchLease RuntimeContextEvaluator RuntimeContextEvent
    RuntimeContextHandle RuntimeContextKey RuntimeContextService
    RuntimeHistoryBudgetExhausted RuntimeHistoryOptions RuntimeHistorySource
    RuntimeHistoryUsage RuntimeStorageObservationIncomplete RuntimeSubdivision
    ScratchIoPolicy ScriptArray ScriptBucketCursor
    ScriptStrFormatValue ScriptString ScriptStringParts ScriptStringScope
    SecurityDiagC Series StrFormatValue StreamOrderAction SymInfo
    TimeframeAggregator TraceEntryC Trade TradeC TradeLedger TradeTick
    WindowCacheCapacity WindowCacheScope WindowCacheStats coded
""".split())

ENGINE_NAMESPACE_NAMES = frozenset("""
    admission broker checked_settings compat detail engine_script_run_v20
    execution exit_legs internal math metrics na_detail native_calendar
    native_driver_v5 native_fx_curve_v1 native_order native_run_spec_v3
    order_action order_cancellation_v1 pine_color report report_sequence
    report_sink reservation_expansion_v1 source ta
""".split())

ENGINE_VALUE_NAMES = frozenset("""
    active_native_day_partition build_native_day_partition
    calendar_day_open_local_ms calendar_period_for classify_run_failure
    clear_run_failure crosses_boundary detect_timeframe deterministic_random
    hhmm_to_minutes is_na kMsPerDay
    kNativeCalendarSemanticVersion kNativeConsumerSemanticVersion
    kNativeDriverSemanticVersion kReportSinkHeldEquityPoints kRunFailureCodeCount
    kSecPerDay local_dayofmonth local_dayofweek local_hour local_minute local_month
    local_second local_time_in_session_windows local_weekofyear local_year
    make_native_execution_consumer make_run_failure na native_auxiliary_feed_digest
    native_bar_structurally_valid native_canonical_input_completion
    native_confirmed_bar_label_admitted native_day_partition_index
    native_failed_run_identity native_failure_cause native_failure_context_bits
    native_failure_context_in_run native_failure_context_kind native_failure_cursor
    native_failure_has_cause native_failure_has_cursor native_failure_has_recipient
    native_failure_recipient native_feed_tolerance_enabled native_fx_curve_digest
    native_instrument_feeds_digest native_intrabar_path_digest native_late_day_opens
    native_legacy_tolerance_enabled native_margin_model_digest
    native_risk_limits_digest native_run_spec_digest
    native_timeframe_subscriptions_digest normalize_native_run_spec
    normalize_timezone_for_posix note_run_failure note_run_failure_unknown
    observe_owned_members observe_owned_storage passes_session_filter path_at
    pine_array_stop pine_collection_stop pine_dayofmonth pine_dayofweek
    pine_engine_invariant pine_hour pine_limit_stop pine_log_error pine_log_info
    pine_log_warning pine_minute pine_month pine_na_stop pine_no_data_stop
    pine_other_symbol_stop pine_random pine_runtime_error pine_second
    pine_session_ismarket pine_session_ispostmarket pine_session_ispremarket
    pine_str_format pine_str_format_time pine_str_match pine_str_split
    pine_str_tostring pine_string_stop pine_time pine_time_close pine_time_tradingday
    pine_timezone_stop pine_unsupported_stop pine_weekofyear pine_year
    preflight_native_inputs run_error_bar_index run_error_bar_prefix
    run_failure_args_of run_failure_code_class run_failure_code_name
    run_failure_code_of run_failure_code_retryable run_failure_value_of
    runtime_subdivide runtime_subdivision sample_price_path
    sample_price_path_volume_weighted script_str_format script_str_format_values
    session_covered_instant_ms session_day_index session_in_market
    session_in_postmarket session_in_premarket session_intraday_bucket_end_ms
    session_intraday_bucket_open_ms session_period_close_ms
    session_period_last_traded_close_ms session_period_open_ms session_trades_at
    session_trading_day_open_ms set_active_native_day_partition str_format
    str_format_time str_format_values str_match str_split str_tonumber str_tostring
    swap tf_change tf_is_daily tf_is_daily_or_higher tf_is_intraday tf_is_monthly
    tf_is_seconds tf_is_weekly tf_multiplier tf_ratio tf_to_seconds timeframe_time
    timeframe_time_close validate_native_auxiliary_feed validate_native_fx_curve
    validate_native_instrument_feeds validate_native_run_spec
    validate_native_timeframe_subscriptions validate_run_failure_name
    window_cache_capacity
""".split())

ENGINE_IMPORTED_NAMES = (
    ENGINE_TYPE_NAMES | ENGINE_NAMESPACE_NAMES | ENGINE_VALUE_NAMES
)


# Refreshed together with the baseline by scripts/gen_engine_names.py.
ENGINE_MACRO_NAMES = frozenset()

ENGINE_HEADER_NAMES = {
    "pineforge/map.hpp": frozenset("""
        MapCapacityError PineMap detail is_na
        is_pine_map_snapshot_value_v
    """.split()),
    "pineforge/matrix.hpp": frozenset("""
        NumericMatrix PineMatrix is_na
    """.split()),
    "pineforge/generic_matrix.hpp": frozenset("""
        GenericMatrix PineGenericMatrix detail is_na
    """.split()),
    "pineforge/drawing.hpp": frozenset("""
        Box BoxRec ChartPoint DrawingAccessStatus
        DrawingAllocationDiagnostic DrawingAllocationResult DrawingAllocationStatus DrawingArena
        DrawingBoundConfig DrawingKind Label LabelRec
        Line LineRec Linefill LinefillRec
        XLoc YLoc detail drawing_bound
        drawing_bound_from_declared_count drawing_bound_from_linefill_sample is_na pf_box_copy
        pf_box_delete pf_box_get_bottom pf_box_get_left pf_box_get_right
        pf_box_get_top pf_box_new pf_box_new_pts pf_box_set_bottom
        pf_box_set_bottom_right_point pf_box_set_left pf_box_set_lefttop pf_box_set_right
        pf_box_set_rightbottom pf_box_set_top pf_box_set_top_left_point pf_box_set_xloc
        pf_label_copy pf_label_delete pf_label_get_text pf_label_get_x
        pf_label_get_y pf_label_new pf_label_new_pt pf_label_set_point
        pf_label_set_text pf_label_set_x pf_label_set_xloc pf_label_set_xy
        pf_label_set_y pf_label_set_yloc pf_line_copy pf_line_delete
        pf_line_get_price pf_line_get_x1 pf_line_get_x2 pf_line_get_y1
        pf_line_get_y2 pf_line_new pf_line_new_pts pf_line_set_first_point
        pf_line_set_second_point pf_line_set_x1 pf_line_set_x2 pf_line_set_xloc
        pf_line_set_xy1 pf_line_set_xy2 pf_line_set_y1 pf_line_set_y2
        pf_linefill_delete pf_linefill_get_line1 pf_linefill_get_line2 pf_linefill_new
        pf_noop pf_point_x pf_require_live pine_drawing_error
        pine_drawing_id_exhausted
    """.split()),
}

