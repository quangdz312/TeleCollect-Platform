"""Technical schema validation for raw Robomimic Can datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]
    demos: int
    total_samples: int


def _schema(path: str | Path) -> dict[str, tuple[tuple[int, ...], str]]:
    with h5py.File(path, "r") as handle:
        demo = handle["data"][sorted(handle["data"].keys(), key=lambda x: int(x.split("_")[1]))[0]]
        return {key: (value.shape[1:], str(value.dtype)) for key, value in demo["obs"].items()}


def validate_robomimic_dataset(
    path: str | Path, *, reference_path: str | Path, action_dim: int = 7,
) -> ValidationResult:
    errors: list[str] = []
    total = 0
    demos = 0
    reference = _schema(reference_path)
    required = ("actions", "states", "rewards", "dones", "obs", "next_obs")
    try:
        with h5py.File(path, "r") as handle:
            if "data" not in handle:
                return ValidationResult(False, ("missing group: data",), 0, 0)
            data = handle["data"]
            try:
                parsed = json.loads(data.attrs["env_args"])
                if not parsed.get("env_name") or not parsed.get("env_kwargs"):
                    errors.append("env_args is missing environment metadata")
            except (KeyError, TypeError, json.JSONDecodeError):
                errors.append("env_args is missing or is not valid JSON")
            names = sorted((key for key in data if key.startswith("demo_")), key=lambda x: int(x.split("_")[1]))
            demos = len(names)
            if not names:
                errors.append("data contains no demos")
            for name in names:
                demo = data[name]
                for field in required:
                    if field not in demo:
                        errors.append(f"{name}: missing field {field}")
                if any(field not in demo for field in required):
                    continue
                length = len(demo["actions"])
                total += length
                if demo["actions"].shape != (length, action_dim):
                    errors.append(f"{name}: invalid action shape {demo['actions'].shape}")
                if int(demo.attrs.get("num_samples", -1)) != length:
                    errors.append(f"{name}: num_samples does not match actions")
                for field in ("states", "rewards", "dones"):
                    if len(demo[field]) != length:
                        errors.append(f"{name}: {field} timestep mismatch")
                for group_name in ("obs", "next_obs"):
                    group = demo[group_name]
                    if set(group.keys()) != set(reference):
                        errors.append(f"{name}: {group_name} keys differ from Can reference")
                    for key, (shape, _dtype) in reference.items():
                        if key in group:
                            if len(group[key]) != length:
                                errors.append(f"{name}: {group_name}/{key} timestep mismatch")
                            if group[key].shape[1:] != shape:
                                errors.append(f"{name}: {group_name}/{key} shape mismatch")
                def datasets(group: h5py.Group):
                    for item in group.values():
                        if isinstance(item, h5py.Dataset):
                            yield item
                        elif isinstance(item, h5py.Group):
                            yield from datasets(item)
                for dataset in datasets(demo):
                    if np.issubdtype(dataset.dtype, np.number) and not np.all(np.isfinite(dataset[...])):
                        errors.append(f"{name}: non-finite values in {dataset.name}")
            if int(data.attrs.get("total", -1)) != total:
                errors.append("data total attribute does not match demo samples")
    except OSError as exc:
        errors.append(f"cannot open HDF5: {exc}")
    return ValidationResult(not errors, tuple(errors), demos, total)
