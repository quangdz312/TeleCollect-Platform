# TeleCollect Local Desktop

TeleCollect Local is an Electron desktop application. Its Next.js UI, FastAPI
backend, Node runtime, MuJoCo/robosuite assets, FFmpeg and FFprobe all run from
the installed application. The target computer does not need Python or Node.
All HTTP services bind to dynamically allocated `127.0.0.1` ports.

The shared `frontend/` and web API source are reused at build time but are not
modified by the local desktop shell.

## Run from the repository

```powershell
cd D:\VIN_AI_TC\Project\P-111
.\.venv\Scripts\python -m local_app.run
```

On the first run, choose a project folder. The choice is saved under
`%LOCALAPPDATA%\TeleCollectLocal\settings.json`. Use **Project** in the app to
switch folders later without restarting the application.

For an explicit development workspace:

```powershell
.\.venv\Scripts\python -m local_app.run --data-dir D:\TeleCollectData
```

## Build the Windows installer

Prerequisites on the build computer:

- Python environment at `.venv` with the runtime requirements and PyInstaller;
- Node/npm and installed dependencies in `frontend/`;
- internet access on the first build for Electron, NSIS and the FFmpeg Windows
  essentials archive. Later builds reuse their caches.

Run the complete checked build:

```powershell
.\.venv\Scripts\python -m pip install -r local_app\requirements-build.txt
cd local_app\electron
npm ci
cd ..\..
powershell -ExecutionPolicy Bypass -File local_app\installer\build.ps1
```

The script runs local tests, frontend type checking, a production frontend
build, PyInstaller, and electron-builder/NSIS. Its output is:

```text
local_app\release\TeleCollectLocalSetup-0.1.3.exe
```

Use `-UnpackedOnly` while debugging packaging. Use `-SkipChecks` only after the
same source revision has already passed tests and type checking:

```powershell
powershell -ExecutionPolicy Bypass -File local_app\installer\build.ps1 -UnpackedOnly -SkipChecks
```

The current installer is not code-signed with a publisher certificate, so
Windows SmartScreen may warn on another machine. Code signing and a custom app
icon are release tasks, not runtime dependencies.

## Runtime logs

Startup and service logs are written to:

```text
%LOCALAPPDATA%\TeleCollectLocal\logs
```

Closing the Electron window terminates the complete local backend and Node
process tree. The application does not leave fixed ports or background
services running.
