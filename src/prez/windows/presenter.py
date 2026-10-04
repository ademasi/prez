"""The presenter window: current slide, next slide, notes, timer and controls."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from prez.document import Region
from prez.widgets.notes_view import NotesPane
from prez.widgets.overview import OverviewWidget
from prez.widgets.slide_view import SlideView

if TYPE_CHECKING:
    from prez.app import PrezApp
    from prez.document import DocumentInfo, Link

GREEN = "#81c995"
ORANGE = "#fdd663"
RED = "#f28b82"
MUTED = "#9aa0a6"

MESSAGE_MS = 3000
WHEEL_STEP = 120


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class InputEdit(QLineEdit):
    """Inline status-bar input; Escape cancels even if no global filter catches it."""

    cancelled = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


# (action, label, standard icon, checkable); None is a separator.
_TOOLBAR: list[tuple[str, str, QStyle.StandardPixmap | None, bool] | None] = [
    ("pick-file", "Open", QStyle.StandardPixmap.SP_DialogOpenButton, False),
    None,
    ("prev", "Prev", QStyle.StandardPixmap.SP_ArrowLeft, False),
    ("next", "Next", QStyle.StandardPixmap.SP_ArrowRight, False),
    None,
    ("blank", "Blank", None, True),
    ("freeze", "Freeze", None, True),
    ("pointer", "Pointer", None, True),
    ("highlight", "Pen", None, True),
    ("overview", "Overview", QStyle.StandardPixmap.SP_FileDialogListView, True),
    ("swap-screens", "Swap screens", None, False),
    None,
    ("pause-timer", "Pause", QStyle.StandardPixmap.SP_MediaPause, False),
    ("reset-timer", "Reset", QStyle.StandardPixmap.SP_BrowserReload, False),
    ("edit-talk-time", "Talk time", None, False),
]


class PresenterWindow(QMainWindow):
    """Dark presenter console. All actions go through `app.dispatch(name)`."""

    def __init__(self, app: PrezApp, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.state = app.state
        self.render = app.render
        self.config = app.config
        self.actions: dict[str, QAction] = {}
        self._input_mode: str = "goto"
        self._wheel_accum = 0
        self._timer_color = ""
        self._restored_splitters = False

        self.setWindowTitle("prez")
        self.setObjectName("PresenterWindow")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_toolbar()
        self._build_central()
        self._build_status()
        self._connect_state()

        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.message_label.setText(""))

        self.resize(1280, 800)
        self.restore_settings()

    # ------------------------------------------------------------------ build

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        style = self.style()
        for entry in _TOOLBAR:
            if entry is None:
                toolbar.addSeparator()
                continue
            name, label, icon, checkable = entry
            action = QAction(label, self)
            if icon is not None:
                action.setIcon(style.standardIcon(icon))
            action.setCheckable(checkable)
            keys = ", ".join(self.config.shortcuts.get(name, []))
            action.setToolTip(f"{label} ({keys})" if keys else label)
            action.triggered.connect(lambda _checked=False, n=name: self.app.dispatch(n))
            toolbar.addAction(action)
            self.actions[name] = action
        for button in toolbar.findChildren(QToolButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.toolbar = toolbar

    def _build_central(self) -> None:
        self.current_view = SlideView(self.render, Region.SLIDE)
        self.current_view.set_interactive(True)
        self.current_view.request_priority = 0
        self.current_view.set_pointer_style(self.config.pointer_color, self.config.pointer_size)
        self.current_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.current_view.installEventFilter(self)

        self.next_view = SlideView(self.render, Region.SLIDE)
        self.next_view.set_interactive(False)
        self.next_view.request_priority = 0

        self.notes = NotesPane(self.render)

        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.setObjectName("RightSplitter")
        self.vsplit.addWidget(self.next_view)
        self.vsplit.addWidget(self.notes)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 6)

        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.setObjectName("MainSplitter")
        self.hsplit.addWidget(self.current_view)
        self.hsplit.addWidget(self.vsplit)
        ratio = min(max(self.config.slide_ratio, 0.1), 0.9)
        self.hsplit.setStretchFactor(0, int(ratio * 100))
        self.hsplit.setStretchFactor(1, int((1 - ratio) * 100))

        self.overview = OverviewWidget(self.render)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.hsplit)
        self.stack.addWidget(self.overview)

        central = QWidget()
        self._central_layout = QVBoxLayout(central)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_layout.setSpacing(0)
        self._central_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.current_view.clicked.connect(self._on_view_clicked)
        self.current_view.link_activated.connect(self._on_link)
        self.overview.activated.connect(self._on_overview_activated)
        self.overview.closed.connect(self.hide_overview)

    def _build_status(self) -> None:
        row = QWidget()
        row.setObjectName("StatusRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 2)
        layout.setSpacing(16)

        big = QFont()
        big.setPointSize(16)
        big.setBold(True)
        medium = QFont()
        medium.setPointSize(13)

        self.slide_button = QToolButton()
        self.slide_button.setAutoRaise(True)
        self.slide_button.setFont(medium)
        self.slide_button.setText("– / –")
        self.slide_button.setToolTip("Go to page (click or press G)")
        self.slide_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slide_button.clicked.connect(lambda: self.show_goto())

        self.input_edit = InputEdit()
        self.input_edit.setFont(medium)
        self.input_edit.setMaximumWidth(140)
        self.input_edit.hide()
        self.input_edit.returnPressed.connect(self.validate_input)
        self.input_edit.cancelled.connect(self.cancel_input)

        self.elapsed_label = ClickableLabel("00:00")
        self.elapsed_label.setFont(big)
        self.elapsed_label.setToolTip("Elapsed time (click to pause/resume)")
        self.elapsed_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.elapsed_label.clicked.connect(lambda: self.app.dispatch("pause-timer"))

        self.remaining_label = QLabel()
        self.remaining_label.setFont(big)
        self.remaining_label.setStyleSheet(f"color: {MUTED};")
        self.remaining_label.setToolTip("Remaining talk time")
        self.remaining_label.hide()

        self.message_label = QLabel()
        self.message_label.setFont(medium)
        self.message_label.setStyleSheet(f"color: {MUTED};")

        self.clock_label = QLabel()
        self.clock_label.setFont(big)
        self.clock_label.setToolTip("Wall clock")

        layout.addWidget(self.slide_button)
        layout.addWidget(self.input_edit)
        layout.addWidget(self.elapsed_label)
        layout.addWidget(self.remaining_label)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.clock_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("TalkProgress")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()

        self._central_layout.addWidget(row)
        self._central_layout.addWidget(self.progress)
        self.status_row = row

    def _connect_state(self) -> None:
        state = self.state
        state.document_changed.connect(self._on_document)
        state.slide_changed.connect(self._on_slide)
        state.blank_changed.connect(lambda kind: self.set_checked("blank", kind is not None))
        state.freeze_changed.connect(lambda on: self.set_checked("freeze", on))
        state.pointer_changed.connect(self.current_view.set_pointer)
        state.strokes_changed.connect(self._on_strokes)
        state.message.connect(self.show_message)

    # ------------------------------------------------------------------ state

    def _on_document(self, doc: DocumentInfo | None) -> None:
        self.current_view.set_document(doc)
        self.next_view.set_document(doc)
        self.notes.set_document(doc)
        self.overview.set_document(doc)
        if doc is None:
            self.setWindowTitle("prez")
            self.current_view.set_slide(None)
            self.next_view.set_slide(None)
            self.slide_button.setText("– / –")
            return
        self.setWindowTitle(f"{os.path.basename(doc.path)} — prez")
        self._on_slide(self.state.slide)

    def _on_slide(self, slide: int) -> None:
        doc = self.state.doc
        if doc is None:
            return
        self.current_view.set_slide(slide)
        self.current_view.set_strokes(list(self.state.strokes_for(slide)))
        self.next_view.set_slide(slide + 1 if slide + 1 < doc.slide_count else None)
        self.notes.set_slide(slide)
        self.overview.set_current(slide)
        self.slide_button.setText(f"{slide + 1} / {doc.slide_count}")
        label = doc.labels[slide] if slide < len(doc.labels) else ""
        self.slide_button.setToolTip(f"Page label: {label} — click to go to a page")

    def _on_strokes(self, slide: int) -> None:
        if slide == self.state.slide:
            self.current_view.set_strokes(list(self.state.strokes_for(slide)))

    def set_checked(self, name: str, on: bool) -> None:
        action = self.actions.get(name)
        if action is not None and action.isCheckable():
            action.setChecked(on)

    # ---------------------------------------------------------- interactions

    def _on_view_clicked(self, button: object) -> None:
        if button == Qt.MouseButton.LeftButton:
            self.app.dispatch("next")
        elif button == Qt.MouseButton.RightButton:
            self.app.dispatch("prev")

    def _on_link(self, link: Link) -> None:
        if link.target is not None:
            self.state.goto(link.target)
        elif link.uri:
            QDesktopServices.openUrl(QUrl(link.uri))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.current_view and event.type() == QEvent.Type.Wheel:
            assert isinstance(event, QWheelEvent)
            self._wheel_accum += event.angleDelta().y()
            while self._wheel_accum <= -WHEEL_STEP:
                self._wheel_accum += WHEEL_STEP
                self.app.dispatch("next")
            while self._wheel_accum >= WHEEL_STEP:
                self._wheel_accum -= WHEEL_STEP
                self.app.dispatch("prev")
            return True
        return super().eventFilter(obj, event)

    # --------------------------------------------------------------- overview

    def overview_visible(self) -> bool:
        return self.stack.currentWidget() is self.overview

    def show_overview(self) -> None:
        self.cancel_input()
        self.overview.set_current(self.state.slide)
        self.stack.setCurrentWidget(self.overview)
        self.overview.setFocus()
        self.set_checked("overview", True)

    def hide_overview(self) -> None:
        if self.overview_visible():
            self.stack.setCurrentWidget(self.hsplit)
            self.current_view.setFocus()
        self.set_checked("overview", False)

    def toggle_overview(self) -> None:
        if self.overview_visible():
            self.hide_overview()
        else:
            self.show_overview()

    def _on_overview_activated(self, slide: int) -> None:
        self.hide_overview()
        self.state.goto(slide)

    # ------------------------------------------------------------ inline input

    def input_active(self) -> bool:
        return self.input_edit.isVisible()

    def _show_input(self, mode: str, prefill: str, placeholder: str) -> None:
        self._input_mode = mode
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setText(prefill)
        self.input_edit.end(False)
        self.slide_button.hide()
        self.input_edit.show()
        self.input_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_goto(self, prefill: str = "", labels_first: bool = False) -> None:
        self._show_input(
            "label" if labels_first else "goto", prefill, "label" if labels_first else "page"
        )

    def show_talk_time_edit(self) -> None:
        timer = self.app.timer
        prefill = timer.format(timer.talk_seconds) if timer.talk_seconds else ""
        self._show_input("talk", prefill, "mm:ss")

    def _hide_input(self) -> None:
        self.input_edit.hide()
        self.slide_button.show()
        if not self.overview_visible():
            self.current_view.setFocus()

    def validate_input(self) -> bool:
        """Apply the inline input; returns False if no input was open."""
        if not self.input_active():
            return False
        text = self.input_edit.text().strip()
        mode = self._input_mode
        self._hide_input()
        if mode == "talk":
            if not text:
                self.app.set_talk_time(None)
                return True
            try:
                self.app.set_talk_time(self.app.timer.parse(text))
            except ValueError:
                self.show_message(f"Invalid talk time: {text}")
            return True
        slide = self._resolve_slide(text, labels_first=(mode == "label"))
        if slide is None:
            if text:
                self.show_message(f"No page {text!r}")
        else:
            self.state.goto(slide)
        return True

    def cancel_input(self) -> bool:
        if not self.input_active():
            return False
        self._hide_input()
        return True

    def _resolve_slide(self, text: str, labels_first: bool) -> int | None:
        doc = self.state.doc
        if doc is None or not text:
            return None
        by_label = doc.label_index(text)
        by_number: int | None = None
        if text.isdigit() and 1 <= int(text) <= doc.slide_count:
            by_number = int(text) - 1
        if labels_first:
            return by_label if by_label is not None else by_number
        return by_number if by_number is not None else by_label

    # ------------------------------------------------------------------ timer

    def update_time(self, timer: Any) -> None:
        """Refresh the elapsed / remaining labels and the progress bar (called every 250 ms)."""
        paused = getattr(timer.state, "name", "") == "PAUSED"
        text = timer.format(timer.elapsed)
        if paused:
            text = "⏸ " + text
        self.elapsed_label.setText(text)

        fraction = timer.fraction
        color = GREEN
        if timer.overtime:
            color = RED
        elif fraction is not None and fraction >= 0.9:
            color = ORANGE
        if color != self._timer_color:
            self._timer_color = color
            self.elapsed_label.setStyleSheet(f"color: {color};")

        remaining = timer.remaining
        if remaining is None:
            self.remaining_label.hide()
            self.progress.hide()
        else:
            self.remaining_label.setText(timer.format(remaining))
            self.remaining_label.show()
            self.progress.setValue(int(max(0.0, min(1.0, fraction or 0.0)) * 1000))
            self.progress.show()

        if self.config.show_clock:
            self.clock_label.setText(QTime.currentTime().toString("HH:mm"))
            self.clock_label.show()
        else:
            self.clock_label.hide()

        pause_action = self.actions.get("pause-timer")
        if pause_action is not None:
            label = "Resume" if paused else "Pause"
            if pause_action.text() != label:
                pause_action.setText(label)
                icon = (
                    QStyle.StandardPixmap.SP_MediaPlay
                    if paused
                    else QStyle.StandardPixmap.SP_MediaPause
                )
                pause_action.setIcon(self.style().standardIcon(icon))

    # --------------------------------------------------------------- messages

    def show_message(self, text: str) -> None:
        self.message_label.setText(text)
        self._msg_timer.start(MESSAGE_MS)

    # --------------------------------------------------------------- settings

    def save_settings(self) -> None:
        settings = QSettings("prez", "prez")
        settings.setValue("presenter/geometry", self.saveGeometry())
        settings.setValue("presenter/hsplitter", self.hsplit.saveState())
        settings.setValue("presenter/vsplitter", self.vsplit.saveState())
        settings.sync()

    def restore_settings(self) -> None:
        settings = QSettings("prez", "prez")
        geometry = settings.value("presenter/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        hsplit = settings.value("presenter/hsplitter")
        vsplit = settings.value("presenter/vsplitter")
        if hsplit:
            self.hsplit.restoreState(hsplit)
        if vsplit:
            self.vsplit.restoreState(vsplit)
        self._restored_splitters = bool(hsplit)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._restored_splitters:
            self._restored_splitters = True
            width = max(self.hsplit.width(), 10)
            ratio = min(max(self.config.slide_ratio, 0.1), 0.9)
            self.hsplit.setSizes([int(width * ratio), int(width * (1 - ratio))])
            height = max(self.vsplit.height(), 10)
            self.vsplit.setSizes([int(height * 0.4), int(height * 0.6)])

    # ------------------------------------------------------------ drag & drop

    @staticmethod
    def _pdf_path(event: QDragEnterEvent | QDropEvent) -> str | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._pdf_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._pdf_path(event)
        if path:
            event.acceptProposedAction()
            self.app.open(path)

    # ------------------------------------------------------------------ close

    def closeEvent(self, event: QCloseEvent) -> None:
        self.app.dispatch("quit")
        event.accept()
