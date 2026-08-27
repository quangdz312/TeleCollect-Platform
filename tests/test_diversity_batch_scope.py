"""Báo cáo đa dạng dữ liệu khi thu hẹp về một đợt thu.

Đợt thu nằm trong `provenance` của từng bản ghi chứ không phải một trường
riêng, nên phần lọc dễ trượt âm thầm: sai thì báo cáo vẫn trả về, chỉ là đếm
nhầm sang đợt khác.
"""

from src.labeling.diversity import build_report


def _record(episode_id: str, *, quality: str, batch: str | None, success: bool = True):
    record = {
        "episode_id": episode_id,
        "task": "lift",
        "requested_quality": quality,
        "recorded_success": success,
        "provenance": {},
    }
    if batch is not None:
        record["provenance"] = {"collection_batch_id": batch}
    return record


RECORDS = [
    _record("a1", quality="clean", batch="lift-v1"),
    _record("a2", quality="good", batch="lift-v1"),
    _record("b1", quality="clean", batch="lift-v2"),
    _record("b2", quality="poor", batch="lift-v2", success=False),
    _record("c1", quality="clean", batch=None),
]

LABELS = {
    record["episode_id"]: {"human_decision": "approved"} for record in RECORDS
}


def _totals(report) -> int:
    return sum(row["total"] for row in report["quality"])


def test_without_a_batch_every_episode_of_the_task_counts():
    report = build_report(RECORDS, LABELS, {}, task="lift", scope="approved")
    assert _totals(report) == 5


def test_batch_scope_counts_only_that_batch():
    report = build_report(
        RECORDS, LABELS, {}, task="lift", scope="approved", collection_batch_id="lift-v1"
    )
    assert _totals(report) == 2
    by_quality = {row["quality"]: row["total"] for row in report["quality"]}
    assert by_quality["clean"] == 1
    assert by_quality["good"] == 1
    assert by_quality["poor"] == 0


def test_episodes_without_a_batch_are_excluded_from_a_scoped_report():
    """`c1` không có đợt thu nên không được rơi vào đợt nào."""
    report = build_report(
        RECORDS, LABELS, {}, task="lift", scope="approved", collection_batch_id="lift-v2"
    )
    assert _totals(report) == 2
    by_quality = {row["quality"]: row["total"] for row in report["quality"]}
    assert by_quality["poor"] == 1


def test_unknown_batch_yields_an_empty_report_not_an_error():
    report = build_report(
        RECORDS, LABELS, {}, task="lift", scope="approved", collection_batch_id="nope"
    )
    assert _totals(report) == 0
    assert report["status"]["code"] == "no_data"
