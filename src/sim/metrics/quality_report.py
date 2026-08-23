"""Stable JSON and Markdown quality reports for TeleCollect batches."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np

from .quality_metrics import (
    TrajectoryEpisode,
    action_entropy,
    batch_outcomes,
    convex_hull_coverage,
    corrupted_frame_rate,
    initial_state_variance,
    json_safe,
    local_action_variance,
    technical_validation,
)

REPORT_SCHEMA_VERSION = '1'


def _distribution(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {'minimum': 0.0, 'mean': 0.0, 'maximum': 0.0}
    return {
        'minimum': float(np.min(array)),
        'mean': float(np.mean(array)),
        'maximum': float(np.max(array)),
    }


def build_quality_report(
    episodes: Sequence[TrajectoryEpisode],
    *,
    requested_count: int,
) -> dict[str, Any]:
    """Compute all v1.0 metrics without dropping failed or horizon episodes."""

    task_values = sorted({episode.task for episode in episodes})
    quality_values = sorted({episode.quality for episode in episodes})
    profile_versions = sorted({episode.profile_version for episode in episodes})
    base_seeds = sorted({episode.base_seed for episode in episodes})
    outcome = batch_outcomes(episodes, requested_count=requested_count)
    entropy = action_entropy(episodes)
    corruption = corrupted_frame_rate(episodes)
    technical = technical_validation(episodes)
    aligned = sum(min(len(ep.planned_actions), len(ep.executed_actions)) for ep in episodes)
    differences = sum(
        not np.array_equal(np.asarray(ep.planned_actions[index]), np.asarray(ep.executed_actions[index]))
        for ep in episodes
        for index in range(min(len(ep.planned_actions), len(ep.executed_actions)))
    )
    warnings = []
    if entropy['mean_normalized_entropy'] < 0.02 and aligned:
        warnings.append('mean normalized arm entropy is near zero')
    if not outcome['count_match']:
        warnings.append('requested_count does not match observed_count')
    if corruption['rate'] != 0.0:
        warnings.append('corrupted frame rate is non-zero')
    if not technical['valid']:
        warnings.append('technical validation failed')

    report = {
        'report_schema_version': REPORT_SCHEMA_VERSION,
        'task': task_values[0] if len(task_values) == 1 else task_values,
        'quality': quality_values[0] if len(quality_values) == 1 else quality_values,
        'profile_version': (
            profile_versions[0] if len(profile_versions) == 1 else profile_versions
        ),
        'base_seed': base_seeds[0] if len(base_seeds) == 1 else base_seeds,
        'episode_indices': sorted(episode.episode_index for episode in episodes),
        'outcome': outcome,
        'dataset_success_rate': {
            'episodes': outcome['observed_count'],
            'successes': outcome['successes'],
            'full_success_rate': outcome['success_rate'],
        },
        'initial_state_variance': initial_state_variance(episodes),
        'convex_hull_of_state_space': convex_hull_coverage(episodes),
        'local_action_variance_index': local_action_variance(episodes),
        'action_entropy': entropy,
        'corrupted_frame_rate': corruption,
        'technical_validation': technical,
        'perturbation_diagnostics': {
            'semantic_noise_magnitude': _distribution(
                [episode.semantic_noise_magnitude for episode in episodes],
            ),
            'common_runtime_noise_magnitude': _distribution(
                [episode.common_noise_magnitude for episode in episodes],
            ),
            'planned_executed_difference_rate': (
                float(differences / aligned) if aligned else 0.0
            ),
            'retry_count_distribution': dict(sorted(Counter(
                str(episode.retry_count) for episode in episodes
            ).items())),
            'failure_stage_distribution': outcome['failure_stages'],
            'terminal_reason_distribution': outcome['terminal_reasons'],
        },
        'warnings': warnings,
    }
    return json_safe(report)


def report_markdown(report: dict[str, Any], *, title: str = 'TeleCollect quality report') -> str:
    """Render a compact deterministic Markdown summary."""

    outcome = report['outcome']
    length = outcome['episode_length']
    entropy = report['action_entropy']
    local = report['local_action_variance_index']
    hull = report['convex_hull_of_state_space']
    initial = report['initial_state_variance']
    diagnostics = report['perturbation_diagnostics']
    lines = [
        f'# {title}',
        '',
        '| Task | Quality | Requested | Observed | Success | Failure | Horizon | Technical |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
        (
            f"| {report['task']} | {report['quality']} | {outcome['requested_count']} | "
            f"{outcome['observed_count']} | {outcome['success_rate']:.3f} | "
            f"{outcome['failure_rate']:.3f} | {outcome['horizon_rate']:.3f} | "
            f"{outcome['technical_exception_rate']:.3f} |"
        ),
        '',
        '| Metric | Value |',
        '|---|---:|',
        f"| Episode length mean | {length['mean']:.3f} |",
        f"| Episode length std | {length['std']:.3f} |",
        f"| Episode length median | {length['median']:.3f} |",
        f"| Episode length p95 | {length['p95']:.3f} |",
        f"| Retry rate | {outcome['retry_rate']:.3f} |",
        f"| Initial EEF variance mean | {initial['eef_variance_mean']:.8f} |",
        f"| Initial object variance mean | {initial['object_variance_mean']:.8f} |",
        f"| EEF convex hull volume | {hull['volume']:.8f} |",
        f"| Local Action Variance | {local['value']:.8f} |",
        f"| Mean normalized arm entropy | {entropy['mean_normalized_entropy']:.6f} |",
        f"| Idle ratio | {entropy['idle_ratio']:.6f} |",
        f"| Planned/executed difference rate | {diagnostics['planned_executed_difference_rate']:.6f} |",
        f"| Corrupted Frame Rate | {report['corrupted_frame_rate']['rate']:.6f} |",
        '',
        f"Technical gate: **{'PASS' if report['technical_validation']['valid'] else 'FAIL'}**",
    ]
    if report['warnings']:
        lines.extend(['', 'Warnings:', ''])
        lines.extend(f'- {warning}' for warning in report['warnings'])
    lines.extend(['', 'Failure stages:', '', '```json'])
    import json

    lines.append(json.dumps(outcome['failure_stages'], sort_keys=True))
    lines.extend(['```', ''])
    return '\n'.join(lines)
