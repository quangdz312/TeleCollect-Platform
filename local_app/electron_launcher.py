"""Development launcher for the Electron-based TeleCollect Local shell."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch TeleCollect Local desktop")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    electron_dir = root / "local_app" / "electron"
    executable = electron_dir / "node_modules" / ".bin" / "electron.cmd"
    if not executable.is_file():
        raise SystemExit("Electron is not installed. Run: cd local_app\\electron; npm install")
    env = {**os.environ, "TELECOLLECT_PYTHON": os.environ.get("TELECOLLECT_PYTHON", str(root / ".venv" / "Scripts" / "python.exe"))}
    # Some developer tools set this for Electron's embedded Node process.
    # A desktop shell must remove it or Electron never creates an app/window.
    env.pop("ELECTRON_RUN_AS_NODE", None)
    command = [str(executable), str(electron_dir)]
    if args.data_dir:
        command += ["--data-dir", args.data_dir]
    raise SystemExit(subprocess.call(command, cwd=root, env=env))


if __name__ == "__main__":
    main()
