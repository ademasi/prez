"""The content window: what the audience sees."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from prez.document import Region
from prez.widgets.slide_view import SlideView

if TYPE_CHECKING:
    from prez.render import RenderService
    from prez.state import PresentationState


class ContentWindow(QWidget):
    """Black, frameless-when-fullscreen window holding one non-interactive `SlideView`.

    It mirrors `state.content_slide` (not `state.slide`, so freeze works), the blank
    state, the laser pointer and the highlight strokes. Keyboard input is handled by the
    application-wide event filter in `prez.app`.
    """

    def __init__(
        self,
        render: RenderService,
        state: PresentationState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self.setWindowTitle("prez — content")
        self.setObjectName("ContentWindow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("black"))
        palette.setColor(QPalette.ColorRole.Base, QColor("black"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.view = SlideView(render, Region.SLIDE, self)
        self.view.set_interactive(False)
        self.view.request_priority = 0
        # Double-clicks land on the view (it fills the window); catch them there too.
        self.view.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        self.resize(960, 540)

        state.document_changed.connect(self._on_document)
        state.content_changed.connect(self._on_content_slide)
        state.blank_changed.connect(self.view.set_blank)
        state.pointer_changed.connect(self.view.set_pointer)
        state.strokes_changed.connect(self._on_strokes)

    # ------------------------------------------------------------------ state

    def _on_document(self, doc: object) -> None:
        self.view.set_document(doc)  # type: ignore[arg-type]
        if doc is None:
            self.view.set_slide(None)
        else:
            self._on_content_slide(self._state.content_slide)

    def _on_content_slide(self, slide: int) -> None:
        if self._state.doc is None:
            self.view.set_slide(None)
            return
        self.view.set_slide(slide)
        self.view.set_strokes(list(self._state.strokes_for(slide)))

    def _on_strokes(self, slide: int) -> None:
        if slide == self._state.content_slide:
            self.view.set_strokes(list(self._state.strokes_for(slide)))

    # ------------------------------------------------------------- fullscreen

    def set_fullscreen(self, on: bool) -> None:
        if on:
            self.showFullScreen()
        else:
            self.showNormal()
        self._update_cursor()

    def is_fullscreen(self) -> bool:
        return self.isFullScreen()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.is_fullscreen())

    def _update_cursor(self) -> None:
        if self.isFullScreen():
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.view.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self.unsetCursor()
            self.view.unsetCursor()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_cursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_fullscreen()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            obj is self.view
            and event.type() == QEvent.Type.MouseButtonDblClick
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.toggle_fullscreen()
            return True
        return super().eventFilter(obj, event)
