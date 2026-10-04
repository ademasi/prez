"""Tests for prez.render.RenderService.

Most tests use small fakes for the document and the page renderer so they exercise the
threading, queueing and caching logic without fitz or a PDF.  The tests at the end use
the real fixtures from conftest (``deck_doc``, ``notes_doc``) and skip when those are
not available.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QThread  # noqa: E402

from prez.render import RenderKey, RenderService  # noqa: E402

MAIN_THREAD_ID = threading.get_ident()
DEFAULT_MAX_BYTES = 64 * 2**20

# -- fakes ----------------------------------------------------------------------------


class Region(StrEnum):
    """Local stand-in for prez.document.Region: the service only passes it through."""

    FULL = "full"
    SLIDE = "slide"
    NOTES = "notes"


@dataclass(frozen=True)
class RawImage:
    width: int
    height: int
    stride: int
    data: bytes


@dataclass
class FakeDoc:
    """The subset of DocumentInfo that RenderService touches."""

    path: str = "deck.pdf"
    slide_count: int = 6
    size: tuple[float, float] = (960.0, 540.0)

    def region(self, slide: int, region: Region) -> tuple[int, tuple[float, float, float, float]]:
        if not 0 <= slide < self.slide_count:
            raise IndexError(slide)
        if region is Region.NOTES:
            raise ValueError("this document has no notes")
        w, h = self.size
        return slide, (0.0, 0.0, w, h)

    def aspect(self, slide: int, region: Region) -> float:
        w, h = self.size
        return w / h


def page_red(page: int) -> int:
    """Red channel the fake renderer paints page ``page`` with."""
    return (page * 40 + 15) % 256


@dataclass
class FakeRenderer:
    """Records calls and produces a solid-colour RGB888 image of the exact requested size."""

    path: str
    delay: float = 0.0
    gate: threading.Event | None = None  # render() blocks until set
    fail_pages: frozenset[int] = frozenset()
    pad: int = 0  # extra bytes per row, to check that stride is honoured
    calls: list[tuple[int, tuple[float, float, float, float], int, int]] = field(
        default_factory=list
    )
    threads: dict[str, int] = field(default_factory=dict)
    closed: bool = False

    def __post_init__(self) -> None:
        self.threads["init"] = threading.get_ident()

    def render(
        self, page: int, clip: tuple[float, float, float, float], width: int, height: int
    ) -> RawImage:
        self.calls.append((page, clip, width, height))
        self.threads.setdefault("render", threading.get_ident())
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.delay:
            time.sleep(self.delay)
        if page in self.fail_pages:
            raise RuntimeError(f"cannot render page {page}")
        stride = width * 3 + self.pad
        row = bytes((page_red(page), 20, 200)) * width + b"\0" * self.pad
        return RawImage(width, height, stride, row * height)

    def close(self) -> None:
        self.closed = True
        self.threads["close"] = threading.get_ident()


class Factory:
    """Renderer factory handed to RenderService; remembers every renderer it created."""

    def __init__(self, **options: object) -> None:
        self.options = options
        self.created: list[FakeRenderer] = []

    def __call__(self, path: str) -> FakeRenderer:
        renderer = FakeRenderer(path, **self.options)  # type: ignore[arg-type]
        self.created.append(renderer)
        return renderer

    @property
    def last(self) -> FakeRenderer:
        return self.created[-1]

    def render_calls(self) -> int:
        """Renders started so far; safe to poll before the worker created a renderer."""
        return sum(len(r.calls) for r in self.created)


_FAKE = object()  # sentinel: "build a fake factory for me"


@pytest.fixture
def make_service(qapp):
    """Factory fixture; every service it builds is stopped at teardown."""
    services: list[RenderService] = []
    factories: list[Factory] = []

    def _make(
        doc=None,
        *,
        factory=_FAKE,
        max_bytes: int = DEFAULT_MAX_BYTES,
        start: bool = True,
    ) -> RenderService:
        if factory is _FAKE:
            factory = Factory()
        if isinstance(factory, Factory):
            factories.append(factory)
        service = RenderService(
            doc if doc is not None else FakeDoc(), max_bytes=max_bytes, renderer_factory=factory
        )
        services.append(service)
        if start:
            service.start()
        return service

    yield _make

    for factory in factories:  # release any blocked fake render first
        gate = factory.options.get("gate")
        if isinstance(gate, threading.Event):
            gate.set()
    for service in services:
        service.stop()


def wait_for(qtbot, service: RenderService, key: RenderKey, timeout: int = 5000):
    """Context manager: process events until ``rendered`` announces exactly ``key``."""
    return qtbot.waitSignal(service.rendered, timeout=timeout, check_params_cb=lambda k: k == key)


def key_for_slide(slide: int, width: int = 64, height: int = 36) -> RenderKey:
    return RenderKey(slide, Region.SLIDE, width, height)


# -- request / rendered / get ---------------------------------------------------------


def test_request_emits_rendered_and_caches_exact_size(qtbot, make_service):
    factory = Factory()
    service = make_service(factory=factory)
    key = RenderKey(2, Region.SLIDE, 320, 180)

    with wait_for(qtbot, service, key) as blocker:
        service.request(key)

    assert blocker.args == [key]
    pixmap = service.get(key)
    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (320, 180)
    # The renderer was asked for the page and clip the document maps the key to.
    assert factory.last.calls == [(2, (0.0, 0.0, 960.0, 540.0), 320, 180)]
    # Pixel content survived the RGB888 → QImage → QPixmap round trip.
    color = pixmap.toImage().pixelColor(5, 5)
    assert (color.red(), color.green(), color.blue()) == (page_red(2), 20, 200)


def test_padded_stride_is_honoured(qtbot, make_service):
    service = make_service(factory=Factory(pad=7))
    key = RenderKey(1, Region.SLIDE, 50, 30)
    with wait_for(qtbot, service, key):
        service.request(key)
    image = service.get(key).toImage()
    assert (image.width(), image.height()) == (50, 30)
    for x, y in ((0, 0), (49, 29), (25, 15)):
        color = image.pixelColor(x, y)
        assert (color.red(), color.green(), color.blue()) == (page_red(1), 20, 200)


def test_rendered_is_emitted_on_the_main_thread(qtbot, qapp, make_service):
    service = make_service()
    seen: list[tuple[int, bool]] = []
    service.rendered.connect(
        lambda _key: seen.append((threading.get_ident(), QThread.currentThread() is qapp.thread()))
    )
    key = key_for_slide(0)
    with wait_for(qtbot, service, key):
        service.request(key)
    assert seen == [(MAIN_THREAD_ID, True)]


def test_renderer_lives_entirely_on_the_worker_thread(qtbot, make_service):
    factory = Factory()
    service = make_service(factory=factory)
    key = key_for_slide(0)
    with wait_for(qtbot, service, key):
        service.request(key)
    service.stop()

    renderer = factory.last
    assert renderer.closed
    threads = set(renderer.threads.values())
    assert len(threads) == 1, renderer.threads  # created, used and closed on one thread
    assert MAIN_THREAD_ID not in threads


def test_request_of_cached_key_is_a_noop(qtbot, make_service):
    factory = Factory()
    service = make_service(factory=factory)
    key = key_for_slide(0)
    with wait_for(qtbot, service, key):
        service.request(key)

    with qtbot.assertNotEmitted(service.rendered, wait=150):
        service.request(key)
        service.request(key, priority=3)
    assert len(factory.last.calls) == 1
    assert service.pending_count() == 0


def test_pending_and_in_flight_requests_are_deduplicated(qtbot, make_service):
    gate = threading.Event()
    factory = Factory(gate=gate)
    service = make_service(factory=factory)
    key = key_for_slide(0)

    service.request(key, priority=2)
    service.request(key, priority=1)
    service.request(key, priority=0)
    qtbot.waitUntil(lambda: factory.render_calls() == 1, timeout=5000)  # inside render()
    assert service.pending_count() == 0
    service.request(key)  # in flight: must not be queued again

    with wait_for(qtbot, service, key):
        gate.set()
    qtbot.wait(100)
    assert len(factory.last.calls) == 1


def test_get_misses_and_degenerate_keys(make_service):
    service = make_service(start=False)
    assert service.get(key_for_slide(0)) is None
    assert service.nearest(0, Region.SLIDE) is None
    service.request(RenderKey(0, Region.SLIDE, 0, 10))
    service.request(RenderKey(0, Region.SLIDE, 10, -1))
    assert service.pending_count() == 0


# -- LRU cache ------------------------------------------------------------------------


def test_lru_eviction_when_max_bytes_is_small(qtbot, make_service):
    # Room for exactly two 64x64 pixmaps (64*64*4 bytes each).
    service = make_service(max_bytes=2 * 64 * 64 * 4)
    keys = [RenderKey(i, Region.SLIDE, 64, 64) for i in range(3)]

    for key in keys[:2]:
        with wait_for(qtbot, service, key):
            service.request(key)
    assert service.cache_count == 2
    assert service.cache_bytes == 2 * 64 * 64 * 4

    with wait_for(qtbot, service, keys[2]):
        service.request(keys[2])
    assert service.get(keys[0]) is None  # least recently used went first
    assert service.get(keys[1]) is not None
    assert service.get(keys[2]) is not None
    assert service.cache_bytes <= service.max_bytes


def test_get_bumps_lru_order(qtbot, make_service):
    service = make_service(max_bytes=2 * 64 * 64 * 4)
    keys = [RenderKey(i, Region.SLIDE, 64, 64) for i in range(3)]
    for key in keys[:2]:
        with wait_for(qtbot, service, key):
            service.request(key)

    assert service.get(keys[0]) is not None  # keys[0] becomes most recently used
    with wait_for(qtbot, service, keys[2]):
        service.request(keys[2])
    assert service.is_cached(keys[0])
    assert not service.is_cached(keys[1])
    assert service.is_cached(keys[2])


def test_single_entry_larger_than_budget_is_still_kept(qtbot, make_service):
    service = make_service(max_bytes=10)
    key = RenderKey(0, Region.SLIDE, 32, 32)
    with wait_for(qtbot, service, key):
        service.request(key)
    assert service.get(key) is not None
    assert service.cache_count == 1


def test_clear_empties_cache_and_pending(qtbot, make_service):
    service = make_service(start=False)
    service.request(key_for_slide(0))
    service.request(key_for_slide(1), priority=3)
    assert service.pending_count() == 2
    service.clear()
    assert service.pending_count() == 0

    service.start()
    key = key_for_slide(2)
    with wait_for(qtbot, service, key):
        service.request(key)
    service.clear()
    assert service.get(key) is None
    assert service.cache_bytes == 0
    assert service.cache_count == 0


# -- nearest --------------------------------------------------------------------------


def test_nearest_returns_largest_for_slide_and_region(qtbot, make_service):
    service = make_service()
    small = RenderKey(0, Region.SLIDE, 64, 36)
    large = RenderKey(0, Region.SLIDE, 640, 360)
    medium = RenderKey(0, Region.SLIDE, 320, 180)
    other_slide = RenderKey(1, Region.SLIDE, 1280, 720)
    other_region = RenderKey(0, Region.FULL, 1920, 1080)
    for key in (small, large, medium, other_slide, other_region):
        with wait_for(qtbot, service, key):
            service.request(key)

    pixmap = service.nearest(0, Region.SLIDE)
    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (640, 360)
    assert service.nearest(1, Region.SLIDE).width() == 1280
    assert service.nearest(0, Region.FULL).width() == 1920
    assert service.nearest(5, Region.SLIDE) is None
    assert service.nearest(0, Region.NOTES) is None


def test_nearest_does_not_bump_lru(qtbot, make_service):
    service = make_service(max_bytes=2 * 64 * 64 * 4)
    large = RenderKey(0, Region.SLIDE, 64, 64)
    small = RenderKey(0, Region.SLIDE, 32, 32)
    for key in (large, small):  # insertion order: `large` is the least recently used
        with wait_for(qtbot, service, key):
            service.request(key)

    nearest = service.nearest(0, Region.SLIDE)
    assert nearest is not None and nearest.width() == 64  # that is `large`

    # Evicting one entry must take `large`: nearest() did not mark it recently used.
    filler = RenderKey(3, Region.SLIDE, 64, 64)
    with wait_for(qtbot, service, filler):
        service.request(filler)
    assert not service.is_cached(large)
    assert service.is_cached(small)
    assert service.is_cached(filler)


# -- priorities and cancellation ------------------------------------------------------


def test_lower_priority_value_is_rendered_first(qtbot, make_service):
    factory = Factory()
    service = make_service(factory=factory, start=False)
    order: list[RenderKey] = []
    service.rendered.connect(order.append)

    thumbs = [RenderKey(i, Region.SLIDE, 100, 56) for i in (1, 2, 3)]
    for thumb in thumbs:
        service.request(thumb, priority=3)
    current = RenderKey(0, Region.SLIDE, 800, 450)
    service.request(current, priority=0)
    assert service.pending_count() == 4

    service.start()
    qtbot.waitUntil(lambda: len(order) == 4, timeout=5000)
    assert order[0] == current
    assert order[1:] == thumbs  # FIFO within one priority
    assert [c[0] for c in factory.last.calls] == [0, 1, 2, 3]


def test_rerequest_bumps_priority(qtbot, make_service):
    service = make_service(start=False)
    order: list[RenderKey] = []
    service.rendered.connect(order.append)

    late = RenderKey(5, Region.SLIDE, 800, 450)
    service.request(late, priority=3)
    others = [RenderKey(i, Region.SLIDE, 100, 56) for i in (1, 2)]
    for key in others:
        service.request(key, priority=2)
    service.request(late, priority=0)  # same key, more urgent now
    service.request(others[0], priority=3)  # a higher value must NOT demote it
    assert service.pending_count() == 3

    service.start()
    qtbot.waitUntil(lambda: len(order) == 3, timeout=5000)
    assert order == [late, *others]


def test_cancel_below_drops_only_lower_priority_requests(qtbot, make_service):
    factory = Factory()
    service = make_service(factory=factory, start=False)
    keep0 = RenderKey(0, Region.SLIDE, 64, 36)
    keep1 = RenderKey(1, Region.SLIDE, 64, 36)
    drop2 = RenderKey(2, Region.SLIDE, 64, 36)
    drop3 = RenderKey(3, Region.SLIDE, 64, 36)
    service.request(keep0, priority=0)
    service.request(drop2, priority=2)
    service.request(drop3, priority=3)
    service.request(keep1, priority=1)

    service.cancel_below(1)
    assert service.pending_count() == 2

    service.start()
    with wait_for(qtbot, service, keep1):
        pass
    qtbot.wait(100)
    assert sorted(c[0] for c in factory.last.calls) == [0, 1]
    assert service.is_cached(keep0) and service.is_cached(keep1)
    assert not service.is_cached(drop2) and not service.is_cached(drop3)


# -- errors ---------------------------------------------------------------------------


def test_render_error_is_logged_and_worker_survives(qtbot, make_service, caplog):
    service = make_service(factory=Factory(fail_pages=frozenset({1})))
    bad = key_for_slide(1)
    good = key_for_slide(0)
    with caplog.at_level(logging.ERROR, logger="prez.render"):
        service.request(bad)
        with wait_for(qtbot, service, good):
            service.request(good)
        qtbot.wait(50)

    assert service.get(good) is not None
    assert service.get(bad) is None
    assert service.is_running()
    errors = [r for r in caplog.records if r.name == "prez.render" and r.levelno >= logging.ERROR]
    assert errors and "cannot render page 1" in errors[0].exc_text


def test_notes_region_without_notes_is_logged_not_fatal(qtbot, make_service, caplog):
    service = make_service()
    bad = RenderKey(0, Region.NOTES, 64, 36)
    good = key_for_slide(0)
    with caplog.at_level(logging.ERROR, logger="prez.render"):
        service.request(bad)
        with wait_for(qtbot, service, good):
            service.request(good)
    assert service.is_running()
    assert any("no notes" in (r.exc_text or "") for r in caplog.records)


def test_failed_open_is_logged(qtbot, make_service, caplog):
    def broken_factory(path: str) -> FakeRenderer:
        raise OSError(f"cannot open {path}")

    with caplog.at_level(logging.ERROR, logger="prez.render"):
        service = make_service(FakeDoc(path="missing.pdf"), factory=broken_factory)
        service.request(key_for_slide(0))
        qtbot.waitUntil(lambda: not service.is_running(), timeout=5000)
    assert any("missing.pdf" in r.getMessage() for r in caplog.records)
    service.stop()  # must not hang on an already-finished worker


def test_start_replaces_a_worker_that_failed_to_open(qtbot, make_service):
    attempts: list[str] = []
    good = Factory()

    def flaky_factory(path: str) -> FakeRenderer:
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("not yet")
        return good(path)

    service = make_service(factory=flaky_factory)
    key = key_for_slide(0)
    service.request(key)  # stays queued: the first worker never gets a renderer
    qtbot.waitUntil(lambda: not service.is_running(), timeout=5000)
    assert service.pending_count() == 1

    with wait_for(qtbot, service, key):
        service.start()  # second attempt succeeds and serves the queued request
    assert service.is_running()
    assert attempts == ["deck.pdf", "deck.pdf"]
    assert good.last.calls == [(0, (0.0, 0.0, 960.0, 540.0), 64, 36)]


# -- lifecycle ------------------------------------------------------------------------


def test_stop_is_prompt_idempotent_and_restartable(qtbot, make_service):
    factory = Factory(delay=0.3)
    service = make_service(factory=factory)
    for slide in range(5):
        service.request(key_for_slide(slide))
    qtbot.waitUntil(lambda: factory.render_calls() == 1, timeout=5000)  # mid-render

    t0 = time.perf_counter()
    service.stop()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.5, f"stop() took {elapsed:.2f}s"
    assert not service.is_running()
    assert service.pending_count() == 0
    assert len(factory.last.calls) == 1  # the queue was dropped, not drained
    assert factory.last.closed

    service.stop()  # idempotent
    assert not service.is_running()

    service.start()
    assert service.is_running()
    qtbot.waitUntil(lambda: len(factory.created) == 2, timeout=5000)  # new renderer opened
    key = key_for_slide(4)
    with wait_for(qtbot, service, key):
        service.request(key)
    assert factory.created[1].calls == [(4, (0.0, 0.0, 960.0, 540.0), 64, 36)]


def test_start_twice_spawns_one_worker(make_service):
    factory = Factory()
    service = make_service(factory=factory)
    service.start()
    service.start()
    service.stop()
    assert len(factory.created) == 1


def test_set_document_swaps_cleanly_and_discards_in_flight_result(qtbot, make_service):
    factory = Factory(delay=0.15)
    doc_a = FakeDoc(path="a.pdf")
    doc_b = FakeDoc(path="b.pdf", size=(400.0, 400.0))
    service = make_service(doc_a, factory=factory)
    assert service.doc is doc_a

    done = key_for_slide(0)
    with wait_for(qtbot, service, done):
        service.request(done)
    in_flight = key_for_slide(1)
    service.request(in_flight)
    qtbot.waitUntil(lambda: factory.render_calls() == 2, timeout=5000)

    with qtbot.assertNotEmitted(service.rendered, wait=400):
        service.set_document(doc_b)  # waits for the in-flight render, then restarts

    assert service.doc is doc_b
    assert factory.created[0].closed
    assert len(factory.created) == 2 and factory.created[1].path == "b.pdf"
    assert service.get(done) is None  # cache cleared
    assert service.get(in_flight) is None  # stale result rejected
    assert service.cache_bytes == 0
    assert service.key_for(0, Region.SLIDE, 100, 50) == RenderKey(0, Region.SLIDE, 50, 50)

    with wait_for(qtbot, service, done):
        service.request(done)
    assert factory.created[1].calls == [(0, (0.0, 0.0, 400.0, 400.0), 64, 36)]
    assert service.get(done) is not None


def test_requests_without_document_are_ignored(make_service):
    service = make_service(start=False)
    service.set_document(None)
    assert not service.is_running()
    service.request(key_for_slide(0))
    assert service.pending_count() == 0
    assert RenderService.fit(None, 0, Region.SLIDE, 300, 200) == (300, 200)
    assert service.key_for(0, Region.SLIDE, 300, 200) == RenderKey(0, Region.SLIDE, 300, 200)


# -- geometry -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "box", "expected"),
    [
        ((960.0, 540.0), (1920, 1080), (1920, 1080)),
        ((960.0, 540.0), (1920, 1200), (1920, 1080)),
        ((960.0, 540.0), (1000, 1080), (1000, 562)),
        ((960.0, 540.0), (300, 300), (300, 169)),
        ((960.0, 540.0), (1, 1), (1, 1)),
        ((960.0, 540.0), (0, 0), (1, 1)),
        ((960.0, 540.0), (-5, 1080), (1, 1)),
        ((500.0, 1000.0), (100, 100), (50, 100)),
        ((100.0, 100.0), (640, 480), (480, 480)),
        ((1920.0, 540.0), (400, 400), (400, 112)),
    ],
)
def test_fit(size, box, expected):
    doc = FakeDoc(size=size)
    assert RenderService.fit(doc, 0, Region.SLIDE, *box) == expected


def test_fit_result_fits_and_keeps_aspect():
    doc = FakeDoc(size=(960.0, 540.0))
    for box in ((1366, 768), (1280, 1024), (2560, 1440), (333, 777), (7, 3)):
        w, h = RenderService.fit(doc, 0, Region.SLIDE, *box)
        assert 1 <= w <= box[0] and 1 <= h <= box[1]
        assert w == box[0] or h == box[1]  # one dimension always fills the box
        if min(w, h) > 20:
            assert abs(w / h - 16 / 9) < 0.02


def test_key_for_uses_fit(make_service):
    service = make_service(start=False)
    key = service.key_for(3, Region.SLIDE, 1920, 1200)
    assert key == RenderKey(3, Region.SLIDE, 1920, 1080)
    assert isinstance(key, tuple) and hash(key) == hash(RenderKey(3, Region.SLIDE, 1920, 1080))


# -- with the real document model (skipped until the core agent's work is in place) ----


@pytest.fixture
def real(request):
    """Return a conftest fixture by name, skipping when core's fixtures are not there yet."""
    pytest.importorskip("prez.document")

    def _get(name: str):
        try:
            return request.getfixturevalue(name)
        except LookupError:
            pytest.skip(f"fixture {name!r} not available yet (owned by the core agent)")

    return _get


def test_real_deck_renders_1080p_in_under_500ms(qtbot, make_service, real):
    from prez.document import Region as DocRegion

    doc = real("deck_doc")
    service = make_service(doc, factory=None)  # real PageRenderer on the worker thread

    warm_up = service.key_for(0, DocRegion.SLIDE, 64, 64)  # opens the document
    with wait_for(qtbot, service, warm_up):
        service.request(warm_up)

    key = service.key_for(0, DocRegion.SLIDE, 1920, 1080)
    assert (key.width, key.height) == (1920, 1080)
    t0 = time.perf_counter()
    with wait_for(qtbot, service, key):
        service.request(key)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    pixmap = service.get(key)
    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (1920, 1080)
    assert elapsed_ms < 500, f"1920x1080 render took {elapsed_ms:.0f} ms"


def test_real_deck_thumbnails_for_every_slide(qtbot, make_service, real):
    from prez.document import Region as DocRegion

    doc = real("deck_doc")
    service = make_service(doc, factory=None)
    keys = [service.key_for(s, DocRegion.SLIDE, 300, 300) for s in range(doc.slide_count)]
    assert all((k.width, k.height) == (300, 169) for k in keys)
    seen: set[RenderKey] = set()
    service.rendered.connect(seen.add)
    for key in keys:
        service.request(key, priority=3)
    qtbot.waitUntil(lambda: seen == set(keys), timeout=10000)
    assert all(service.get(k) is not None for k in keys)
    # Pages differ (each shows its own number), so the thumbnails must too.
    images = [service.get(k).toImage() for k in keys]
    assert images[0] != images[1]


def test_real_notes_document_regions_are_clipped(qtbot, make_service, real):
    from prez.document import Region as DocRegion

    doc = real("notes_doc")
    assert doc.has_notes()
    service = make_service(doc, factory=None)
    slide = service.key_for(0, DocRegion.SLIDE, 400, 400)
    notes = service.key_for(0, DocRegion.NOTES, 400, 400)
    full = service.key_for(0, DocRegion.FULL, 400, 400)
    assert (slide.width, slide.height) == (400, 225)
    assert (notes.width, notes.height) == (400, 225)
    assert (full.width, full.height) == (400, 112)
    for key in (slide, notes, full):
        with wait_for(qtbot, service, key):
            service.request(key)
    assert service.get(slide).toImage() != service.get(notes).toImage()
    assert service.nearest(0, DocRegion.NOTES).width() == 400
