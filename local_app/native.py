"""Native Windows application for offline TeleCollect data work.

Unlike the earlier prototype, this module does not start a web server, a
browser, Next.js, FastAPI, or WebView. It reads the selected folder directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QPlainTextEdit, QStackedWidget, QStatusBar, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QUrl

from local_app.config import resolve_data_dir
from local_app.workspace import ExportMode, LocalEpisode, LocalWorkspace

APP_STYLE = """
QMainWindow { background: #f5f7fb; }
QToolBar { background: white; border: 0; border-bottom: 1px solid #dce3ee; spacing: 7px; padding: 8px 14px; }
QToolButton { border: 0; border-radius: 8px; padding: 8px 12px; color: #283750; font-weight: 600; }
QToolButton:checked { background: #e4ecff; color: #2563eb; }
QPushButton { background: #2563eb; color: white; border: 0; border-radius: 7px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover { background: #1d4ed8; }
QPushButton[secondary="true"] { background: white; color: #27364f; border: 1px solid #cfd8e6; }
QGroupBox { background: white; border: 1px solid #dce3ee; border-radius: 10px; margin-top: 15px; padding: 15px; font-weight: 700; color: #14213d; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 4px; }
QTableWidget { background: white; border: 1px solid #dce3ee; border-radius: 8px; gridline-color: #edf0f5; }
QHeaderView::section { background: #f8fafc; border: 0; border-bottom: 1px solid #dce3ee; padding: 8px; font-weight: 700; color: #53627b; }
QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #cfd8e6; border-radius: 7px; padding: 7px; }
QStatusBar { background: white; border-top: 1px solid #dce3ee; color: #52627d; }
"""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def button(text: str, *, secondary: bool = False) -> QPushButton:
    value = QPushButton(text)
    if secondary:
        value.setProperty("secondary", True)
    return value


def formatted_size(path: Path | None) -> str:
    if path is None or not path.exists():
        return "—"
    size = path.stat().st_size
    for suffix in ("B", "KB", "MB", "GB"):
        if size < 1024 or suffix == "GB":
            return f"{size:.1f} {suffix}" if suffix != "B" else f"{size} B"
        size /= 1024
    return "—"


class ExportThread(QThread):
    completed = Signal(int, int, str)
    failed = Signal(str)

    def __init__(self, workspace: LocalWorkspace, output: Path, mode: ExportMode, selected: set[str]) -> None:
        super().__init__()
        self.workspace, self.output, self.mode, self.selected = workspace, output, mode, selected

    def run(self) -> None:
        try:
            count, frames = self.workspace.export_hdf5(self.output, self.mode, self.selected)
        except Exception as exc:  # show the concrete conversion error in the native UI
            self.failed.emit(str(exc))
        else:
            self.completed.emit(count, frames, str(self.output))


class EpisodesTable(QTableWidget):
    headers = ["Select", "Episode", "Task", "Source", "Result", "Frames", "Review", "Video"]

    def __init__(self, *, checks: bool = False) -> None:
        super().__init__(0, len(self.headers))
        self.setHorizontalHeaderLabels(self.headers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnHidden(0, not checks)
        self._checks = checks
        self.episodes: list[LocalEpisode] = []

    def load(self, workspace: LocalWorkspace, episodes: list[LocalEpisode]) -> None:
        self.episodes = episodes
        self.setRowCount(len(episodes))
        for row, episode in enumerate(episodes):
            selected = QTableWidgetItem()
            selected.setData(Qt.ItemDataRole.UserRole, episode.id)
            if self._checks:
                selected.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                selected.setCheckState(Qt.CheckState.Unchecked)
            self.setItem(row, 0, selected)
            self.setItem(row, 1, QTableWidgetItem(episode.id))
            self.setItem(row, 2, QTableWidgetItem(episode.task))
            self.setItem(row, 3, QTableWidgetItem("Manual" if episode.source == "manual" else "Scripted HDF5"))
            outcome = "Success" if episode.success is True else "Failure" if episode.success is False else "—"
            self.setItem(row, 4, QTableWidgetItem(outcome))
            self.setItem(row, 5, QTableWidgetItem(str(episode.frames) if episode.frames is not None else "—"))
            self.setItem(row, 6, QTableWidgetItem(workspace.decision_for(episode.id)))
            self.setItem(row, 7, QTableWidgetItem("Available" if episode.video_path else "—"))
        self.resizeColumnsToContents()

    def checked_ids(self) -> set[str]:
        return {
            self.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.rowCount())
            if self.item(row, 0) and self.item(row, 0).checkState() == Qt.CheckState.Checked
        }

    def selected_episode_ids(self) -> list[str]:
        return [self.item(row.row(), 0).data(Qt.ItemDataRole.UserRole) for row in self.selectionModel().selectedRows()]

    def selected_episode(self) -> LocalEpisode | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        return self.episodes[rows[0].row()]


class MainWindow(QMainWindow):
    pages = ("Overview", "Collect", "Review", "Raw episodes", "Data diversity", "Datasets")

    def __init__(self, initial_workspace: Path) -> None:
        super().__init__()
        self.workspace = LocalWorkspace(initial_workspace)
        self.export_thread: ExportThread | None = None
        self.collector: QProcess | None = None
        self.setWindowTitle("TeleCollect Local")
        self.setMinimumSize(1100, 720)
        self.resize(1380, 880)
        self._build_toolbar()
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._build_pages()
        self.setStatusBar(QStatusBar())
        self.refresh_all()
        self.show_page(0)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("TeleCollect Local")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        logo = QLabel("<b style='color:#2563eb;font-size:18px'>TC</b>  <b>TeleCollect Local</b>")
        logo.setContentsMargins(4, 0, 14, 0)
        toolbar.addWidget(logo)
        self.navigation: list[QAction] = []
        for index, page in enumerate(self.pages):
            action = QAction(page, self, checkable=True)
            action.triggered.connect(lambda _checked=False, target=index: self.show_page(target))
            toolbar.addAction(action)
            self.navigation.append(action)
        toolbar.addSeparator()
        folder_action = QAction("Data folder", self)
        folder_action.triggered.connect(self.choose_workspace)
        toolbar.addAction(folder_action)
        self.workspace_label = QLabel()
        self.workspace_label.setToolTip("Selected local data folder")
        toolbar.addWidget(self.workspace_label)

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:25px;font-weight:700;color:#14213d")
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setStyleSheet("font-size:14px;color:#62718a;margin-bottom:8px")
        layout.addWidget(description)
        return page, layout

    def _build_pages(self) -> None:
        self._build_overview()
        self._build_collect()
        self._build_review()
        self._build_raw()
        self._build_diversity()
        self._build_datasets()

    def _build_overview(self) -> None:
        page, layout = self._page("Overview", "Your selected folder is opened directly; no account and no local web server are used.")
        group = QGroupBox("Workspace")
        form = QFormLayout(group)
        self.overview_folder = QLabel()
        self.overview_count = QLabel()
        self.overview_review = QLabel()
        self.overview_exports = QLabel()
        form.addRow("Data folder", self.overview_folder)
        form.addRow("Episodes", self.overview_count)
        form.addRow("Review", self.overview_review)
        form.addRow("Exports", self.overview_exports)
        layout.addWidget(group)
        pick = button("Choose data folder", secondary=True)
        pick.clicked.connect(self.choose_workspace)
        layout.addWidget(pick, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self.stack.addWidget(page)

    def _build_collect(self) -> None:
        page, layout = self._page("Collect data", "Open a direct simulator collector. Saved recordings appear after refresh.")
        group = QGroupBox("Manual teleoperation")
        row = QHBoxLayout(group)
        self.collect_task = QComboBox()
        self.collect_task.addItems(["lift_cube", "pick_place_can", "nut_assembly_square", "tool_hang"])
        row.addWidget(self.collect_task, 1)
        manual = button("Open manual collector")
        manual.clicked.connect(self.open_manual_collector)
        row.addWidget(manual)
        layout.addWidget(group)
        scripted = QGroupBox("Scripted collection")
        scripted_layout = QHBoxLayout(scripted)
        self.scripted_count = QLineEdit("1")
        self.scripted_count.setMaximumWidth(90)
        scripted_layout.addWidget(QLabel("Episodes"))
        scripted_layout.addWidget(self.scripted_count)
        scripted_button = button("Generate scripted HDF5", secondary=True)
        scripted_button.clicked.connect(self.run_scripted_collection)
        scripted_layout.addWidget(scripted_button)
        scripted_layout.addStretch()
        layout.addWidget(scripted)
        self.collect_message = QLabel("No collector is running.")
        self.collect_message.setWordWrap(True)
        layout.addWidget(self.collect_message)
        layout.addStretch()
        self.stack.addWidget(page)

    def _build_review(self) -> None:
        page, layout = self._page("Review", "Inspect local recordings, open their video, and retain a local acceptance decision.")
        self.review_table = EpisodesTable()
        layout.addWidget(self.review_table, 1)
        controls = QHBoxLayout()
        video = button("Open selected video", secondary=True)
        video.clicked.connect(self.open_selected_video)
        controls.addWidget(video)
        self.review_note = QPlainTextEdit()
        self.review_note.setPlaceholderText("Optional review note")
        self.review_note.setFixedHeight(58)
        controls.addWidget(self.review_note, 1)
        accept = button("Accept")
        accept.clicked.connect(lambda: self.review_selected("accepted"))
        reject = button("Reject", secondary=True)
        reject.clicked.connect(lambda: self.review_selected("rejected"))
        controls.addWidget(accept)
        controls.addWidget(reject)
        layout.addLayout(controls)
        self.stack.addWidget(page)

    def _build_raw(self) -> None:
        page, layout = self._page("Raw episodes", "Read-only inventory of every manual recording and scripted HDF5 demonstration in this workspace.")
        self.raw_table = EpisodesTable()
        layout.addWidget(self.raw_table, 1)
        refresh = button("Refresh inventory", secondary=True)
        refresh.clicked.connect(self.refresh_all)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(page)

    def _build_diversity(self) -> None:
        page, layout = self._page("Data diversity", "A quick local breakdown for balancing collection before export.")
        self.diversity_table = QTableWidget(0, 5)
        self.diversity_table.setHorizontalHeaderLabels(["Task", "Episodes", "Manual", "Scripted", "Accepted"])
        self.diversity_table.horizontalHeader().setStretchLastSection(True)
        self.diversity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.diversity_table, 1)
        self.stack.addWidget(page)

    def _build_datasets(self) -> None:
        page, layout = self._page("Datasets", "Export local manual and scripted data to a RoboMimic-compatible HDF5 file.")
        options = QGroupBox("Export options")
        form = QFormLayout(options)
        self.export_mode = QComboBox()
        self.export_mode.addItem("1. Export all data", "all")
        self.export_mode.addItem("2. Exclude rejected data", "not_rejected")
        self.export_mode.addItem("3. Export selected data", "selected")
        self.export_name = QLineEdit("telecollect_export.hdf5")
        form.addRow("Mode", self.export_mode)
        form.addRow("File name", self.export_name)
        layout.addWidget(options)
        self.dataset_table = EpisodesTable(checks=True)
        layout.addWidget(self.dataset_table, 1)
        export = button("Export HDF5")
        export.clicked.connect(self.start_export)
        layout.addWidget(export, alignment=Qt.AlignmentFlag.AlignRight)
        self.stack.addWidget(page)

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, action in enumerate(self.navigation):
            action.setChecked(position == index)

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose TeleCollect data folder", str(self.workspace.root))
        if not selected:
            return
        self.workspace = LocalWorkspace(resolve_data_dir(selected))
        self.refresh_all()
        self.statusBar().showMessage("Workspace changed. Data was read directly from the selected folder.", 6000)

    def refresh_all(self) -> None:
        episodes = self.workspace.episodes()
        self.workspace_label.setText(self.workspace.root.name or str(self.workspace.root))
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.overview_folder.setText(str(self.workspace.root))
        accepted = sum(self.workspace.decision_for(item.id) == "accepted" for item in episodes)
        rejected = sum(self.workspace.decision_for(item.id) == "rejected" for item in episodes)
        self.overview_count.setText(f"{len(episodes)} total")
        self.overview_review.setText(f"{accepted} accepted · {rejected} rejected · {len(episodes) - accepted - rejected} unreviewed")
        exports = list((self.workspace.root / "exports").glob("*.hdf5")) if (self.workspace.root / "exports").is_dir() else []
        self.overview_exports.setText(f"{len(exports)} HDF5 file(s)")
        self.review_table.load(self.workspace, episodes)
        self.raw_table.load(self.workspace, episodes)
        self.dataset_table.load(self.workspace, episodes)
        self._load_diversity(episodes)

    def _load_diversity(self, episodes: list[LocalEpisode]) -> None:
        grouped: dict[str, list[LocalEpisode]] = {}
        for item in episodes:
            grouped.setdefault(item.task, []).append(item)
        self.diversity_table.setRowCount(len(grouped))
        for row, (task, records) in enumerate(sorted(grouped.items())):
            manual = sum(item.source == "manual" for item in records)
            scripted = len(records) - manual
            accepted = sum(self.workspace.decision_for(item.id) == "accepted" for item in records)
            for column, value in enumerate((task, len(records), manual, scripted, accepted)):
                self.diversity_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.diversity_table.resizeColumnsToContents()

    def review_selected(self, decision: str) -> None:
        ids = self.review_table.selected_episode_ids()
        if not ids:
            QMessageBox.information(self, "Review", "Choose one or more episodes first.")
            return
        self.workspace.review(ids, decision, self.review_note.toPlainText())  # type: ignore[arg-type]
        self.review_note.clear()
        self.refresh_all()
        self.statusBar().showMessage(f"Marked {len(ids)} episode(s) as {decision}.", 4000)

    def open_selected_video(self) -> None:
        episode = self.review_table.selected_episode()
        if episode is None:
            QMessageBox.information(self, "Video", "Choose an episode first.")
        elif episode.video_path is None:
            QMessageBox.information(self, "Video", "This episode has no local MP4 video.")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(episode.video_path)))

    def _start_process(self, program: str, arguments: list[str], description: str) -> None:
        if self.collector is not None and self.collector.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Collector", "A collection process is already running.")
            return
        process = QProcess(self)
        process.setWorkingDirectory(str(repo_root()))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("STORAGE_DIR", str(self.workspace.root))
        environment.insert("REVIEW_DIR", str(self.workspace.root / "review"))
        environment.insert("PYTHONPATH", str(repo_root()))
        process.setProcessEnvironment(environment)
        process.finished.connect(lambda code, _status: self._collector_finished(description, code))
        process.errorOccurred.connect(lambda _error: self.collect_message.setText(f"{description} could not start."))
        self.collector = process
        self.collect_message.setText(f"{description} is running. Its own window/output may take a moment to appear.")
        process.start(program, arguments)

    def _collector_finished(self, description: str, code: int) -> None:
        self.collect_message.setText(f"{description} finished with code {code}. Inventory refreshed.")
        self.refresh_all()

    def open_manual_collector(self) -> None:
        self._start_process(sys.executable, [str(repo_root() / "scripts" / "teleop_ui.py"), "--task", self.collect_task.currentText()], "Manual collector")

    def run_scripted_collection(self) -> None:
        task = self.collect_task.currentText()
        scripts = {
            "lift_cube": "collect_scripted_lift.py", "pick_place_can": "collect_scripted_can.py",
            "nut_assembly_square": "collect_scripted_square.py",
        }
        script = scripts.get(task)
        if script is None:
            QMessageBox.information(self, "Scripted collection", "Tool-hang scripted collection is not exposed here yet.")
            return
        try:
            episodes = max(1, int(self.scripted_count.text()))
        except ValueError:
            QMessageBox.warning(self, "Scripted collection", "Episodes must be a positive number.")
            return
        output = self.workspace.root / "review" / "datasets" / f"{task}_clean_local.hdf5"
        output.parent.mkdir(parents=True, exist_ok=True)
        self._start_process(sys.executable, [str(repo_root() / "scripts" / script), "--episodes", str(episodes), "--output", str(output), "--overwrite"], "Scripted collection")

    def start_export(self) -> None:
        if self.export_thread is not None and self.export_thread.isRunning():
            return
        filename = self.export_name.text().strip()
        if not filename:
            QMessageBox.warning(self, "Export HDF5", "Enter an output file name.")
            return
        if not filename.lower().endswith((".hdf5", ".h5")):
            filename += ".hdf5"
        output = self.workspace.root / "exports" / filename
        mode = self.export_mode.currentData()
        selected = self.dataset_table.checked_ids()
        self.export_thread = ExportThread(self.workspace, output, mode, selected)
        self.export_thread.completed.connect(self._export_complete)
        self.export_thread.failed.connect(self._export_failed)
        self.export_thread.start()
        self.statusBar().showMessage("Exporting HDF5…")

    def _export_complete(self, count: int, frames: int, path: str) -> None:
        self.refresh_all()
        QMessageBox.information(self, "Export HDF5", f"Created {Path(path).name}\n{count} episode(s), {frames} frame(s).")
        self.statusBar().showMessage("HDF5 export complete.", 6000)

    def _export_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Export HDF5", message)
        self.statusBar().showMessage("HDF5 export failed.", 6000)


def main() -> None:
    parser = argparse.ArgumentParser(description="TeleCollect Local native desktop app")
    parser.add_argument("--data-dir", help="Folder to open as the initial workspace")
    args = parser.parse_args()
    application = QApplication(sys.argv)
    application.setApplicationName("TeleCollect Local")
    application.setStyle("Fusion")
    palette = application.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f7fb"))
    application.setPalette(palette)
    application.setStyleSheet(APP_STYLE)
    window = MainWindow(resolve_data_dir(args.data_dir))
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
