"""Shared collection runner behind the scripted Lift/Can/Square collectors.

The three CLIs are thin wrappers over :func:`run_collection`. Keeping one runner
means a dataset collected from the command line uses the same profile
resolution, seed contract and runtime construction as calibration, so measured
quality bands stay applicable to what users actually collect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.sim.collection.robomimic_hdf5_writer import RobomimicHDF5Writer
from src.sim.perturbations.collection import (
    DatasetProvenance,
    EpisodeProvenance,
    build_runtime,
    dataset_provenance,
    episode_provenance,
)
from src.sim.perturbations.profiles import (
    NOISE_STREAM_CODE,
    ResolvedProfile,
    resolve_profile,
)
from src.sim.perturbations.runtime import MAX_BASE_SEED, environment_seed
from src.sim.tool_hang import TOOLHANG_TOOL_NAME
from src.sim.tools.defaults import build_default_registry
from src.sim.tools.executor import execute_tool

SUPPORTED_TASKS = ('lift', 'can', 'square', 'tool_hang')
SUPPORTED_QUALITIES = ('clean', 'good', 'medium', 'poor')
DEFAULT_QUALITY = 'clean'


@dataclass(frozen=True)
class CollectionTaskSpec:
    """Everything task-specific the shared runner needs."""

    task: str
    tool_name: str
    default_horizon: int
    environment_factory: Callable[..., AbstractContextManager[Any]]
    context_factory: Callable[..., Any]
    env_args_reader: Callable[[Any], dict[str, Any]]
    reference_dataset: Path


def _lift_spec() -> CollectionTaskSpec:
    from src.sim.lift_env import LIFT_REFERENCE_DATASET, lift_environment, read_lift_env_args
    from src.sim.task_adapters.lift import build_lift_tool_context

    return CollectionTaskSpec(
        task='lift',
        tool_name='lift_cube',
        default_horizon=300,
        environment_factory=lift_environment,
        context_factory=build_lift_tool_context,
        env_args_reader=read_lift_env_args,
        reference_dataset=LIFT_REFERENCE_DATASET,
    )


def _can_spec() -> CollectionTaskSpec:
    from src.sim.can_env import REFERENCE_DATASET, can_environment, read_reference_env_args
    from src.sim.task_adapters.can import build_can_tool_context

    return CollectionTaskSpec(
        task='can',
        tool_name='pick_place_can',
        default_horizon=400,
        environment_factory=can_environment,
        context_factory=build_can_tool_context,
        env_args_reader=read_reference_env_args,
        reference_dataset=REFERENCE_DATASET,
    )


def _square_spec() -> CollectionTaskSpec:
    from src.sim.square_env import (
        SQUARE_REFERENCE_DATASET,
        read_square_env_args,
        square_environment,
    )
    from src.sim.task_adapters.square import build_square_tool_context

    return CollectionTaskSpec(
        task='square',
        tool_name='assemble_square',
        default_horizon=500,
        environment_factory=square_environment,
        context_factory=build_square_tool_context,
        env_args_reader=read_square_env_args,
        reference_dataset=SQUARE_REFERENCE_DATASET,
    )


def _tool_hang_spec() -> CollectionTaskSpec:
    from src.sim.tool_hang_collection import environment

    return CollectionTaskSpec(
        task='tool_hang',
        tool_name=TOOLHANG_TOOL_NAME,
        default_horizon=6000,
        environment_factory=environment,
        context_factory=lambda *args, **kwargs: None,
        env_args_reader=lambda _reference: {
            'env_name': 'ToolHang',
            'type': 1,
            'env_kwargs': {
                'robots': 'Panda', 'control_freq': 20, 'stage': 'stage1+stage2',
            },
        },
        reference_dataset=Path('data/toolhang_stage1_reference.hdf5'),
    )


def _tool_hang_dataset_provenance(seed: int) -> DatasetProvenance:
    return DatasetProvenance(
        task='tool_hang', tool_name=TOOLHANG_TOOL_NAME, requested_quality='clean',
        profile_version=TOOLHANG_PROFILE,
        candidate_profile_version=TOOLHANG_PROFILE,
        acceptance_amendment='full-task-env-predicate-gate', noise_scale=0.0,
        base_seed=seed, task_code=TOOLHANG_TASK_CODE, stream_code=1,
        coverage='stage1+stage2',
        position_landmarks=('frame_pos', 'tool_pos'),
        orientation_landmarks=('frame_quat', 'tool_quat'),
    )


_SPEC_FACTORIES: Mapping[str, Callable[[], CollectionTaskSpec]] = {
    'lift': _lift_spec,
    'can': _can_spec,
    'square': _square_spec,
    'tool_hang': _tool_hang_spec,
}


def collection_task_spec(task: str) -> CollectionTaskSpec:
    """Resolve the task spec, rejecting tasks excluded from this release."""

    if str(task).lower() == 'tool_hang':
        return _tool_hang_spec()
    profile = resolve_profile(task, DEFAULT_QUALITY)
    return _SPEC_FACTORIES[profile.task.value]()


@dataclass(frozen=True)
class EpisodeRecord:
    episode_index: int
    environment_seed: int
    steps: int
    success: bool
    outcome: str
    termination_reason: str
    provenance: EpisodeProvenance


@dataclass(frozen=True)
class CollectionResult:
    task: str
    tool_name: str
    quality: str
    output: Path | None
    horizon: int
    episodes: tuple[EpisodeRecord, ...]
    provenance: DatasetProvenance

    @property
    def success_count(self) -> int:
        return sum(1 for episode in self.episodes if episode.success)


def _validate_request(episodes: int, horizon: int, base_seed: int) -> None:
    if episodes < 1:
        raise ValueError('episodes must be positive')
    if horizon < 1:
        raise ValueError('horizon must be positive')
    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise TypeError('seed must be an integer')
    if not 0 <= base_seed <= MAX_BASE_SEED:
        raise ValueError(f'seed must be in 0..{MAX_BASE_SEED}')


def _outcome(success: bool, termination_reason: str) -> str:
    if success:
        return 'success'
    return 'horizon' if termination_reason == 'horizon' else 'failure'


def iter_episodes(
    env: Any,
    profile: ResolvedProfile,
    spec: CollectionTaskSpec,
    *,
    episodes: int,
    horizon: int,
    base_seed: int,
    verbose: bool = False,
    logger: Callable[[str], None] = print,
) -> Iterator[tuple[EpisodeRecord, Any]]:
    """Yield each rollout with its raw episode, retaining failures as collected."""

    registry = build_default_registry()
    for episode_index in range(episodes):
        seed = environment_seed(base_seed, episode_index)
        runtime = build_runtime(
            profile,
            env.action_spec,
            base_seed=base_seed,
            episode_index=episode_index,
        )
        tool = registry.create(profile.tool_name, env)
        context = spec.context_factory(env, horizon=horizon, seed=seed, verbose=verbose)
        result = execute_tool(tool, context, runtime=runtime)
        debug = tool.debug_info
        failure_stage = debug.get('failure_stage')
        if not result.success and not failure_stage:
            failure_stage = str(result.final_state)
        outcome = _outcome(result.success, result.termination_reason)
        record = EpisodeRecord(
            episode_index=episode_index,
            environment_seed=seed,
            steps=result.steps,
            success=bool(result.success),
            outcome=outcome,
            termination_reason=str(result.termination_reason),
            provenance=episode_provenance(
                profile,
                runtime,
                episode_index=episode_index,
                outcome=outcome,
                success=bool(result.success),
                episode_length=int(result.steps),
                terminal_reason=str(result.termination_reason),
                failure_stage=None if failure_stage is None else str(failure_stage),
                terminal_phase=str(result.final_state),
                debug=debug,
            ),
        )
        if verbose:
            logger(
                f'episode={episode_index} seed={seed} quality={profile.quality.value} '
                f'steps={record.steps} outcome={outcome}'
            )
        yield record, result.episode


def run_collection(
    task: str,
    *,
    episodes: int = 1,
    horizon: int | None = None,
    seed: int = 0,
    quality: str = DEFAULT_QUALITY,
    output: str | Path | None = None,
    render: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    verbose: bool = False,
    logger: Callable[[str], None] = print,
    reference_path: str | Path | None = None,
    video_dir: str | Path | None = None,
    collection_batch_id: str = '',
) -> CollectionResult:
    """Collect one task/quality batch into a raw Robomimic-compatible HDF5.

    Failed and horizon episodes are written like any other; nothing is
    resampled, relabelled or dropped to reach a target success rate.

    `video_dir` (ToolHang only for now) writes each episode's review mp4 during
    the rollout, so opening the review page costs no render.
    """

    if task == 'tool_hang':
        profile = resolve_profile(task, quality)
        resolved_horizon = 6000 if horizon is None else int(horizon)
        _validate_request(episodes, resolved_horizon, seed)
        batch_provenance = dataset_provenance(
            profile,
            base_seed=seed,
            stream_code=NOISE_STREAM_CODE,
            coverage='stage1+stage2',
            collection_batch_id=collection_batch_id,
        )
        if not dry_run and output is None:
            raise ValueError('output is required unless dry_run is used')
        if dry_run:
            return CollectionResult(
                task='tool_hang', tool_name=TOOLHANG_TOOL_NAME,
                quality=profile.quality.value,
                output=None, horizon=resolved_horizon, episodes=(),
                provenance=batch_provenance,
            )
        from src.sim.tool_hang_collection import collect

        result = collect(
            output, episodes=episodes, seed=seed, overwrite=overwrite, logger=logger,
            video_dir=video_dir, quality=profile.quality.value,
            collection_batch_id=collection_batch_id,
        )
        records = tuple(
            EpisodeRecord(
                episode_index=item['episode_index'],
                environment_seed=item['seed'],
                steps=item['steps'],
                success=item['success'],
                outcome=_outcome(item['success'], item['terminal_reason']),
                termination_reason=item['terminal_reason'],
                provenance=EpisodeProvenance(
                    task='tool_hang', tool_name=TOOLHANG_TOOL_NAME,
                    requested_quality=profile.quality.value,
                    profile_version=profile.profile_version,
                    candidate_profile_version=profile.candidate_profile_version,
                    noise_scale=profile.noise_scale,
                    base_seed=seed, task_code=profile.task_code,
                    stream_code=NOISE_STREAM_CODE,
                    episode_index=item['episode_index'], environment_seed=item['seed'],
                    sampled_variation=item.get('sampled_variation', item['summary']),
                    outcome=_outcome(item['success'], item['terminal_reason']),
                    success=item['success'], episode_length=item['steps'],
                    terminal_reason=item['terminal_reason'],
                    failure_stage=item['failure_stage'],
                    terminal_phase=item['terminal_phase'],
                ),
            ) for item in result['records']
        )
        return CollectionResult(
            task='tool_hang', tool_name=TOOLHANG_TOOL_NAME,
            quality=profile.quality.value,
            output=Path(output), horizon=resolved_horizon, episodes=records,
            provenance=batch_provenance,
        )

    profile = resolve_profile(task, quality)
    spec = collection_task_spec(task)
    resolved_horizon = spec.default_horizon if horizon is None else int(horizon)
    _validate_request(episodes, resolved_horizon, seed)
    if not dry_run and output is None:
        raise ValueError('output is required unless dry_run is used')

    batch_provenance = dataset_provenance(
        profile,
        base_seed=seed,
        stream_code=NOISE_STREAM_CODE,
        collection_batch_id=collection_batch_id,
    )

    writer = None
    records: list[EpisodeRecord] = []
    try:
        if not dry_run:
            reference = spec.reference_dataset if reference_path is None else Path(reference_path)
            writer = RobomimicHDF5Writer(
                Path(output),  # type: ignore[arg-type]
                spec.env_args_reader(reference),
                overwrite=overwrite,
                collection_metadata=batch_provenance.as_attrs(),
            )
        with spec.environment_factory(render=render, seed=seed) as env:
            for record, episode in iter_episodes(
                env,
                profile,
                spec,
                episodes=episodes,
                horizon=resolved_horizon,
                base_seed=seed,
                verbose=verbose,
                logger=logger,
            ):
                records.append(record)
                logger(
                    f'episode={record.episode_index} seed={record.environment_seed} '
                    f'quality={quality} steps={record.steps} success={record.success} '
                    f'reason={record.termination_reason}'
                )
                if writer is not None:
                    writer.write_episode(episode, provenance=record.provenance.as_attrs())
        if writer is not None:
            writer.close()
            logger(f'raw dataset written: {output}')
    except Exception:
        if writer is not None:
            writer.close(publish=False)
        raise

    return CollectionResult(
        task=profile.task.value,
        tool_name=profile.tool_name,
        quality=profile.quality.value,
        output=None if dry_run else Path(output),  # type: ignore[arg-type]
        horizon=resolved_horizon,
        episodes=tuple(records),
        provenance=batch_provenance,
    )


def add_collection_arguments(parser: Any, *, default_horizon: int) -> None:
    """Register the argument contract shared by all three collector CLIs."""

    parser.add_argument('--episodes', type=int, default=1)
    parser.add_argument('--horizon', type=int, default=default_horizon)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument(
        '--quality',
        choices=SUPPORTED_QUALITIES,
        default=DEFAULT_QUALITY,
        help='perturbation preset; clean keeps the legacy baseline behaviour',
    )
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--verbose', action='store_true')


def main_for_task(task: str, argv: Sequence[str] | None = None) -> int:
    """Shared CLI entry point; the per-task scripts only supply the task name."""

    import argparse

    spec = collection_task_spec(task)
    parser = argparse.ArgumentParser(
        description=f'Collect raw {task} demonstrations with the registered scripted tool.',
    )
    add_collection_arguments(parser, default_horizon=spec.default_horizon)
    args = parser.parse_args(argv)
    if args.episodes < 1 or args.horizon < 1:
        parser.error('--episodes and --horizon must be positive')
    if not args.dry_run and args.output is None:
        parser.error('--output is required unless --dry-run is used')

    result = run_collection(
        task,
        episodes=args.episodes,
        horizon=args.horizon,
        seed=args.seed,
        quality=args.quality,
        output=args.output,
        render=args.render,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        verbose=args.verbose,
    )
    print(
        f'task={result.task} quality={result.quality} '
        f'profile_version={result.provenance.profile_version} '
        f'episodes={len(result.episodes)} success={result.success_count}'
    )
    return 0
