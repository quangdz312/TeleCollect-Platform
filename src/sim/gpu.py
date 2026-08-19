"""Put MuJoCo's offscreen rendering on the discrete GPU.

On a laptop with switchable graphics, Windows hands OpenGL to the integrated
GPU by default. MuJoCo then rasterises on it, and every offscreen render pays
for it: measured on this machine, one 480px frame takes 22.0 ms on the Intel
Iris Xe against 0.9 ms on the RTX 3050, and a 3-camera 640px teleop step costs
137 ms against 11 ms. That is ~7 fps versus ~93 fps for the same work.

`SHIM_MCCOMPAT` is the NVIDIA Optimus flag that moves a process onto the
discrete GPU. It has to be set *before* the first OpenGL context is created,
which is why this is imported for its side effect rather than called later.
Verified in isolation: `__NV_PRIME_RENDER_OFFLOAD` and
`__GLX_VENDOR_LIBRARY_NAME` are Linux-only and change nothing on Windows.

Reading `.env` here rather than through `Settings` is deliberate: the driver
reads process environment variables, and pydantic-settings parses `.env` into
its own object without exporting anything.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Variables that steer GPU selection. Only these are taken from `.env`, so a
#: stray line in that file cannot inject arbitrary environment.
_GPU_VARS = ("SHIM_MCCOMPAT", "MUJOCO_GL")


def apply(env_file: str | Path = ".env") -> dict[str, str]:
    """Export the GPU variables from `env_file`, without overriding the shell.

    Returns what was applied. Anything already set in the real environment wins,
    so a developer can still force a different GPU for one run.
    """

    path = Path(env_file)
    if not path.is_file():
        return {}

    applied: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name in _GPU_VARS and name not in os.environ:
            os.environ[name] = value
            applied[name] = value
    return applied


def active_renderer() -> str | None:
    """The GL renderer string, or None if no context could be made.

    Costs a throwaway context, so call it once at startup to log which GPU won,
    not per render.
    """

    try:
        import mujoco
        from OpenGL import GL

        model = mujoco.MjModel.from_xml_string(
            "<mujoco><worldbody><body><geom size='1'/></body></worldbody></mujoco>",
        )
        renderer = mujoco.Renderer(model, height=64, width=64)
        try:
            renderer.render()
            return GL.glGetString(GL.GL_RENDERER).decode()
        finally:
            renderer.close()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break startup
        logger.debug("could not determine GL renderer: %s", exc)
        return None


def log_active_renderer() -> None:
    """Warn when rendering landed on an integrated GPU anyway.

    Silent success would hide a 20x slowdown behind "the sim feels sluggish",
    which is exactly how this cost a day of guessing before it was measured.
    """

    name = active_renderer()
    if name is None:
        logger.warning("GPU: could not determine the OpenGL renderer")
    elif "nvidia" in name.lower():
        logger.info("GPU: rendering on %s", name)
    else:
        logger.warning(
            "GPU: rendering on %s - offscreen rendering will be far slower. "
            "Set SHIM_MCCOMPAT=0x800000001 to use the discrete GPU.",
            name,
        )


# Applied at import so it lands before anything creates a GL context.
apply()
