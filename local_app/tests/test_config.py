from __future__ import annotations

import json
from pathlib import Path

from local_app import config


def test_requested_data_dir_is_created_and_persisted(monkeypatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(config, "_config_path", lambda: settings)
    selected = config.resolve_data_dir(str(tmp_path / "data"))

    assert selected.is_dir()
    assert json.loads(settings.read_text(encoding="utf-8"))["data_dir"] == str(selected)


def test_first_run_uses_folder_picker_and_persists_project(monkeypatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    project = tmp_path / "chosen-project"
    monkeypatch.setattr(config, "_config_path", lambda: settings)
    monkeypatch.setattr(config, "_choose_folder", lambda initial: project)

    selected = config.resolve_data_dir(None)

    assert selected == project.resolve()
    assert (project / "batches").is_dir()
    assert json.loads(settings.read_text(encoding="utf-8"))["data_dir"] == str(project.resolve())


def test_reopen_uses_saved_project_without_showing_picker(monkeypatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    project = tmp_path / "remembered-project"
    settings.write_text(json.dumps({"data_dir": str(project)}), encoding="utf-8")
    monkeypatch.setattr(config, "_config_path", lambda: settings)
    monkeypatch.setattr(
        config,
        "_choose_folder",
        lambda initial: (_ for _ in ()).throw(AssertionError("picker must not reopen")),
    )

    selected = config.resolve_data_dir(None)

    assert selected == project.resolve()
    assert selected.is_dir()
