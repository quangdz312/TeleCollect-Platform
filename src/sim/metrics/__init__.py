"""Shared deterministic trajectory-quality metrics for TeleCollect."""

from .quality_metrics import (
    TrajectoryEpisode,
    action_entropy,
    batch_outcomes,
    convex_hull_coverage,
    corrupted_frame_rate,
    initial_state_variance,
    local_action_variance,
    noise_magnitudes,
    technical_validation,
)
from .quality_report import build_quality_report, report_markdown

__all__ = [
    'TrajectoryEpisode',
    'action_entropy',
    'batch_outcomes',
    'build_quality_report',
    'convex_hull_coverage',
    'corrupted_frame_rate',
    'initial_state_variance',
    'local_action_variance',
    'noise_magnitudes',
    'report_markdown',
    'technical_validation',
]
