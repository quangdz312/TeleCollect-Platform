from src.labeling.duplicates import duplicate_episode_ids, find_duplicates


def record(episode_id: str, task: str, seed: int | None, *, key: str = "environment_seed"):
    provenance = {} if seed is None else {key: seed}
    return {"episode_id": episode_id, "task": task, "provenance": provenance}


def test_same_task_and_seed_is_one_group():
    groups = find_duplicates([
        record("a", "lift", 0),
        record("b", "lift", 0),
        record("c", "lift", 1),
    ])

    assert len(groups) == 1
    assert groups[0].task == "lift"
    assert groups[0].environment_seed == 0
    assert groups[0].keep == "a"
    assert groups[0].repeats == ("b",)


def test_same_seed_on_different_tasks_is_not_a_duplicate():
    assert find_duplicates([record("a", "lift", 0), record("b", "can", 0)]) == []


def test_unique_corpus_reports_nothing():
    assert find_duplicates([record("a", "lift", i) for i in range(5)]) == []


def test_missing_seed_means_unknown_not_shared():
    # Two records without a seed must not be grouped with each other: unknown is
    # not a shared configuration.
    assert find_duplicates([record("a", "lift", None), record("b", "lift", None)]) == []


def test_base_seed_is_used_when_environment_seed_is_absent():
    groups = find_duplicates([
        record("a", "lift", 7, key="base_seed"),
        record("b", "lift", 7, key="base_seed"),
    ])

    assert len(groups) == 1
    assert groups[0].environment_seed == 7


def test_only_the_repeats_are_flagged():
    ids = duplicate_episode_ids([
        record("a", "lift", 0),
        record("b", "lift", 0),
        record("c", "lift", 0),
        record("d", "can", 3),
    ])

    assert ids == {"b", "c"}


def test_real_corpus_has_no_duplicates():
    # tool_hang seeds 0 and 1 are different configurations of the same task.
    assert find_duplicates([
        record("can_clean_seed0", "can", 0),
        record("lift_clean_seed0", "lift", 0),
        record("square_clean_seed0", "square", 0),
        record("tool_hang_clean_seed0", "tool_hang", 0),
        record("tool_hang_clean_seed1", "tool_hang", 1),
    ]) == []
