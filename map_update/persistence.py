"""Assumption-based duration prediction, separate from permission to edit a map.

The four hidden states are temporary/long_term crossed with active/cleared.
Class never changes, while active -> cleared has an exponential lifetime; cleared
is absorbing. Class posteriors adapt to observations, but the parameters below
are chosen assumptions, not parameters learned from labelled data. Predictions
are conditional on the hypothesis that this event is a real change initially.

One latest informative observation represents each fixed temporal block. Updating
that representative recomputes from the block's prior, rather than multiplying
correlated camera/frame evidence. Prefix traces are causal. Unknown observations
only advance model time; they never constitute observed clearance. This module
neither evaluates nor bypasses the independent structural map-writing gate.
"""

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class PersistencePolicy:
    temporary_mean_s: float = 3600.
    long_term_mean_s: float = 30 * 86400.
    horizon_s: float = 86400.
    evidence_block_s: float = 30.
    independent_visit_gap_s: float = 3600.
    movable_temporary_prior: float = .8
    structural_temporary_prior: float = .2
    unknown_temporary_prior: float = .5
    initial_active_probability: float = .99
    emission_floor: float = 1e-6

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('invalid persistence policy: ' + name)
            if name.endswith('_prior') or name in {'emission_floor', 'initial_active_probability'}:
                if not 0 < value < (0.5 if name == 'emission_floor' else 1):
                    raise ValueError('invalid persistence policy: ' + name)
            elif value <= 0:
                raise ValueError('invalid persistence policy: ' + name)
        if self.long_term_mean_s <= self.temporary_mean_s:
            raise ValueError('long_term_mean_s must exceed temporary_mean_s')
        if self.evidence_block_s < 1e-9:
            raise ValueError('evidence_block_s must span at least one nanosecond')


_SEMANTICS = {'movable_occupancy': 'movable_temporary_prior',
              'structural_marking': 'structural_temporary_prior',
              'unknown': 'unknown_temporary_prior'}
_OBSERVATIONS = {'supports_change', 'supports_prior', 'unknown'}
_STATES = ('temporary_active', 'temporary_cleared', 'long_term_active', 'long_term_cleared')


def _timestamp(value):
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise ValueError('timestamp_ns must be a nonnegative decimal string')
    return int(value)


def _identity(value, field):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int)) or value == ''):
        raise ValueError(field + ' must be a nonempty string, integer or null')
    return None if value is None else str(value)


def _frames(samples, cutoff):
    """Collapse camera duplicates at one timestamp without multiplying evidence."""
    grouped, identity_times = {}, {}
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError('samples must contain objects')
        stamp = _timestamp(sample.get('timestamp_ns'))
        if cutoff is not None and stamp > cutoff:
            continue
        observation = sample.get('observation')
        if observation not in _OBSERVATIONS:
            raise ValueError('invalid persistence observation')
        quality = sample.get('quality', 1.)
        if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not math.isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError('quality must be finite and between zero and one')
        full = sample.get('fully_observed', False)
        if type(full) is not bool:
            raise ValueError('fully_observed must be boolean')
        frame_id = _identity(sample.get('frame_id'), 'frame_id')
        visit_id = _identity(sample.get('visit_id'), 'visit_id')
        if frame_id is not None:
            identity = visit_id, frame_id
            if identity in identity_times and identity_times[identity] != stamp:
                raise ValueError('frame_id has conflicting timestamps within a visit')
            identity_times[identity] = stamp
        frame = grouped.setdefault(stamp, {'ids': set(), 'visits': set(), 'directions': {},
                                          'assumed_quality': False})
        frame['ids'].add(frame_id or str(stamp))
        frame['visits'].add(visit_id)
        frame['assumed_quality'] |= 'quality' not in sample
        if quality > 0 and (observation == 'supports_change' or observation == 'supports_prior' and full):
            frame['directions'][observation] = max(quality, frame['directions'].get(observation, 0.))
    result = []
    for stamp, frame in sorted(grouped.items()):
        if len(frame['visits']) != 1:
            raise ValueError('a timestamp cannot identify multiple independent visits')
        directions = frame['directions']
        observation = next(iter(directions)) if len(directions) == 1 else 'unknown'
        result.append({'timestamp_ns': str(stamp), 'frame_ids': sorted(frame['ids']),
                       'visit_id': next(iter(frame['visits'])), 'observation': observation,
                       'quality': directions.get(observation, 0.),
                       'fully_observed': observation == 'supports_prior',
                       'conflicting_frame_observations': len(directions) > 1,
                       'quality_assumed': frame['assumed_quality']})
    return result


def _propagate(state, elapsed_s, policy):
    out = list(state)
    for index, lifetime in ((0, policy.temporary_mean_s), (2, policy.long_term_mean_s)):
        cleared = state[index] * -math.expm1(-elapsed_s / lifetime)
        out[index] = state[index] * math.exp(-elapsed_s / lifetime)
        out[index + 1] += cleared
    return out


def _emit(state, observation, quality, policy):
    correct = min(1. - policy.emission_floor, .5 + .5 * quality)
    active_likelihood = correct if observation == 'supports_change' else 1. - correct
    weighted = [value * (active_likelihood if index % 2 == 0 else 1. - active_likelihood)
                for index, value in enumerate(state)]
    total = sum(weighted)
    return [value / total for value in weighted]


def _belief(state):
    temporary, long_term = state[0] + state[1], state[2] + state[3]
    total = temporary + long_term
    return {'temporary': temporary / total, 'long_term': long_term / total}


def _prediction(belief):
    if math.isclose(belief['temporary'], belief['long_term'], abs_tol=1e-12, rel_tol=0):
        return 'undecided'
    return max(belief, key=belief.get)


def _independent_visits(frames, policy):
    # Blocks/cameras within one visit do not manufacture independent revisits.
    first = {}
    for frame in frames:
        if frame['observation'] != 'unknown':
            first.setdefault(frame['visit_id'], int(frame['timestamp_ns']))
    last, count = None, 0
    for stamp in sorted(first.values()):
        if last is None or (stamp - last) / 1e9 >= policy.independent_visit_gap_s:
            count += 1
            last = stamp
    return count


def predict_persistence(event, policy=None):
    """Return a conditional duration forecast without claiming verified permanence.

    ``event`` supplies semantic_kind and samples with timestamp_ns (decimal string),
    observation and quality (model weight, not calibrated correctness). Optional
    frame_id/visit_id permit identity checks. supports_prior requires explicit
    fully_observed=True. An optional as_of_timestamp_ns excludes future samples.
    All dataset names, ground-truth fields and other unused metadata are ignored.
    The forecast horizon is measured from the final sample or supplied as-of time.
    """
    if not isinstance(event, dict):
        raise ValueError('event must be an object')
    if policy is None:
        policy = PersistencePolicy()
    elif isinstance(policy, dict):
        policy = PersistencePolicy(**policy)
    elif not isinstance(policy, PersistencePolicy):
        raise ValueError('policy must be PersistencePolicy or a mapping')
    semantic = event.get('semantic_kind', 'unknown')
    if semantic not in _SEMANTICS:
        raise ValueError('unsupported semantic_kind')
    samples = event.get('samples', [])
    if not isinstance(samples, list):
        raise ValueError('samples must be a list')
    cutoff = _timestamp(event['as_of_timestamp_ns']) if 'as_of_timestamp_ns' in event else None
    frames = _frames(samples, cutoff)
    prior = getattr(policy, _SEMANTICS[semantic])
    active = policy.initial_active_probability
    state = [prior * active, prior * (1. - active),
             (1. - prior) * active, (1. - prior) * (1. - active)]
    trace, evidence_blocks = [], set()
    previous_time = block_start = block_id = None
    block_prior = None
    clearance_observed = False
    block_ns = round(policy.evidence_block_s * 1e9)
    for frame in frames:
        stamp = int(frame['timestamp_ns'])
        current_block = stamp // block_ns
        elapsed = (stamp - previous_time) / 1e9 if previous_time is not None else 0.
        state = _propagate(state, elapsed, policy)
        if current_block != block_id:
            block_id, block_start, block_prior = current_block, stamp, list(state)
        informative = frame['observation'] != 'unknown'
        replacing = informative and current_block in evidence_blocks
        if informative:
            # A later frame replaces this block's representative. Reuse the state
            # before any block emission, so higher FPS cannot multiply likelihoods.
            state = _emit(_propagate(block_prior, (stamp - block_start) / 1e9, policy),
                          frame['observation'], frame['quality'], policy)
            evidence_blocks.add(current_block)
            clearance_observed |= frame['observation'] == 'supports_prior'
        belief = _belief(state)
        trace.append({**frame, 'block_id': str(current_block), 'emission_applied': informative,
                      'replaces_within_block_evidence': replacing,
                      'evidence_count': len(evidence_blocks), 'class_belief': belief,
                      'joint_state_belief': dict(zip(_STATES, state)),
                      'predicted_duration': _prediction(belief),
                      'clearance_observed': clearance_observed})
        previous_time = stamp
    forecast_time = cutoff if cutoff is not None else previous_time
    if previous_time is not None and forecast_time > previous_time:
        state = _propagate(state, (forecast_time - previous_time) / 1e9, policy)
    belief = _belief(state)
    log_prior_odds = math.log(prior) - math.log1p(-prior)
    # Finite emission floors preserve both classes for ordinary recorded windows.
    log_posterior_odds = math.log(max(belief['temporary'], 1e-300)) - math.log(max(belief['long_term'], 1e-300))
    log_bayes_factor = log_posterior_odds - log_prior_odds
    prior_dominant = abs(log_prior_odds) > 1e-12 and abs(log_prior_odds) >= abs(log_bayes_factor)
    active_probability = state[0] + state[2]
    survival = (state[0] * math.exp(-policy.horizon_s / policy.temporary_mean_s)
                + state[2] * math.exp(-policy.horizon_s / policy.long_term_mean_s))
    reliability = event.get('reliability')
    return {
        'schema': 'conditional-duration-prediction-1.0',
        'model': 'four_state_bayesian_exponential_survival_mixture',
        'semantic_kind': semantic, 'policy': asdict(policy),
        'predicted_duration': _prediction(belief), 'class_belief': belief,
        'class_prior': {'temporary': prior, 'long_term': 1. - prior},
        'joint_state_belief': dict(zip(_STATES, state)),
        'conditional_on_change': True, 'belief_is_calibrated_probability': False,
        'calibrated': False,
        'parameters_learned_from_data': False,
        'probability_interpretation': 'model probabilities conditional on an initially real change and stated lifetime/observation assumptions; not measured correctness',
        'quality_interpretation': 'observation model weight; missing quality defaults to assumed 1; unknown observations have no emission',
        'quality_assumed': any(frame['quality_assumed'] for frame in frames),
        'semantic_prior_dominance': prior_dominant,
        'prediction_basis': 'semantic_prior' if prior_dominant else 'uninformative_prior' if abs(log_bayes_factor) < 1e-12 else 'temporal_evidence',
        'temporal_log_bayes_factor_temporary_over_long_term': log_bayes_factor,
        'evidence_count': len(evidence_blocks),
        'evidence_unit': 'one latest informative frame per temporal block, not an independent visit',
        'independent_visit_count': _independent_visits(frames, policy),
        'observed_visit_count': len({frame['visit_id'] for frame in frames}),
        'horizon_s': policy.horizon_s, 'horizon_survival_probability': survival,
        'horizon_survival_definition': 'joint probability active now and still active at forecast horizon, conditional on the initial change hypothesis',
        'horizon_survival_given_currently_active': survival / active_probability if active_probability > 0 else None,
        'active_probability': active_probability,
        'clearance_observed': clearance_observed,
        'duration_verification': 'not_verified_by_this_predictor',
        'base_map_write_eligible': False,
        'reliability_status': reliability.get('status', 'not_assessed') if isinstance(reliability, dict) else 'not_assessed',
        'origin_timestamp_ns': frames[0]['timestamp_ns'] if frames else None,
        'as_of_timestamp_ns': str(forecast_time) if forecast_time is not None else None,
        'posterior_trace': trace,
    }
