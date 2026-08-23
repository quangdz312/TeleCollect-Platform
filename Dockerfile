# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /app

# build-essential + linux-libc-dev: pynput (scripts/teleop_ui.py, not imported
# by the web backend) pulls evdev>=1.3 on Linux, which has no prebuilt wheel
# and compiles a C extension against linux/input*.h. Without these, `pip
# install -r requirements.txt` fails outright on a fresh Linux image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*

# A venv rather than `pip install --user`: the final stage runs as `appuser`
# (HOME=/home/appuser), and a --user install under /root/.local is only ever
# on sys.path for the user whose home it lives in — root, not appuser. Every
# import failed with that layout (verified: `docker run --rm <image> python
# -c "import cv2"` raised ModuleNotFoundError as appuser, worked as root). A
# venv's site-packages is tied to the venv's prefix, not to $HOME, so it
# works for whichever user activates it.
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Production ----
FROM python:3.11-slim

WORKDIR /app

# ffmpeg: video upload/thumbnail/playback (src/main.py checks it's on PATH).
# libgl1, libglib2.0-0: opencv-python-headless still dlopen()s these despite
# the "headless" name.
# libglfw3, libosmesa6: mujoco/glfw (requirements.txt) need one of GLFW
# (needs a display), EGL or OSMesa to create a render context; OSMesa is the
# only one that works without a GPU or an X server, which is what a CPU-only
# VPS container is. MUJOCO_GL=osmesa below selects it — src/sim/gpu.py only
# sets MUJOCO_GL from .env if it is not already in the environment, so this
# always wins in the container regardless of what a checked-in .env says.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 libglfw3 libosmesa6 \
    && rm -rf /var/lib/apt/lists/*

# MUJOCO_GL picks MuJoCo's own backend; PyOpenGL has a separate platform
# detector (OpenGL.PLATFORM) that defaults to GLX on Linux regardless of
# MUJOCO_GL and must be told about OSMesa too, or `import mujoco` fails with
# `AttributeError: 'GLXPlatform' object has no attribute 'OSMesa'` (verified
# by importing mujoco in this image with only MUJOCO_GL set).
ENV MUJOCO_GL=osmesa
ENV PYOPENGL_PLATFORM=osmesa

# Copy the venv from builder — see the note on stage 1 for why this is a venv
# and not `pip install --user`.
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Security: run as non-root user
RUN useradd -m appuser

# Copy application code
COPY . .

# Create data directory with correct ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# One worker: SessionManager/TrainingJobManager keep state in RAM (see
# src/core/session.py, src/training/jobs.py) — a second uvicorn worker would
# be a second process with its own copy of that state, silently dropping
# whichever teleop session/training job didn't land on it.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
