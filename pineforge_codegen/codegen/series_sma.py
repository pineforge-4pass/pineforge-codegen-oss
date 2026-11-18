"""Caller-owned evaluation checkpoints for the engine's series SMA primitive.

The engine primitive appends on compute and replaces one invocation on
recompute. A written Pine call can run repeatedly during one evaluation,
so replacing an evaluation must restore its entire pre-evaluation object,
then append every reached invocation again. The chart/requested evaluator
supplies its existing history-slot decision; source equality is irrelevant.
"""


SERIES_SMA_CPP = r"""
class _PFSeriesSma {
    pineforge::source::SeriesSma _pf_state_;
    pineforge::source::SeriesSma _pf_before_;
    bool _pf_replace_ = false;
public:
    void begin_evaluation(bool _pf_advances) {
        if (_pf_advances) _pf_before_ = _pf_state_;
        _pf_replace_ = !_pf_advances;
    }

    double compute(double _pf_source, double _pf_length) {
        if (_pf_replace_) {
            // Validate and evaluate on a complete saved object before
            // publishing the replacement. A stopped call changes no state.
            auto _pf_candidate = _pf_before_;
            const double _pf_result = _pf_candidate.compute(_pf_source, _pf_length);
            _pf_state_ = std::move(_pf_candidate);
            _pf_replace_ = false;
            return _pf_result;
        }
        return _pf_state_.compute(_pf_source, _pf_length);
    }

    double recompute(double _pf_source, double _pf_length) {
        // begin_evaluation restores once; later loop/helper reaches append,
        // even when the evaluator's shared history decision remains false.
        return compute(_pf_source, _pf_length);
    }

    template <typename _PFArguments>
    double compute(_PFArguments&& _pf_arguments) {
        const auto _pf_values = _pf_arguments();
        return compute(_pf_values.first, _pf_values.second);
    }

    template <typename _PFArguments>
    double recompute(_PFArguments&& _pf_arguments) {
        return compute(std::forward<_PFArguments>(_pf_arguments));
    }

    void observe_owned_storage(pineforge::OwnedStorage& _pf_out) const noexcept {
        pineforge::observe_owned_members(_pf_out, _pf_state_, _pf_before_);
    }
};
"""
