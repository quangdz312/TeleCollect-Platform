"""Fixed-bank calibration runner for TeleCollect perturbation v1.0."""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from src.sim.metrics.quality_metrics import TrajectoryEpisode, json_safe, noise_magnitudes
from src.sim.metrics.quality_report import build_quality_report, report_markdown
from src.sim.task_adapters.can import build_can_tool_context
from src.sim.task_adapters.lift import build_lift_tool_context
from src.sim.task_adapters.square import build_square_tool_context
from src.sim.tools.defaults import build_default_registry
from src.sim.tools.executor import execute_tool

from .collection import TASK_LANDMARKS, build_runtime, retry_count
from .profiles import PerturbationTask, Quality, resolve_profile
from .runtime import environment_seed


CALIBRATION_SEEDS = tuple(range(100, 120))
HELDOUT_SEEDS = tuple(range(200, 210))
PILOT_SEEDS = tuple(range(100, 105))
D2_CALIBRATION_SEEDS = tuple(range(300, 330))
D2_HELDOUT_SEEDS = tuple(range(400, 420))
D2_PILOT_SEEDS = tuple(range(300, 310))
SUPPORTED_TASKS = ('lift', 'can', 'square')
SUPPORTED_QUALITIES = ('clean', 'good', 'medium', 'poor')


@dataclass(frozen=True)
class CalibrationTaskSpec:
    task: str
    tool_name: str
    horizon: int
    environment_factory: Callable[[], AbstractContextManager[Any]]
    context_factory: Callable[..., Any]
    position_landmarks: tuple[str, ...]
    orientation_landmarks: tuple[str, ...]


def _lift_environment() -> AbstractContextManager[Any]:
    from src.sim.lift_env import lift_environment

    return lift_environment(render=False, seed=0)


def _can_environment() -> AbstractContextManager[Any]:
    from src.sim.can_env import can_environment

    return can_environment(render=False, seed=0)


def _square_environment() -> AbstractContextManager[Any]:
    from src.sim.square_env import square_environment

    return square_environment(render=False, seed=0)


TASK_SPECS: Mapping[str, CalibrationTaskSpec] = {
    'lift': CalibrationTaskSpec(
        task='lift',
        tool_name='lift_cube',
        horizon=300,
        environment_factory=_lift_environment,
        context_factory=build_lift_tool_context,
        position_landmarks=TASK_LANDMARKS[PerturbationTask.LIFT].position,
        orientation_landmarks=TASK_LANDMARKS[PerturbationTask.LIFT].orientation,
    ),
    'can': CalibrationTaskSpec(
        task='can',
        tool_name='pick_place_can',
        horizon=400,
        environment_factory=_can_environment,
        context_factory=build_can_tool_context,
        position_landmarks=TASK_LANDMARKS[PerturbationTask.CAN].position,
        orientation_landmarks=TASK_LANDMARKS[PerturbationTask.CAN].orientation,
    ),
    'square': CalibrationTaskSpec(
        task='square',
        tool_name='assemble_square',
        horizon=500,
        environment_factory=_square_environment,
        context_factory=build_square_tool_context,
        position_landmarks=TASK_LANDMARKS[PerturbationTask.SQUARE].position,
        orientation_landmarks=TASK_LANDMARKS[PerturbationTask.SQUARE].orientation,
    ),
}


class _RecordingTool:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.planned_actions: list[np.ndarray] = []
        self.phases: list[str] = []

    def reset(self) -> None:
        self.planned_actions = []
        self.phases = []
        self.inner.reset()

    def set_variation(self, variation: Any) -> None:
        self.inner.set_variation(variation)

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        self.phases.append(str(self.inner.state))
        action = np.asarray(self.inner.act(observation)).copy()
        self.planned_actions.append(action)
        return action

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _copy_observations(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, np.ndarray], ...]:
    return tuple(
        {key: np.asarray(value).copy() for key, value in observation.items()}
        for observation in observations
    )


def _retry_count(task: str, debug: Mapping[str, Any]) -> int:
    return retry_count(task, debug)


def _target_identity_valid(task: str, env: Any, debug: Mapping[str, Any]) -> bool:
    if task == 'can':
        return int(debug.get('target_bin_id', -1)) == int(env.object_id)
    if task == 'square':
        return int(debug.get('target_peg_id', -1)) == int(env.peg1_body_id)
    return True


def resolve_calibration_profile(
    task: str,
    quality: str,
    *,
    noise_scale_override: float | None = None,
    profile_version_override: str | None = None,
) -> Any:
    """Resolve a profile-level calibration override without mutating source config."""

    profile = resolve_profile(task, quality)
    updates: dict[str, Any] = {}
    if noise_scale_override is not None:
        scale = float(noise_scale_override)
        if not np.isfinite(scale) or scale < 0.0:
            raise ValueError('noise_scale_override must be finite and non-negative')
        updates['noise_scale'] = scale
    if profile_version_override is not None:
        marker = str(profile_version_override).strip()
        if not marker:
            raise ValueError('profile_version_override must be non-empty')
        updates['profile_version'] = marker
    return replace(profile, **updates) if updates else profile


def run_episode(
    env: Any,
    *,
    task: str,
    quality: str,
    episode_index: int,
    base_seed: int = 0,
    noise_scale_override: float | None = None,
    profile_version_override: str | None = None,
) -> TrajectoryEpisode:
    """Run one requested episode and retain failures rather than replacing them."""

    spec = TASK_SPECS[task]
    profile = resolve_calibration_profile(
        task,
        quality,
        noise_scale_override=noise_scale_override,
        profile_version_override=profile_version_override,
    )
    low, high = (np.asarray(bound, dtype=np.float64).copy() for bound in env.action_spec)
    runtime = build_runtime(
        profile,
        env.action_spec,
        base_seed=base_seed,
        episode_index=episode_index,
    )
    tool = _RecordingTool(build_default_registry().create(spec.tool_name, env))
    started = time.perf_counter()
    result = None
    exception_text = None
    try:
        context = spec.context_factory(
            env,
            horizon=spec.horizon,
            seed=environment_seed(base_seed, episode_index),
        )
        result = execute_tool(tool, context, runtime=runtime)
    except Exception as exc:  # Calibration must report, not silently replace, bad episodes.
        exception_text = f'{type(exc).__name__}: {exc}'
    elapsed = time.perf_counter() - started

    try:
        variation = asdict(runtime.variation)
    except RuntimeError:
        variation = {}
    semantic_magnitude, common_magnitude = noise_magnitudes(variation)
    debug = tool.debug_info if result is not None else {}
    if result is None:
        return TrajectoryEpisode(
            task=task,
            tool_name=spec.tool_name,
            quality=quality,
            profile_version=profile.profile_version,
            base_seed=base_seed,
            task_code=profile.task_code,
            episode_index=episode_index,
            stream_code=runtime.seed_provenance.stream_code,
            environment_seed=environment_seed(base_seed, episode_index),
            success=False,
            outcome='technical_exception',
            episode_length=0,
            terminal_reason=exception_text or 'technical_exception',
            failure_stage='setup_or_execution',
            terminal_phase=str(getattr(tool, 'state', 'unknown')),
            retry_count=_retry_count(task, debug),
            sampled_variation=variation,
            semantic_noise_magnitude=semantic_magnitude,
            common_noise_magnitude=common_magnitude,
            action_low=low,
            action_high=high,
            target_identity_valid=False,
            technical_exception=exception_text,
            runtime_seconds=elapsed,
        )

    if result.success:
        outcome = 'success'
    elif result.termination_reason == 'horizon':
        outcome = 'horizon'
    else:
        outcome = 'failure'
    failure_stage = debug.get('failure_stage')
    if not result.success and not failure_stage:
        failure_stage = str(result.final_state)
    return TrajectoryEpisode(
        task=task,
        tool_name=spec.tool_name,
        quality=quality,
        profile_version=profile.profile_version,
        base_seed=base_seed,
        task_code=profile.task_code,
        episode_index=episode_index,
        stream_code=runtime.seed_provenance.stream_code,
        environment_seed=environment_seed(base_seed, episode_index),
        success=bool(result.success),
        outcome=outcome,
        episode_length=int(result.steps),
        terminal_reason=str(result.termination_reason),
        failure_stage=None if failure_stage is None else str(failure_stage),
        terminal_phase=str(result.final_state),
        retry_count=_retry_count(task, debug),
        sampled_variation=variation,
        semantic_noise_magnitude=semantic_magnitude,
        common_noise_magnitude=common_magnitude,
        planned_actions=tuple(tool.planned_actions),
        executed_actions=tuple(np.asarray(action).copy() for action in result.episode.actions),
        observations=_copy_observations(result.episode.observations),
        next_observations=_copy_observations(result.episode.next_observations),
        phases=tuple(tool.phases),
        action_low=low,
        action_high=high,
        target_identity_valid=_target_identity_valid(task, env, debug),
        runtime_seconds=elapsed,
    )


def run_evaluation(
    seeds: Sequence[int],
    *,
    tasks: Sequence[str] = SUPPORTED_TASKS,
    qualities: Sequence[str] = SUPPORTED_QUALITIES,
    base_seed: int = 0,
    scale_overrides: Mapping[tuple[str, str], float] | None = None,
    profile_version_override: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[TrajectoryEpisode], dict[str, dict[str, Any]], float]:
    """Run fixed banks without any scheduler RNG or rejection sampling."""

    calibration_seeds = set(CALIBRATION_SEEDS) | set(D2_CALIBRATION_SEEDS)
    heldout_seeds = set(HELDOUT_SEEDS) | set(D2_HELDOUT_SEEDS)
    if set(seeds) & heldout_seeds and set(seeds) & calibration_seeds:
        raise ValueError('A single evaluation may not mix calibration and held-out seeds')
    scale_overrides = scale_overrides or {}
    started = time.perf_counter()
    episodes: list[TrajectoryEpisode] = []
    for task in tasks:
        if task not in TASK_SPECS:
            resolve_profile(task, 'clean')
            raise ValueError(f'Unsupported calibration task: {task!r}')
        spec = TASK_SPECS[task]
        with spec.environment_factory() as env:
            for quality in qualities:
                for episode_index in seeds:
                    episode = run_episode(
                        env,
                        task=task,
                        quality=quality,
                        episode_index=int(episode_index),
                        base_seed=base_seed,
                        noise_scale_override=scale_overrides.get((task, quality)),
                        profile_version_override=profile_version_override,
                    )
                    episodes.append(episode)
                    if progress is not None:
                        progress(
                            f'task={task} quality={quality} seed={episode_index} '
                            f'outcome={episode.outcome} length={episode.episode_length}',
                        )
    reports: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for quality in qualities:
            selected = [
                episode for episode in episodes
                if episode.task == task and episode.quality == quality
            ]
            reports[f'{task}/{quality}'] = build_quality_report(
                selected,
                requested_count=len(seeds),
            )
    return episodes, reports, time.perf_counter() - started


def profile_snapshot() -> dict[str, Any]:
    return {
        'tasks': {
            task: {
                quality: json_safe(asdict(resolve_profile(task, quality)))
                for quality in SUPPORTED_QUALITIES
            }
            for task in SUPPORTED_TASKS
        },
        'calibration_seeds': list(CALIBRATION_SEEDS),
        'heldout_seeds': list(HELDOUT_SEEDS),
        'pilot_seeds': list(PILOT_SEEDS),
    }


def evaluation_markdown(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    title: str,
) -> str:
    lines = [
        f'# {title}',
        '',
        '| Task | Quality | Success | Failure | Horizon | Length mean | p95 | Retry | LAV | Arm entropy | Idle | Action diff | Technical |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|',
    ]
    for key in sorted(reports):
        report = reports[key]
        outcome = report['outcome']
        lines.append(
            f"| {report['task']} | {report['quality']} | {outcome['success_rate']:.3f} | "
            f"{outcome['failure_rate']:.3f} | {outcome['horizon_rate']:.3f} | "
            f"{outcome['episode_length']['mean']:.2f} | {outcome['episode_length']['p95']:.2f} | "
            f"{outcome['retry_rate']:.3f} | {report['local_action_variance_index']['value']:.6f} | "
            f"{report['action_entropy']['mean_normalized_entropy']:.4f} | "
            f"{report['action_entropy']['idle_ratio']:.4f} | "
            f"{report['perturbation_diagnostics']['planned_executed_difference_rate']:.4f} | "
            f"{'PASS' if report['technical_validation']['valid'] else 'FAIL'} |"
        )
    return '\n'.join(lines) + '\n'


def write_evaluation_artifacts(
    output_dir: Path,
    *,
    prefix: str,
    episodes: Sequence[TrajectoryEpisode],
    reports: Mapping[str, Mapping[str, Any]],
    title: str,
) -> None:
    """Write new sidecars only; callers must provide a fresh output directory."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        'episodes': output_dir / f'{prefix}_episodes.jsonl',
        'metrics': output_dir / f'{prefix}_metrics.json',
        'markdown': output_dir / f'{prefix}_metrics.md',
    }
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(f'Refusing to overwrite Phase D artifacts: {existing}')
    paths['episodes'].write_text(
        ''.join(json.dumps(episode.summary(), sort_keys=True) + '\n' for episode in episodes),
        encoding='utf-8',
    )
    paths['metrics'].write_text(
        json.dumps(json_safe(dict(reports)), indent=2, sort_keys=True),
        encoding='utf-8',
    )
    paths['markdown'].write_text(
        evaluation_markdown(reports, title=title),
        encoding='utf-8',
    )


def assess_quality_ordering(
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for task in SUPPORTED_TASKS:
        task_reports = {quality: reports[f'{task}/{quality}'] for quality in SUPPORTED_QUALITIES}
        success = {
            quality: task_reports[quality]['outcome']['success_rate']
            for quality in SUPPORTED_QUALITIES
        }
        technical = all(
            report['technical_validation']['valid']
            and report['outcome']['count_match']
            and report['corrupted_frame_rate']['rate'] == 0.0
            for report in task_reports.values()
        )
        dose = [
            task_reports[quality]['perturbation_diagnostics']['semantic_noise_magnitude']['mean']
            + task_reports[quality]['perturbation_diagnostics']['common_runtime_noise_magnitude']['mean']
            for quality in SUPPORTED_QUALITIES
        ]
        dose_ordered = dose[0] == 0.0 and dose[0] < dose[1] < dose[2] < dose[3]
        success_ordered = (
            success['clean'] >= success['good']
            and success['good'] > success['medium']
            and success['medium'] > success['poor']
        )
        soft_targets = (
            0.70 <= success['good'] <= 1.0
            and 0.30 <= success['medium'] <= 0.80
            and 0.0 <= success['poor'] <= 0.50
        )
        tasks[task] = {
            'success_rates': success,
            'technical_gate': technical,
            'dose_ordered': dose_ordered,
            'success_ordered': success_ordered,
            'soft_targets': soft_targets,
            'pass': technical and dose_ordered and success_ordered and soft_targets,
        }
    return {
        'tasks': tasks,
        'pass': all(value['pass'] for value in tasks.values()),
    }


V1_SUCCESS_BANDS = MappingProxyType({
    'good': (0.70, 1.00),
    'medium': (0.30, 0.75),
    'poor': (0.00, 0.55),
})
V1_MIN_ADJACENT_GAP = 0.05


def assess_d2_quality_ordering(
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the D2 success-gap, soft-band, dose, and technical gates."""

    tasks: dict[str, Any] = {}
    for task in SUPPORTED_TASKS:
        task_reports = {
            quality: reports[f'{task}/{quality}']
            for quality in SUPPORTED_QUALITIES
        }
        success = {
            quality: task_reports[quality]['outcome']['success_rate']
            for quality in SUPPORTED_QUALITIES
        }
        technical = all(
            report['technical_validation']['valid']
            and report['outcome']['count_match']
            and report['corrupted_frame_rate']['rate'] == 0.0
            for report in task_reports.values()
        )
        doses = [
            task_reports[quality]['perturbation_diagnostics']['semantic_noise_magnitude']['mean']
            + task_reports[quality]['perturbation_diagnostics']['common_runtime_noise_magnitude']['mean']
            for quality in SUPPORTED_QUALITIES
        ]
        dose_ordered = doses[0] == 0.0 and doses[0] < doses[1] < doses[2] < doses[3]
        good_medium_gap = success['good'] - success['medium']
        medium_poor_gap = success['medium'] - success['poor']
        success_ordered = (
            good_medium_gap >= 0.10 - 1e-12
            and medium_poor_gap >= 0.10 - 1e-12
        )
        soft_targets = (
            0.70 <= success['good'] <= 1.0
            and 0.30 <= success['medium'] <= 0.75
            and 0.0 <= success['poor'] <= 0.45
        )
        tasks[task] = {
            'success_rates': success,
            'clean_reference_only': True,
            'clean_quality_ordering_gate': False,
            'good_medium_gap': good_medium_gap,
            'medium_poor_gap': medium_poor_gap,
            'technical_gate': technical,
            'dose_ordered': dose_ordered,
            'success_ordered': success_ordered,
            'soft_targets': soft_targets,
            'pass': technical and dose_ordered and success_ordered and soft_targets,
        }
    return {
        'tasks': tasks,
        'pass': all(value['pass'] for value in tasks.values()),
    }


def assess_v1_quality_ordering(
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the profile v1 gate from amendment TC-QP-2026-08-09-02.

    This is deliberately a separate function from
    :func:`assess_d2_quality_ordering`. The D2 gate stays frozen so its
    recorded outcome remains reproducible; publishing v1 must not rewrite it.
    """

    tasks: dict[str, Any] = {}
    for task in SUPPORTED_TASKS:
        task_reports = {
            quality: reports[f'{task}/{quality}']
            for quality in SUPPORTED_QUALITIES
        }
        success = {
            quality: task_reports[quality]['outcome']['success_rate']
            for quality in SUPPORTED_QUALITIES
        }
        technical = all(
            report['technical_validation']['valid']
            and report['outcome']['count_match']
            and report['corrupted_frame_rate']['rate'] == 0.0
            for report in task_reports.values()
        )
        doses = [
            task_reports[quality]['perturbation_diagnostics']['semantic_noise_magnitude']['mean']
            + task_reports[quality]['perturbation_diagnostics']['common_runtime_noise_magnitude']['mean']
            for quality in SUPPORTED_QUALITIES
        ]
        dose_ordered = doses[0] == 0.0 and doses[0] < doses[1] < doses[2] < doses[3]
        good_medium_gap = success['good'] - success['medium']
        medium_poor_gap = success['medium'] - success['poor']
        success_ordered = (
            good_medium_gap >= V1_MIN_ADJACENT_GAP - 1e-12
            and medium_poor_gap >= V1_MIN_ADJACENT_GAP - 1e-12
        )
        soft_targets = all(
            V1_SUCCESS_BANDS[quality][0] <= success[quality] <= V1_SUCCESS_BANDS[quality][1]
            for quality in V1_SUCCESS_BANDS
        )
        tasks[task] = {
            'success_rates': success,
            'clean_reference_only': True,
            'clean_quality_ordering_gate': False,
            'good_medium_gap': good_medium_gap,
            'medium_poor_gap': medium_poor_gap,
            'minimum_adjacent_gap': V1_MIN_ADJACENT_GAP,
            'success_bands': {
                quality: list(bounds) for quality, bounds in V1_SUCCESS_BANDS.items()
            },
            'technical_gate': technical,
            'dose_ordered': dose_ordered,
            'success_ordered': success_ordered,
            'soft_targets': soft_targets,
            'pass': technical and dose_ordered and success_ordered and soft_targets,
        }
    return {
        'gate': 'TC-QP-2026-08-09-02',
        'tasks': tasks,
        'pass': all(value['pass'] for value in tasks.values()),
    }
