"""A named front-on camera for review playback.

Every robosuite arena ships a `frontview`, but each one places it differently.
ToolHang's sits where the table fills the shot and the frame, stand and wrench
are all hidden, so it cannot be used to review the task. The angle that does
work was found by sweeping a free camera around the stand; a free camera has no
name though, and the Teleop recorder addresses cameras by name.

So the angle is installed into the model as a real camera instead. Both the
scripted playback and the Teleop recorder can then ask for it the same way, and
both produce the same shot.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Name the installed camera answers to, in `TELEOP_CAMERAS` and in playback.
REVIEW_CAMERA = "review_front"

#: Looking at the stand from the front, tilted down just enough to read how far
#: the rod has gone into the base. Chosen by rendering the sweep and picking by
#: eye, not derived: azimuth 180, elevation -15, distance 1.2 about the stand.
_AZIMUTH_DEG = 180.0
_ELEVATION_DEG = -15.0
_DISTANCE_M = 1.2
#: The stand sits at z≈0.88 and the action happens above it, so aim a little
#: over the base or the shot fills up with table legs and floor.
LOOKAT_LIFT_M = 0.15
_LOOKAT_LIFT_M = LOOKAT_LIFT_M

#: The offscreen buffer defaults to 640x480, which caps how large a frame can be
#: rendered. Review panes are larger than that.
_OFFWIDTH = 1280
_OFFHEIGHT = 960


def _pose(target: np.ndarray) -> tuple[np.ndarray, float]:
    """Camera position and orientation for the review angle about `target`.

    Mirrors how MuJoCo resolves a free camera from azimuth/elevation/distance,
    so the installed camera reproduces the angle the sweep was picked from.
    """

    azimuth = np.radians(_AZIMUTH_DEG)
    elevation = np.radians(_ELEVATION_DEG)
    # Free-camera convention: `forward` points from the camera at the lookat,
    # with elevation measured downward from horizontal.
    forward = np.array(
        [
            np.cos(elevation) * np.cos(azimuth),
            np.cos(elevation) * np.sin(azimuth),
            np.sin(elevation),
        ],
    )
    position = target - _DISTANCE_M * forward
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return position, right, up


def camera_xml(target: np.ndarray) -> str:
    """A `<camera>` element placed at the review angle about `target`."""

    position, right, up = _pose(np.asarray(target, dtype=float))
    xyaxes = " ".join(f"{value:.6f}" for value in (*right, *up))
    pos = " ".join(f"{value:.6f}" for value in position)
    return f'<camera name="{REVIEW_CAMERA}" pos="{pos}" xyaxes="{xyaxes}" mode="fixed"/>'


def install(xml: str, target: np.ndarray) -> str:
    """Add the review camera and a big enough offscreen buffer to `xml`.

    A camera left over from an earlier install is replaced rather than kept:
    the angle is computed from where the objects are, and a scene reset moves
    them, so re-installing must be able to re-aim.
    """

    if f'name="{REVIEW_CAMERA}"' in xml:
        xml = re.sub(
            rf'<camera name="{REVIEW_CAMERA}"[^>]*/>',
            "",
            xml,
            count=1,
        )

    buffer_tag = f'<global offwidth="{_OFFWIDTH}" offheight="{_OFFHEIGHT}"/>'
    if "<global" in xml:

        def resize(match: re.Match[str]) -> str:
            # Drop any size this tag already carries before adding ours, or a
            # second install would emit the attribute twice and MuJoCo's XML
            # parser rejects the model with "duplicate attribute".
            attrs = re.sub(r'\s*off(?:width|height)="[^"]*"', "", match.group(1))
            return f'<global{attrs} offwidth="{_OFFWIDTH}" offheight="{_OFFHEIGHT}"/>'

        xml = re.sub(r"<global([^>]*?)/>", resize, xml, count=1)
    elif "<visual>" in xml:
        xml = xml.replace("<visual>", f"<visual>{buffer_tag}", 1)
    else:
        xml = re.sub(r"(<worldbody>)", f"<visual>{buffer_tag}</visual>\\1", xml, count=1)

    return re.sub(r"(<worldbody>)", f"\\1{camera_xml(target)}", xml, count=1)


def stand_target(env: Any) -> np.ndarray:
    """Point the review camera should look at, lifted off the stand base."""

    body = env.sim.model.body_name2id("stand_root")
    target = np.asarray(env.sim.data.body_xpos[body], dtype=float).copy()
    target[2] += _LOOKAT_LIFT_M
    return target


#: The body each task's action happens around, in preference order. Only
#: ToolHang has a `stand_root`; aiming at a task's own manipulated object keeps
#: the same shot meaningful everywhere. `table` is the fallback for a task whose
#: object body is named something this list does not know.
_TARGET_BODIES = (
    "stand_root",       # tool_hang
    "cube_main",        # lift
    "Can_main",         # pick_place_can
    "SquareNut_main",   # nut_assembly_square
    "table",
    "bin1",             # PickPlaceCan has bins instead of a `table` body
)


def resolve_target(env: Any) -> np.ndarray | None:
    """Where to aim the review camera for whatever task `env` is running.

    Returns None when no known body is present, which is the caller's signal to
    leave the scene alone and keep using the environment's existing cameras.
    """

    model = env.sim.model
    for name in _TARGET_BODIES:
        try:
            body = model.body_name2id(name)
        except Exception:  # noqa: BLE001 - absent body is the normal case here
            continue
        target = np.asarray(env.sim.data.body_xpos[body], dtype=float).copy()
        target[2] += _LOOKAT_LIFT_M
        return target
    return None


def install_into_env(env: Any) -> bool:
    """Rebuild a live robosuite env's sim with the review camera in its model.

    Every path that renders a review pane calls this, so the operator's live
    view, the mp4 written during collection and the replay all frame the same
    shot. Returns whether the camera is now present.

    Keeps the original environment when anything goes wrong: a missing review
    angle costs a nice shot, but failing here would cost the whole session. It
    is logged rather than swallowed — a silent fall back to a different camera
    is what let the review video disagree with Teleop unnoticed.

    The physics state is saved and restored across the swap.
    `reset_from_xml_string` rebuilds the model from the XML alone, which drops
    where the placement sampler put the objects: the cube reappears at the world
    origin and drops through the table, and the scene renders as an empty table
    with nothing to manipulate.
    """

    try:
        env.sim.forward()
        target = resolve_target(env)
        if target is None:
            logger.warning(
                "review camera %s not installed: this scene has none of the bodies "
                "it can be aimed at (%s); review panes will use another camera and "
                "will not match the Teleop framing",
                REVIEW_CAMERA,
                ", ".join(_TARGET_BODIES),
            )
            return False
        state = env.sim.get_state().flatten().copy()
        env.reset_from_xml_string(install(env.sim.model.get_xml(), target))
        env.sim.set_state_from_flattened(state)
        env.sim.forward()
        return True
    except Exception:
        logger.exception(
            "review camera %s could not be installed; review panes will use another "
            "camera and will not match the Teleop framing",
            REVIEW_CAMERA,
        )
        # A failed `reset_from_xml_string` can leave the env holding no sim at
        # all, which would break every later step. Put the scene back the way
        # robosuite builds it rather than hand back a dead environment.
        if getattr(env, "sim", None) is None:
            env.reset()
        return False
