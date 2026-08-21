"""Background work for the review UI: collecting episodes and rendering video.

Both are blocking, CPU-bound simulator work measured in tens of seconds, so
neither can run on the event loop — a single collection request would freeze
every other client until it finished.

Each kind gets its own single-worker pool. One worker because a MuJoCo
environment is not cheap and two of them competing does not make either
faster; separate pools because a video render must not sit behind a
five-minute collection just to let someone watch the episode that finished a
moment ago.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .workspace import Workspace

JobKind = Literal["collect", "render"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]

#: Keep the log bounded; a verbose collection would otherwise grow without end.
MAX_LOG_LINES = 400


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: JobKind
    request: dict[str, Any]
    status: JobStatus = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    done: int = 0
    total: int = 0
    log: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["progress"] = self.done / self.total if self.total else 0.0
        return data


class JobRegistry:
    """In-memory job table. Jobs do not survive a restart, and need not.

    Anything worth keeping — the dataset, the scores, the labels — is on disk
    the moment the job succeeds; a job record is only the progress bar.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pools: dict[JobKind, ThreadPoolExecutor] = {
            "collect": ThreadPoolExecutor(max_workers=1, thread_name_prefix="collect"),
            "render": ThreadPoolExecutor(max_workers=1, thread_name_prefix="render"),
        }

    # --- table --------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else Job(**asdict(job))

    def list(self, *, kind: JobKind | None = None, limit: int = 25) -> list[Job]:
        with self._lock:
            jobs = [Job(**asdict(job)) for job in self._jobs.values()]
        if kind is not None:
            jobs = [job for job in jobs if job.kind == kind]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def active(self, kind: JobKind) -> Job | None:
        for job in self.list(kind=kind, limit=100):
            if job.status in {"queued", "running"}:
                return job
        return None

    # --- mutation (always under the lock) -----------------------------------

    def _mutate(self, job_id: str, apply: Callable[[Job], None]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                apply(job)

    def _append_log(self, job_id: str, line: str) -> None:
        def apply(job: Job) -> None:
            job.log.append(line)
            if len(job.log) > MAX_LOG_LINES:
                del job.log[: len(job.log) - MAX_LOG_LINES]
            if line.startswith("episode="):
                job.done = min(job.done + 1, job.total or job.done + 1)

        self._mutate(job_id, apply)

    # --- submission ---------------------------------------------------------

    def submit(
        self,
        kind: JobKind,
        request: dict[str, Any],
        work: Callable[[Callable[[str], None]], dict[str, Any]],
        *,
        total: int = 0,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, request=request, total=total)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            self._mutate(job.id, lambda item: setattr(item, "status", "running"))
            self._mutate(job.id, lambda item: setattr(item, "started_at", _now()))
            try:
                result = work(lambda line: self._append_log(job.id, line))
            except Exception as error:  # noqa: BLE001 - surfaced to the client verbatim
                # Bound before the handler exits: Python deletes the `as` name
                # at the end of the block, so a lambda closing over it would be
                # a time bomb the moment this stops running synchronously.
                message = str(error) or repr(error)
                detail = traceback.format_exc(limit=4)
                self._mutate(job.id, lambda item: setattr(item, "status", "failed"))
                self._mutate(job.id, lambda item: setattr(item, "error", message))
                self._append_log(job.id, detail.strip().splitlines()[-1])
            else:
                self._mutate(job.id, lambda item: setattr(item, "result", result))
                self._mutate(job.id, lambda item: setattr(item, "status", "succeeded"))
            finally:
                self._mutate(job.id, lambda item: setattr(item, "finished_at", _now()))

        self._pools[kind].submit(run)
        return job


_REGISTRY: JobRegistry | None = None


def registry() -> JobRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = JobRegistry()
    return _REGISTRY


# --- the two jobs -----------------------------------------------------------


def submit_collection(
    workspace: Workspace,
    *,
    task: str,
    quality: str,
    episodes: int,
    seed: int,
    horizon: int | None = None,
    overwrite: bool = False,
) -> Job:
    """Collect one batch, then rescore the whole corpus.

    The rescore is not optional bookkeeping: three penalties are relative to
    the other episodes of the same task, so the episodes already in the
    workspace have different scores once this batch lands.
    """

    from src.sim.scripted_generation import run_collection

    workspace.ensure()
    output = workspace.dataset_path(task, quality, seed)
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"{output.name} already exists; pick another seed or ask to overwrite",
        )

    def work(log: Callable[[str], None]) -> dict[str, Any]:
        capture_video_dir = None if task == "tool_hang" else workspace.videos_dir
        result = run_collection(
            task,
            episodes=episodes,
            horizon=horizon,
            seed=seed,
            quality=quality,
            output=output,
            overwrite=True,
            logger=log,
            # Tool Hang episodes are long and use a three-pane camera. Holding
            # every live frame until encode used several GB and could kill the
            # API before the HDF5 writer closed. Finish the trajectory first;
            # its videos are queued on the single render worker below.
            video_dir=capture_video_dir,
        )
        scores = workspace.rescore()
        from .auto_gate import apply as apply_auto_gate

        gate_counts = apply_auto_gate(workspace)
        batch_scores = [
            score for score in scores
            if Path(str(score.get("source", ""))).name == output.name
        ]
        rendered = 0
        videos_queued = 0
        if task == "tool_hang":
            # The HDF5 is closed and scores are visible before anything enters
            # the render queue. The render pool has one worker, so videos are
            # built sequentially and an encoder failure cannot lose the batch.
            for score in batch_scores:
                submit_render(workspace, str(score["episode_id"]))
                videos_queued += 1
                log(f"video queued: {score.get('display_name', score['episode_id'])}")
        else:
            for score in batch_scores:
                render_video(workspace, str(score["episode_id"]))
                rendered += 1
                log(f"video saved: {score.get('display_name', score['episode_id'])}")
        return {
            "output": str(output),
            "task": result.task,
            "quality": result.quality,
            "episodes": len(result.episodes),
            "successes": result.success_count,
            "profile_version": result.provenance.profile_version,
            "corpus_episodes": len(scores),
            "videos": rendered,
            "videos_queued": videos_queued,
            "auto_gate": gate_counts,
        }

    return registry().submit(
        "collect",
        {
            "task": task,
            "quality": quality,
            "episodes": episodes,
            "seed": seed,
            "horizon": horizon,
            "output": output.name,
        },
        work,
        total=episodes,
    )


def render_video(workspace: Workspace, episode_id: str) -> Path:
    """Render one episode's playback, or return the cached file.

    Runs on the render pool via :func:`submit_render` when called from the API;
    exposed directly so tests and scripts can render without a job.
    """

    from .playback import PlaybackConfig, render_demo

    # Matches what the collection-time recorder writes, so a rebuilt video is
    # interchangeable with a cached one. The reviewer is judging whether a grasp
    # was solid, and a small frame upscaled in a browser hides exactly that.
    config = PlaybackConfig(height=640, width=640)

    target = workspace.video_path(episode_id)
    if target.exists():
        return target

    score = workspace.scores_by_id().get(episode_id)
    if score is None:
        raise KeyError(episode_id)
    source = workspace.resolve_source(str(score["source"]))

    workspace.videos_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial.mp4")
    render_demo(
        source,
        str(score["demo"]),
        partial,
        task=str(score["task"]),
        trim=(int(score["suggested_trim_start"]), int(score["suggested_trim_end"])),
        config=config,
    )
    # Only publish once the encode finished, so an interrupted render cannot
    # leave a truncated file that later requests would happily serve.
    partial.replace(target)
    return target


def submit_render(workspace: Workspace, episode_id: str) -> Job:
    def work(log: Callable[[str], None]) -> dict[str, Any]:
        log(f"rendering {episode_id}")
        path = render_video(workspace, episode_id)
        return {"episode_id": episode_id, "video": path.name}

    return registry().submit("render", {"episode_id": episode_id}, work, total=1)
