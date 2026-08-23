"""Build an immutable RoboMimic HDF5 snapshot from reviewed demonstrations."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

import h5py
import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.export.teleop_robomimic import TeleopRoboMimicEpisode, load_teleop_episode
from src.models.db import Dataset
from src.models.enums import DatasetStatus
from src.sim.collection.robomimic_hdf5_writer import normalize_robomimic_env_args
from src.sim.collection.schema_validator import validate_training_dataset


def _split_names(
    episodes: Sequence[dict[str, object]], validation_ratio: float,
) -> tuple[list[bytes], list[bytes]]:
    """Return a stable split independent of input ordering and Python hash seeds."""
    if not 0 <= validation_ratio < 1:
        raise ValueError("validation_ratio phải nằm trong khoảng [0, 1)")
    ranked = sorted(
        range(len(episodes)),
        key=lambda index: sha256(str(episodes[index]["episode_id"]).encode()).digest(),
    )
    valid_count = 0 if len(ranked) < 2 or validation_ratio == 0 else max(
        1, round(len(ranked) * validation_ratio)
    )
    valid_count = min(valid_count, max(0, len(ranked) - 1))
    valid_indexes = set(ranked[:valid_count])
    train = [f"demo_{index}".encode() for index in range(len(episodes)) if index not in valid_indexes]
    valid = [f"demo_{index}".encode() for index in range(len(episodes)) if index in valid_indexes]
    return train, valid


#: Soft-penalty ceiling for the ``clean`` slice. This is a filter, not a gate:
#: an episode above it stays in the file and in every other mask, it is simply
#: absent from the one slice that asks for low roughness. No published threshold
#: exists for these penalties, so the value is a starting point for whoever
#: trains rather than a claim about quality.
CLEAN_SLICE_MAX_PENALTY = 0.15


def _write_teleop_demo(
    target_data: h5py.Group,
    name: str,
    episode: TeleopRoboMimicEpisode,
) -> h5py.Group:
    """Write already-aligned manual arrays without materialising another HDF5."""
    demo = target_data.create_group(name)
    demo.create_dataset("actions", data=episode.actions)
    demo.create_dataset("states", data=episode.states)
    demo.create_dataset("rewards", data=episode.rewards)
    demo.create_dataset("dones", data=episode.dones)
    for group_name, values in (
        ("obs", episode.observations),
        ("next_obs", episode.next_observations),
    ):
        group = demo.create_group(group_name)
        for key in sorted(values):
            group.create_dataset(key, data=values[key])
    demo.attrs["num_samples"] = episode.num_samples
    for key, value in episode.attrs.items():
        demo.attrs[key] = value
    return demo


def _quality_masks(episodes: Sequence[dict[str, object]]) -> dict[str, list[bytes]]:
    """Name the slices a training run can ask for, without removing anything.

    RoboMimic reads ``mask/<name>`` to select demos, so a corpus can carry
    several overlapping views of itself instead of being pruned down to one.
    That matters here because "good demonstration" is not a property of an
    episode on its own -- a rough trajectory that hurts single-task behaviour
    cloning can be exactly what a robustness run wants -- and pruning at export
    time makes that choice for someone who is not in the room.
    """

    masks: dict[str, list[bytes]] = {"all": [], "verified": [], "clean": []}
    for index, item in enumerate(episodes):
        name = f"demo_{index}".encode()
        masks["all"].append(name)

        flags = item.get("auto_flags")
        if not isinstance(flags, dict):
            # No flags means nobody scored this episode, which is not the same
            # as an episode that scored clean. Treating a missing record as a
            # pass is how a check ends up asserting more than it verified.
            continue
        if not flags.get("failed_checks") and not flags.get("unavailable_checks"):
            masks["verified"].append(name)
            try:
                penalty = float(flags.get("worst_penalty_value", 0.0) or 0.0)
            except (TypeError, ValueError):
                penalty = 0.0
            if penalty <= CLEAN_SLICE_MAX_PENALTY:
                masks["clean"].append(name)
    return masks


def build_robomimic_hdf5(
    output: Path,
    episodes: Sequence[dict[str, object]],
    *,
    validation_ratio: float = 0.1,
) -> tuple[int, int]:
    """Copy selected source groups without changing tensors or RoboMimic schema."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    total = 0
    try:
        with h5py.File(temporary, "w") as target:
            target_data = target.create_group("data")
            env_args: dict[str, object] | None = None
            for index, item in enumerate(episodes):
                name = f"demo_{index}"
                if item.get("artifact_format") == "teleop_dir":
                    converted = load_teleop_episode(
                        Path(str(item["episode_dir"])),
                        trim_start_s=item.get("trim_start_s"),  # type: ignore[arg-type]
                        trim_end_s=item.get("trim_end_s"),  # type: ignore[arg-type]
                        successful=item.get("successful"),  # type: ignore[arg-type]
                    )
                    current_env_args = normalize_robomimic_env_args(converted.env_args)
                    demo = _write_teleop_demo(target_data, name, converted)
                else:
                    source_path = Path(str(item["source_path"]))
                    source_demo = str(item["demo"])
                    with h5py.File(source_path, "r") as source:
                        source_data = source["data"]
                        current_env_args = normalize_robomimic_env_args(
                            json.loads(str(source_data.attrs.get("env_args", "")))
                        )
                        if env_args is None:
                            for key, value in source_data.attrs.items():
                                target_data.attrs[key] = value
                        if source_demo not in source_data:
                            raise KeyError(f"Không tìm thấy {source_demo} trong {source_path.name}")
                        source.copy(source_data[source_demo], target_data, name=name)
                        demo = target_data[name]
                if env_args is None:
                    env_args = current_env_args
                    target_data.attrs["env_args"] = json.dumps(current_env_args, indent=4)
                elif current_env_args != env_args:
                    raise ValueError("Các episode không cùng environment; hãy export từng task riêng")
                count = int(demo.attrs.get("num_samples", len(demo["actions"])))
                total += count
                demo.attrs["source_episode_id"] = str(item["episode_id"])
                demo.attrs["source"] = (
                    "manual_teleop"
                    if item.get("artifact_format") == "teleop_dir"
                    else "scripted"
                )
                demo.attrs["review_decision"] = str(item["decision"])
                demo.attrs["reviewer"] = str(item.get("reviewer", "unknown"))
                demo.attrs["reviewed_at"] = str(item.get("reviewed_at", ""))
                demo.attrs["review_note"] = str(item.get("note", ""))
                demo.attrs["review_reasons"] = json.dumps(item.get("reasons", []))
            target_data.attrs["total"] = total
            target_data.attrs["telecollect_export_format"] = "robomimic"
            target_data.attrs["telecollect_reviewed_only"] = True
            mask = target.create_group("mask")
            train, valid = _split_names(episodes, validation_ratio)
            string_dtype = h5py.string_dtype(encoding="ascii")
            mask.create_dataset("train", data=np.asarray(train, dtype=string_dtype))
            mask.create_dataset("valid", data=np.asarray(valid, dtype=string_dtype))
            mask.attrs["split_method"] = "sha256_episode_id"
            mask.attrs["validation_ratio"] = validation_ratio
            for name, members in _quality_masks(episodes).items():
                mask.create_dataset(name, data=np.asarray(members, dtype=string_dtype))
            mask.attrs["clean_max_penalty"] = CLEAN_SLICE_MAX_PENALTY
        validation = validate_training_dataset(temporary)
        if not validation.valid:
            raise ValueError("RoboMimic export không hợp lệ: " + "; ".join(validation.errors))
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return len(episodes), total


async def build_robomimic_dataset(
    dataset_id: str,
    output: Path,
    episodes: Sequence[dict[str, object]],
    factory: async_sessionmaker,
) -> None:
    try:
        count, frames = build_robomimic_hdf5(output, episodes)
        status = DatasetStatus.READY
        error = None
    except Exception as exc:  # background job must persist its failure
        count, frames = 0, 0
        status = DatasetStatus.FAILED
        error = str(exc)[:2000]
    async with factory() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            output.unlink(missing_ok=True)
            return
        dataset.status = status
        dataset.error_message = error
        if status == DatasetStatus.READY:
            dataset.num_episodes = count
            dataset.num_frames = frames
            dataset.size_bytes = output.stat().st_size
            dataset.zip_path = str(output)
        await session.commit()
