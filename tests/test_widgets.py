"""Tests for prez.widgets (SlideView, OverviewWidget, NotesPane).

Most tests use an in-process ``FakeRenderService`` and ``FakeDoc`` so they do not
depend on the render service or on fixture PDFs. The integration tests at the bottom
use the real ``RenderService`` and the ``deck_doc``/``notes_doc`` fixtures and skip
when those are not available yet.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from typing import NamedTuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent  # noqa: E402

from prez.document import Link, Region  # noqa: E402
from prez.widgets import NotesPane, OverviewWidget, SlideView  # noqa: E402
from prez.widgets.notes_view import notes_path_for  # noqa: E402
from prez.widgets.slide_view import parse_color  # noqa: E402

FILL = QColor(40, 120, 200)  # colour the fake renderer paints every slide with


# --------------------------------------------------------------------------- fakes


class FakeKey(NamedTuple):
    slide: int
    region: Region
    width: int
    height: int


@dataclass
class FakeDoc:
    slide_count: int = 6
    aspect_ratio: float = 16 / 9
    notes: bool = False
    path: str = "/nonexistent/deck.pdf"
    labels: list[str] = field(default_factory=list)
    links: list[list[Link]] = field(default_factory=list)
    annotations: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.labels:
            self.labels = [str(i + 1) for i in range(self.slide_count)]
        if not self.links:
            self.links = [[] for _ in range(self.slide_count)]
        if not self.annotations:
            self.annotations = [[] for _ in range(self.slide_count)]

    def has_notes(self) -> bool:
        return self.notes

    def aspect(self, slide: int, region: Region) -> float:
        if region == Region.NOTES and not self.notes:
            raise ValueError("no notes")
        return self.aspect_ratio


@dataclass
class FakeStroke:
    color: str
    width: float
    points: list[tuple[float, float]]
    eraser: bool = False


class FakeRenderService(QObject):
    """Synchronous stand-in for prez.render.RenderService."""

    rendered = Signal(object)

    def __init__(self, doc: FakeDoc | None = None, deferred: bool = False) -> None:
        super().__init__()
        self.doc = doc
        self.deferred = deferred
        self.cache: dict[FakeKey, QPixmap] = {}
        self.requests: list[tuple[FakeKey, int]] = []
        self.pending: list[FakeKey] = []

    def set_document(self, doc: FakeDoc) -> None:
        self.doc = doc
        self.cache.clear()

    @staticmethod
    def fit(doc: FakeDoc, slide: int, region: Region, box_w: int, box_h: int) -> tuple[int, int]:
        aspect = doc.aspect(slide, region)
        if box_w / max(box_h, 1) > aspect:
            h = box_h
            w = int(box_h * aspect)
        else:
            w = box_w
            h = int(box_w / aspect)
        return max(1, w), max(1, h)

    def key_for(self, slide: int, region: Region, box_w: int, box_h: int) -> FakeKey:
        assert self.doc is not None
        w, h = self.fit(self.doc, slide, region, box_w, box_h)
        return FakeKey(slide, region, w, h)

    def get(self, key: FakeKey) -> QPixmap | None:
        return self.cache.get(key)

    def nearest(self, slide: int, region: Region) -> QPixmap | None:
        best = None
        for key, pix in self.cache.items():
            if (
                key.slide == slide
                and key.region == region
                and (best is None or key.width * key.height > best[0])
            ):
                best = (key.width * key.height, pix)
        return best[1] if best else None

    def request(self, key: FakeKey, priority: int = 0) -> None:
        if key in self.cache:
            return
        self.requests.append((key, priority))
        if key in self.pending:
            return
        self.pending.append(key)
        if not self.deferred:
            self.flush()

    def cancel_below(self, priority: int) -> None:
        pass

    def clear(self) -> None:
        self.cache.clear()

    def flush(self) -> None:
        """Render every pending key now and emit ``rendered``."""
        pending, self.pending = self.pending, []
        for key in pending:
            pix = QPixmap(key.width, key.height)
            pix.fill(FILL)
            self.cache[key] = pix
            self.rendered.emit(key)


# ------------------------------------------------------------------------- helpers


def pixel(widget, x: int, y: int) -> QColor:
    return widget.grab().toImage().pixelColor(x, y)


def mouse_event(
    kind: QEvent.Type,
    x: float,
    y: float,
    button=Qt.MouseButton.NoButton,
    buttons=Qt.MouseButton.NoButton,
) -> QMouseEvent:
    pos = QPointF(x, y)
    return QMouseEvent(kind, pos, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)


def send(widget, event) -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(widget, event)


def settle(qtbot, ms: int = 60) -> None:
    """Let the resize debounce timer fire."""
    qtbot.wait(ms)


@pytest.fixture
def doc() -> FakeDoc:
    link = Link(rect=(0.1, 0.1, 0.3, 0.3), target=3)
    d = FakeDoc()
    d.links[0] = [link]
    return d


@pytest.fixture
def view(qtbot, doc):
    svc = FakeRenderService(doc)
    v = SlideView(svc, Region.SLIDE)
    qtbot.addWidget(v)
    v.set_document(doc)
    v.set_slide(0)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    settle(qtbot)
    return v


def svc_of(view: SlideView) -> FakeRenderService:
    return view._render  # noqa: SLF001 (test access)


# ------------------------------------------------------------------------ SlideView


def test_parse_color_rgba_reorders_alpha():
    c = parse_color("#ff000080")
    assert (c.red(), c.green(), c.blue(), c.alpha()) == (255, 0, 0, 0x80)
    c2 = parse_color("#00ff00")
    assert (c2.green(), c2.alpha()) == (255, 255)
    assert parse_color("not-a-colour", default="#0000ff").blue() == 255


def test_slide_rect_letterbox(view):
    rect = view.slide_rect()
    assert rect.width() == pytest.approx(400)
    assert rect.height() == pytest.approx(225)
    assert rect.x() == pytest.approx(0)
    assert rect.y() == pytest.approx(37.5)

    view.resize(200, 600)
    rect = view.slide_rect()
    assert rect.width() == pytest.approx(200)
    assert rect.height() == pytest.approx(112.5)
    assert rect.y() == pytest.approx((600 - 112.5) / 2)


def test_slide_rect_empty_without_slide(qtbot, doc):
    v = SlideView(FakeRenderService(doc), Region.SLIDE)
    qtbot.addWidget(v)
    v.resize(400, 300)
    assert v.slide_rect().isEmpty()
    assert v.current_key() is None
    v.set_document(doc)
    assert v.current_key() is None  # no slide yet
    v.set_slide(99)  # out of range
    assert v.current_key() is None


def test_current_key_matches_size(view):
    key = view.current_key()
    assert key == FakeKey(0, Region.SLIDE, 400, 225)


def test_paint_requests_exact_key_then_paints_after_rendered(qtbot, doc):
    svc = FakeRenderService(doc, deferred=True)
    v = SlideView(svc, Region.SLIDE)
    qtbot.addWidget(v)
    v.set_document(doc)
    v.set_slide(0)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    settle(qtbot)

    assert pixel(v, 200, 150) == QColor(0, 0, 0)  # nothing cached yet: black
    assert (FakeKey(0, Region.SLIDE, 400, 225), 0) in svc.requests

    updates: list[int] = []
    v.update = lambda *a: updates.append(1)  # type: ignore[method-assign]
    svc.flush()  # emits rendered → view.update()
    assert updates
    del v.update
    assert pixel(v, 200, 150) == FILL
    assert pixel(v, 200, 10) == QColor(0, 0, 0)  # letterbox band stays black


def test_rendered_for_other_slide_does_not_repaint(view):
    svc = svc_of(view)
    updates: list[int] = []
    view.update = lambda *a: updates.append(1)  # type: ignore[method-assign]
    svc.rendered.emit(FakeKey(5, Region.SLIDE, 10, 10))
    svc.rendered.emit(FakeKey(0, Region.NOTES, 10, 10))
    assert not updates
    svc.rendered.emit(FakeKey(0, Region.SLIDE, 10, 10))
    assert len(updates) == 1
    del view.update


def test_nearest_is_drawn_scaled_while_exact_renders(qtbot, doc):
    svc = FakeRenderService(doc, deferred=True)
    # Prime the cache with a small pixmap only, before the view ever paints.
    small = FakeKey(0, Region.SLIDE, 160, 90)
    svc.request(small, 1)
    svc.flush()
    svc.requests.clear()
    v = SlideView(svc, Region.SLIDE)
    qtbot.addWidget(v)
    v.set_document(doc)
    v.set_slide(0)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    settle(qtbot)
    assert pixel(v, 200, 150) == FILL  # scaled fallback drawn
    assert pixel(v, 200, 10) == QColor(0, 0, 0)
    assert any(k == FakeKey(0, Region.SLIDE, 400, 225) for k, _ in svc.requests)
    assert svc.get(FakeKey(0, Region.SLIDE, 400, 225)) is None  # still pending


def test_blank_paints_solid_and_requests_nothing(view):
    svc = svc_of(view)
    svc.requests.clear()
    view.set_blank("white")
    assert pixel(view, 200, 150) == QColor(255, 255, 255)
    view.set_blank("black")
    assert pixel(view, 200, 150) == QColor(0, 0, 0)
    assert not svc.requests
    view.set_blank(None)
    assert pixel(view, 200, 150) == FILL


def test_request_priority_is_used(qtbot, doc):
    svc = FakeRenderService(doc, deferred=True)
    v = SlideView(svc, Region.SLIDE)
    qtbot.addWidget(v)
    v.request_priority = 1
    v.set_document(doc)
    v.set_slide(2)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    settle(qtbot)
    v.grab()
    assert svc.requests and svc.requests[-1][1] == 1
    assert svc.requests[-1][0].slide == 2


def test_resize_requests_new_key_after_debounce(qtbot, view):
    svc = svc_of(view)
    svc.requests.clear()
    view.resize(800, 600)
    settle(qtbot)
    view.grab()
    assert any(k == FakeKey(0, Region.SLIDE, 800, 450) for k, _ in svc.requests)


def test_link_hit_test_emits_link_activated(qtbot, view, doc):
    view.set_interactive(True)
    link = doc.links[0][0]
    # normalized (0.2, 0.2) inside the link rect → widget coords
    rect = view.slide_rect()
    inside = QPoint(int(rect.x() + 0.2 * rect.width()), int(rect.y() + 0.2 * rect.height()))
    outside = QPoint(int(rect.x() + 0.7 * rect.width()), int(rect.y() + 0.7 * rect.height()))

    with qtbot.waitSignal(view.link_activated, timeout=1000) as blocker:
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=inside)
    assert blocker.args == [link]

    with qtbot.waitSignal(view.clicked, timeout=1000) as blocker:
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=outside)
    assert blocker.args == [Qt.MouseButton.LeftButton]

    with qtbot.waitSignal(view.clicked, timeout=1000) as blocker:
        qtbot.mouseClick(view, Qt.MouseButton.RightButton, pos=inside)
    assert blocker.args == [Qt.MouseButton.RightButton]


def test_link_hover_sets_hand_cursor(view):
    view.set_interactive(True)
    rect = view.slide_rect()
    send(
        view,
        mouse_event(
            QEvent.Type.MouseMove, rect.x() + 0.2 * rect.width(), rect.y() + 0.2 * rect.height()
        ),
    )
    assert view.cursor().shape() == Qt.CursorShape.PointingHandCursor
    send(
        view,
        mouse_event(
            QEvent.Type.MouseMove, rect.x() + 0.8 * rect.width(), rect.y() + 0.8 * rect.height()
        ),
    )
    assert view.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_non_interactive_view_emits_nothing(qtbot, view):
    received: list = []
    view.pointer_moved.connect(received.append)
    view.clicked.connect(received.append)
    send(view, mouse_event(QEvent.Type.MouseMove, 200, 150))
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    assert received == []


def test_pointer_moved_normalized_and_none_on_leave(view):
    view.set_interactive(True)
    received: list = []
    view.pointer_moved.connect(received.append)
    send(view, mouse_event(QEvent.Type.MouseMove, 200, 150))
    assert received[-1] == pytest.approx((0.5, 0.5))
    send(view, mouse_event(QEvent.Type.MouseMove, 100, 10))  # in the letterbox band
    assert received[-1] is None
    send(view, mouse_event(QEvent.Type.MouseMove, 0, 37.5))
    assert received[-1] == pytest.approx((0.0, 0.0))
    send(view, QEvent(QEvent.Type.Leave))
    assert received[-1] is None


def test_draw_mode_emits_stroke_signals(view):
    view.set_interactive(True)
    view.set_draw_mode(True)
    assert view.cursor().shape() == Qt.CursorShape.CrossCursor
    started: list = []
    moved: list = []
    ended: list = []
    pointer: list = []
    view.stroke_started.connect(lambda x, y: started.append((x, y)))
    view.stroke_moved.connect(lambda x, y: moved.append((x, y)))
    view.stroke_ended.connect(lambda: ended.append(1))
    view.pointer_moved.connect(pointer.append)
    view.clicked.connect(pointer.append)

    left = Qt.MouseButton.LeftButton
    send(view, mouse_event(QEvent.Type.MouseButtonPress, 200, 150, left, left))
    send(view, mouse_event(QEvent.Type.MouseMove, 300, 150, Qt.MouseButton.NoButton, left))
    send(view, mouse_event(QEvent.Type.MouseButtonRelease, 300, 150, left, Qt.MouseButton.NoButton))
    assert started == [pytest.approx((0.5, 0.5))]
    assert moved == [pytest.approx((0.75, 0.5))]
    assert ended == [1]
    assert pointer == []  # no pointer / click signals in draw mode

    # Disabling draw mode mid-stroke ends it.
    send(view, mouse_event(QEvent.Type.MouseButtonPress, 200, 150, left, left))
    view.set_draw_mode(False)
    assert ended == [1, 1]
    assert view.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_strokes_are_painted_except_eraser(view):
    view.set_strokes(
        [
            FakeStroke(color="#ff0000", width=0.1, points=[(0.2, 0.5), (0.8, 0.5)]),
            FakeStroke(color="#00ff00", width=0.1, points=[(0.5, 0.1), (0.5, 0.9)], eraser=True),
        ]
    )
    c = pixel(view, 200, 150)
    assert c.red() > 200 and c.green() < 60 and c.blue() < 60
    # Eraser stroke (green) is not painted: a point only on its path keeps the fill.
    c2 = pixel(view, 200, int(37.5 + 0.15 * 225))
    assert c2 == FILL


def test_single_point_stroke_draws_a_dot(view):
    view.set_strokes([FakeStroke(color="#ffff00", width=0.1, points=[(0.5, 0.5)])])
    c = pixel(view, 200, 150)
    assert c.red() > 200 and c.green() > 200 and c.blue() < 60


def test_translucent_stroke_color(view):
    view.set_strokes([FakeStroke(color="#ff000080", width=0.1, points=[(0.2, 0.5), (0.8, 0.5)])])
    c = pixel(view, 200, 150)
    # 50 % red over FILL(40,120,200): red up, blue down, neither saturated.
    assert 120 < c.red() < 200 and 70 < c.blue() < 150


def test_pointer_is_painted(view):
    view.set_pointer_style("#00ff00", 0.1)
    view.set_pointer((0.5, 0.5))
    c = pixel(view, 200, 150)
    assert c.green() > 200 and c.red() < 60
    assert pixel(view, 100, 150) == FILL
    view.set_pointer(None)
    assert pixel(view, 200, 150) == FILL


def test_notes_region_without_notes_paints_black(qtbot, doc):
    svc = FakeRenderService(doc)
    v = SlideView(svc, Region.NOTES)
    qtbot.addWidget(v)
    v.set_document(doc)
    v.set_slide(0)
    v.resize(400, 300)
    v.show()
    qtbot.waitExposed(v)
    settle(qtbot)
    assert v.current_key() is None
    assert pixel(v, 200, 150) == QColor(0, 0, 0)
    assert not svc.requests


# ------------------------------------------------------------------- OverviewWidget


@pytest.fixture
def overview(qtbot):
    d = FakeDoc(slide_count=10, labels=["1", "2", "2", "3", "4", "5", "6", "7", "8", "9"])
    svc = FakeRenderService(d)
    ov = OverviewWidget(svc)
    qtbot.addWidget(ov)
    ov.set_document(d)
    ov.resize(700, 400)
    ov.show()
    qtbot.waitExposed(ov)
    return ov


def test_overview_columns(qtbot, overview):
    assert overview.columns() == 3
    overview.resize(300, 400)
    qtbot.wait(10)
    assert overview.columns() == 2
    overview.resize(1000, 400)
    qtbot.wait(10)
    assert overview.columns() == 4


def test_overview_requests_thumbnails_at_priority_3(overview):
    svc: FakeRenderService = overview.grid._render  # noqa: SLF001
    overview.grid.grab()
    assert svc.requests
    assert all(p == 3 for _, p in svc.requests)
    assert all(k.region == Region.SLIDE and k.width == 200 for k, _ in svc.requests)
    # Visible cells only: 3 columns × ~2 rows fit in 400 px.
    assert {k.slide for k, _ in svc.requests} <= set(range(10))


def test_overview_click_activates(qtbot, overview):
    target = overview.thumb_rect(4).center()
    with qtbot.waitSignal(overview.activated, timeout=1000) as blocker:
        qtbot.mouseClick(overview.grid, Qt.MouseButton.LeftButton, pos=target)
    assert blocker.args == [4]
    assert overview.current() == 4


def test_overview_keyboard(qtbot, overview):
    overview.setFocus()
    cols = overview.columns()
    assert overview.current() == 0
    qtbot.keyClick(overview, Qt.Key.Key_Right)
    assert overview.current() == 1
    qtbot.keyClick(overview, Qt.Key.Key_Down)
    assert overview.current() == 1 + cols
    qtbot.keyClick(overview, Qt.Key.Key_Up)
    qtbot.keyClick(overview, Qt.Key.Key_Left)
    assert overview.current() == 0
    qtbot.keyClick(overview, Qt.Key.Key_Left)  # clamps
    assert overview.current() == 0
    qtbot.keyClick(overview, Qt.Key.Key_End)
    assert overview.current() == 9
    with qtbot.waitSignal(overview.activated, timeout=1000) as blocker:
        qtbot.keyClick(overview, Qt.Key.Key_Return)
    assert blocker.args == [9]
    with qtbot.waitSignal(overview.closed, timeout=1000):
        qtbot.keyClick(overview, Qt.Key.Key_Escape)


def test_overview_set_current_scrolls(qtbot):
    d = FakeDoc(slide_count=60)
    ov = OverviewWidget(FakeRenderService(d))
    qtbot.addWidget(ov)
    ov.set_document(d)
    ov.resize(480, 300)
    ov.show()
    qtbot.waitExposed(ov)
    bar = ov.scroll_area().verticalScrollBar()
    assert bar.value() == 0
    ov.set_current(59)
    assert bar.value() > 0
    cell = ov.grid.cell_rect(59)
    viewport = ov.scroll_area().viewport()
    top_left = ov.grid.mapTo(viewport, cell.topLeft())
    assert viewport.rect().contains(QRect(top_left, cell.size()))
    ov.set_current(0)
    assert bar.value() == 0


def test_overview_paints_labels_and_repaints_on_rendered(overview):
    svc: FakeRenderService = overview.grid._render  # noqa: SLF001
    overview.grid.grab()  # requests + renders synchronously
    trect = overview.thumb_rect(0)
    assert pixel(overview.grid, trect.center().x(), trect.center().y()) == FILL
    updates: list = []
    overview.grid.update = lambda *a: updates.append(a)  # type: ignore[method-assign]
    svc.rendered.emit(FakeKey(0, Region.SLIDE, 200, 112))
    assert updates
    del overview.grid.update


# ----------------------------------------------------------------------- NotesPane


@pytest.fixture
def text_doc(tmp_path) -> FakeDoc:
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    d = FakeDoc(slide_count=4, path=str(pdf))
    d.annotations[0] = ["Note A", "Note B"]
    return d


def test_notes_path():
    assert notes_path_for("/x/deck.pdf") == "/x/deck.pdf.notes.json"


def test_notes_pane_text_mode_shows_annotations(qtbot, text_doc):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.set_slide(0)
    pane.show()
    qtbot.waitExposed(pane)
    assert pane.current_page() == NotesPane.PAGE_TEXT
    assert pane.slide_view() is None
    assert pane.annotations_view().isVisible()
    assert pane.annotations_view().toPlainText() == "Note A\n\nNote B"
    pane.set_slide(1)
    assert not pane.annotations_view().isVisible()
    assert pane.editor().font().pointSize() >= 14


def test_notes_pane_saves_and_loads(qtbot, text_doc):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.set_slide(1)
    pane.editor().setPlainText("hello")
    pane.set_slide(2)
    assert pane.editor().toPlainText() == ""
    pane.editor().setPlainText("world")
    pane.set_slide(1)
    assert pane.editor().toPlainText() == "hello"
    path = pane.notes_path()
    assert path == notes_path_for(text_doc.path)
    assert not os.path.exists(path)  # debounced, not yet written
    pane.flush()
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"1": "hello", "2": "world"}

    other = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(other)
    other.set_document(text_doc)
    other.set_slide(2)
    assert other.editor().toPlainText() == "world"
    other.set_slide(1)
    assert other.editor().toPlainText() == "hello"
    # Clearing a note removes its key.
    other.editor().setPlainText("")
    other.flush()
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"2": "world"}


def test_notes_pane_debounced_autosave(qtbot, text_doc):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.set_slide(0)
    pane.editor().setPlainText("auto")
    path = pane.notes_path()
    assert not os.path.exists(path)
    qtbot.waitUntil(lambda: os.path.exists(path), timeout=3000)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"0": "auto"}


def test_notes_pane_set_document_flushes_previous(qtbot, text_doc, tmp_path):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.set_slide(3)
    pane.editor().setPlainText("bye")
    other = FakeDoc(slide_count=2, path=str(tmp_path / "other.pdf"))
    pane.set_document(other)
    assert pane.editor().toPlainText() == ""
    with open(notes_path_for(text_doc.path), encoding="utf-8") as fh:
        assert json.load(fh) == {"3": "bye"}


def test_notes_pane_ignores_corrupt_json(qtbot, text_doc, caplog):
    with open(notes_path_for(text_doc.path), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    with caplog.at_level("WARNING", logger="prez.widgets.notes_view"):
        pane.set_document(text_doc)
    assert pane.notes() == {}
    assert "Cannot read notes file" in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_notes_pane_readonly_dir_logs_warning(qtbot, text_doc, tmp_path, caplog):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.set_slide(0)
    pane.editor().setPlainText("cannot save")
    mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with caplog.at_level("WARNING", logger="prez.widgets.notes_view"):
            pane.flush()
            pane.editor().setPlainText("still cannot")
            pane.flush()
    finally:
        os.chmod(tmp_path, mode)
    assert caplog.text.count("Cannot save notes") == 1  # warned once per document
    assert not os.path.exists(pane.notes_path())


def test_notes_pane_slide_view_mode(qtbot):
    d = FakeDoc(slide_count=5, notes=True, aspect_ratio=16 / 9)
    svc = FakeRenderService(d)
    pane = NotesPane(svc)
    qtbot.addWidget(pane)
    pane.set_document(d)
    pane.set_slide(2)
    pane.resize(400, 300)
    pane.show()
    qtbot.waitExposed(pane)
    assert pane.current_page() == NotesPane.PAGE_SLIDE
    sv = pane.slide_view()
    assert isinstance(sv, SlideView)
    assert sv.region == Region.NOTES
    assert sv.slide == 2
    assert not sv.is_interactive()
    settle(qtbot)
    sv.grab()
    assert any(k.region == Region.NOTES and k.slide == 2 for k, _ in svc.requests)


def test_notes_pane_ctrl_wheel_zooms(qtbot, text_doc):
    pane = NotesPane(FakeRenderService(text_doc))
    qtbot.addWidget(pane)
    pane.set_document(text_doc)
    pane.show()
    qtbot.waitExposed(pane)
    before = pane.font_point_size()

    def wheel(delta: int) -> QWheelEvent:
        pos = QPointF(10, 10)
        return QWheelEvent(
            pos,
            pos,
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    send(pane.editor().viewport(), wheel(120))
    assert pane.font_point_size() == before + 1
    assert pane.editor().font().pointSize() == before + 1
    assert pane.annotations_view().font().pointSize() == before + 1
    send(pane.editor().viewport(), wheel(-120))
    send(pane.editor().viewport(), wheel(-120))
    assert pane.font_point_size() == before - 1


# --------------------------------------------------- integration with real services


@pytest.fixture
def real_deck(request):
    try:
        return request.getfixturevalue("deck_doc")
    except pytest.FixtureLookupError:
        pytest.skip("deck_doc fixture not available yet")


@pytest.fixture
def real_notes(request):
    try:
        return request.getfixturevalue("notes_doc")
    except pytest.FixtureLookupError:
        pytest.skip("notes_doc fixture not available yet")


@pytest.fixture
def real_render():
    mod = pytest.importorskip("prez.render")
    return mod.RenderService


def test_real_slide_view_renders(qtbot, real_deck, real_render):
    svc = real_render(real_deck)
    svc.start()
    try:
        v = SlideView(svc, Region.SLIDE)
        qtbot.addWidget(v)
        v.set_document(real_deck)
        v.set_slide(0)
        v.resize(480, 270)
        v.show()
        qtbot.waitExposed(v)
        settle(qtbot)
        key = v.current_key()
        assert key is not None and (key.width, key.height) == (480, 270)
        v.grab()  # requests the exact key if the first paint did not already
        qtbot.waitUntil(lambda: svc.get(key) is not None, timeout=5000)
        pix = svc.get(key)
        assert (pix.width(), pix.height()) == (480, 270)
        img = v.grab().toImage()
        src = pix.toImage()
        # Exact-size hit is blitted 1:1: the widget shows the pixmap's own pixels.
        for x, y in ((5, 5), (240, 135), (474, 264)):
            assert img.pixelColor(x, y) == src.pixelColor(x, y)
        assert img.pixelColor(5, 5) != QColor(0, 0, 0)
    finally:
        svc.stop()


def test_real_overview_thumbnails(qtbot, real_deck, real_render):
    svc = real_render(real_deck)
    svc.start()
    try:
        ov = OverviewWidget(svc)
        qtbot.addWidget(ov)
        ov.set_document(real_deck)
        ov.resize(700, 500)
        ov.show()
        qtbot.waitExposed(ov)
        with qtbot.waitSignal(svc.rendered, timeout=5000):
            ov.grid.grab()
        dpr = ov.grid.devicePixelRatioF()
        trect = ov.thumb_rect(0)
        key = svc.key_for(0, Region.SLIDE, int(trect.width() * dpr), int(trect.height() * dpr))
        qtbot.waitUntil(lambda: svc.get(key) is not None, timeout=5000)
        assert svc.get(key).width() <= 300
    finally:
        svc.stop()


def test_real_notes_pane_picks_slide_view(qtbot, real_notes, real_render):
    svc = real_render(real_notes)
    svc.start()
    try:
        pane = NotesPane(svc)
        qtbot.addWidget(pane)
        pane.set_document(real_notes)
        pane.set_slide(0)
        assert pane.current_page() == NotesPane.PAGE_SLIDE
        assert pane.slide_view() is not None
    finally:
        svc.stop()
