"""Render a collected episode to video so a person can watch it.

Collection runs with the offscreen renderer off, so nothing was recorded to
look at. Nothing needs re-collecting though: every frame's full MuJoCo state was
written to the dataset along with the scene XML, so the episode can be replayed
and rendered afterwards. Restoring a state and rendering reproduces the original
frame exactly, which keeps this idempotent and safe to re-run in batch.

This is separate from ``src/sim/render.py``, which is the live teleoperation
render path and is still a scaffold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

_LOG = logging.getLogger(__name__)

TASK_ENVIRONMENTS = {
    "lift": "Lift",
    "can": "PickPlaceCan",
    "square": "NutAssemblySquare",
}


@dataclass(frozen=True)
class PlaybackConfig:
    camera: str = "agentview"
    height: int = 640
    width: int = 640
    fps: int = 20
    #: Dim the frames that the suggested trim would cut, so the reviewer can see
    #: what is being proposed instead of guessing.
    #:
    #: Off by default now that the mp4 is written during collection: the trim is
    #: computed from the scores, which do not exist until after the rollout has
    #: finished, so a video encoded as the episode runs cannot know it. The
    #: reviewer still gets the range — `suggested_trim_start/end` ride along on
    #: the episode record and the review page marks them on the transport, which
    #: keeps them adjustable instead of burning one guess into the pixels.
    shade_trimmed: bool = False
    #: Longest playback a reviewer should have to sit through, in seconds. An
    #: episode longer than this is sampled every Nth frame rather than slowed
    #: down or sped up, so every task keeps the same `fps` and the same
    #: real-time feel. ToolHang runs ~1950 control steps where Lift runs ~200,
    #: which is a 98 s clip against a 10 s one at the shared 20 fps.
    max_playback_seconds: float = 35.0
    #: Two smaller panes stacked beside the main one, so the reviewer sees the
    #: layout and the gripper at once. The wrist view matters most on ToolHang:
    #: the task turns on a 1.25 mm clearance, which is under a pixel from a
    #: static camera half a metre away.
    top_camera: str = "birdview"
    wrist_camera: str = "robot0_eye_in_hand"


DEFAULT_PLAYBACK = PlaybackConfig()


def frame_stride(total: int, config: PlaybackConfig) -> int:
    """Keep every Nth frame so playback fits `max_playback_seconds`."""

    budget = config.max_playback_seconds * config.fps
    if budget <= 0 or total <= budget:
        return 1
    return int(np.ceil(total / budget))


def _render_views(env: Any, config: PlaybackConfig) -> np.ndarray:
    """The main view, with the overhead and wrist views stacked beside it."""

    def shot(name: str, size: int) -> np.ndarray | None:
        if not name:
            return None
        try:
            return env.sim.render(height=size, width=size, camera_name=name)[::-1]
        except Exception:
            return None

    half = config.height // 2
    return _compose(
        shot(config.camera, config.height),
        shot(config.top_camera, half),
        shot(config.wrist_camera, half),
    )


def _compose(
    main: np.ndarray,
    top: np.ndarray | None,
    bottom: np.ndarray | None,
) -> np.ndarray:
    """Main pane on the left, the two smaller panes stacked on its right.

    Falls back to whatever is available, so a task whose scene lacks one of the
    side cameras still renders rather than failing.
    """

    import cv2

    side = [pane for pane in (top, bottom) if pane is not None]
    if not side:
        return main

    half = main.shape[0] // 2
    column = []
    for pane in side:
        if pane.shape[0] != half:
            scale = half / pane.shape[0]
            pane = cv2.resize(pane, (int(round(pane.shape[1] * scale)), half))
        column.append(pane)
    if len(column) == 1:
        # One side view only: pad the column so it still matches the main pane.
        column.append(np.zeros_like(column[0]))

    stacked = np.vstack(column)
    if stacked.shape[0] != main.shape[0]:
        stacked = cv2.resize(stacked, (stacked.shape[1], main.shape[0]))
    return np.hstack((main, stacked))


def _make_environment(task: str, config: PlaybackConfig) -> Any:
    if task == "tool_hang":
        from src.sim.tool_hang import make_tool_hang_environment

        return make_tool_hang_environment(
            render=False,
            offscreen=True,
            control_freq=config.fps,
            horizon=6000,
        )
    import robosuite
    from robosuite.controllers import load_composite_controller_config

    return robosuite.make(
        TASK_ENVIRONMENTS[task],
        robots="Panda",
        controller_configs=load_composite_controller_config(controller="BASIC"),
        has_renderer=False,
        has_offscreen_renderer=True,
        use_object_obs=True,
        use_camera_obs=False,
        camera_names=[name for name in (config.camera, config.wrist_camera) if name],
        camera_heights=config.height,
        camera_widths=config.width,
        control_freq=config.fps,
        ignore_done=True,
        reward_shaping=False,
        hard_reset=False,
    )


def _annotate(
    frame: np.ndarray,
    *,
    index: int,
    total: int,
    inside_trim: bool,
    config: PlaybackConfig,
) -> np.ndarray:
    import cv2

    image = np.ascontiguousarray(frame)
    if config.shade_trimmed and not inside_trim:
        image = (image * 0.35).astype(np.uint8)
    label = f"{index}/{total}" + ("" if inside_trim else "  TRIM")
    cv2.putText(
        image, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
    )
    return image


def _render_tool_hang_demo(
    source: Path,
    demo: str,
    output: Path,
    *,
    trim: tuple[int, int] | None,
    config: PlaybackConfig,
) -> Path:
    """Render ToolHang from its recorded XML without robosuite remapping.

    ToolHang's external skill modifies the scene after robosuite builds its
    model. Restoring that XML through ``reset_from_xml_string`` makes the old
    geom mapping invalid, so the playback path uses MuJoCo directly instead.
    """

    import mujoco

    with h5py.File(source, "r") as handle:
        group = handle["data"][demo]
        states = np.asarray(group["states"])
        model_xml = group.attrs["model_file"]
        if isinstance(model_xml, bytes):
            model_xml = model_xml.decode("utf-8")

    from src.sim.review_camera import LOOKAT_LIFT_M as REVIEW_LOOKAT_LIFT_M
    from src.sim.review_camera import REVIEW_CAMERA, install

    # Settle the scene once to read where the stand ended up, then install the
    # review camera aimed at it. ToolHang's own `frontview` looks along the
    # table and hides everything the reviewer needs to see.
    probe = mujoco.MjModel.from_xml_string(model_xml)
    probe_data = mujoco.MjData(probe)
    first = np.asarray(states[0])[1:]
    probe_data.qpos[:] = first[: probe.nq]
    probe_data.qvel[:] = first[probe.nq : probe.nq + probe.nv]
    mujoco.mj_forward(probe, probe_data)
    stand = probe_data.xpos[probe.body("stand_root").id].copy()
    stand[2] += REVIEW_LOOKAT_LIFT_M

    model = mujoco.MjModel.from_xml_string(install(model_xml, stand))
    data = mujoco.MjData(model)

    def resolve(name: str | None) -> str | None:
        if not name:
            return None
        found = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        return name if found >= 0 else None

    camera = resolve(REVIEW_CAMERA) or resolve(config.camera) or resolve("sideview")
    if camera != REVIEW_CAMERA:
        _LOG.warning(
            "review camera %r is missing after installing it into the recorded XML; "
            "replaying with %r instead, which is a different shot from the Teleop "
            "framing and from the video written at collection time",
            REVIEW_CAMERA,
            camera,
        )
    top = resolve(config.top_camera)
    wrist = resolve(config.wrist_camera)

    # Robosuite renders group 1 (visual meshes) and hides group 0 (collision
    # primitives); see `robosuite/environments/base.py`, which sets
    # `vopt.geomgroup[0] = render_collision_mesh`. A bare MuJoCo renderer
    # defaults to showing both, so the Panda's green collision capsules draw
    # over its visual shells and the robot replays green. Match robosuite's
    # choice so the replay looks like the mp4 written at collection time.
    scene_option = mujoco.MjvOption()
    scene_option.geomgroup[0] = 0
    scene_option.geomgroup[1] = 1

    half = config.height // 2
    small = mujoco.Renderer(model, height=half, width=half)
    renderer = mujoco.Renderer(model, height=config.height, width=config.width)
    frames: list[np.ndarray] = []
    try:
        total = len(states)
        start, end = trim if trim is not None else (0, total - 1)
        stride = frame_stride(total, config)
        for index, state in enumerate(states):
            if index % stride:
                continue
            # `sim.get_state().flatten()` is [time, qpos, qvel]; dropping the
            # leading time element is what `set_state_from_flattened` does for
            # the other tasks. Reading it as qpos shifts every joint by one and
            # pushes the objects out of frame, which renders an empty table.
            flat = np.asarray(state)[1:]
            data.qpos[:] = flat[: model.nq]
            data.qvel[:] = flat[model.nq : model.nq + model.nv]
            mujoco.mj_forward(model, data)
            renderer.update_scene(data, camera=camera, scene_option=scene_option)
            main = renderer.render().copy()

            def pane(name: str | None) -> np.ndarray | None:
                if name is None:
                    return None
                small.update_scene(data, camera=name, scene_option=scene_option)
                return small.render().copy()

            frame = _compose(main, pane(top), pane(wrist))
            frames.append(_annotate(
                frame,
                index=index,
                total=total,
                inside_trim=start <= index <= end,
                config=config,
            ))
    finally:
        small.close()
        renderer.close()
    _write_mp4(frames, output, config.fps)
    return output


def _write_mp4(frames: list[np.ndarray], output: Path, fps: int) -> None:
    import av

    if not frames:
        raise ValueError("No frames to write")
    height, width = frames[0].shape[:2]
    # H.264 needs even dimensions.
    height -= height % 2
    width -= width % 2
    output.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(output), mode="w")
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for frame in frames:
            picture = av.VideoFrame.from_ndarray(
                np.ascontiguousarray(frame[:height, :width]), format="rgb24",
            )
            for packet in stream.encode(picture):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()


class RolloutRecorder:
    """Capture the three review panes while an episode is being collected.

    The replay path below rebuilds a video by restoring recorded states, which
    means paying for a second pass over the episode before anyone can watch it.
    A rollout already has the scene in exactly the right state at every step, so
    the frames can be taken as they happen and the replay skipped entirely.

    Frames are held in memory until :meth:`write`; a ToolHang episode strided to
    `max_playback_seconds` is a few hundred 640px frames, which is tens of MB.
    The replay path stays for old datasets, re-renders, and any episode whose
    mp4 is missing.
    """

    def __init__(self, env: Any, config: PlaybackConfig = DEFAULT_PLAYBACK) -> None:
        self._env = env
        self._config = config
        self._frames: list[np.ndarray] = []
        self._seen = 0
        #: Stride is only known once the episode length is, which is after it
        #: ends. Capture everything and thin it out at write time instead.
        self._names = self._resolve_cameras()

    def _resolve_cameras(self) -> tuple[str | None, str | None, str | None]:
        """Which of the three configured cameras this scene actually has."""

        model = self._env.sim.model
        available = {model.camera_id2name(i) for i in range(model.ncam)}

        def pick(*candidates: str) -> str | None:
            return next((name for name in candidates if name in available), None)

        from src.sim.review_camera import REVIEW_CAMERA

        main = pick(REVIEW_CAMERA, self._config.camera, "agentview", "sideview")
        if main != REVIEW_CAMERA:
            # This fallback is what let the collected mp4 disagree with what the
            # operator sees without anyone noticing. The video is still worth
            # writing, but not quietly: `make_tool_hang_environment` installs the
            # camera, so reaching here means that install failed.
            _LOG.warning(
                "review camera %r is missing from this scene; the collected video "
                "will use %r, which is a different shot from the Teleop framing. "
                "Cameras present: %s",
                REVIEW_CAMERA,
                main,
                ", ".join(sorted(name for name in available if name)),
            )
        return (main, pick(self._config.top_camera), pick(self._config.wrist_camera))

    def capture(self) -> None:
        """Render one composed frame from the live scene. Never raises.

        A failed render must not take the episode down with it: the trajectory
        is the artefact that matters, and the video can always be rebuilt from
        the recorded states by the replay path.
        """

        self._seen += 1
        main_name, top_name, wrist_name = self._names
        if main_name is None:
            return
        try:
            size = self._config.height
            half = size // 2

            def shot(name: str | None, pixels: int) -> np.ndarray | None:
                if name is None:
                    return None
                return self._env.sim.render(
                    height=pixels, width=pixels, camera_name=name,
                )[::-1].copy()

            main = shot(main_name, size)
            if main is None:
                return
            self._frames.append(_compose(main, shot(top_name, half), shot(wrist_name, half)))
        except Exception:  # noqa: BLE001 - see docstring
            pass

    def write(self, output: str | Path) -> Path | None:
        """Encode what was captured, thinned to `max_playback_seconds`."""

        output = Path(output)
        if not self._frames:
            return None
        stride = frame_stride(len(self._frames), self._config)
        total = len(self._frames)
        frames = [
            _annotate(
                frame,
                index=index,
                total=total,
                inside_trim=True,
                config=self._config,
            )
            for index, frame in enumerate(self._frames)
            if index % stride == 0
        ]
        _write_mp4(frames, output, self._config.fps)
        return output


def render_demo(
    source: str | Path,
    demo: str,
    output: str | Path,
    *,
    task: str | None = None,
    trim: tuple[int, int] | None = None,
    config: PlaybackConfig = DEFAULT_PLAYBACK,
) -> Path:
    """Replay one demo from its recorded states and write an mp4."""

    source = Path(source)
    output = Path(output)
    with h5py.File(source, "r") as handle:
        group = handle["data"][demo]
        states = np.asarray(group["states"])
        actions = np.asarray(group["actions"])
        model_xml = group.attrs["model_file"]
        if isinstance(model_xml, bytes):
            model_xml = model_xml.decode("utf-8")
        resolved = task or str(group.attrs.get("telecollect_task", "")) or None
        if resolved is None:
            raise ValueError(f"{source}::{demo}: cannot determine the task; pass task=")

    if resolved == "tool_hang":
        return _render_tool_hang_demo(
            source,
            demo,
            output,
            trim=trim,
            config=config,
        )

    env = _make_environment(resolved, config)
    frames: list[np.ndarray] = []
    try:
        env.reset()
        env.reset_from_xml_string(model_xml)
        env.sim.reset()
        total = len(states)
        start, end = trim if trim is not None else (0, total - 1)
        stride = frame_stride(total, config)
        for index, state in enumerate(states):
            if index % stride:
                continue
            env.sim.set_state_from_flattened(np.asarray(state).copy())
            env.sim.forward()
            frame = _render_views(env, config)
            frames.append(
                _annotate(
                    frame,
                    index=index,
                    total=total,
                    inside_trim=start <= index <= end,
                    config=config,
                ),
            )
        # The state after the final action is never stored in ``states``; on a
        # successful episode that is the frame the task actually succeeded on,
        # so it is worth one more step to show the reviewer the outcome.
        if len(actions):
            env.step(np.asarray(actions[-1]))
            frame = _render_views(env, config)
            frames.append(
                _annotate(
                    frame, index=total, total=total, inside_trim=True, config=config,
                ),
            )
    finally:
        env.close()

    _write_mp4(frames, output, config.fps)
    return output
