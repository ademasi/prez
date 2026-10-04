"""OverviewWidget: a scrollable, custom-painted grid of slide thumbnails."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from prez.document import Region

if TYPE_CHECKING:
    from prez.document import DocumentInfo
    from prez.render import RenderKey, RenderService

log = logging.getLogger(__name__)

THUMB_W = 200  # logical px
CELL_W = 220  # logical px per column
PAD = 10
LABEL_H = 22
MARGIN = 10
THUMB_PRIORITY = 3

BG = QColor("#202124")
TEXT = QColor("#e8eaed")
HIGHLIGHT = QColor("#8ab4f8")
HOVER = QColor("#9aa0a6")
PLACEHOLDER = QColor("#3c4043")


class _Grid(QWidget):
    """Inner widget: owns the layout math and paints the thumbnails."""

    activated = Signal(int)
    current_changed = Signal(int)

    def __init__(self, render: RenderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._render = render
        self._doc: DocumentInfo | None = None
        self._count = 0
        self._current = 0
        self._hover = -1
        self._press = -1
        self._cols = 2
        self._cell_w = CELL_W
        self._thumb_h: list[int] = []
        self._thumb_h_max = round(THUMB_W * 9 / 16)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumWidth(2 * CELL_W + 2 * MARGIN)
        render.rendered.connect(self._on_rendered)
        self._relayout()

    # ------------------------------------------------------------------- model

    def set_document(self, doc: DocumentInfo | None) -> None:
        self._doc = doc
        self._count = doc.slide_count if doc is not None else 0
        heights: list[int] = []
        for i in range(self._count):
            try:
                aspect = float(doc.aspect(i, Region.SLIDE))  # type: ignore[union-attr]
            except (ValueError, IndexError, ZeroDivisionError):
                aspect = 16 / 9
            if aspect <= 0:
                aspect = 16 / 9
            # ceil keeps the box at least as tall as the slide so fit() keeps the width.
            heights.append(max(8, math.ceil(THUMB_W / aspect)))
        self._thumb_h = heights
        self._thumb_h_max = max(heights) if heights else round(THUMB_W * 9 / 16)
        self._current = min(self._current, max(0, self._count - 1))
        self._hover = -1
        self._relayout()
        self.update()

    def count(self) -> int:
        return self._count

    def current(self) -> int:
        return self._current

    def set_current(self, slide: int) -> None:
        if self._count == 0:
            self._current = 0
            return
        slide = min(max(int(slide), 0), self._count - 1)
        if slide == self._current:
            return
        old = self._current
        self._current = slide
        self.update(self.cell_rect(old))
        self.update(self.cell_rect(slide))
        self.current_changed.emit(slide)

    def columns(self) -> int:
        return self._cols

    def cell_height(self) -> int:
        return PAD + self._thumb_h_max + LABEL_H + PAD

    def rows(self) -> int:
        return math.ceil(self._count / self._cols) if self._count else 0

    # ---------------------------------------------------------------- geometry

    def cell_rect(self, index: int) -> QRect:
        row, col = divmod(index, self._cols)
        return QRect(
            MARGIN + col * self._cell_w,
            MARGIN + row * self.cell_height(),
            self._cell_w,
            self.cell_height(),
        )

    def thumb_rect(self, index: int) -> QRect:
        cell = self.cell_rect(index)
        h = self._thumb_h[index] if index < len(self._thumb_h) else self._thumb_h_max
        x = cell.x() + (cell.width() - THUMB_W) // 2
        y = cell.y() + PAD + (self._thumb_h_max - h) // 2
        return QRect(x, y, THUMB_W, h)

    def index_at(self, pos: QPoint) -> int:
        if self._count == 0:
            return -1
        x = pos.x() - MARGIN
        y = pos.y() - MARGIN
        if x < 0 or y < 0:
            return -1
        col = x // self._cell_w
        row = y // self.cell_height()
        if col >= self._cols:
            return -1
        index = row * self._cols + col
        return index if 0 <= index < self._count else -1

    def _relayout(self) -> None:
        width = max(self.width(), self.minimumWidth())
        self._cols = max(2, (width - 2 * MARGIN) // CELL_W)
        self._cell_w = max(CELL_W, (width - 2 * MARGIN) // self._cols)
        total_h = 2 * MARGIN + self.rows() * self.cell_height()
        self.setMinimumHeight(total_h)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self.minimumWidth(), self.minimumHeight())

    # ----------------------------------------------------------------- painting

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        try:
            painter.fillRect(event.rect(), BG)
            if self._count == 0:
                return
            cell_h = self.cell_height()
            first_row = max(0, (event.rect().top() - MARGIN) // cell_h)
            last_row = min(self.rows() - 1, (event.rect().bottom() - MARGIN) // cell_h)
            for row in range(first_row, last_row + 1):
                for col in range(self._cols):
                    index = row * self._cols + col
                    if index >= self._count:
                        break
                    self._paint_cell(painter, index)
        finally:
            painter.end()

    def _paint_cell(self, painter: QPainter, index: int) -> None:
        trect = self.thumb_rect(index)
        dpr = self.devicePixelRatioF()
        key = self._render.key_for(
            index, Region.SLIDE, int(trect.width() * dpr), int(trect.height() * dpr)
        )
        pix = self._render.get(key)
        if pix is not None:
            if pix.devicePixelRatio() != dpr:
                pix.setDevicePixelRatio(dpr)
            painter.drawPixmap(trect.topLeft(), pix)
        else:
            self._render.request(key, THUMB_PRIORITY)
            near = self._render.nearest(index, Region.SLIDE)
            if near is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(QRectF(trect), near, QRectF(0, 0, near.width(), near.height()))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            else:
                painter.fillRect(trect, PLACEHOLDER)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        if index == self._current:
            painter.setPen(QPen(HIGHLIGHT, 3))
            painter.drawRect(trect.adjusted(-2, -2, 1, 1))
        elif index == self._hover:
            painter.setPen(QPen(HOVER, 1))
            painter.drawRect(trect.adjusted(-2, -2, 1, 1))

        label = self._label(index)
        cell = self.cell_rect(index)
        painter.setPen(TEXT)
        label_rect = QRect(cell.x(), trect.bottom() + 3, cell.width(), LABEL_H)
        painter.drawText(
            label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label
        )

    def _label(self, index: int) -> str:
        labels = getattr(self._doc, "labels", None)
        if labels and index < len(labels) and labels[index]:
            return str(labels[index])
        return str(index + 1)

    # ------------------------------------------------------------------- slots

    def _on_rendered(self, key: RenderKey) -> None:
        if key.region != Region.SLIDE or not (0 <= key.slide < self._count):
            return
        cell = self.cell_rect(key.slide)
        if cell.intersects(self.visibleRegion().boundingRect()):
            self.update(cell)

    # ------------------------------------------------------------------ events

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._relayout()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        index = self.index_at(event.position().toPoint())
        if index != self._hover:
            old = self._hover
            self._hover = index
            if old >= 0:
                self.update(self.cell_rect(old))
            if index >= 0:
                self.update(self.cell_rect(index))
        event.accept()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        if self._hover >= 0:
            old = self._hover
            self._hover = -1
            self.update(self.cell_rect(old))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press = self.index_at(event.position().toPoint())
        if self._press >= 0:
            self.set_current(self._press)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        index = self.index_at(event.position().toPoint())
        pressed = self._press
        self._press = -1
        if index >= 0 and index == pressed:
            self.activated.emit(index)
        event.accept()


class OverviewWidget(QWidget):
    """Scrollable grid of slide thumbnails; keyboard and mouse pick a slide."""

    activated = Signal(int)  # slide chosen
    closed = Signal()  # Escape

    def __init__(self, render: RenderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = _Grid(render)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Always-on vertical bar avoids the width/columns/height oscillation that
        # "as needed" causes when a relayout toggles the bar.
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        viewport = self._scroll.viewport()
        palette = viewport.palette()
        palette.setColor(QPalette.ColorRole.Window, BG)
        viewport.setPalette(palette)
        viewport.setAutoFillBackground(True)
        self._scroll.setWidget(self._grid)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._scroll)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid.activated.connect(self._on_grid_activated)

    # --------------------------------------------------------------------- API

    def set_document(self, doc: DocumentInfo | None) -> None:
        self._grid.set_document(doc)
        self._ensure_visible(self._grid.current())

    def set_current(self, slide: int) -> None:
        self._grid.set_current(slide)
        self._ensure_visible(self._grid.current())

    def current(self) -> int:
        return self._grid.current()

    def count(self) -> int:
        return self._grid.count()

    def columns(self) -> int:
        return self._grid.columns()

    @property
    def grid(self) -> QWidget:
        """The inner painted widget (grid coordinates for ``thumb_rect``)."""
        return self._grid

    def thumb_rect(self, slide: int) -> QRect:
        """Thumbnail rect of ``slide`` in grid-widget coordinates."""
        return self._grid.thumb_rect(slide)

    def scroll_area(self) -> QScrollArea:
        return self._scroll

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(4 * CELL_W + 2 * MARGIN, 600)

    # ----------------------------------------------------------------- helpers

    def _ensure_visible(self, slide: int) -> None:
        """Scroll so the whole cell of ``slide`` is inside the viewport."""
        if self._grid.count() == 0:
            return
        cell = self._grid.cell_rect(slide)
        viewport = self._scroll.viewport()
        vbar = self._scroll.verticalScrollBar()
        hbar = self._scroll.horizontalScrollBar()
        if cell.top() - MARGIN < vbar.value():
            vbar.setValue(cell.top() - MARGIN)
        elif cell.bottom() + 1 + MARGIN > vbar.value() + viewport.height():
            vbar.setValue(cell.bottom() + 1 + MARGIN - viewport.height())
        if cell.left() < hbar.value():
            hbar.setValue(cell.left())
        elif cell.right() + 1 > hbar.value() + viewport.width():
            hbar.setValue(cell.right() + 1 - viewport.width())

    def _on_grid_activated(self, slide: int) -> None:
        self.setFocus()
        self.activated.emit(slide)

    def _rows_per_page(self) -> int:
        return max(1, self._scroll.viewport().height() // self._grid.cell_height())

    # ------------------------------------------------------------------ events

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        key = event.key()
        count = self._grid.count()
        if key == Qt.Key.Key_Escape:
            self.closed.emit()
            event.accept()
            return
        if count == 0:
            super().keyPressEvent(event)
            return
        cur = self._grid.current()
        cols = self._grid.columns()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(cur)
        elif key == Qt.Key.Key_Right:
            self.set_current(cur + 1)
        elif key == Qt.Key.Key_Left:
            self.set_current(cur - 1)
        elif key == Qt.Key.Key_Down:
            self.set_current(cur + cols)
        elif key == Qt.Key.Key_Up:
            self.set_current(cur - cols)
        elif key == Qt.Key.Key_Home:
            self.set_current(0)
        elif key == Qt.Key.Key_End:
            self.set_current(count - 1)
        elif key == Qt.Key.Key_PageDown:
            self.set_current(cur + self._rows_per_page() * cols)
        elif key == Qt.Key.Key_PageUp:
            self.set_current(cur - self._rows_per_page() * cols)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def showEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._ensure_visible(self._grid.current())
