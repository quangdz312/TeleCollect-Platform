"""Build an immutable RoboMimic HDF5 snapshot from reviewed scripted demos."""

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
                source_path = Path(str(item["source_path"]))
                source_demo = str(item["demo"])
                with h5py.File(source_path, "r") as source:
                    source_data = source["data"]
                    current_env_args = normalize_robomimic_env_args(
                        json.loads(str(source_data.attrs.get("env_args", "")))
                    )
                    if env_args is None:
                        env_args = current_env_args
                        for key, value in source_data.attrs.items():
                            target_data.attrs[key] = value
                        target_data.attrs["env_args"] = json.dumps(current_env_args, indent=4)
                    elif current_env_args != env_args:
                        raise ValueError("Các episode không cùng environment; hãy export từng task riêng")
                    if source_demo not in source_data:
                        raise KeyError(f"Không tìm thấy {source_demo} trong {source_path.name}")
                    name = f"demo_{index}"
                    source.copy(source_data[source_demo], target_data, name=name)
                    demo = target_data[name]
                    count = int(demo.attrs.get("num_samples", len(demo["actions"])))
                    total += count
                    demo.attrs["source_episode_id"] = str(item["episode_id"])
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
