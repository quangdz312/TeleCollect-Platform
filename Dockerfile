# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /app

# build-essential + linux-libc-dev: pynput (scripts/teleop_ui.py, not imported
# by the web backend) pulls evdev>=1.3 on Linux, which has no prebuilt wheel
# and compiles a C extension against linux/input*.h. Without these, `pip
# install -r requirements.txt` fails outright on a fresh Linux image.
# git: robomimic 0.5 is only on GitHub, so pip clones it (see below).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential linux-libc-dev git \
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

# Evaluation runs rollouts here, which needs torch and robomimic — the same
# pieces the training image installs, minus CUDA. Training itself still does
# not happen on this box; it is the policy rollout that has to.
#
# The CPU index is not an optimisation, it is the whole point: PyPI's default
# torch wheel bundles the CUDA runtime and costs ~2.5 GB on a machine with no
# GPU to use it. The +cpu build is around 200 MB.
RUN pip install --no-cache-dir "torch>=2.5.0" "torchvision>=0.20.0" \
        --index-url https://download.pytorch.org/whl/cpu

# Same install dance as Dockerfile.train, and for the same reasons — see the
# comments there. --no-deps keeps robomimic from pulling numpy 2.x, which
# breaks robosuite and numba, so its real dependencies go in by hand;
# huggingface_hub in particular is imported at the top of
# robomimic/utils/file_utils.py, and without it every rollout fails on import.
RUN pip install --no-cache-dir --no-deps \
        "robomimic @ git+https://github.com/ARISE-Initiative/robomimic.git@v0.5" \
    && pip install --no-cache-dir imageio imageio-ffmpeg tensorboard tensorboardX matplotlib \
    && pip install --no-cache-dir huggingface-hub diffusers transformers safetensors \
    && pip install --no-cache-dir "numpy>=1.26.0,<2.0.0"

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
