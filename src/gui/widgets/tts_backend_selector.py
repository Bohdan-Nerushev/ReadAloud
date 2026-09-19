"""
TTS Backend Selector Widget.

Provides a UI control for switching between Edge TTS (cloud) and Piper (local Docker).
Runs prerequisite checks in a background thread to avoid blocking the UI.

Design:
  - Emits backendChanged(TtsBackend) when the user selects a different backend.
  - Shows a status indicator (✅/⚠️/❌) next to the Piper option.
  - Provides action buttons (Download Image, Download Model, Start Container)
    that are enabled only when the corresponding action is needed.
  - All blocking operations (docker checks, container start) run in a QThread
    worker to keep the event loop responsive.
"""

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from src.domain.models import TtsBackend
from src.application.services.piper_setup_service import PiperPrerequisiteResult, PiperSetupService
from src.gui.styles import Styles

logger = logging.getLogger(__name__)


class _PiperCheckWorker(QObject):
    """
    Runs PiperSetupService.check_prerequisites() in a QThread.

    Separating the worker from the thread object follows the Qt "worker object"
    pattern, which is safer than subclassing QThread for non-trivial work.
    """
    finished = pyqtSignal(object)  # PiperPrerequisiteResult

    def __init__(self, setup_service: PiperSetupService, language: str, gender: str) -> None:
        super().__init__()
        self._service = setup_service
        self._language = language
        self._gender = gender

    def run(self) -> None:
        result = self._service.check_prerequisites(self._language, self._gender)
        self.finished.emit(result)


class _PiperActionWorker(QObject):
    """Runs start/stop/pull operations in a QThread."""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, setup_service: PiperSetupService, action: str, language: str, gender: str) -> None:
        super().__init__()
        self._service = setup_service
        self._action = action
        self._language = language
        self._gender = gender
        self._service.statusMessage.connect(self.status)

    def run(self) -> None:
        try:
            if self._action == "pull_image":
                self._service.pull_image()
            elif self._action == "download_model":
                self._service.download_model(self._language, self._gender)
            elif self._action == "start":
                self._service.start_container(self._language, self._gender)
            elif self._action == "stop":
                self._service.stop_container()
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class TtsBackendSelector(QWidget):
    """
    Widget for selecting the TTS backend (Edge TTS or Piper).

    Signals:
        backendChanged(TtsBackend): Emitted when the user selects a different backend.
    """

    backendChanged = pyqtSignal(object)  # TtsBackend

    def __init__(
            self,
            setup_service: PiperSetupService,
            parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._setup_service = setup_service
        self._current_language = "ru"
        self._current_gender = "male"
        self._last_check_result: Optional[PiperPrerequisiteResult] = None
        self._check_thread: Optional[QThread] = None
        self._action_thread: Optional[QThread] = None
        self._check_worker: Optional[_PiperCheckWorker] = None
        self._action_worker: Optional[_PiperActionWorker] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout()
        main_layout.setSpacing(Styles.SPACING_SMALL)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(main_layout)

        title_label = QLabel("TTS Engine:")
        title_label.setStyleSheet(Styles.LABEL_FIELD)
        main_layout.addWidget(title_label)

        self._container = QFrame()
        self._container.setObjectName("TtsBackendContainer")
        self._container.setStyleSheet(Styles.TTS_CONTAINER_STYLE)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(8)

        # --- Radio buttons ---
        radio_row = QHBoxLayout()
        radio_row.setSpacing(25)
        self._button_group = QButtonGroup(self)

        self._edge_radio = QRadioButton("Edge TTS (Cloud)")
        self._edge_radio.setToolTip(
            "Microsoft Edge Text-to-Speech. Requires internet connection. "
            "Fast but may be unstable for very large files."
        )
        self._edge_radio.setChecked(True)
        self._button_group.addButton(self._edge_radio, 0)

        self._piper_radio = QRadioButton("Piper (Local Docker)")
        self._piper_radio.setToolTip(
            "Local synthesis via rhasspy/wyoming-piper Docker container. "
            "More stable for large files. Requires Docker and downloaded voice model."
        )
        self._button_group.addButton(self._piper_radio, 1)

        radio_row.addWidget(self._edge_radio)
        radio_row.addWidget(self._piper_radio)
        radio_row.addStretch()
        container_layout.addLayout(radio_row)

        # --- Piper status panel (hidden when Edge TTS is selected) ---
        self._piper_panel = QFrame()
        self._piper_panel.setObjectName("PiperPanel")
        self._piper_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self._piper_panel.setStyleSheet("""
            QFrame#PiperPanel {
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                background-color: #f9f9f9;
                margin-top: 6px;
            }
            QLabel {
                border: none;
                background-color: transparent;
                font-size: 12px;
                color: #333333;
                padding: 2px 0px;
            }
            QPushButton {
                background-color: #2196F3;
                color: #ffffff;
                border: none;
                padding: 5px 12px;
                font-size: 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        piper_layout = QVBoxLayout(self._piper_panel)
        piper_layout.setContentsMargins(10, 8, 10, 8)
        piper_layout.setSpacing(4)

        # Status labels
        self._status_docker = QLabel("Docker: checking…")
        self._status_image = QLabel("Image: checking…")
        self._status_model = QLabel("Voice model: checking…")
        self._status_container = QLabel("")

        for lbl in (self._status_docker, self._status_image,
                    self._status_model, self._status_container):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            piper_layout.addWidget(lbl)

        # Action buttons row (only Re-check is needed since pull, download & start are automatic)
        btn_row = QHBoxLayout()
        self._btn_recheck = QPushButton("↻ Re-check")
        self._btn_recheck.setToolTip("Run prerequisite checks again.")

        btn_row.addStretch()
        btn_row.addWidget(self._btn_recheck)
        piper_layout.addLayout(btn_row)

        self._piper_panel.setMinimumHeight(145)
        self._piper_panel.setVisible(False)
        container_layout.addWidget(self._piper_panel)

        main_layout.addWidget(self._container)
        self.setMinimumHeight(65)

        # --- Connect signals ---
        self._edge_radio.toggled.connect(self._on_radio_toggled)
        self._piper_radio.toggled.connect(self._on_radio_toggled)
        self._btn_recheck.clicked.connect(self._run_prerequisite_check)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_language_and_gender(self, language: str, gender: str) -> None:
        """
        Updates the language/gender context used for model checks.

        Call this whenever the user changes language or gender selection
        so Piper checks validate the correct voice model.
        """
        changed = (language != self._current_language or gender != self._current_gender)
        self._current_language = language
        self._current_gender = gender
        if changed and self._piper_radio.isChecked():
            self._run_prerequisite_check()

    def selected_backend(self) -> TtsBackend:
        """Returns the currently selected TTS backend."""
        return TtsBackend.PIPER if self._piper_radio.isChecked() else TtsBackend.EDGE_TTS

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_radio_toggled(self, checked: bool) -> None:
        if not checked:
            return
        backend = self.selected_backend()
        is_piper = backend == TtsBackend.PIPER
        self._piper_panel.setVisible(is_piper)
        self.setMinimumHeight(225 if is_piper else 65)
        if is_piper:
            self._run_prerequisite_check()
        self.backendChanged.emit(backend)

    def _run_prerequisite_check(self) -> None:
        """Runs Docker/model checks in a background thread."""
        self._set_checking_state()

        if self._check_thread and self._check_thread.isRunning():
            return  # Already checking

        self._check_thread = QThread()
        self._check_worker = _PiperCheckWorker(
            self._setup_service, self._current_language, self._current_gender
        )
        self._check_worker.moveToThread(self._check_thread)
        self._check_thread.started.connect(self._check_worker.run)
        self._check_worker.finished.connect(self._on_check_finished)
        self._check_worker.finished.connect(self._check_thread.quit)
        self._check_thread.start()

    def _on_check_finished(self, result: PiperPrerequisiteResult) -> None:
        self._last_check_result = result
        self._update_status_display(result)
        self._update_button_states(result)

    def _run_action(self, action: str) -> None:
        """Runs start/stop/pull in a background thread."""
        if self._action_thread and self._action_thread.isRunning():
            return

        self._set_buttons_busy(True)
        self._action_thread = QThread()
        self._action_worker = _PiperActionWorker(
            self._setup_service, action, self._current_language, self._current_gender
        )
        self._action_worker.moveToThread(self._action_thread)
        self._action_thread.started.connect(self._action_worker.run)
        self._action_worker.finished.connect(self._on_action_finished)
        self._action_worker.error.connect(self._on_action_error)
        self._action_worker.status.connect(self._status_container.setText)
        self._action_worker.finished.connect(self._action_thread.quit)
        self._action_thread.start()

    def _on_action_finished(self) -> None:
        self._set_buttons_busy(False)
        self._run_prerequisite_check()

    def _on_action_error(self, message: str) -> None:
        self._set_buttons_busy(False)
        QMessageBox.critical(self, "Piper Error", message)
        self._run_prerequisite_check()

    # ------------------------------------------------------------------
    # UI update helpers
    # ------------------------------------------------------------------

    def _set_checking_state(self) -> None:
        checking_text = "<span style='color: gray'>⏳ checking…</span>"
        self._status_docker.setText(f"Docker: {checking_text}")
        self._status_image.setText(f"Image: {checking_text}")
        self._status_model.setText(f"Voice model: {checking_text}")
        self._status_container.setText("")

    def _update_status_display(self, result: PiperPrerequisiteResult) -> None:
        ok = "<span style='color: green'>✅</span>"
        fail = "<span style='color: red'>❌</span>"
        warn = "<span style='color: orange'>⚠️</span>"

        self._status_docker.setText(
            f"Docker: {ok if result.docker_available else fail}"
        )
        self._status_image.setText(
            f"Image (rhasspy/wyoming-piper): {ok if result.image_available else fail}"
        )
        self._status_model.setText(
            f"Voice model: {ok if result.model_present else fail}"
        )

        if result.port_conflict:
            self._status_container.setText(
                f"Port {PIPER_DEFAULT_PORT}: {warn} occupied by another process"
            )
        elif result.container_running:
            self._status_container.setText(
                f"Container: {ok} running (port {PIPER_DEFAULT_PORT})"
            )
        else:
            self._status_container.setText(
                f"Container: {fail} not running"
            )

    def _update_button_states(self, result: PiperPrerequisiteResult) -> None:
        self._btn_recheck.setEnabled(True)

    def _set_buttons_busy(self, busy: bool) -> None:
        self._btn_recheck.setEnabled(not busy)


# Import here to avoid circular import at module level
try:
    from src.infrastructure.docker_manager import PIPER_DEFAULT_PORT
except ImportError:
    PIPER_DEFAULT_PORT = 10200
