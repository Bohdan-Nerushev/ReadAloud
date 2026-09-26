"""
TTS Backend Selector Widget.

Provides a UI control for switching between Edge TTS (cloud), Piper (local Docker),
and XTTS-v2 (local GPU neural TTS).
Runs prerequisite checks in a background thread to avoid blocking the UI.

Design:
  - Emits backendChanged(TtsBackend) when the user selects a different backend.
  - Shows a status panel for Piper (Docker/model checks) and XTTS (CUDA/VRAM/model).
  - All blocking operations run in QThread workers to keep the event loop responsive.
  - When switching away from XTTS, the model is unloaded via XttsSetupService.
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
from src.application.services.xtts_setup_service import XttsSetupService
from src.infrastructure.xtts_model_manager import XttsDiagnosticsResult
from src.gui.styles import Styles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Piper background workers (unchanged from original implementation)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# XTTS background workers
# ---------------------------------------------------------------------------

class _XttsCheckWorker(QObject):
    """Runs XttsSetupService.check_prerequisites() in a QThread."""
    finished = pyqtSignal(object)  # XttsDiagnosticsResult

    def __init__(self, setup_service: XttsSetupService) -> None:
        super().__init__()
        self._service = setup_service

    def run(self) -> None:
        result = self._service.check_prerequisites()
        self.finished.emit(result)


class _XttsActionWorker(QObject):
    """Runs XTTS model download or unload in a QThread."""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, setup_service: XttsSetupService, action: str) -> None:
        super().__init__()
        self._service = setup_service
        self._action = action
        self._service.statusMessage.connect(self.status)
        self._service.setupError.connect(self.error)

    def run(self) -> None:
        try:
            if self._action == "download":
                self._service.download_model()
            elif self._action == "unload":
                self._service.unload_model()
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class TtsBackendSelector(QWidget):
    """
    Widget for selecting the TTS backend (Edge TTS, Piper, or XTTS-v2).

    Signals:
        backendChanged(TtsBackend): Emitted when the user selects a different backend.
    """

    backendChanged = pyqtSignal(object)  # TtsBackend

    def __init__(
            self,
            piper_setup_service: PiperSetupService,
            xtts_setup_service: XttsSetupService,
            parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._piper_setup_service = piper_setup_service
        self._xtts_setup_service = xtts_setup_service
        self._current_language = "ru"
        self._current_gender = "male"

        self._last_piper_result: Optional[PiperPrerequisiteResult] = None
        self._last_xtts_result: Optional[XttsDiagnosticsResult] = None

        self._piper_check_thread: Optional[QThread] = None
        self._piper_action_thread: Optional[QThread] = None
        self._piper_check_worker: Optional[_PiperCheckWorker] = None
        self._piper_action_worker: Optional[_PiperActionWorker] = None

        self._xtts_check_thread: Optional[QThread] = None
        self._xtts_action_thread: Optional[QThread] = None
        self._xtts_check_worker: Optional[_XttsCheckWorker] = None
        self._xtts_action_worker: Optional[_XttsActionWorker] = None

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
        radio_row.setSpacing(20)
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

        self._xtts_radio = QRadioButton("XTTS-v2 (Local GPU)")
        self._xtts_radio.setToolTip(
            "Neural TTS via Coqui XTTS-v2. Highest quality, supports voice cloning. "
            "Requires NVIDIA GPU with ≥4 GB VRAM and installed TTS package."
        )
        self._button_group.addButton(self._xtts_radio, 2)

        radio_row.addWidget(self._edge_radio)
        radio_row.addWidget(self._piper_radio)
        radio_row.addWidget(self._xtts_radio)
        radio_row.addStretch()
        container_layout.addLayout(radio_row)

        # --- Piper status panel ---
        self._piper_panel = self._build_piper_panel()
        self._piper_panel.setVisible(False)
        container_layout.addWidget(self._piper_panel)

        # --- XTTS status panel ---
        self._xtts_panel = self._build_xtts_panel()
        self._xtts_panel.setVisible(False)
        container_layout.addWidget(self._xtts_panel)

        main_layout.addWidget(self._container)
        self.setMinimumHeight(65)

        # --- Connect signals ---
        self._edge_radio.toggled.connect(self._on_radio_toggled)
        self._piper_radio.toggled.connect(self._on_radio_toggled)
        self._xtts_radio.toggled.connect(self._on_radio_toggled)
        self._btn_piper_recheck.clicked.connect(self._run_piper_check)
        self._btn_xtts_recheck.clicked.connect(self._run_xtts_check)
        self._btn_xtts_download.clicked.connect(lambda: self._run_xtts_action("download"))
        self._btn_xtts_unload.clicked.connect(lambda: self._run_xtts_action("unload"))

    _PANEL_STYLE = """
        QFrame#StatusPanel {
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
        QPushButton:hover { background-color: #0b7dda; }
        QPushButton:disabled { background-color: #cccccc; color: #666666; }
    """

    def _build_piper_panel(self) -> QFrame:
        """Builds the Piper prerequisite status panel."""
        panel = QFrame()
        panel.setObjectName("StatusPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(self._PANEL_STYLE)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self._status_docker = QLabel("Docker: checking…")
        self._status_image = QLabel("Image: checking…")
        self._status_model = QLabel("Voice model: checking…")
        self._status_container = QLabel("")

        for lbl in (self._status_docker, self._status_image,
                    self._status_model, self._status_container):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(lbl)

        btn_row = QHBoxLayout()
        self._btn_piper_recheck = QPushButton("↻ Re-check")
        self._btn_piper_recheck.setToolTip("Run prerequisite checks again.")
        btn_row.addStretch()
        btn_row.addWidget(self._btn_piper_recheck)
        layout.addLayout(btn_row)

        panel.setMinimumHeight(145)
        return panel

    def _build_xtts_panel(self) -> QFrame:
        """Builds the XTTS-v2 prerequisite status panel."""
        panel = QFrame()
        panel.setObjectName("StatusPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(self._PANEL_STYLE)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self._xtts_status_package = QLabel("TTS package: checking…")
        self._xtts_status_cuda = QLabel("CUDA: checking…")
        self._xtts_status_vram = QLabel("VRAM: checking…")
        self._xtts_status_model = QLabel("Model cache: checking…")
        self._xtts_status_speakers = QLabel("")

        for lbl in (self._xtts_status_package, self._xtts_status_cuda,
                    self._xtts_status_vram, self._xtts_status_model,
                    self._xtts_status_speakers):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(lbl)

        btn_row = QHBoxLayout()
        self._btn_xtts_download = QPushButton("⬇ Download Model")
        self._btn_xtts_download.setToolTip("Download XTTS-v2 model weights (~1.8 GB) from Coqui hub.")
        self._btn_xtts_download.setEnabled(False)

        self._btn_xtts_unload = QPushButton("🗑 Unload from VRAM")
        self._btn_xtts_unload.setToolTip("Release XTTS model from GPU memory to free VRAM.")
        self._btn_xtts_unload.setEnabled(False)

        self._btn_xtts_recheck = QPushButton("↻ Re-check")
        self._btn_xtts_recheck.setToolTip("Run XTTS prerequisite checks again.")

        btn_row.addWidget(self._btn_xtts_download)
        btn_row.addWidget(self._btn_xtts_unload)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_xtts_recheck)
        layout.addLayout(btn_row)

        panel.setMinimumHeight(175)
        return panel

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
            self._run_piper_check()

    def selected_backend(self) -> TtsBackend:
        """Returns the currently selected TTS backend."""
        if self._piper_radio.isChecked():
            return TtsBackend.PIPER
        if self._xtts_radio.isChecked():
            return TtsBackend.XTTS
        return TtsBackend.EDGE_TTS

    # ------------------------------------------------------------------
    # Radio toggle handler
    # ------------------------------------------------------------------

    def _on_radio_toggled(self, checked: bool) -> None:
        if not checked:
            return
        backend = self.selected_backend()
        is_piper = backend == TtsBackend.PIPER
        is_xtts = backend == TtsBackend.XTTS

        self._piper_panel.setVisible(is_piper)
        self._xtts_panel.setVisible(is_xtts)

        if is_piper:
            self.setMinimumHeight(225)
            self._run_piper_check()
        elif is_xtts:
            self.setMinimumHeight(255)
            self._run_xtts_check()
        else:
            self.setMinimumHeight(65)

        self.backendChanged.emit(backend)

    # ------------------------------------------------------------------
    # Piper check/action slots
    # ------------------------------------------------------------------

    def _run_piper_check(self) -> None:
        """Runs Docker/model checks in a background thread."""
        self._set_piper_checking_state()
        if self._piper_check_thread and self._piper_check_thread.isRunning():
            return

        self._piper_check_thread = QThread()
        self._piper_check_worker = _PiperCheckWorker(
            self._piper_setup_service, self._current_language, self._current_gender
        )
        self._piper_check_worker.moveToThread(self._piper_check_thread)
        self._piper_check_thread.started.connect(self._piper_check_worker.run)
        self._piper_check_worker.finished.connect(self._on_piper_check_finished)
        self._piper_check_worker.finished.connect(self._piper_check_thread.quit)
        self._piper_check_thread.start()

    def _on_piper_check_finished(self, result: PiperPrerequisiteResult) -> None:
        self._last_piper_result = result
        self._update_piper_status_display(result)

    def _run_piper_action(self, action: str) -> None:
        if self._piper_action_thread and self._piper_action_thread.isRunning():
            return
        self._btn_piper_recheck.setEnabled(False)
        self._piper_action_thread = QThread()
        self._piper_action_worker = _PiperActionWorker(
            self._piper_setup_service, action, self._current_language, self._current_gender
        )
        self._piper_action_worker.moveToThread(self._piper_action_thread)
        self._piper_action_thread.started.connect(self._piper_action_worker.run)
        self._piper_action_worker.finished.connect(self._on_piper_action_finished)
        self._piper_action_worker.error.connect(self._on_piper_action_error)
        self._piper_action_worker.status.connect(self._status_container.setText)
        self._piper_action_worker.finished.connect(self._piper_action_thread.quit)
        self._piper_action_thread.start()

    def _on_piper_action_finished(self) -> None:
        self._btn_piper_recheck.setEnabled(True)
        self._run_piper_check()

    def _on_piper_action_error(self, message: str) -> None:
        self._btn_piper_recheck.setEnabled(True)
        QMessageBox.critical(self, "Piper Error", message)
        self._run_piper_check()

    # ------------------------------------------------------------------
    # XTTS check/action slots
    # ------------------------------------------------------------------

    def _run_xtts_check(self) -> None:
        """Runs XTTS prerequisite checks in a background thread."""
        self._set_xtts_checking_state()
        if self._xtts_check_thread and self._xtts_check_thread.isRunning():
            return

        self._xtts_check_thread = QThread()
        self._xtts_check_worker = _XttsCheckWorker(self._xtts_setup_service)
        self._xtts_check_worker.moveToThread(self._xtts_check_thread)
        self._xtts_check_thread.started.connect(self._xtts_check_worker.run)
        self._xtts_check_worker.finished.connect(self._on_xtts_check_finished)
        self._xtts_check_worker.finished.connect(self._xtts_check_thread.quit)
        self._xtts_check_thread.start()

    def _on_xtts_check_finished(self, result: XttsDiagnosticsResult) -> None:
        self._last_xtts_result = result
        self._update_xtts_status_display(result)
        self._update_xtts_button_states(result)

    def _run_xtts_action(self, action: str) -> None:
        if self._xtts_action_thread and self._xtts_action_thread.isRunning():
            return
        self._set_xtts_buttons_busy(True)
        self._xtts_action_thread = QThread()
        self._xtts_action_worker = _XttsActionWorker(self._xtts_setup_service, action)
        self._xtts_action_worker.moveToThread(self._xtts_action_thread)
        self._xtts_action_thread.started.connect(self._xtts_action_worker.run)
        self._xtts_action_worker.finished.connect(self._on_xtts_action_finished)
        self._xtts_action_worker.error.connect(self._on_xtts_action_error)
        self._xtts_action_worker.status.connect(self._xtts_status_model.setText)
        self._xtts_action_worker.finished.connect(self._xtts_action_thread.quit)
        self._xtts_action_thread.start()

    def _on_xtts_action_finished(self) -> None:
        self._set_xtts_buttons_busy(False)
        self._run_xtts_check()

    def _on_xtts_action_error(self, message: str) -> None:
        self._set_xtts_buttons_busy(False)
        QMessageBox.critical(self, "XTTS Error", message)
        self._run_xtts_check()

    # ------------------------------------------------------------------
    # UI update helpers — Piper
    # ------------------------------------------------------------------

    def _set_piper_checking_state(self) -> None:
        checking_text = "<span style='color: gray'>⏳ checking…</span>"
        self._status_docker.setText(f"Docker: {checking_text}")
        self._status_image.setText(f"Image: {checking_text}")
        self._status_model.setText(f"Voice model: {checking_text}")
        self._status_container.setText("")

    def _update_piper_status_display(self, result: PiperPrerequisiteResult) -> None:
        ok = "<span style='color: green'>✅</span>"
        fail = "<span style='color: red'>❌</span>"
        warn = "<span style='color: orange'>⚠️</span>"

        self._status_docker.setText(f"Docker: {ok if result.docker_available else fail}")
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
            self._status_container.setText(f"Container: {fail} not running")

        self._btn_piper_recheck.setEnabled(True)

    # ------------------------------------------------------------------
    # UI update helpers — XTTS
    # ------------------------------------------------------------------

    def _set_xtts_checking_state(self) -> None:
        checking = "<span style='color: gray'>⏳ checking…</span>"
        self._xtts_status_package.setText(f"TTS package: {checking}")
        self._xtts_status_cuda.setText(f"CUDA: {checking}")
        self._xtts_status_vram.setText(f"VRAM: {checking}")
        self._xtts_status_model.setText(f"Model cache: {checking}")
        self._xtts_status_speakers.setText("")

    def _update_xtts_status_display(self, result: XttsDiagnosticsResult) -> None:
        ok = "<span style='color: green'>✅</span>"
        fail = "<span style='color: red'>❌</span>"
        warn = "<span style='color: orange'>⚠️</span>"

        self._xtts_status_package.setText(
            f"TTS package: {ok if result.tts_package_installed else fail}"
        )
        self._xtts_status_cuda.setText(
            f"CUDA: {ok if result.cuda_available else fail}"
        )

        if result.vram_total_gb > 0:
            vram_icon = ok if result.vram_sufficient else warn
            vram_text = (
                f"VRAM: {vram_icon} {result.vram_free_gb:.1f} GB free "
                f"/ {result.vram_total_gb:.1f} GB total"
            )
        else:
            vram_text = f"VRAM: {fail} not available"
        self._xtts_status_vram.setText(vram_text)

        self._xtts_status_model.setText(
            f"Model cache (XTTS-v2): {ok if result.model_cached else fail}"
        )

        if result.missing_speaker_files:
            count = len(result.missing_speaker_files)
            self._xtts_status_speakers.setText(
                f"Speaker WAVs: {warn} {count} file(s) missing — "
                f"see src/resource/xtts_speakers/README.md"
            )
        else:
            self._xtts_status_speakers.setText(
                f"Speaker WAVs: {ok} all present"
            )

    def _update_xtts_button_states(self, result: XttsDiagnosticsResult) -> None:
        self._btn_xtts_recheck.setEnabled(True)
        # Show download button only when TTS package is installed but model not yet cached.
        self._btn_xtts_download.setEnabled(
            result.tts_package_installed and not result.model_cached
        )
        # Unload button only useful when model is loaded (we can't easily detect this
        # from diagnostics alone, so enable whenever XTTS is the selected backend and
        # prerequisites pass, as a convenience).
        self._btn_xtts_unload.setEnabled(result.is_ready)

    def _set_xtts_buttons_busy(self, busy: bool) -> None:
        self._btn_xtts_download.setEnabled(not busy)
        self._btn_xtts_unload.setEnabled(not busy)
        self._btn_xtts_recheck.setEnabled(not busy)


# Import here to avoid circular import at module level
try:
    from src.infrastructure.docker_manager import PIPER_DEFAULT_PORT
except ImportError:
    PIPER_DEFAULT_PORT = 10200
