"""Local mixed-source RoboMimic export with one canonical control rate."""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from src.export.teleop_robomimic import TeleopRoboMimicEpisode, load_teleop_episode
from src.services.robomimic_dataset_builder import _write_teleop_demo, build_robomimic_hdf5
from src.sim.collection.robomimic_hdf5_writer import normalize_robomimic_env_args


def _canonical_env(value: dict[str, Any], fps: int) -> dict[str, Any]:
    result = normalize_robomimic_env_args(value)
    kwargs = result["env_kwargs"]
    robots = kwargs.get("robots")
    if isinstance(robots, str):
        kwargs["robots"] = [robots]
    kwargs["control_freq"] = fps
    return result


def _fps_for(item: dict[str, Any]) -> int:
    if item.get("artifact_format") == "teleop_dir":
        return int(load_teleop_episode(Path(str(item["episode_dir"]))).env_args["env_kwargs"]["control_freq"])
    with h5py.File(Path(str(item["source_path"])), "r") as source:
        args = json.loads(str(source["data"].attrs.get("env_args", "")))
    return int(args.get("env_kwargs", {}).get("control_freq") or 20)


def _resample_manual(episode: TeleopRoboMimicEpisode, source_fps: int, target_fps: int) -> TeleopRoboMimicEpisode:
    ratio_float = source_fps / target_fps
    ratio = int(round(ratio_float))
    if ratio < 1 or abs(ratio_float - ratio) > 1e-6:
        raise ValueError(f"Cannot safely resample manual HDF5 data from {source_fps} FPS to {target_fps} FPS")
    if ratio == 1:
        return episode
    starts = np.arange(0, episode.num_samples, ratio)
    actions = np.empty((len(starts), 7), dtype=np.float32)
    next_indexes: list[int] = []
    rewards = np.empty(len(starts), dtype=np.float32)
    dones = np.empty(len(starts), dtype=np.int64)
    for output_index, start in enumerate(starts):
        stop = min(int(start) + ratio, episode.num_samples)
        actions[output_index, :6] = episode.actions[start:stop, :6].sum(axis=0)
        actions[output_index, 6] = episode.actions[stop - 1, 6]
        rewards[output_index] = episode.rewards[start:stop].sum()
        dones[output_index] = int(np.any(episode.dones[start:stop]))
        next_indexes.append(stop - 1)
    attrs = dict(episode.attrs)
    attrs["control_hz"] = target_fps
    attrs["resampled_from_hz"] = source_fps
    return replace(
        episode,
        actions=actions,
        states=episode.states[starts],
        rewards=rewards,
        dones=dones,
        observations={key: value[starts] for key, value in episode.observations.items()},
        next_observations={key: value[next_indexes] for key, value in episode.next_observations.items()},
        attrs=attrs,
        env_args={**episode.env_args, "env_kwargs": {**episode.env_args["env_kwargs"], "control_freq": target_fps}},
    )


def _write_wrapper(path: Path, item: dict[str, Any], target_fps: int) -> None:
    with h5py.File(path, "w") as output:
        output_data = output.create_group("data")
        if item.get("artifact_format") == "teleop_dir":
            episode = load_teleop_episode(
                Path(str(item["episode_dir"])),
                trim_start_s=item.get("trim_start_s"),
                trim_end_s=item.get("trim_end_s"),
                successful=item.get("successful"),
            )
            source_fps = int(episode.env_args["env_kwargs"]["control_freq"])
            episode = _resample_manual(episode, source_fps, target_fps)
            output_data.attrs["env_args"] = json.dumps(_canonical_env(episode.env_args, target_fps))
            _write_teleop_demo(output_data, "demo_0", episode)
        else:
            source_path = Path(str(item["source_path"]))
            demo_name = str(item["demo"])
            with h5py.File(source_path, "r") as source:
                source_data = source["data"]
                args = json.loads(str(source_data.attrs.get("env_args", "")))
                source_fps = int(args.get("env_kwargs", {}).get("control_freq") or 20)
                if source_fps != target_fps:
                    raise ValueError(
                        f"Scripted source {source_path.name} is {source_fps} FPS; "
                        f"automatic scripted resampling to {target_fps} FPS is not supported"
                    )
                output_data.attrs["env_args"] = json.dumps(_canonical_env(args, target_fps))
                source.copy(source_data[demo_name], output_data, name="demo_0")


def export(output: Path, episodes: list[dict[str, Any]]) -> tuple[int, int]:
    if not episodes:
        raise ValueError("No episode selected for HDF5 export")
    rates = [_fps_for(item) for item in episodes]
    target_fps = min(rates)
    if any(abs(rate / target_fps - round(rate / target_fps)) > 1e-6 for rate in rates):
        raise ValueError(f"Cannot safely combine control rates {sorted(set(rates))}")
    with tempfile.TemporaryDirectory(prefix="telecollect-hdf5-") as temporary:
        wrappers: list[dict[str, Any]] = []
        root = Path(temporary)
        for index, item in enumerate(episodes):
            path = root / f"episode-{index:05d}.hdf5"
            _write_wrapper(path, item, target_fps)
            wrappers.append({**item, "artifact_format": "scripted", "source_path": path, "demo": "demo_0"})
        return build_robomimic_hdf5(output, wrappers)
