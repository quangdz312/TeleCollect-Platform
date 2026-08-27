"""Local app configuration, deliberately outside the shared web configuration."""

from __future__ import annotations

import json
import os
import secrets
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = ".telecollect"
PROJECT_VERSION = 1


def _config_path() -> Path:
    root = app_data_dir()
    return root / "settings.json"


def app_data_dir() -> Path:
    """Private application state; never placed in a user's data workspace."""
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TeleCollectLocal"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _choose_folder(initial: Path) -> Path | None:
    try:
        import tkinter
        from tkinter import filedialog

        window = tkinter.Tk()
        window.withdraw()
        selected = filedialog.askdirectory(title="Choose TeleCollect data folder", initialdir=str(initial))
        window.destroy()
        return Path(selected) if selected else None
    except Exception:
        return None


def _load_settings() -> dict[str, str]:
    try:
        loaded = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_settings(settings: dict[str, str]) -> None:
    config_path = _config_path()
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    temporary.replace(config_path)


def resolve_data_dir(requested: str | None) -> Path:
    if requested:
        selected = Path(requested).expanduser().resolve()
    else:
        saved = _load_settings().get("data_dir")
        if isinstance(saved, str) and saved:
            selected = Path(saved).expanduser().resolve()
        else:
            default = Path.home() / "TeleCollectData"
            selected = (_choose_folder(default) or default).expanduser().resolve()
    selected.mkdir(parents=True, exist_ok=True)
    ensure_project_layout(selected)
    settings = _load_settings()
    settings["data_dir"] = str(selected)
    _save_settings(settings)
    return selected


def local_admin_password() -> str:
    """Return the app-private credential used for automatic local sign-in."""
    settings = _load_settings()
    password = settings.get("local_admin_password")
    if isinstance(password, str) and password:
        return password
    password = secrets.token_urlsafe(32)
    settings["local_admin_password"] = password
    _save_settings(settings)
    return password


def choose_data_dir() -> Path | None:
    """Show the native folder chooser and persist a new data location."""
    settings = _load_settings()
    saved = settings.get("data_dir")
    initial = Path(saved) if isinstance(saved, str) and saved else Path.home() / "TeleCollectData"
    selected = _choose_folder(initial)
    return resolve_data_dir(str(selected)) if selected else None


def workspace_database_path(workspace: Path) -> Path:
    """The database belongs to the selected project, not the app profile."""
    return ensure_project_layout(workspace) / "app.db"


def project_control_dir(workspace: Path) -> Path:
    return workspace.resolve() / PROJECT_DIR


def legacy_workspace_database_path(workspace: Path) -> Path:
    """Location used by local builds before project folders were introduced."""
    key = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()[:24]
    root = app_data_dir() / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{key}.db"


def ensure_project_layout(workspace: Path) -> Path:
    """Create/upgrade one self-contained local data project safely.

    Existing recording files stay where they are.  If an older local database
    lives under AppData, it is copied once into ``.telecollect/app.db`` before
    the app starts using the project-local copy.  A root-level ``app.db`` is
    intentionally left untouched because it can belong to the web/dev setup.
    """
    root = workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    control = project_control_dir(root)
    control.mkdir(parents=True, exist_ok=True)
    for directory in ("batches", "episodes", "datasets", "exports", "review"):
        (root / directory).mkdir(exist_ok=True)

    manifest = control / "project.json"
    if not manifest.exists():
        manifest.write_text(
            json.dumps(
                {
                    "format_version": PROJECT_VERSION,
                    "name": root.name or "TeleCollect project",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "layout": {
                        "batches": "batches",
                        "episodes": "episodes",
                        "datasets": "datasets",
                        "exports": "exports",
                        "review": "review",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        try:
            project = json.loads(manifest.read_text(encoding="utf-8"))
            layout = project.setdefault("layout", {})
            if layout.get("batches") != "batches":
                layout["batches"] = "batches"
                temporary = manifest.with_suffix(".tmp")
                temporary.write_text(json.dumps(project, indent=2), encoding="utf-8")
                temporary.replace(manifest)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    database = control / "app.db"
    old_local_database = legacy_workspace_database_path(root)
    if not database.exists() and old_local_database.is_file():
        shutil.copy2(old_local_database, database)
    return control
