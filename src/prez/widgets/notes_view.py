"""NotesPane: the speaker notes the PDF carries.

With a notes region (Beamer "show notes on second screen") the pane is a non-interactive
``SlideView(Region.NOTES)``. Otherwise it shows the slide's text annotations, read-only,
or a short placeholder when the slide has none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QWheelEvent
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget

from prez import theme
from prez.document import Region
from prez.widgets.slide_view import SlideView

if TYPE_CHECKING:
    from prez.document import DocumentInfo
    from prez.render import RenderService

DEFAULT_FONT_PT = 14
MIN_FONT_PT = 6
MAX_FONT_PT = 72
BG = theme.BASE
FG = theme.TEXT
_STYLE = (
    f"QPlainTextEdit {{ background: {BG}; color: {FG}; border: none; padding: 4px; "
    f"selection-background-color: {theme.SELECTION}; selection-color: {FG}; }}"
)


class _NotesEdit(QPlainTextEdit):
    """Read-only text box that turns Ctrl+wheel into a zoom request instead of scrolling."""

    zoom_requested = Signal(int)  # +1 / -1 steps

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt override)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class NotesPane(QWidget):
    """Notes region of the slide when the document has one, else its text annotations."""

    PAGE_SLIDE = 0
    PAGE_TEXT = 1

    def __init__(self, render: RenderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: DocumentInfo | None = None
        self._slide = 0
        self._font_pt = DEFAULT_FONT_PT

        self._view = SlideView(render, Region.NOTES)
        self._view.set_interactive(False)
        self._view.set_background(theme.CRUST)

        self._annotations = _NotesEdit()
        self._annotations.setReadOnly(True)
        self._annotations.setStyleSheet(_STYLE)
        self._annotations.setFrameShape(QPlainTextEdit.Shape.NoFrame)

        self._placeholder = QLabel("No notes in this PDF")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {theme.OVERLAY0}; background: {BG};")

        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self._annotations, 1)
        text_layout.addWidget(self._placeholder, 1)
        text_page.setStyleSheet(f"background: {BG};")

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._view)
        self._stack.addWidget(text_page)
        self._stack.setCurrentIndex(self.PAGE_TEXT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._annotations.zoom_requested.connect(self._zoom)
        self._apply_font()
        self._show_slide()

    # --------------------------------------------------------------------- API

    def set_document(self, doc: DocumentInfo | None) -> None:
        self._doc = doc
        self._view.set_document(doc)
        has_notes = bool(doc is not None and doc.has_notes())
        self._stack.setCurrentIndex(self.PAGE_SLIDE if has_notes else self.PAGE_TEXT)
        self._show_slide()

    def set_slide(self, slide: int) -> None:
        self._slide = int(slide)
        self._view.set_slide(self._slide)
        self._show_slide()

    def slide(self) -> int:
        return self._slide

    def slide_view(self) -> SlideView | None:
        """The notes SlideView when the document has a notes region, else None."""
        if self._doc is not None and self._doc.has_notes():
            return self._view
        return None

    def current_page(self) -> int:
        """``PAGE_SLIDE`` or ``PAGE_TEXT``."""
        return self._stack.currentIndex()

    def annotations_view(self) -> QPlainTextEdit:
        return self._annotations

    def font_point_size(self) -> int:
        return self._font_pt

    def set_font_point_size(self, points: int) -> None:
        self._font_pt = min(max(int(points), MIN_FONT_PT), MAX_FONT_PT)
        self._apply_font()

    # ----------------------------------------------------------------- helpers

    def _show_slide(self) -> None:
        doc = self._doc
        slide = self._slide
        annotations: list[str] = []
        if doc is not None and 0 <= slide < doc.slide_count:
            try:
                annotations = [str(a) for a in doc.annotations[slide] if a]
            except (IndexError, AttributeError, TypeError):
                annotations = []
        self._annotations.setPlainText("\n\n".join(annotations))
        self._annotations.setVisible(bool(annotations))
        self._placeholder.setVisible(not annotations)

    def _zoom(self, steps: int) -> None:
        self.set_font_point_size(self._font_pt + steps)

    def _apply_font(self) -> None:
        font = QFont(self.font())
        font.setPointSize(self._font_pt)
        self._annotations.setFont(font)
