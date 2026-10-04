"""SlideView: paints one region of one slide, letterboxed, with overlays.

Overlays are the laser pointer dot, highlight strokes and (when interactive) link
hotspots. Rendering is delegated to a ``RenderService``; this widget only blits cached
pixmaps and asks for missing ones.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from prez.document import Region

if TYPE_CHECKING:
    from prez.document import DocumentInfo, Link
    from prez.render import RenderKey, RenderService
    from prez.state import Stroke

log = logging.getLogger(__name__)

#: Quiet time after the last resize before exact-size renders are requested again.
RESIZE_DEBOUNCE_MS = 40

_DEFAULT_ASPECT = 16 / 9
_BLACK = QColor(0, 0, 0)
_WHITE = QColor(255, 255, 255)


def parse_color(text: str, default: str = "#ff0000") -> QColor:
    """Parse ``#rrggbb`` or ``#rrggbbaa`` (CSS order) into a QColor.

    QColor only understands ``#aarrggbb`` for 8-digit strings, so the alpha byte is
    moved to the front. Invalid input falls back to ``default`` with a warning.
    """
    t = text.strip()
    if t.startswith("#") and len(t) == 9:
        t = "#" + t[7:9] + t[1:7]
    color = QColor(t)
    if not color.isValid():
        log.warning("Invalid colour %r, using %s", text, default)
        color = QColor(default)
    return color


class SlideView(QWidget):
    """Paints one Region of one slide, letterboxed on black (or the blank colour), plus
    overlays: pointer dot, strokes, (optional) link hotspots."""

    link_activated = Signal(object)  # Link
    pointer_moved = Signal(object)  # (x, y) normalized | None when leaving
    stroke_started = Signal(float, float)
    stroke_moved = Signal(float, float)
    stroke_ended = Signal()
    clicked = Signal(object)  # Qt.MouseButton

    #: Priority used for this view's own render requests (0 = visible now).
    request_priority: int = 0

    def __init__(
        self, render: RenderService, region: Region, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._render = render
        self._region = region
        self._doc: DocumentInfo | None = None
        self._slide: int | None = None
        self._blank: str | None = None
        self._pointer: tuple[float, float] | None = None
        self._pointer_color = QColor("#ff0000")
        self._bg = QColor(_BLACK)
        self._pointer_size = 0.02
        self._strokes: list[Stroke] = []
        self._interactive = False
        self._draw_mode = False
        self._drawing = False
        self._press_link: Link | None = None
        self._hand_cursor = False
        self._suppress_requests = False

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(RESIZE_DEBOUNCE_MS)
        self._resize_timer.timeout.connect(self._resize_settled)

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setMinimumSize(16, 9)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        render.rendered.connect(self._on_rendered)

    # ------------------------------------------------------------------ properties

    @property
    def region(self) -> Region:
        return self._region

    @property
    def slide(self) -> int | None:
        return self._slide

    @property
    def document(self) -> DocumentInfo | None:
        return self._doc

    @property
    def blank(self) -> str | None:
        return self._blank

    def is_interactive(self) -> bool:
        return self._interactive

    def is_draw_mode(self) -> bool:
        return self._draw_mode

    # --------------------------------------------------------------------- setters

    def set_document(self, doc: DocumentInfo | None) -> None:
        self._doc = doc
        self.update()

    def set_slide(self, slide: int | None) -> None:
        if slide == self._slide:
            return
        self._slide = slide
        self.update()

    def set_background(self, color: QColor | str) -> None:
        """Letterbox colour when not blanked (black by default)."""
        self._bg = QColor(color)
        self.update()

    def set_blank(self, kind: str | None) -> None:
        if kind == self._blank:
            return
        self._blank = kind
        self.update()

    def set_pointer(self, pos: tuple[float, float] | None) -> None:
        if pos == self._pointer:
            return
        old = self._pointer_dirty_rect(self._pointer)
        self._pointer = pos
        new = self._pointer_dirty_rect(pos)
        for rect in (old, new):
            if rect is not None:
                self.update(rect)

    def set_pointer_style(self, color: str, size: float) -> None:
        self._pointer_color = parse_color(color)
        self._pointer_size = max(0.0, float(size))
        self.update()

    def set_strokes(self, strokes: Iterable[Stroke]) -> None:
        self._strokes = list(strokes)
        self.update()

    def set_interactive(self, enabled: bool) -> None:
        self._interactive = bool(enabled)
        self._update_cursor(None)

    def set_draw_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._drawing and not enabled:
            self._drawing = False
            self.stroke_ended.emit()
        self._draw_mode = enabled
        self._update_cursor(None)

    # -------------------------------------------------------------------- geometry

    def slide_rect(self) -> QRectF:
        """Letterboxed rect of the slide in widget (logical) coordinates."""
        if not self._active():
            return QRectF()
        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            return QRectF()
        aspect = self._aspect()
        if w / h > aspect:
            sh = h
            sw = h * aspect
        else:
            sw = w
            sh = w / aspect
        return QRectF((w - sw) / 2, (h - sh) / 2, sw, sh)

    def current_key(self) -> RenderKey | None:
        """Exact render key for the current widget size and device pixel ratio."""
        if not self._active():
            return None
        dpr = self.devicePixelRatioF()
        w = int(self.width() * dpr)
        h = int(self.height() * dpr)
        if w < 1 or h < 1:
            return None
        return self._render.key_for(self._slide, self._region, w, h)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(640, 360)

    # --------------------------------------------------------------------- helpers

    def _active(self) -> bool:
        """True when there is a slide to paint for this region."""
        doc = self._doc
        slide = self._slide
        if doc is None or slide is None or slide < 0 or slide >= doc.slide_count:
            return False
        return not (self._region == Region.NOTES and not doc.has_notes())

    def _aspect(self) -> float:
        assert self._doc is not None and self._slide is not None
        try:
            aspect = float(self._doc.aspect(self._slide, self._region))
        except (ValueError, IndexError, ZeroDivisionError):
            return _DEFAULT_ASPECT
        return aspect if aspect > 0 else _DEFAULT_ASPECT

    def _background(self) -> QColor:
        if self._blank == "white":
            return _WHITE
        if self._blank == "black":
            return _BLACK
        return self._bg

    def _normalize(self, pos: QPointF, rect: QRectF) -> tuple[float, float]:
        x = (pos.x() - rect.x()) / rect.width()
        y = (pos.y() - rect.y()) / rect.height()
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    def _link_at(self, pos: QPointF) -> Link | None:
        doc = self._doc
        slide = self._slide
        if doc is None or slide is None or self._region != Region.SLIDE:
            return None
        rect = self.slide_rect()
        if rect.isEmpty() or not rect.contains(pos):
            return None
        try:
            links = doc.links[slide]
        except (IndexError, AttributeError, TypeError):
            return None
        x, y = self._normalize(pos, rect)
        for link in links:
            x0, y0, x1, y1 = link.rect
            if min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1):
                return link
        return None

    def _pointer_dirty_rect(self, pos: tuple[float, float] | None) -> QRect | None:
        if pos is None:
            return None
        rect = self.slide_rect()
        if rect.isEmpty():
            return None
        radius = self._pointer_size * rect.width() / 2
        cx = rect.x() + pos[0] * rect.width()
        cy = rect.y() + pos[1] * rect.height()
        margin = radius + 3
        return QRectF(cx - margin, cy - margin, 2 * margin, 2 * margin).toAlignedRect()

    def _update_cursor(self, pos: QPointF | None) -> None:
        if self._interactive and self._draw_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._hand_cursor = False
            return
        hand = bool(self._interactive and pos is not None and self._link_at(pos) is not None)
        if hand != self._hand_cursor or pos is None:
            self._hand_cursor = hand
            if hand:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                # Inherit the parent's cursor (the content window hides it).
                self.unsetCursor()

    # ----------------------------------------------------------------------- slots

    def _on_rendered(self, key: RenderKey) -> None:
        if self._blank is not None or not self._active():
            return
        if key.slide == self._slide and key.region == self._region:
            self.update()

    def _resize_settled(self) -> None:
        self._suppress_requests = False
        self.update()

    # -------------------------------------------------------------------- painting

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        try:
            painter.fillRect(event.rect(), self._background())
            if self._blank is not None or not self._active():
                return
            rect = self.slide_rect()
            if rect.isEmpty():
                return
            self._paint_slide(painter, rect)
            painter.setClipRect(rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_strokes(painter, rect)
            self._paint_pointer(painter, rect)
        finally:
            painter.end()

    def _paint_slide(self, painter: QPainter, rect: QRectF) -> None:
        assert self._slide is not None
        dpr = self.devicePixelRatioF()
        key = self.current_key()
        pix = self._render.get(key) if key is not None else None
        if pix is not None:
            if pix.devicePixelRatio() != dpr:
                pix.setDevicePixelRatio(dpr)
            lw = pix.width() / dpr
            lh = pix.height() / dpr
            # Centre the exact-size pixmap, snapped to the device pixel grid: a 1:1 blit.
            x = round((rect.center().x() - lw / 2) * dpr) / dpr
            y = round((rect.center().y() - lh / 2) * dpr) / dpr
            painter.drawPixmap(QPointF(x, y), pix)
            return
        if key is not None and not self._suppress_requests:
            self._render.request(key, self.request_priority)
        near = self._render.nearest(self._slide, self._region)
        if near is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(rect, near, QRectF(0, 0, near.width(), near.height()))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def _paint_strokes(self, painter: QPainter, rect: QRectF) -> None:
        if not self._strokes:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for stroke in self._strokes:
            if getattr(stroke, "eraser", False):
                continue
            points = stroke.points
            if not points:
                continue
            width = max(1.0, float(stroke.width) * rect.width())
            pen = QPen(
                parse_color(stroke.color),
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            x0, y0 = points[0]
            first = QPointF(rect.x() + x0 * rect.width(), rect.y() + y0 * rect.height())
            if len(points) == 1:
                # The path stroker drops zero-length segments: draw the dot explicitly.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(pen.color())
                painter.drawEllipse(first, width / 2, width / 2)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                continue
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(first)
            for px, py in points[1:]:
                path.lineTo(rect.x() + px * rect.width(), rect.y() + py * rect.height())
            painter.drawPath(path)

    def _paint_pointer(self, painter: QPainter, rect: QRectF) -> None:
        if self._pointer is None or self._pointer_size <= 0:
            return
        radius = self._pointer_size * rect.width() / 2
        cx = rect.x() + self._pointer[0] * rect.width()
        cy = rect.y() + self._pointer[1] * rect.height()
        painter.setPen(QPen(self._pointer_color.darker(150), 1))
        painter.setBrush(self._pointer_color)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

    # ---------------------------------------------------------------------- events

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # Leading + trailing edge debounce: the first resize of a burst still requests
        # immediately; subsequent ones within RESIZE_DEBOUNCE_MS only paint the fallback.
        if self._resize_timer.isActive():
            self._suppress_requests = True
        self._resize_timer.start()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if not self._interactive:
            event.ignore()
            return
        pos = event.position()
        if self._draw_mode:
            if self._drawing:
                rect = self.slide_rect()
                if not rect.isEmpty():
                    x, y = self._normalize(pos, rect)
                    self.stroke_moved.emit(x, y)
            event.accept()
            return
        rect = self.slide_rect()
        if rect.isEmpty() or not rect.contains(pos):
            self.pointer_moved.emit(None)
        else:
            self.pointer_moved.emit(self._normalize(pos, rect))
        self._update_cursor(pos)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if not self._interactive:
            event.ignore()
            return
        pos = event.position()
        if self._draw_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                rect = self.slide_rect()
                if not rect.isEmpty():
                    self._drawing = True
                    x, y = self._normalize(pos, rect)
                    self.stroke_started.emit(x, y)
            event.accept()
            return
        self._press_link = self._link_at(pos)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if not self._interactive:
            event.ignore()
            return
        if self._draw_mode:
            if self._drawing and event.button() == Qt.MouseButton.LeftButton:
                self._drawing = False
                self.stroke_ended.emit()
            event.accept()
            return
        link = self._link_at(event.position())
        pressed = self._press_link
        self._press_link = None
        if link is not None and link is pressed and event.button() == Qt.MouseButton.LeftButton:
            self.link_activated.emit(link)
        else:
            self.clicked.emit(event.button())
        event.accept()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._interactive and not self._draw_mode:
            self.pointer_moved.emit(None)
            self._update_cursor(None)
