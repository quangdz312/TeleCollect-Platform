"""Filesystem state owned only by the standalone local application."""

from __future__ import annotations

import json
from pathlib import Path


def state_path(data_dir: Path) -> Path:
    return data_dir / "local-state.json"


def load(data_dir: Path) -> dict[str, str]:
    path = state_path(data_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def save(data_dir: Path, values: dict[str, str]) -> None:
    path = state_path(data_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2), encoding="utf-8")
    temporary.replace(path)
