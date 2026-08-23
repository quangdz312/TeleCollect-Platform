"""Metric definitions shared by calibration and later dataset reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

ARM_DIMENSIONS = 6
ACTION_ENTROPY_BINS = 20
IDLE_EPSILON = 1e-6
LOCAL_ACTION_K = 5


def json_safe(value: Any) -> Any:
    """Convert numpy-rich metric data into stable JSON-compatible values."""

    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class TrajectoryEpisode:
    """One observed rollout, including failures and technical exceptions."""

    task: str
    tool_name: str
    quality: str
    profile_version: str
    base_seed: int
    task_code: int
    episode_index: int
    stream_code: int
    environment_seed: int
    success: bool
    outcome: str
    episode_length: int
    terminal_reason: str
    failure_stage: str | None
    terminal_phase: str
    retry_count: int
    sampled_variation: Mapping[str, Any]
    semantic_noise_magnitude: float
    common_noise_magnitude: float
    planned_actions: Sequence[np.ndarray] = field(default_factory=tuple)
    executed_actions: Sequence[np.ndarray] = field(default_factory=tuple)
    observations: Sequence[Mapping[str, np.ndarray]] = field(default_factory=tuple)
    next_observations: Sequence[Mapping[str, np.ndarray]] = field(default_factory=tuple)
    phases: Sequence[str] = field(default_factory=tuple)
    action_low: np.ndarray = field(default_factory=lambda: np.full(7, -1.0))
    action_high: np.ndarray = field(default_factory=lambda: np.full(7, 1.0))
    target_identity_valid: bool = True
    technical_exception: str | None = None
    runtime_seconds: float = 0.0

    def summary(self) -> dict[str, Any]:
        validation = technical_validation([self])
        aligned = min(len(self.planned_actions), len(self.executed_actions))
        different = sum(
            not np.array_equal(
                np.asarray(self.planned_actions[index]),
                np.asarray(self.executed_actions[index]),
            )
            for index in range(aligned)
        )
        return json_safe(
            {
                'task': self.task,
                'tool_name': self.tool_name,
                'quality': self.quality,
                'profile_version': self.profile_version,
                'base_seed': self.base_seed,
                'task_code': self.task_code,
                'episode_index': self.episode_index,
                'stream_code': self.stream_code,
                'environment_seed': self.environment_seed,
                'success': self.success,
                'outcome': self.outcome,
                'episode_length': self.episode_length,
                'terminal_reason': self.terminal_reason,
                'failure_stage': self.failure_stage,
                'terminal_phase': self.terminal_phase,
                'retry_count': self.retry_count,
                'sampled_variation': self.sampled_variation,
                'semantic_noise_magnitude': self.semantic_noise_magnitude,
                'common_noise_magnitude': self.common_noise_magnitude,
                'planned_executed_difference_rate': (
                    float(different / aligned) if aligned else 0.0
                ),
                'target_identity_valid': self.target_identity_valid,
                'technical_exception': self.technical_exception,
                'runtime_seconds': self.runtime_seconds,
                'technical_valid': validation['valid'],
                'technical_reasons': validation['reasons'],
            },
        )


def _flatten_actions(episodes: Sequence[TrajectoryEpisode]) -> np.ndarray:
    arrays = [
        np.asarray(action, dtype=np.float64)
        for episode in episodes
        for action in episode.executed_actions
        if np.asarray(action).shape == (7,)
    ]
    return np.asarray(arrays, dtype=np.float64) if arrays else np.empty((0, 7))


def _first_observation(
    episode: TrajectoryEpisode,
) -> Mapping[str, np.ndarray] | None:
    return episode.observations[0] if episode.observations else None


def initial_state_variance(episodes: Sequence[TrajectoryEpisode]) -> dict[str, Any]:
    eef = []
    objects = []
    for episode in episodes:
        observation = _first_observation(episode)
        if observation is None:
            continue
        eef_value = np.asarray(observation.get('robot0_eef_pos'))
        object_value = np.asarray(observation.get('object'))
        if eef_value.shape == (3,) and np.isfinite(eef_value).all():
            eef.append(eef_value.astype(np.float64))
        if object_value.ndim == 1 and np.isfinite(object_value).all():
            objects.append(object_value.astype(np.float64))

    def variance(values: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        if not values or len({value.shape for value in values}) != 1:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        array = np.asarray(values, dtype=np.float64)
        result = np.var(array, axis=0)
        return result, np.sqrt(result)

    eef_variance, eef_std = variance(eef)
    object_variance, object_std = variance(objects)
    return json_safe(
        {
            'initial_frames': len(eef),
            'eef_variance': eef_variance,
            'eef_std': eef_std,
            'eef_variance_mean': float(np.mean(eef_variance)) if eef_variance.size else 0.0,
            'object_variance': object_variance,
            'object_std': object_std,
            'object_variance_mean': (
                float(np.mean(object_variance)) if object_variance.size else 0.0
            ),
            'coverage_nonzero': bool(
                eef_variance.size and np.any(eef_variance > 0.0)
                and object_variance.size and np.any(object_variance > 0.0)
            ),
        },
    )


def convex_hull_coverage(episodes: Sequence[TrajectoryEpisode]) -> dict[str, Any]:
    positions = [
        np.asarray(observation.get('robot0_eef_pos'), dtype=np.float64)
        for episode in episodes
        for observation in episode.observations
        if np.asarray(observation.get('robot0_eef_pos')).shape == (3,)
        and np.isfinite(np.asarray(observation.get('robot0_eef_pos'))).all()
    ]
    if not positions:
        return {'points': 0, 'unique_points': 0, 'volume': 0.0, 'warning': 'no EEF positions'}
    points = np.asarray(positions, dtype=np.float64)
    unique = np.unique(points, axis=0)
    if len(unique) < 4:
        return {
            'points': len(points),
            'unique_points': len(unique),
            'volume': 0.0,
            'warning': 'fewer than four unique 3D points',
        }
    try:
        from scipy.spatial import ConvexHull, QhullError

        volume = float(ConvexHull(unique).volume)
        warning = None
    except (QhullError, ValueError) as exc:
        volume = 0.0
        warning = f'convex hull unavailable: {type(exc).__name__}'
    return {
        'points': len(points),
        'unique_points': len(unique),
        'volume': volume,
        'warning': warning,
    }


def local_action_variance(
    episodes: Sequence[TrajectoryEpisode],
    *,
    k: int = LOCAL_ACTION_K,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for episode in episodes:
        count = min(
            len(episode.observations),
            len(episode.executed_actions),
            len(episode.phases),
        )
        for index in range(count):
            eef = np.asarray(
                episode.observations[index].get('robot0_eef_pos'),
                dtype=np.float64,
            )
            action = np.asarray(episode.executed_actions[index], dtype=np.float64)
            if eef.shape == (3,) and action.shape == (7,) and np.isfinite(eef).all() and np.isfinite(action).all():
                grouped.setdefault(str(episode.phases[index]), []).append((eef, action[:6]))

    phase_values: dict[str, float] = {}
    weighted_sum = 0.0
    weighted_count = 0
    for phase in sorted(grouped):
        pairs = grouped[phase]
        if len(pairs) < 2:
            phase_values[phase] = 0.0
            continue
        states = np.asarray([pair[0] for pair in pairs], dtype=np.float64)
        actions = np.asarray([pair[1] for pair in pairs], dtype=np.float64)
        neighbors = min(k, len(states) - 1)
        from scipy.spatial import cKDTree

        _distance, indices = cKDTree(states).query(states, k=neighbors + 1)
        indices = np.asarray(indices)
        if indices.ndim == 1:
            indices = indices[:, None]
        neighbor_actions = actions[indices[:, 1:]]
        mse = np.mean((neighbor_actions - actions[:, None, :]) ** 2, axis=2)
        value = float(np.mean(mse)) if mse.size else 0.0
        phase_values[phase] = value
        weighted_sum += value * len(states)
        weighted_count += len(states)
    return {
        'arm_dimensions': ARM_DIMENSIONS,
        'k': k,
        'state_features': ['robot0_eef_pos'],
        'phase_values': phase_values,
        'value': float(weighted_sum / weighted_count) if weighted_count else 0.0,
    }


def action_entropy(
    episodes: Sequence[TrajectoryEpisode],
    *,
    bins: int = ACTION_ENTROPY_BINS,
    idle_epsilon: float = IDLE_EPSILON,
) -> dict[str, Any]:
    actions = _flatten_actions(episodes)
    if not len(actions):
        zeros = [0.0] * ARM_DIMENSIONS
        return {
            'arm_dimensions': ARM_DIMENSIONS,
            'bins': bins,
            'entropy': zeros,
            'normalized_entropy': zeros,
            'mean_entropy': 0.0,
            'mean_normalized_entropy': 0.0,
            'idle_epsilon': idle_epsilon,
            'idle_ratio': 0.0,
            'warning': 'no valid actions',
        }
    low = np.asarray(episodes[0].action_low, dtype=np.float64)[:ARM_DIMENSIONS]
    high = np.asarray(episodes[0].action_high, dtype=np.float64)[:ARM_DIMENSIONS]
    entropies = []
    for dimension in range(ARM_DIMENSIONS):
        histogram, _edges = np.histogram(
            actions[:, dimension],
            bins=bins,
            range=(float(low[dimension]), float(high[dimension])),
        )
        probabilities = histogram[histogram > 0].astype(np.float64)
        probabilities /= probabilities.sum()
        entropies.append(float(-np.sum(probabilities * np.log(probabilities))))
    normalization = float(np.log(bins))
    normalized = [value / normalization for value in entropies]
    idle_ratio = float(np.mean(np.all(np.abs(actions[:, :ARM_DIMENSIONS]) <= idle_epsilon, axis=1)))
    return {
        'arm_dimensions': ARM_DIMENSIONS,
        'bins': bins,
        'entropy': entropies,
        'normalized_entropy': normalized,
        'mean_entropy': float(np.mean(entropies)),
        'mean_normalized_entropy': float(np.mean(normalized)),
        'idle_epsilon': idle_epsilon,
        'idle_ratio': idle_ratio,
        'warning': None,
    }


def batch_outcomes(
    episodes: Sequence[TrajectoryEpisode],
    *,
    requested_count: int,
) -> dict[str, Any]:
    observed = len(episodes)
    counts = Counter(episode.outcome for episode in episodes)
    lengths = np.asarray([episode.episode_length for episode in episodes], dtype=np.float64)
    median = float(np.median(lengths)) if len(lengths) else 0.0
    return {
        'requested_count': requested_count,
        'observed_count': observed,
        'count_match': requested_count == observed,
        'successes': counts['success'],
        'failures': counts['failure'],
        'horizons': counts['horizon'],
        'technical_exceptions': counts['technical_exception'],
        'success_rate': float(counts['success'] / observed) if observed else 0.0,
        'failure_rate': float(counts['failure'] / observed) if observed else 0.0,
        'horizon_rate': float(counts['horizon'] / observed) if observed else 0.0,
        'technical_exception_rate': (
            float(counts['technical_exception'] / observed) if observed else 0.0
        ),
        'episode_length': {
            'mean': float(np.mean(lengths)) if len(lengths) else 0.0,
            'std': float(np.std(lengths)) if len(lengths) else 0.0,
            'median': median,
            'p95': float(np.percentile(lengths, 95)) if len(lengths) else 0.0,
            'minimum': int(np.min(lengths)) if len(lengths) else 0,
            'maximum': int(np.max(lengths)) if len(lengths) else 0,
            'outlier_rate': (
                float(np.mean(lengths > 3.0 * median)) if len(lengths) and median > 0 else 0.0
            ),
        },
        'retry_rate': float(np.mean([episode.retry_count > 0 for episode in episodes])) if observed else 0.0,
        'retry_mean': float(np.mean([episode.retry_count for episode in episodes])) if observed else 0.0,
        'failure_stages': dict(sorted(Counter(
            episode.failure_stage or episode.terminal_phase
            for episode in episodes
            if not episode.success
        ).items())),
        'terminal_reasons': dict(sorted(Counter(
            episode.terminal_reason for episode in episodes
        ).items())),
    }


def corrupted_frame_rate(episodes: Sequence[TrajectoryEpisode]) -> dict[str, Any]:
    corrupted = 0
    total = 0
    reasons: Counter[str] = Counter()
    for episode in episodes:
        lengths = {
            'actions': len(episode.executed_actions),
            'planned_actions': len(episode.planned_actions),
            'observations': len(episode.observations),
            'next_observations': len(episode.next_observations),
            'phases': len(episode.phases),
        }
        frame_count = max([episode.episode_length, *lengths.values()], default=0)
        total += frame_count
        for index in range(frame_count):
            frame_reasons: set[str] = set()
            if any(index >= length for length in lengths.values()):
                frame_reasons.add('missing_or_misaligned_field')
            if index < len(episode.executed_actions):
                action = np.asarray(episode.executed_actions[index])
                if action.shape != (7,):
                    frame_reasons.add('invalid_action_shape')
                elif not np.isfinite(action).all():
                    frame_reasons.add('nonfinite_action')
            for field_name, observations in (
                ('observation', episode.observations),
                ('next_observation', episode.next_observations),
            ):
                if index >= len(observations):
                    continue
                observation = observations[index]
                if not observation:
                    frame_reasons.add(f'empty_{field_name}')
                elif any(not np.isfinite(np.asarray(value)).all() for value in observation.values()):
                    frame_reasons.add(f'nonfinite_{field_name}')
            if index + 1 < len(episode.observations) and index < len(episode.next_observations):
                current_next = episode.next_observations[index]
                following = episode.observations[index + 1]
                if tuple(current_next) != tuple(following) or any(
                    not np.array_equal(np.asarray(current_next[key]), np.asarray(following[key]))
                    for key in current_next
                ):
                    frame_reasons.add('next_observation_chain_mismatch')
            if frame_reasons:
                corrupted += 1
                reasons.update(frame_reasons)
    return {
        'total_frames': total,
        'corrupted_frames': corrupted,
        'rate': float(corrupted / total) if total else 0.0,
        'reasons': dict(sorted(reasons.items())),
    }


def technical_validation(episodes: Sequence[TrajectoryEpisode]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    valid_actions = 0
    total_actions = 0
    valid_observations = 0
    total_observations = 0
    for episode in episodes:
        low = np.asarray(episode.action_low)
        high = np.asarray(episode.action_high)
        if episode.technical_exception:
            reasons['technical_exception'] += 1
        if not episode.target_identity_valid:
            reasons['target_identity_changed'] += 1
        if episode.retry_count > 1:
            reasons['retry_cap_exceeded'] += 1
        for action in episode.executed_actions:
            total_actions += 1
            array = np.asarray(action)
            if (
                array.shape == (7,)
                and np.isfinite(array).all()
                and low.shape == (7,)
                and high.shape == (7,)
                and np.all(array >= low)
                and np.all(array <= high)
            ):
                valid_actions += 1
            else:
                reasons['invalid_action'] += 1
        for observation in list(episode.observations) + list(episode.next_observations):
            total_observations += 1
            if observation and all(np.isfinite(np.asarray(value)).all() for value in observation.values()):
                valid_observations += 1
            else:
                reasons['invalid_observation'] += 1
    corruption = corrupted_frame_rate(episodes)
    if corruption['rate'] != 0.0:
        reasons['corrupted_frames'] += corruption['corrupted_frames']
    return {
        'valid': not reasons,
        'reasons': dict(sorted(reasons.items())),
        'action_valid_rate': float(valid_actions / total_actions) if total_actions else 1.0,
        'observation_valid_rate': (
            float(valid_observations / total_observations) if total_observations else 1.0
        ),
        'target_identity_valid_rate': (
            float(np.mean([episode.target_identity_valid for episode in episodes]))
            if episodes else 1.0
        ),
        'retry_contract_valid_rate': (
            float(np.mean([episode.retry_count <= 1 for episode in episodes]))
            if episodes else 1.0
        ),
        'corrupted_frame_rate': corruption['rate'],
    }


def noise_magnitudes(variation: Mapping[str, Any]) -> tuple[float, float]:
    common_names = {
        'landmark_position_bias',
        'landmark_orientation_bias',
        'arm_gain',
        'arm_bias',
    }
    ignored = {'quality', 'noise_scale', 'schedule', 'retry_cap'}

    def numeric_norm(value: Any, *, identity: float = 0.0) -> float:
        if isinstance(value, bool) or value is None:
            return 0.0
        array = np.asarray(value, dtype=np.float64)
        return float(np.linalg.norm(array - identity))

    common = 0.0
    semantic = 0.0
    for name, value in variation.items():
        if name in ignored:
            continue
        if name == 'arm_gain':
            common += numeric_norm(value, identity=1.0)
        elif name in common_names:
            common += numeric_norm(value)
        elif name.endswith('_enabled'):
            continue
        else:
            semantic += numeric_norm(value)
    return semantic, common
