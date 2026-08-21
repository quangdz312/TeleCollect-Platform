from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.labeling import jobs
from src.sim import scripted_generation


class _ImmediateRegistry:
    def __init__(self) -> None:
        self.result = None

    def submit(self, _kind, _request, work, *, total=0):
        self.result = work(lambda _line: None)
        return SimpleNamespace(result=self.result, total=total)


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.videos_dir = root / "videos"

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def dataset_path(self, task: str, quality: str, seed: int) -> Path:
        return self.root / f"{task}_{quality}_seed{seed}.hdf5"

    def rescore(self):
        return [
            {
                "episode_id": f"tool_hang_clean_seed1.hdf5::demo_{index}",
                "display_name": f"tool_hang_{index + 1:03d}",
                "source": "tool_hang_clean_seed1.hdf5",
            }
            for index in range(2)
        ]

    def scores(self):
        return self.rescore()

    @staticmethod
    def labels_by_id():
        return {}


def test_tool_hang_closes_dataset_before_queueing_videos(monkeypatch, tmp_path: Path) -> None:
    registry = _ImmediateRegistry()
    capture_dirs: list[Path | None] = []
    queued: list[str] = []

    def run_collection(*_args, video_dir=None, **_kwargs):
        capture_dirs.append(video_dir)
        return SimpleNamespace(
            task="tool_hang",
            quality="clean",
            episodes=(object(), object()),
            success_count=2,
            provenance=SimpleNamespace(profile_version="test"),
        )

    monkeypatch.setattr(scripted_generation, "run_collection", run_collection)
    monkeypatch.setattr(jobs, "registry", lambda: registry)
    monkeypatch.setattr(
        jobs,
        "submit_render",
        lambda _workspace, episode_id: queued.append(episode_id),
    )
    monkeypatch.setattr(
        jobs,
        "render_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("eager render")),
    )

    job = jobs.submit_collection(
        _Workspace(tmp_path),
        task="tool_hang",
        quality="clean",
        episodes=2,
        seed=1,
    )

    assert capture_dirs == [None]
    assert queued == [
        "tool_hang_clean_seed1.hdf5::demo_0",
        "tool_hang_clean_seed1.hdf5::demo_1",
    ]
    assert job.result["videos"] == 0
    assert job.result["videos_queued"] == 2
