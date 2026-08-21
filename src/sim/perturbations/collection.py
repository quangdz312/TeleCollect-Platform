"""Shared perturbation wiring for calibration and dataset collection.

Calibration and the collector CLIs must construct runtimes identically, or a
dataset stops reproducing the bank its quality bands were measured on. Both
paths therefore build their runtime here rather than repeating the landmark
tuples and the seed contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any

from .profiles import PerturbationTask, ResolvedProfile, resolve_profile
from .runtime import PerturbationRuntime, environment_seed


@dataclass(frozen=True)
class TaskLandmarks:
    """Observation keys the operator plans from, and so the ones noise biases."""

    position: tuple[str, ...] = ()
    orientation: tuple[str, ...] = ()


TASK_LANDMARKS: Mapping[PerturbationTask, TaskLandmarks] = MappingProxyType({
    PerturbationTask.LIFT: TaskLandmarks(position=('cube_pos',)),
    PerturbationTask.CAN: TaskLandmarks(position=('Can_pos',)),
    PerturbationTask.SQUARE: TaskLandmarks(
        position=('SquareNut_pos',),
        orientation=('SquareNut_quat',),
    ),
    PerturbationTask.TOOL_HANG: TaskLandmarks(),
})

#: Each operator counts its bounded recovery under a different debug key.
RETRY_DEBUG_KEYS: Mapping[PerturbationTask, str] = MappingProxyType({
    PerturbationTask.LIFT: 'regrasp_attempts',
    PerturbationTask.CAN: 'retry_attempts',
    PerturbationTask.SQUARE: 'retry_count',
    PerturbationTask.TOOL_HANG: 'attempts',
})

PROVENANCE_SCHEMA_VERSION = 1


def task_landmarks(task: str | PerturbationTask) -> TaskLandmarks:
    """Return the landmark contract for a task, accepting registry aliases."""

    resolved = task if isinstance(task, PerturbationTask) else resolve_profile(task, 'clean').task
    return TASK_LANDMARKS[resolved]


def retry_count(task: str | PerturbationTask, debug: Mapping[str, Any]) -> int:
    resolved = task if isinstance(task, PerturbationTask) else resolve_profile(task, 'clean').task
    return int(debug.get(RETRY_DEBUG_KEYS[resolved], 0))


def build_runtime(
    profile: ResolvedProfile,
    action_spec: Any,
    *,
    base_seed: int,
    episode_index: int,
) -> PerturbationRuntime:
    """Build the one runtime shape both calibration and collection must use."""

    landmarks = TASK_LANDMARKS[profile.task]
    return PerturbationRuntime(
        profile,
        action_spec,
        base_seed=base_seed,
        episode_index=episode_index,
        position_landmarks=landmarks.position,
        orientation_landmarks=landmarks.orientation,
    )


def build_runtime_for(
    task: str | PerturbationTask,
    quality: str,
    action_spec: Any,
    *,
    base_seed: int,
    episode_index: int,
) -> PerturbationRuntime:
    """Resolve a profile and build its runtime in one step."""

    return build_runtime(
        resolve_profile(task, quality),
        action_spec,
        base_seed=base_seed,
        episode_index=episode_index,
    )


@dataclass(frozen=True)
class DatasetProvenance:
    """Batch-level provenance describing how a dataset was requested."""

    task: str
    tool_name: str
    requested_quality: str
    profile_version: str
    candidate_profile_version: str
    acceptance_amendment: str
    noise_scale: float
    base_seed: int
    task_code: int
    stream_code: int
    coverage: str = 'standard'
    position_landmarks: tuple[str, ...] = ()
    orientation_landmarks: tuple[str, ...] = ()
    schema_version: int = PROVENANCE_SCHEMA_VERSION

    def as_attrs(self) -> dict[str, Any]:
        return {
            'telecollect_schema_version': self.schema_version,
            'telecollect_task': self.task,
            'telecollect_tool_name': self.tool_name,
            'telecollect_requested_quality': self.requested_quality,
            'telecollect_profile_version': self.profile_version,
            'telecollect_candidate_profile_version': self.candidate_profile_version,
            'telecollect_acceptance_amendment': self.acceptance_amendment,
            'telecollect_noise_scale': float(self.noise_scale),
            'telecollect_base_seed': int(self.base_seed),
            'telecollect_task_code': int(self.task_code),
            'telecollect_stream_code': int(self.stream_code),
            'telecollect_coverage': self.coverage,
            'telecollect_position_landmarks': json.dumps(list(self.position_landmarks)),
            'telecollect_orientation_landmarks': json.dumps(list(self.orientation_landmarks)),
        }


@dataclass(frozen=True)
class EpisodeProvenance:
    """Everything needed to reproduce one episode's variation and outcome."""

    task: str
    tool_name: str
    requested_quality: str
    profile_version: str
    candidate_profile_version: str
    noise_scale: float
    base_seed: int
    task_code: int
    stream_code: int
    episode_index: int
    environment_seed: int
    sampled_variation: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    outcome: str = 'unknown'
    success: bool = False
    episode_length: int = 0
    terminal_reason: str = 'unknown'
    failure_stage: str | None = None
    terminal_phase: str = 'unknown'
    schema_version: int = PROVENANCE_SCHEMA_VERSION

    def as_attrs(self) -> dict[str, Any]:
        """Flatten to HDF5 attribute scalars; the variation stays JSON."""

        return {
            'telecollect_schema_version': self.schema_version,
            'telecollect_task': self.task,
            'telecollect_tool_name': self.tool_name,
            'telecollect_requested_quality': self.requested_quality,
            'telecollect_profile_version': self.profile_version,
            'telecollect_candidate_profile_version': self.candidate_profile_version,
            'telecollect_noise_scale': float(self.noise_scale),
            'telecollect_base_seed': int(self.base_seed),
            'telecollect_task_code': int(self.task_code),
            'telecollect_stream_code': int(self.stream_code),
            'telecollect_episode_index': int(self.episode_index),
            'telecollect_environment_seed': int(self.environment_seed),
            'telecollect_sampled_variation': json.dumps(
                _json_ready(self.sampled_variation), sort_keys=True,
            ),
            'telecollect_retry_count': int(self.retry_count),
            'telecollect_outcome': self.outcome,
            'telecollect_success': bool(self.success),
            'telecollect_episode_length': int(self.episode_length),
            'telecollect_terminal_reason': self.terminal_reason,
            'telecollect_failure_stage': '' if self.failure_stage is None else self.failure_stage,
            'telecollect_terminal_phase': self.terminal_phase,
        }


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def dataset_provenance(
    profile: ResolvedProfile,
    *,
    base_seed: int,
    stream_code: int,
    coverage: str = 'standard',
) -> DatasetProvenance:
    landmarks = TASK_LANDMARKS[profile.task]
    return DatasetProvenance(
        task=profile.task.value,
        tool_name=profile.tool_name,
        requested_quality=profile.quality.value,
        profile_version=profile.profile_version,
        candidate_profile_version=profile.candidate_profile_version,
        acceptance_amendment=profile.acceptance_amendment,
        noise_scale=profile.noise_scale,
        base_seed=base_seed,
        task_code=profile.task_code,
        stream_code=stream_code,
        coverage=coverage,
        position_landmarks=landmarks.position,
        orientation_landmarks=landmarks.orientation,
    )


def episode_provenance(
    profile: ResolvedProfile,
    runtime: PerturbationRuntime,
    *,
    episode_index: int,
    outcome: str,
    success: bool,
    episode_length: int,
    terminal_reason: str,
    failure_stage: str | None,
    terminal_phase: str,
    debug: Mapping[str, Any],
) -> EpisodeProvenance:
    """Record the sampled variation exactly as the episode ran it."""

    provenance = runtime.seed_provenance
    try:
        variation = asdict(runtime.variation)
    except RuntimeError:  # Episode failed before reset sampled a variation.
        variation = {}
    return EpisodeProvenance(
        task=profile.task.value,
        tool_name=profile.tool_name,
        requested_quality=profile.quality.value,
        profile_version=profile.profile_version,
        candidate_profile_version=profile.candidate_profile_version,
        noise_scale=profile.noise_scale,
        base_seed=provenance.base_seed,
        task_code=provenance.task_code,
        stream_code=provenance.stream_code,
        episode_index=episode_index,
        environment_seed=environment_seed(provenance.base_seed, episode_index),
        sampled_variation=variation,
        retry_count=retry_count(profile.task, debug),
        outcome=outcome,
        success=success,
        episode_length=episode_length,
        terminal_reason=terminal_reason,
        failure_stage=failure_stage,
        terminal_phase=terminal_phase,
    )
