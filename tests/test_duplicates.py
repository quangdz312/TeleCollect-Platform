import numpy as np

from src.labeling.duplicates import (
    FINGERPRINT_VERSION,
    duplicate_episode_ids,
    find_duplicates,
    trajectory_fingerprint,
)


def record(episode_id: str, task: str, fingerprint: str | None, *, seed: int = 0):
    return {
        "episode_id": episode_id,
        "task": task,
        "trajectory_fingerprint": fingerprint,
        "provenance": {"environment_seed": seed},
    }


FP_A = f"{FINGERPRINT_VERSION}:aaa"
FP_B = f"{FINGERPRINT_VERSION}:bbb"


def test_same_task_and_fingerprint_is_one_group():
    groups = find_duplicates([
        record("a", "lift", FP_A),
        record("b", "lift", FP_A),
        record("c", "lift", FP_B),
    ])

    assert len(groups) == 1
    assert groups[0].task == "lift"
    assert groups[0].fingerprint == FP_A
    assert groups[0].keep == "a"
    assert groups[0].repeats == ("b",)


def test_same_fingerprint_on_different_tasks_is_not_a_duplicate():
    assert find_duplicates([record("a", "lift", FP_A), record("b", "can", FP_A)]) == []


def test_unique_corpus_reports_nothing():
    assert find_duplicates([record(str(i), "lift", f"{FINGERPRINT_VERSION}:{i}") for i in range(5)]) == []


def test_missing_fingerprint_means_unknown_not_shared():
    assert find_duplicates([record("a", "lift", None), record("b", "lift", None)]) == []


def test_same_seed_with_different_fingerprints_is_not_duplicate():
    assert find_duplicates([
        record("a", "lift", FP_A, seed=7),
        record("b", "lift", FP_B, seed=7),
    ]) == []


def test_only_the_repeats_are_flagged():
    ids = duplicate_episode_ids([
        record("a", "lift", FP_A),
        record("b", "lift", FP_A),
        record("c", "lift", FP_A),
        record("d", "can", FP_B),
    ])

    assert ids == {"b", "c"}


def test_fingerprint_changes_with_actions_even_when_seed_is_shared():
    state = np.array([1.0, 2.0])
    first = trajectory_fingerprint("can", state, np.zeros((2, 7)))
    second_actions = np.zeros((2, 7))
    second_actions[1, 0] = 0.1
    second = trajectory_fingerprint("can", state, second_actions)
    assert first != second


def test_fingerprint_is_stable_across_numeric_dtypes():
    state32 = np.array([1.0, 2.0], dtype=np.float32)
    actions32 = np.zeros((2, 7), dtype=np.float32)
    assert trajectory_fingerprint("lift", state32, actions32) == trajectory_fingerprint(
        "lift", state32.astype(np.float64), actions32.astype(np.float64),
    )


def test_empty_state_cannot_prove_duplicate():
    assert trajectory_fingerprint("lift", np.array([]), np.zeros((2, 7))) is None
