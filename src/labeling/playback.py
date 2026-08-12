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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

TASK_ENVIRONMENTS = {
    "lift": "Lift",
    "can": "PickPlaceCan",
    "square": "NutAssemblySquare",
}


@dataclass(frozen=True)
class PlaybackConfig:
    camera: str = "agentview"
    height: int = 256
    width: int = 256
    fps: int = 20
    #: Dim the frames that the suggested trim would cut, so the reviewer can see
    #: what is being proposed instead of guessing.
    shade_trimmed: bool = True


DEFAULT_PLAYBACK = PlaybackConfig()


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
        camera_names=[config.camera],
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

    model = mujoco.MjModel.from_xml_string(model_xml)
    data = mujoco.MjData(model)
    camera = config.camera
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera) < 0:
        camera = "sideview" if mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, "sideview",
        ) >= 0 else -1
    renderer = mujoco.Renderer(model, height=config.height, width=config.width)
    frames: list[np.ndarray] = []
    try:
        total = len(states)
        start, end = trim if trim is not None else (0, total - 1)
        for index, state in enumerate(states):
            flat = np.asarray(state)
            data.qpos[:] = flat[: model.nq]
            data.qvel[:] = flat[model.nq : model.nq + model.nv]
            mujoco.mj_forward(model, data)
            renderer.update_scene(data, camera=camera)
            frame = renderer.render()
            frames.append(_annotate(
                frame,
                index=index,
                total=total,
                inside_trim=start <= index <= end,
                config=config,
            ))
    finally:
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
        for index, state in enumerate(states):
            env.sim.set_state_from_flattened(np.asarray(state).copy())
            env.sim.forward()
            frame = env.sim.render(
                height=config.height, width=config.width, camera_name=config.camera,
            )[::-1]
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
            frame = env.sim.render(
                height=config.height, width=config.width, camera_name=config.camera,
            )[::-1]
            frames.append(
                _annotate(
                    frame, index=total, total=total, inside_trim=True, config=config,
                ),
            )
    finally:
        env.close()

    _write_mp4(frames, output, config.fps)
    return output
