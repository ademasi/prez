"""Presentation state shared by the content and presenter windows.

`PresentationState` is the single source of truth for "which slide are we on, what is the
content window showing, is it blanked / frozen, where is the pointer, which strokes exist".
It knows nothing about rendering or widgets: it only clamps, records history and emits Qt
signals that the windows connect to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from prez.document import DocumentInfo


@dataclass
class Stroke:
    """One freehand highlight stroke, in coordinates normalized to the SLIDE region."""

    color: str
    width: float  # normalized to slide width
    points: list[tuple[float, float]] = field(default_factory=list)
    eraser: bool = False


def _dist_point_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Euclidean distance from P to the segment AB (all normalized coordinates)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _stroke_hits(stroke: Stroke, x: float, y: float, radius: float) -> bool:
    """True if the point (x, y) is within `radius` of the stroke's polyline."""
    pts = stroke.points
    if not pts:
        return False
    if len(pts) == 1:
        return math.hypot(pts[0][0] - x, pts[0][1] - y) <= radius
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        if _dist_point_segment(x, y, ax, ay, bx, by) <= radius:
            return True
    return False


class PresentationState(QObject):
    """Navigation, blank/freeze, pointer and highlight state with change signals."""

    document_changed = Signal(object)  # DocumentInfo | None
    slide_changed = Signal(int)  # presenter slide
    content_changed = Signal(int)  # slide shown on the content window (differs when frozen)
    blank_changed = Signal(object)  # None | "black" | "white"
    freeze_changed = Signal(bool)
    pointer_changed = Signal(object)  # None | (x, y) normalized in the slide region
    strokes_changed = Signal(int)  # slide whose strokes changed
    message = Signal(str)  # transient status text for the presenter status bar

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.doc: DocumentInfo | None = None
        self.slide: int = 0
        self.content_slide: int = 0
        self.blank: str | None = None
        self.frozen: bool = False
        self.pointer: tuple[float, float] | None = None
        self.strokes: dict[int, list[Stroke]] = {}
        self.history: list[int] = []
        self.history_pos: int = -1
        # Undo/redo stacks hold snapshots of a slide's stroke list.
        self._undo: dict[int, list[list[Stroke]]] = {}
        self._redo: dict[int, list[list[Stroke]]] = {}
        self._current_stroke: Stroke | None = None
        self._stroke_slide: int = 0

    # ------------------------------------------------------------------ document

    @property
    def slide_count(self) -> int:
        return self.doc.slide_count if self.doc is not None else 0

    def _clamp(self, slide: int) -> int:
        count = self.slide_count
        if count <= 0:
            return 0
        return max(0, min(int(slide), count - 1))

    def set_document(self, doc: DocumentInfo | None, keep_slide: bool = True) -> None:
        """Install a (re)loaded document.

        Strokes and history survive a reload of the same file (same path) and are dropped
        when a different file is opened. With `keep_slide` the current slide is clamped
        into the new document, otherwise we go back to the first slide.
        """
        same_file = self.doc is not None and doc is not None and self.doc.path == doc.path
        self.doc = doc
        self._current_stroke = None
        if not same_file:
            self.strokes.clear()
            self._undo.clear()
            self._redo.clear()
            self.history.clear()
            self.history_pos = -1
        if doc is None:
            self.slide = 0
            self.content_slide = 0
            self.document_changed.emit(None)
            return
        self.slide = self._clamp(self.slide) if keep_slide else 0
        if self.frozen and keep_slide:
            self.content_slide = self._clamp(self.content_slide)
        else:
            self.content_slide = self.slide
        if self.history:
            self.history = [self._clamp(s) for s in self.history]
            self.history_pos = max(0, min(self.history_pos, len(self.history) - 1))
        else:
            self.history = [self.slide]
            self.history_pos = 0
        self.document_changed.emit(doc)
        self.slide_changed.emit(self.slide)
        self.content_changed.emit(self.content_slide)

    # ---------------------------------------------------------------- navigation

    def _record(self, slide: int) -> None:
        if 0 <= self.history_pos < len(self.history) and self.history[self.history_pos] == slide:
            return
        del self.history[self.history_pos + 1 :]
        self.history.append(slide)
        self.history_pos = len(self.history) - 1

    def goto(self, slide: int, record_history: bool = True) -> bool:
        """Go to `slide` (clamped). Returns True if the presenter slide actually changed."""
        if self.doc is None:
            return False
        slide = self._clamp(slide)
        if record_history:
            self._record(slide)
        if slide == self.slide:
            return False
        self._current_stroke = None
        self.slide = slide
        self.slide_changed.emit(slide)
        if not self.frozen:
            self.content_slide = slide
            self.content_changed.emit(slide)
        return True

    def next(self) -> bool:
        return self.goto(self.slide + 1)

    def prev(self) -> bool:
        return self.goto(self.slide - 1)

    def first(self) -> bool:
        return self.goto(0)

    def last(self) -> bool:
        return self.goto(self.slide_count - 1)

    def next_label(self) -> bool:
        if self.doc is None:
            return False
        return self.goto(self.doc.next_label(self.slide))

    def prev_label(self) -> bool:
        if self.doc is None:
            return False
        return self.goto(self.doc.prev_label(self.slide))

    def back(self) -> bool:
        """Go back in the history of visited slides."""
        if self.history_pos <= 0:
            return False
        self.history_pos -= 1
        return self.goto(self.history[self.history_pos], record_history=False)

    def forward(self) -> bool:
        """Go forward in the history of visited slides."""
        if self.history_pos + 1 >= len(self.history):
            return False
        self.history_pos += 1
        return self.goto(self.history[self.history_pos], record_history=False)

    # ------------------------------------------------------------ blank / freeze

    def toggle_blank(self, kind: str = "black") -> None:
        """Toggle blanking: the same kind again clears it, a different kind switches."""
        self.blank = None if self.blank == kind else kind
        self.blank_changed.emit(self.blank)

    def toggle_freeze(self) -> None:
        """Freeze keeps the content window on its current slide while the presenter moves."""
        self.frozen = not self.frozen
        if not self.frozen and self.content_slide != self.slide:
            self.content_slide = self.slide
            self.content_changed.emit(self.content_slide)
        self.freeze_changed.emit(self.frozen)

    # ------------------------------------------------------------------- pointer

    def set_pointer(self, pos: tuple[float, float] | None) -> None:
        if pos is not None:
            pos = (float(pos[0]), float(pos[1]))
        if pos == self.pointer:
            return
        self.pointer = pos
        self.pointer_changed.emit(pos)

    # ------------------------------------------------------------------- strokes

    def strokes_for(self, slide: int) -> list[Stroke]:
        return self.strokes.get(slide, [])

    def _push_undo(self, slide: int) -> None:
        self._undo.setdefault(slide, []).append(list(self.strokes.get(slide, [])))
        self._redo.pop(slide, None)

    def begin_stroke(self, color: str, width: float, eraser: bool = False) -> None:
        """Start a stroke on the current slide. Eraser strokes remove what they touch."""
        if self.doc is None:
            return
        self.end_stroke()
        slide = self.slide
        self._push_undo(slide)
        stroke = Stroke(color=color, width=float(width), points=[], eraser=eraser)
        self._current_stroke = stroke
        self._stroke_slide = slide
        if not eraser:
            self.strokes.setdefault(slide, []).append(stroke)

    def add_point(self, x: float, y: float) -> None:
        stroke = self._current_stroke
        if stroke is None:
            return
        stroke.points.append((float(x), float(y)))
        if stroke.eraser:
            self._erase_at(self._stroke_slide, float(x), float(y), stroke.width)
        self.strokes_changed.emit(self._stroke_slide)

    def _erase_at(self, slide: int, x: float, y: float, eraser_width: float) -> None:
        strokes = self.strokes.get(slide)
        if not strokes:
            return
        kept = [s for s in strokes if not _stroke_hits(s, x, y, (eraser_width + s.width) / 2.0)]
        if len(kept) != len(strokes):
            strokes[:] = kept

    def end_stroke(self) -> None:
        stroke = self._current_stroke
        if stroke is None:
            return
        self._current_stroke = None
        slide = self._stroke_slide
        strokes = self.strokes.get(slide, [])
        if not stroke.eraser and not stroke.points and stroke in strokes:
            strokes.remove(stroke)
        # Drop the undo snapshot if the stroke did not change anything.
        undo = self._undo.get(slide)
        if undo and undo[-1] == strokes:
            undo.pop()
        self.strokes_changed.emit(slide)

    def undo_stroke(self, slide: int | None = None) -> bool:
        slide = self.slide if slide is None else slide
        self.end_stroke()
        undo = self._undo.get(slide)
        if not undo:
            return False
        self._redo.setdefault(slide, []).append(list(self.strokes.get(slide, [])))
        self.strokes[slide] = undo.pop()
        self.strokes_changed.emit(slide)
        return True

    def redo_stroke(self, slide: int | None = None) -> bool:
        slide = self.slide if slide is None else slide
        self.end_stroke()
        redo = self._redo.get(slide)
        if not redo:
            return False
        self._undo.setdefault(slide, []).append(list(self.strokes.get(slide, [])))
        self.strokes[slide] = redo.pop()
        self.strokes_changed.emit(slide)
        return True

    def clear_strokes(self, slide: int | None = None) -> bool:
        slide = self.slide if slide is None else slide
        self.end_stroke()
        if not self.strokes.get(slide):
            return False
        self._push_undo(slide)
        self.strokes[slide] = []
        self.strokes_changed.emit(slide)
        return True

    # ------------------------------------------------------------------ messages

    def notify(self, text: str) -> None:
        """Show a transient message in the presenter status bar."""
        self.message.emit(text)
