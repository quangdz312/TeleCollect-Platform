"""Compare two deterministic-evaluation result files episode by episode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    return parser.parse_args()


def _episodes(path: Path) -> dict[int, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError(f"{path}: missing episodes list")
    result: dict[int, dict[str, Any]] = {}
    for episode in episodes:
        seed = int(episode["seed"])
        if seed in result:
            raise ValueError(f"{path}: duplicate seed {seed}")
        result[seed] = episode
    return result


def compare_episode(
    first: dict[str, Any],
    second: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    prefix_a = np.asarray(first.get("action_prefix", []), dtype=np.float64)
    prefix_b = np.asarray(second.get("action_prefix", []), dtype=np.float64)
    compared_steps = min(len(prefix_a), len(prefix_b))
    if compared_steps:
        if prefix_a.ndim != 2 or prefix_b.ndim != 2 or prefix_a.shape[1:] != prefix_b.shape[1:]:
            raise ValueError("action_prefix dimensions do not match")
        difference = np.abs(prefix_a[:compared_steps] - prefix_b[:compared_steps])
        per_step = np.max(difference, axis=1)
        divergent = np.flatnonzero(per_step > tolerance)
        max_error = float(difference.max())
        mean_error = float(difference.mean())
        first_divergence = int(divergent[0]) if len(divergent) else None
    else:
        max_error = None
        mean_error = None
        first_divergence = None
    state_prefix_a = first.get("state_prefix_hashes", [])
    state_prefix_b = second.get("state_prefix_hashes", [])
    compared_state_steps = min(len(state_prefix_a), len(state_prefix_b))
    first_state_divergence = next(
        (
            step
            for step in range(compared_state_steps)
            if state_prefix_a[step] != state_prefix_b[step]
        ),
        None,
    )
    return {
        "seed": int(first["seed"]),
        "initial_state_same": first.get("initial_state_hash") == second.get("initial_state_hash"),
        "action_hash_same": first.get("action_hash") == second.get("action_hash"),
        "success_same": bool(first.get("success")) == bool(second.get("success")),
        "steps_first": int(first.get("steps", 0)),
        "steps_second": int(second.get("steps", 0)),
        "compared_prefix_steps": compared_steps,
        "prefix_max_abs_error": max_error,
        "prefix_mean_abs_error": mean_error,
        "first_divergence_step": first_divergence,
        "first_state_divergence_step": first_state_divergence,
    }


def compare_files(first_path: Path, second_path: Path, tolerance: float) -> list[dict[str, Any]]:
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    first = _episodes(first_path)
    second = _episodes(second_path)
    if set(first) != set(second):
        missing_first = sorted(set(second) - set(first))
        missing_second = sorted(set(first) - set(second))
        raise ValueError(
            f"seed sets differ; missing_first={missing_first}, missing_second={missing_second}"
        )
    return [compare_episode(first[seed], second[seed], tolerance) for seed in sorted(first)]


def main() -> int:
    args = parse_args()
    rows = compare_files(args.first, args.second, args.tolerance)
    for row in rows:
        print(
            f"seed={row['seed']} initial={'same' if row['initial_state_same'] else 'DIFF'} "
            f"success={'same' if row['success_same'] else 'DIFF'} "
            f"steps={row['steps_first']}/{row['steps_second']} "
            f"prefix_max={row['prefix_max_abs_error']} "
            f"first_divergence={row['first_divergence_step']} "
            f"state_divergence={row['first_state_divergence_step']}"
        )
    print(
        "summary "
        f"seeds={len(rows)} "
        f"initial_same={sum(bool(row['initial_state_same']) for row in rows)} "
        f"action_hash_same={sum(bool(row['action_hash_same']) for row in rows)} "
        f"success_same={sum(bool(row['success_same']) for row in rows)} "
        f"prefix_within_tolerance={sum(row['first_divergence_step'] is None for row in rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
