"""NotesPane: shows the notes region of the slide, or annotation + free-text notes.

When the document has a notes region (beamer "show notes on second screen"), page 0 of
the internal stack is a non-interactive ``SlideView(Region.NOTES)``. Otherwise page 1
shows the PDF text annotations of the slide (read-only) above an editable text box
whose content is persisted to ``<pdf path>.notes.json`` as ``{"<slide>": "text"}``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QWheelEvent
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from prez.document import Region
from prez.widgets.slide_view import SlideView

if TYPE_CHECKING:
    from prez.document import DocumentInfo
    from prez.render import RenderService

log = logging.getLogger(__name__)

SAVE_DEBOUNCE_MS = 500
DEFAULT_FONT_PT = 14
MIN_FONT_PT = 6
MAX_FONT_PT = 72
BG = "#202124"
FG = "#e8eaed"
_STYLE = (
    f"QPlainTextEdit {{ background: {BG}; color: {FG}; border: none; "
    f"selection-background-color: #8ab4f8; selection-color: #202124; }}"
)


def _slide_key(item: tuple[str, str]) -> tuple[int, int, str]:
    """Sort notes numerically by slide index, non-numeric keys last."""
    key = item[0]
    return (0, int(key), key) if key.isdigit() else (1, 0, key)


def notes_path_for(pdf_path: str) -> str:
    """Path of the user-notes JSON file for ``pdf_path`` (``<pdf path>.notes.json``)."""
    return os.fspath(pdf_path) + ".notes.json"


class _NotesEdit(QPlainTextEdit):
    """QPlainTextEdit that turns Ctrl+wheel into a zoom request instead of scrolling."""

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
    """If doc.has_notes(): a SlideView(Region.NOTES). Else: annotations text (read-only,
    top) + editable per-slide text notes persisted to ``<pdf>.notes.json``."""

    PAGE_SLIDE = 0
    PAGE_TEXT = 1

    def __init__(self, render: RenderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: DocumentInfo | None = None
        self._slide = 0
        self._notes: dict[str, str] = {}
        self._dirty = False
        self._loading = False
        self._warned = False
        self._font_pt = DEFAULT_FONT_PT

        self._view = SlideView(render, Region.NOTES)
        self._view.set_interactive(False)

        self._annotations = _NotesEdit()
        self._annotations.setReadOnly(True)
        self._annotations.setPlaceholderText("")
        self._annotations.setStyleSheet(_STYLE)
        self._annotations.setFrameShape(QPlainTextEdit.Shape.NoFrame)

        self._editor = _NotesEdit()
        self._editor.setPlaceholderText("Notes for this slide…")
        self._editor.setStyleSheet(_STYLE)
        self._editor.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._editor.setTabChangesFocus(True)
        self._editor.setEnabled(False)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self._annotations)
        self._splitter.addWidget(self._editor)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setChildrenCollapsible(False)

        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self._splitter)
        text_page.setStyleSheet(f"background: {BG};")

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._view)
        self._stack.addWidget(text_page)
        self._stack.setCurrentIndex(self.PAGE_TEXT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save)

        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.zoom_requested.connect(self._zoom)
        self._annotations.zoom_requested.connect(self._zoom)
        self._apply_font()

    # --------------------------------------------------------------------- API

    def set_document(self, doc: DocumentInfo | None) -> None:
        self.flush()
        self._doc = doc
        self._notes = self._load_notes()
        self._dirty = False
        self._warned = False
        self._view.set_document(doc)
        has_notes = bool(doc is not None and doc.has_notes())
        self._stack.setCurrentIndex(self.PAGE_SLIDE if has_notes else self.PAGE_TEXT)
        self._editor.setEnabled(doc is not None)
        self._show_slide()

    def set_slide(self, slide: int) -> None:
        self._slide = int(slide)
        self._view.set_slide(self._slide)
        self._show_slide()

    def slide(self) -> int:
        return self._slide

    def flush(self) -> None:
        """Save pending edits now."""
        self._save_timer.stop()
        self._save()

    def slide_view(self) -> SlideView | None:
        """The notes SlideView when the document has a notes region, else None."""
        if self._doc is not None and self._doc.has_notes():
            return self._view
        return None

    def current_page(self) -> int:
        """``PAGE_SLIDE`` or ``PAGE_TEXT``."""
        return self._stack.currentIndex()

    def notes_path(self) -> str | None:
        return notes_path_for(self._doc.path) if self._doc is not None else None

    def notes(self) -> dict[str, str]:
        """Current in-memory user notes (``{"<slide>": text}``)."""
        return dict(self._notes)

    def editor(self) -> QPlainTextEdit:
        return self._editor

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
        self._loading = True
        try:
            self._editor.setPlainText(self._notes.get(str(slide), ""))
        finally:
            self._loading = False

    def _on_text_changed(self) -> None:
        if self._loading or self._doc is None:
            return
        text = self._editor.toPlainText()
        key = str(self._slide)
        if text:
            self._notes[key] = text
        else:
            self._notes.pop(key, None)
        self._dirty = True
        self._save_timer.start()

    def _load_notes(self) -> dict[str, str]:
        path = self.notes_path()
        if path is None or not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("Cannot read notes file %s: %s", path, exc)
            return {}
        if not isinstance(data, dict):
            log.warning("Ignoring notes file %s: not a JSON object", path)
            return {}
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}

    def _save(self) -> None:
        if self._doc is None or not self._dirty:
            return
        self._dirty = False
        path = self.notes_path()
        assert path is not None
        if not self._notes and not os.path.exists(path):
            return
        ordered = dict(sorted(self._notes.items(), key=_slide_key))
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(ordered, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except OSError as exc:
            if not self._warned:
                log.warning("Cannot save notes to %s: %s", path, exc)
                self._warned = True
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _zoom(self, steps: int) -> None:
        self.set_font_point_size(self._font_pt + steps)

    def _apply_font(self) -> None:
        font = QFont(self.font())
        font.setPointSize(self._font_pt)
        self._editor.setFont(font)
        self._annotations.setFont(font)

    # ------------------------------------------------------------------ events

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.flush()
        super().closeEvent(event)
