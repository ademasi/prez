"""Render service: one worker thread owning the PDF renderer, plus an LRU pixmap cache.

Threading model
---------------
* :class:`RenderService` lives on the GUI thread and its public API is only ever called
  from there.
* One :class:`_Worker` (a ``QThread`` subclass) creates, uses and closes the
  ``PageRenderer`` inside ``run()``.  PyMuPDF is not thread-safe, so fitz never runs
  anywhere else.
* The worker hands finished ``QImage`` objects back through a queued signal.  The GUI
  thread converts them to ``QPixmap`` (which has to happen there), stores them in the
  cache and emits :attr:`RenderService.rendered`.
* Pending requests live in :class:`_PendingQueue`, a dict keyed by :class:`RenderKey`
  that both threads share under a lock + condition variable.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

if TYPE_CHECKING:
    from prez.document import DocumentInfo, PageRenderer, Region

log = logging.getLogger("prez.render")

#: Builds the object that renders pages.  It must offer ``render(page, clip, w, h)``
#: returning a ``RawImage`` and ``close()``; the default is ``document.PageRenderer``.
RendererFactory = Callable[[str], Any]

#: How long ``stop()`` waits for the worker before abandoning it (milliseconds).
_STOP_TIMEOUT_MS = 5000


def _open_page_renderer(path: str) -> PageRenderer:
    """Default renderer factory.

    The import is deliberately lazy: it runs on the worker thread, and it keeps
    ``prez.render`` importable (and testable with fakes) without pulling in fitz.
    """
    from prez.document import PageRenderer

    return PageRenderer(path)


class RenderKey(NamedTuple):
    """Identifies one rendered image: a region of a slide at an exact device-pixel size."""

    slide: int
    region: Region
    width: int  # device px
    height: int  # device px


def _estimate_bytes(key: RenderKey) -> int:
    """Memory estimate for a cached pixmap (32-bit per pixel, as the spec prescribes)."""
    return key.width * key.height * 4


class _PendingQueue:
    """Priority queue of render requests shared by the GUI thread and the worker.

    Entries map ``key -> (priority, seq)`` and the worker pops the minimum, so lower
    priority values go first and equal priorities are served FIFO.  A key that is being
    rendered right now is tracked in ``_in_flight`` so that re-requests are ignored until
    its result has been cached (or dropped).  ``generation`` is bumped by :meth:`clear` so
    results of requests queued before the clear can be recognised and discarded.
    """

    def __init__(self, generation: int = 0) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._entries: dict[RenderKey, tuple[int, int]] = {}
        self._in_flight: dict[RenderKey, int] = {}  # key -> generation it was popped in
        self._seq = 0
        self._generation = generation
        self._stopping = False

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def put(self, key: RenderKey, priority: int) -> None:
        """Queue ``key``; if it is already queued with a higher priority value, lower it."""
        with self._cond:
            if key in self._in_flight:
                return
            current = self._entries.get(key)
            if current is None:
                self._entries[key] = (priority, self._seq)
                self._seq += 1
                self._cond.notify()
            elif priority < current[0]:
                self._entries[key] = (priority, current[1])

    def pop(self) -> tuple[RenderKey, int] | None:
        """Block until a request is available and return ``(key, generation)``.

        Returns ``None`` once :meth:`request_stop` has been called.
        """
        with self._cond:
            while not self._entries and not self._stopping:
                # Timed wait: cheap insurance against any missed wake-up.
                self._cond.wait(0.5)
            if self._stopping:
                return None
            key = min(self._entries, key=self._entries.__getitem__)
            del self._entries[key]
            self._in_flight[key] = self._generation
            return key, self._generation

    def done(self, key: RenderKey, generation: int) -> None:
        """Forget the in-flight mark of ``key`` (only if it belongs to ``generation``)."""
        with self._lock:
            if self._in_flight.get(key) == generation:
                del self._in_flight[key]

    def cancel_below(self, priority: int) -> None:
        """Drop queued requests whose priority value is greater than ``priority``."""
        with self._lock:
            stale = [k for k, (p, _seq) in self._entries.items() if p > priority]
            for k in stale:
                del self._entries[k]

    def clear(self) -> int:
        """Drop everything (queued and in flight) and start a new generation."""
        with self._lock:
            self._entries.clear()
            self._in_flight.clear()
            self._generation += 1
            return self._generation

    def request_stop(self) -> None:
        """Make :meth:`pop` return ``None`` as soon as the worker looks again."""
        with self._cond:
            self._stopping = True
            self._entries.clear()
            self._in_flight.clear()
            self._cond.notify_all()

    def reset(self) -> None:
        """Allow a new worker to consume the queue after a stop."""
        with self._lock:
            self._stopping = False


class _Worker(QThread):
    """The render thread.  Everything that touches fitz happens inside :meth:`run`."""

    #: (RenderKey, QImage, generation) — connected with a queued connection to the service.
    result = Signal(object, object, int)

    def __init__(
        self,
        doc: DocumentInfo,
        queue: _PendingQueue,
        factory: RendererFactory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._queue = queue
        self._factory = factory

    def run(self) -> None:  # runs on the worker thread
        try:
            renderer = self._factory(self._doc.path)
        except Exception:
            log.exception("cannot open %r for rendering", self._doc.path)
            return
        try:
            while (item := self._queue.pop()) is not None:
                key, generation = item
                try:
                    image = self._render(renderer, key)
                except Exception:
                    log.exception("rendering %s failed; request dropped", key)
                    self._queue.done(key, generation)
                    continue
                self.result.emit(key, image, generation)
        finally:
            try:
                renderer.close()
            except Exception:
                log.exception("closing the renderer failed")

    def _render(self, renderer: Any, key: RenderKey) -> QImage:
        page, clip = self._doc.region(key.slide, key.region)
        raw = renderer.render(page, clip, key.width, key.height)
        data = raw.data if isinstance(raw.data, bytes) else bytes(raw.data)
        # .copy() detaches the QImage from the Python buffer so it can outlive `data`.
        image = QImage(data, raw.width, raw.height, raw.stride, QImage.Format.Format_RGB888).copy()
        if image.isNull():
            raise RuntimeError(f"QImage conversion of {raw.width}x{raw.height} failed")
        if (image.width(), image.height()) != (key.width, key.height):
            # The renderer must give exact sizes; tolerate a rounding slip rather than
            # hand the cache a pixmap that does not match its key.
            log.debug(
                "renderer returned %dx%d for %s; rescaling", image.width(), image.height(), key
            )
            image = image.scaled(
                key.width,
                key.height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return image


class RenderService(QObject):
    """Single worker QThread owning a PageRenderer, plus an LRU ``QPixmap`` cache.

    Main-thread API only (the worker is internal).  Lifecycle: ``start()`` spawns the
    worker, ``stop()`` joins it, ``set_document()`` does stop → clear → swap → start.
    """

    #: RenderKey — emitted on the MAIN thread once the pixmap is in the cache.
    rendered = Signal(object)

    def __init__(
        self,
        doc: DocumentInfo | None,
        max_bytes: int = 512 * 2**20,
        parent: QObject | None = None,
        *,
        renderer_factory: RendererFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._max_bytes = max(0, int(max_bytes))
        self._factory: RendererFactory = renderer_factory or _open_page_renderer
        self._cache: OrderedDict[RenderKey, QPixmap] = OrderedDict()
        self._cache_bytes = 0
        self._pending = _PendingQueue()
        self._worker: _Worker | None = None
        # Workers that did not stop in time are parked here so Qt never destroys a
        # running QThread (which would abort the process).
        self._abandoned: list[_Worker] = []

    # -- lifecycle -----------------------------------------------------------------

    @property
    def doc(self) -> DocumentInfo | None:
        return self._doc

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self) -> None:
        """Spawn the worker; it opens ``PageRenderer(doc.path)`` on its own thread."""
        if self._worker is not None:
            if self._worker.isRunning():
                return
            # It exited on its own (the document could not be opened): replace it.
            self._worker.deleteLater()
            self._worker = None
        if self._doc is None:
            log.debug("start() without a document: nothing to render")
            return
        self._pending.reset()
        worker = _Worker(self._doc, self._pending, self._factory, parent=self)
        worker.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.start()
        self._worker = worker

    def stop(self) -> None:
        """Drop pending requests, let the current render finish, close, join. Idempotent."""
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        self._pending.request_stop()
        if worker.wait(_STOP_TIMEOUT_MS):
            worker.deleteLater()
            self._pending.reset()
        else:
            log.error("render worker did not stop within %d ms; abandoning it", _STOP_TIMEOUT_MS)
            self._abandoned.append(worker)
            # Never share a queue with a thread we lost track of; a newer generation
            # makes anything it still emits get discarded on arrival.
            self._pending = _PendingQueue(generation=self._pending.generation + 1)

    def set_document(self, doc: DocumentInfo | None) -> None:
        """Stop the worker, forget every cached pixmap, swap the document and restart."""
        self.stop()
        self.clear()
        self._doc = doc
        self.start()

    # -- cache ---------------------------------------------------------------------

    def get(self, key: RenderKey) -> QPixmap | None:
        """Exact cache hit; marks the entry as most recently used."""
        pixmap = self._cache.get(key)
        if pixmap is not None:
            self._cache.move_to_end(key)
        return pixmap

    def is_cached(self, key: RenderKey) -> bool:
        """Exact cache hit test that does not touch the LRU order."""
        return key in self._cache

    def nearest(self, slide: int, region: Region) -> QPixmap | None:
        """Largest cached pixmap for ``slide``/``region`` at any size (no LRU bump)."""
        best: QPixmap | None = None
        best_area = -1
        for key, pixmap in self._cache.items():
            if key.slide == slide and key.region == region:
                area = key.width * key.height
                if area > best_area:
                    best, best_area = pixmap, area
        return best

    def clear(self) -> None:
        """Drop the cache and every pending or in-flight request."""
        self._pending.clear()
        self._cache.clear()
        self._cache_bytes = 0

    @property
    def cache_bytes(self) -> int:
        return self._cache_bytes

    @property
    def cache_count(self) -> int:
        return len(self._cache)

    # -- requests ------------------------------------------------------------------

    def request(self, key: RenderKey, priority: int = 0) -> None:
        """Queue ``key`` unless cached or already pending; re-requesting lowers the value.

        Lower priority value = sooner: 0 visible now, 1 neighbours, 2 prerender,
        3 thumbnails.  Requests made before :meth:`start` are served once it runs.
        """
        if self._doc is None or key.width < 1 or key.height < 1 or key in self._cache:
            return
        self._pending.put(key, priority)

    def cancel_below(self, priority: int) -> None:
        """Drop pending requests whose priority value is greater than ``priority``."""
        self._pending.cancel_below(priority)

    def pending_count(self) -> int:
        """Number of queued (not yet started) requests."""
        return len(self._pending)

    # -- geometry ------------------------------------------------------------------

    @staticmethod
    def fit(
        doc: DocumentInfo | None, slide: int, region: Region, box_w: int, box_h: int
    ) -> tuple[int, int]:
        """Largest integer ``(w, h)`` with the region's aspect that fits in the box; min 1×1."""
        box_w, box_h = max(1, int(box_w)), max(1, int(box_h))
        aspect = float(doc.aspect(slide, region)) if doc is not None else 0.0
        if not math.isfinite(aspect) or aspect <= 0:
            return box_w, box_h
        width = min(box_w, round(box_h * aspect))
        height = min(box_h, round(box_w / aspect))
        return max(1, width), max(1, height)

    def key_for(self, slide: int, region: Region, box_w: int, box_h: int) -> RenderKey:
        """The key for ``slide``/``region`` rendered to fit a ``box_w`` × ``box_h`` box."""
        width, height = self.fit(self._doc, slide, region, box_w, box_h)
        return RenderKey(slide, region, width, height)

    # -- worker → main thread ------------------------------------------------------

    @Slot(object, object, int)
    def _on_result(self, key: RenderKey, image: QImage, generation: int) -> None:
        """Runs on the main thread: cache the pixmap and announce it."""
        if generation != self._pending.generation:
            return  # rendered for a document/cache that is gone
        self._pending.done(key, generation)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            log.warning("QPixmap conversion failed for %s", key)
            return
        self._store(key, pixmap)
        self.rendered.emit(key)

    def _store(self, key: RenderKey, pixmap: QPixmap) -> None:
        if key in self._cache:
            del self._cache[key]
            self._cache_bytes -= _estimate_bytes(key)
        self._cache[key] = pixmap
        self._cache_bytes += _estimate_bytes(key)
        # Evict least recently used first; always keep the entry just added, otherwise a
        # tiny budget could make a single large slide unrenderable.
        while self._cache_bytes > self._max_bytes and len(self._cache) > 1:
            old_key, _old = self._cache.popitem(last=False)
            self._cache_bytes -= _estimate_bytes(old_key)
