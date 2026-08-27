"""Accept a RoboMimic HDF5 built elsewhere as a dataset in this system.

Datasets normally come out of the export pipeline, which means the review gate
already vouched for every episode in them. An uploaded file has no such history,
so the only thing standing between it and a training run is this check.

It is deliberately a structural check, not a deep one. Training reads
``data/demo_*/actions`` and the observation groups; a file missing those fails
hours later inside the trainer with an error nobody can act on, whereas a file
whose numbers are merely poor is a data problem for review, not a parse problem.
So this refuses what cannot be read and reports what was found, and does not try
to judge quality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py


class DatasetUploadError(ValueError):
    """The file is not a RoboMimic dataset this system can train on."""


@dataclass(frozen=True)
class DatasetProbe:
    """What the file says about itself."""

    episodes: int
    frames: int
    tasks: list[str]
    env_name: str
    action_dim: int


#: RoboMimic BC reads actions plus at least one observation group per demo.
REQUIRED_DEMO_KEYS = ("actions", "obs")


def _task_names(data: h5py.Group, demos: list[str]) -> tuple[list[str], str]:
    """Prefer TeleCollect provenance, fall back to the RoboSuite env name."""

    tasks: set[str] = set()
    for name in demos:
        value = data[name].attrs.get("telecollect_task")
        if value:
            tasks.add(str(value))
    file_task = data.attrs.get("telecollect_task")
    if file_task:
        tasks.add(str(file_task))

    env_name = ""
    raw = data.attrs.get("env_args")
    if raw:
        try:
            env_name = str(json.loads(str(raw)).get("env_name") or "")
        except (json.JSONDecodeError, AttributeError):
            env_name = ""
    if not tasks and env_name:
        tasks.add(env_name)
    return sorted(tasks), env_name


def probe_robomimic(path: Path) -> DatasetProbe:
    """Read the structure of a RoboMimic HDF5, or explain why it cannot be."""

    try:
        handle = h5py.File(path, "r")
    except OSError as exc:
        raise DatasetUploadError(
            "The upload is not a readable HDF5 file",
        ) from exc

    with handle as source:
        if "data" not in source:
            raise DatasetUploadError(
                "No `data` group — a RoboMimic dataset stores its demos under `data/`",
            )
        data = source["data"]
        demos = sorted(
            (key for key in data if key.startswith("demo_")),
            key=lambda name: int(name.split("_")[1]) if name.split("_")[1].isdigit() else 0,
        )
        if not demos:
            raise DatasetUploadError("`data` contains no `demo_*` groups")

        frames = 0
        action_dim = 0
        for name in demos:
            demo = data[name]
            missing = [key for key in REQUIRED_DEMO_KEYS if key not in demo]
            if missing:
                raise DatasetUploadError(
                    f"{name} is missing {', '.join(missing)}",
                )
            actions = demo["actions"]
            if actions.ndim != 2:
                raise DatasetUploadError(
                    f"{name}/actions must be 2-dimensional, got shape {actions.shape}",
                )
            if action_dim and actions.shape[1] != action_dim:
                raise DatasetUploadError(
                    f"{name} has {actions.shape[1]}-D actions but an earlier demo has "
                    f"{action_dim}-D — one dataset cannot mix action spaces",
                )
            action_dim = actions.shape[1]
            frames += int(actions.shape[0])

        tasks, env_name = _task_names(data, demos)

    return DatasetProbe(
        episodes=len(demos),
        frames=frames,
        tasks=tasks,
        env_name=env_name,
        action_dim=action_dim,
    )


def probe_as_dict(probe: DatasetProbe) -> dict[str, Any]:
    return {
        "episodes": probe.episodes,
        "frames": probe.frames,
        "tasks": probe.tasks,
        "env_name": probe.env_name,
        "action_dim": probe.action_dim,
    }
