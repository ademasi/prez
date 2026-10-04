# prez — specification and interface contract

`prez` is a from-scratch reimplementation of **pympress** (dual-screen PDF presenter) in
Python with **PySide6 (Qt 6)** and **PyMuPDF (fitz)**. Goals, in order:

1. **Fast.** Slide changes must feel instant. Rendering happens on a worker thread,
   results are cached as `QPixmap`s, neighbours are pre-rendered, and an already-cached
   image at another size is shown scaled while the exact size renders.
2. **Robust.** No crashes on resize, screen hotplug, reload, or odd PDFs. Pure-Python
   core (document model, timer, config) with unit tests and no Qt dependency.
3. **Familiar.** Same default keyboard shortcuts and the same concepts as pympress
   (content window + presenter window, notes modes, blank, pointer, highlight, overview,
   talk timer).

Reference implementation to read when unsure about behaviour:
`/usr/lib/python3.14/site-packages/pympress/` (pympress 1.8.6: `document.py`, `ui.py`,
`talk_time.py`, `pointer.py`, `scribble.py`, `surfacecache.py`, `share/defaults.conf`).
Do **not** copy its code; match its behaviour.

Out of scope for v1: embedded video/gstreamer, VLC, zoom. (Leave hooks, don't build.)

## Tooling

- Project managed with **uv**: `uv run pytest`, `uv run ruff check src tests`,
  `uv run ruff format src tests`, `uv run prez file.pdf`.
- Python ≥ 3.12. Type hints everywhere. Dataclasses over dicts.
- Tests: `pytest` + `pytest-qt`. Anything touching Qt must run with
  `QT_QPA_PLATFORM=offscreen` (set it in `tests/conftest.py` via `os.environ.setdefault`
  **before** importing PySide6).
- **PyMuPDF is not thread-safe.** Rule: `fitz` is used only (a) inside
  `document.load_document()` on the main thread while no render worker is running, and
  (b) inside `document.PageRenderer`, which is created, used and closed **on the render
  worker thread only**. Nothing else imports `fitz`.
- Coordinates: PDF regions are in **points**; everything the UI exchanges (pointer,
  strokes, link rects) is **normalized 0..1 relative to the SLIDE region** of a slide.
  Pixel sizes handed to the renderer are **device pixels** (already multiplied by DPR).

## Package layout and ownership

```
src/prez/
  __init__.py
  __main__.py          core-ui   `from prez.cli import main; main()`
  cli.py               core-ui   argparse → PrezApp
  app.py               core-ui   PrezApp: wires everything, action dispatch, prerender policy
  screens.py           core-ui   screen selection / placement / hotplug
  state.py             core-ui   PresentationState (QObject with signals)
  document.py          core      DocumentInfo, NotesMode, Region, Link, load_document, PageRenderer
  timer.py             core      TalkTimer (pure python)
  config.py            core      Config, load_config, DEFAULT_SHORTCUTS, ACTIONS
  render.py            render    RenderService (worker thread + LRU pixmap cache)
  widgets/slide_view.py widgets  SlideView
  widgets/overview.py   widgets  OverviewWidget
  widgets/notes_view.py widgets  NotesPane
  windows/content.py    core-ui  ContentWindow
  windows/presenter.py  core-ui  PresenterWindow
tests/
  conftest.py          core      offscreen env + PDF fixtures (see below)
  test_document.py     core
  test_timer.py        core
  test_config.py       core
  test_render.py       render
  test_widgets.py      widgets
  test_app.py          core-ui   (smoke: start app offscreen on fixture PDF, navigate, grab windows)
```

Each agent owns the files tagged with its name and **must not edit other files**. If an
interface in this spec is insufficient, implement your side against the spec as written
and list the needed change in your final report; the integration pass reconciles.

## Test fixtures (`tests/conftest.py`, owned by core)

Generated with fitz at session scope into `tmp_path_factory`:

- `deck_pdf` — 6 pages, 16:9 (960×540 pt), page labels `1,2,2,3,4,5` (beamer-like
  overlays: pages 2 and 3 share label "2"), big page number drawn on each page, an
  internal link rect on page 1 jumping to page 4 (0-based 3), a URI link on page 2, a
  text annotation ("Note on page 3") on page 3, an outline with 3 entries.
- `notes_pdf` — 5 pages, 1920×540 pt (two 16:9 halves): left half slide with number,
  right half "Notes for N" text. Expect `NotesMode.RIGHT` auto-detection.
- `after_pdf` — 6 pages alternating slide / notes page (same size), for `NotesMode.AFTER`.

Expose fixtures: `deck_pdf`, `notes_pdf`, `after_pdf` (paths as `str`), and
`deck_doc`, `notes_doc` (loaded `DocumentInfo`).

## `prez/document.py` (core) — no Qt imports

```python
from dataclasses import dataclass
from enum import Enum

class NotesMode(str, Enum):
    NONE = "none"      # whole page is the slide
    RIGHT = "right"    # page = [slide | notes]  (beamer "show notes on second screen=right")
    LEFT = "left"      # page = [notes | slide]
    TOP = "top"        # page = [notes / slide]
    BOTTOM = "bottom"  # page = [slide / notes]
    AFTER = "after"    # pages alternate: slide page, then its notes page

class Region(str, Enum):
    FULL = "full"      # the whole PDF page
    SLIDE = "slide"    # slide part
    NOTES = "notes"    # notes part (only meaningful if has_notes)

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

@dataclass(frozen=True)
class Link:
    rect: Rect              # normalized 0..1 within the SLIDE region
    target: int | None      # 0-based *slide* index for internal links
    uri: str | None = None  # external links

@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    slide: int              # 0-based slide index

@dataclass
class DocumentInfo:
    path: str
    page_count: int                       # PDF pages
    page_sizes: list[tuple[float, float]] # (w, h) points per PDF page
    labels: list[str]                     # per *slide*; falls back to "1".."N"
    links: list[list[Link]]               # per *slide*
    annotations: list[list[str]]          # per *slide*: text of Text/FreeText/popup annots
    outline: list[OutlineEntry]
    notes_mode: NotesMode
    mtime: float                          # os.stat mtime at load, for reload detection

    @property
    def slide_count(self) -> int: ...            # AFTER → ceil(page_count / 2), else page_count
    def has_notes(self) -> bool: ...             # notes_mode != NONE
    def page_for_slide(self, slide: int) -> int: ...        # PDF page index with the slide content
    def notes_page_for_slide(self, slide: int) -> int | None: ...
    def region(self, slide: int, region: Region) -> tuple[int, Rect]:
        """(pdf page index, clip rect in points). NOTES when not has_notes → raises ValueError.
        AFTER mode: SLIDE → (2*slide, full rect); NOTES → (2*slide+1, full rect) or the
        slide page itself if the notes page is missing (odd page count)."""
    def region_size(self, slide: int, region: Region) -> tuple[float, float]: ...  # points
    def aspect(self, slide: int, region: Region) -> float: ...                      # w / h
    def next_label(self, slide: int) -> int: ...  # first slide after `slide` with a different label (clamped)
    def prev_label(self, slide: int) -> int: ...  # first slide of the previous label group (clamped)
    def label_index(self, label: str) -> int | None: ...  # first slide with that label

def detect_notes_mode(page_sizes: list[tuple[float, float]]) -> NotesMode:
    """RIGHT if every page's w/h > 2.4 (two landscape slides side by side), else NONE."""

def load_document(path: str, notes_mode: NotesMode | str = "auto") -> DocumentInfo:
    """Open with fitz, extract everything, close the fitz doc before returning.
    `notes_mode` accepts a NotesMode, its value string, or "auto" (→ detect_notes_mode).
    Links: fitz `page.get_links()`; kind LINK_GOTO → target page → slide; LINK_URI → uri.
    Only keep links whose rect intersects the SLIDE region; normalize relative to it.
    Labels: `page.get_label()` if non-empty else str(pdf_page+1); for AFTER use slide page."""

@dataclass(frozen=True)
class RawImage:
    width: int
    height: int
    stride: int
    data: bytes   # RGB888, no alpha

class PageRenderer:
    """Owns a fitz.Document. Create/use/close on ONE thread only."""
    def __init__(self, path: str) -> None: ...
    def render(self, page: int, clip: Rect, width_px: int, height_px: int) -> RawImage:
        """Render `clip` (points) of PDF page `page` scaled so the output is exactly
        width_px × height_px (Matrix(width_px/clip_w, height_px/clip_h)), alpha=False."""
    def close(self) -> None: ...
```

## `prez/timer.py` (core) — pure python

```python
class TimerState(Enum): IDLE, RUNNING, PAUSED

class TalkTimer:
    def __init__(self, talk_seconds: int | None = None, clock: Callable[[], float] = time.monotonic): ...
    state: TimerState
    elapsed: float            # seconds, property
    talk_seconds: int | None
    remaining: float | None   # talk_seconds - elapsed, may go negative
    fraction: float | None    # elapsed / talk_seconds (unclamped)
    overtime: bool
    def start(self): ...      # from IDLE or PAUSED
    def pause(self): ...
    def toggle(self): ...     # IDLE/PAUSED → start; RUNNING → pause
    def reset(self): ...      # back to IDLE, elapsed 0
    def set_talk_time(self, seconds: int | None): ...
    @staticmethod
    def format(seconds: float) -> str: ...   # "mm:ss" below 1h, "h:mm:ss" above; negative → "-mm:ss"
    @staticmethod
    def parse(text: str) -> int: ...  # "20" → 1200 (minutes), "20:30" → 1230, "1:05:00" → 3900; ValueError otherwise
```

## `prez/config.py` (core) — no Qt imports

TOML at `$XDG_CONFIG_HOME/prez/config.toml` (default `~/.config/prez/config.toml`).
Read with `tomllib`. If missing, write a commented default file (string constant) and return
defaults. Unknown keys ignored, bad values fall back to default with a `logging.warning`.

```python
ACTIONS: tuple[str, ...]  # every action name, in this order:
# next, prev, first, last, next-label, prev-label, hist-back, hist-forward,
# goto-page, jump-label, content-fullscreen, presenter-fullscreen, notes-mode,
# overview, swap-screens, blank, blank-white, freeze, quit, pointer, pause-timer,
# reset-timer, edit-talk-time, highlight, highlight-clear, highlight-undo,
# highlight-redo, highlight-pen-1 .. highlight-pen-9, highlight-eraser,
# pick-file, reload, validate, cancel

DEFAULT_SHORTCUTS: dict[str, list[str]]   # Qt portable QKeySequence strings, mirroring pympress:
# next: Right Down PgDown Space        prev: Left Up PgUp
# first: Home                          last: End
# next-label: Ctrl+Right Ctrl+Down Ctrl+PgDown Ctrl+Space
# prev-label: Ctrl+Left Ctrl+Up Ctrl+PgUp
# hist-back: Alt+Left                  hist-forward: Alt+Right
# goto-page: G                         jump-label: J
# content-fullscreen: F11 F F5 Ctrl+L  presenter-fullscreen: Ctrl+F
# notes-mode: N   overview: D   swap-screens: S   blank: B   blank-white: W   freeze: Z
# quit: Q Ctrl+Q  pointer: L   pause-timer: P Pause   reset-timer: R   edit-talk-time: T
# highlight: H   highlight-clear: Ctrl+Del   highlight-undo: Ctrl+Z   highlight-redo: Ctrl+R
# highlight-pen-N: N (1..9)   highlight-eraser: 0
# pick-file: O   reload: Ctrl+Shift+R   validate: Return Enter   cancel: Escape

@dataclass
class Pen:
    color: str   # "#rrggbbaa" or "#rrggbb"
    width: float # normalized to slide width (e.g. 0.01)

@dataclass
class Config:
    shortcuts: dict[str, list[str]]        # merged: defaults overridden per action by [shortcuts] table
    notes_mode: str = "auto"               # "auto" | NotesMode value
    talk_time: int | None = None           # seconds
    cache_max_mb: int = 512
    prerender: int = 2                     # slides ahead to prerender (1 behind always)
    content_screen: str | None = None      # QScreen.name() to put content on
    start_fullscreen: bool = True          # content fullscreen on start
    start_blanked: bool = False
    pointer_color: str = "#ff0000"
    pointer_size: float = 0.02             # fraction of slide width
    pens: list[Pen]                        # 9 pens, defaults mirroring pympress highlight colors
    active_pen: int = 1                    # 1..9
    next_count: int = 1                    # next-slide previews (v1 uses 1; keep the field)
    slide_ratio: float = 0.6               # presenter: current-slide pane fraction
    show_clock: bool = True

def config_path() -> Path: ...
def load_config(path: Path | None = None) -> Config: ...
def parse_shortcuts(table: dict[str, str | list[str]]) -> dict[str, list[str]]: ...  # "Right Down" → ["Right","Down"]
```

## `prez/render.py` (render)

```python
class RenderKey(NamedTuple):
    slide: int
    region: Region
    width: int     # device px
    height: int    # device px

class RenderService(QObject):
    """Single worker QThread owning a PageRenderer. Main-thread API only (except the worker)."""
    rendered = Signal(object)        # RenderKey — emitted on the MAIN thread once the pixmap is cached

    def __init__(self, doc: DocumentInfo, max_bytes: int = 512 * 2**20, parent: QObject | None = None): ...
    def start(self) -> None: ...     # spawn worker (opens PageRenderer(doc.path) on the worker thread)
    def stop(self) -> None: ...      # drain, close renderer, join. Idempotent. Call before load_document again.
    def set_document(self, doc: DocumentInfo) -> None: ...  # stop(), clear cache, swap doc, start()

    def get(self, key: RenderKey) -> QPixmap | None: ...  # exact cache hit, bumps LRU
    def nearest(self, slide: int, region: Region) -> QPixmap | None: ...  # largest cached pixmap for slide/region (any size)
    def request(self, key: RenderKey, priority: int = 0) -> None:
        """Queue if not cached and not already pending. Lower priority value = sooner.
        0 visible now, 1 neighbours, 2 prerender, 3 thumbnails. Re-requesting bumps priority."""
    def cancel_below(self, priority: int) -> None: ...  # drop pending requests with priority > given (stale prerender)
    def clear(self) -> None: ...

    @staticmethod
    def fit(doc: DocumentInfo, slide: int, region: Region, box_w: int, box_h: int) -> tuple[int, int]:
        """Largest integer (w, h) with the region's aspect fitting inside box; min 1×1."""
    def key_for(self, slide: int, region: Region, box_w: int, box_h: int) -> RenderKey: ...  # uses fit()
```

Behaviour:
- Worker loop: pop the lowest-(priority, seq) pending key, render with `PageRenderer`,
  build a `QImage(RawImage)` (RGB888, `.copy()` so the buffer is owned), emit an internal
  queued signal to the main thread which converts to `QPixmap`, stores in an LRU bounded
  by `max_bytes` (estimate `w*h*4`), then emits `rendered(key)`.
- Rendering errors are logged and the key dropped (never crash the worker).
- Pending set is deduplicated; `request` of an already-cached key is a no-op.
- `stop()` must return within ~1 s even mid-render (wait for the current render, then exit).
- Tests (`tests/test_render.py`, offscreen, `qtbot`): request → `rendered` emitted with the
  key, `get` returns a pixmap of exactly that size; LRU eviction when `max_bytes` small;
  `nearest` returns the largest; priorities respected (queue 3 thumbs at prio 3 then one
  at prio 0, the prio 0 one arrives first); `set_document` swaps cleanly.

## `prez/state.py` (core-ui)

```python
@dataclass
class Stroke:
    color: str
    width: float                      # normalized to slide width
    points: list[tuple[float, float]] # normalized in SLIDE region
    eraser: bool = False

class PresentationState(QObject):
    document_changed = Signal(object)  # DocumentInfo
    slide_changed = Signal(int)        # presenter slide
    content_changed = Signal(int)      # slide shown on content window (differs when frozen)
    blank_changed = Signal(object)     # None | "black" | "white"
    freeze_changed = Signal(bool)
    pointer_changed = Signal(object)   # None | (x, y) normalized in slide region
    strokes_changed = Signal(int)      # slide whose strokes changed
    message = Signal(str)              # transient status text for the presenter status bar

    doc: DocumentInfo | None
    slide: int; content_slide: int; blank: str | None; frozen: bool; pointer: tuple | None
    strokes: dict[int, list[Stroke]]; history: list[int]; history_pos: int

    def set_document(doc, keep_slide=True)
    def goto(slide: int, record_history=True)   # clamps; emits slide_changed and (unless frozen) content_changed
    def next(); prev(); first(); last(); next_label(); prev_label(); back(); forward()
    def toggle_blank(kind="black")  # same kind again → None; different kind → switch
    def toggle_freeze()             # unfreezing syncs content_slide to slide
    def set_pointer(pos | None)
    def begin_stroke(color, width, eraser=False); add_point(x, y); end_stroke()
    def undo_stroke(slide=None); redo_stroke(slide=None); clear_strokes(slide=None)
```

## `prez/widgets/slide_view.py` (widgets)

```python
class SlideView(QWidget):
    """Paints one Region of one slide, letterboxed on black (or the blank colour), plus
    overlays: pointer dot, strokes, (optional) link hotspots."""
    link_activated = Signal(object)           # Link
    pointer_moved = Signal(object)            # (x, y) normalized | None when leaving
    stroke_started = Signal(float, float)     # x, y
    stroke_moved = Signal(float, float)
    stroke_ended = Signal()
    clicked = Signal(object)                  # Qt.MouseButton, for prev/next on plain click

    def __init__(self, render: RenderService, region: Region, parent=None): ...
    def set_document(self, doc: DocumentInfo | None): ...
    def set_slide(self, slide: int | None): ...     # None → paints nothing (black)
    def set_blank(self, kind: str | None): ...      # "black" | "white" | None
    def set_pointer(self, pos: tuple[float, float] | None): ...
    def set_pointer_style(self, color: str, size: float): ...
    def set_strokes(self, strokes: list[Stroke]): ...
    def set_interactive(self, enabled: bool): ...   # emit pointer_moved on mouse move, link clicks, clicked
    def set_draw_mode(self, enabled: bool): ...     # drag emits stroke_* instead of pointer/clicks
    def slide_rect(self) -> QRectF: ...             # letterboxed rect in widget coords (logical px)
    def current_key(self) -> RenderKey | None: ...  # exact key for current size/DPR
    def request_priority: int = 0                   # priority used for its own requests
```
Paint logic: on `paintEvent`, `key = current_key()`; `pix = render.get(key)`; if None,
`render.request(key, request_priority)` and draw `render.nearest()` scaled (smooth) if
any; draw pixmap with `setDevicePixelRatio(dpr)`. Connect `render.rendered` and
`update()` only when the key matches `(slide, region)`. Debounce resize-triggered
requests by ~40 ms (QTimer single shot) but always paint immediately. Pointer drawn as a
filled circle (`pointer_size * slide width`) with a 1px darker outline. Strokes: polyline
with round caps/joins, width = `stroke.width * slide width`; `eraser=True` strokes are
drawn with `CompositionMode_Clear`-like effect by painting over with the underlying pixmap
clip — simpler accepted approach: eraser strokes remove intersecting strokes at state
level, so SlideView only paints non-eraser strokes. Links: hand cursor on hover when
interactive; click inside a link rect → `link_activated`, else `clicked(button)`.

`widgets/overview.py`:
```python
class OverviewWidget(QWidget):
    activated = Signal(int)   # slide chosen
    closed = Signal()         # Escape
    def __init__(self, render: RenderService, parent=None)
    def set_document(doc); def set_current(slide: int); def columns(self) -> int  # ~ width / 220px, min 2
```
Painted grid (not one widget per slide): thumbnails via `render.request(..., priority=3)`,
labels underneath, current slide outlined, arrow keys/Return/mouse. Scrollable (QScrollArea
or own scroll), keeps current visible.

`widgets/notes_view.py`:
```python
class NotesPane(QWidget):
    """If doc.has_notes(): a SlideView(Region.NOTES). Else: annotations text (read-only,
    top) + editable per-slide text notes (QPlainTextEdit) persisted to
    `<pdf>.notes.json` ({"<slide>": "text"}) on change (debounced 500 ms) and on close."""
    def __init__(self, render: RenderService, parent=None)
    def set_document(doc); def set_slide(slide: int); def flush(self)  # save now
    def slide_view(self) -> SlideView | None
```
Text font: large (14pt+), zoom with Ctrl+wheel. Dark background, light text.

`tests/test_widgets.py` (offscreen): SlideView requests the right key for its size and
paints after `rendered`; letterbox math; link hit-test emits `link_activated`; OverviewWidget
`activated` on click of a thumbnail; NotesPane saves/loads the json.

## `prez/windows/content.py` (core-ui)

`ContentWindow(QWidget)`: black background, no frame when fullscreen, hides the cursor,
contains one `SlideView(Region.SLIDE)` (non-interactive). Shows `state.content_slide`,
blank, pointer, strokes. Double-click toggles fullscreen. `set_fullscreen(bool)`,
`is_fullscreen()`. Key presses are forwarded to the app dispatcher (see app.py).

## `prez/windows/presenter.py` (core-ui)

`PresenterWindow(QMainWindow)`, dark theme (Fusion style + dark palette set in app.py):

```
┌──────────────────────────────────┬──────────────────────┐
│                                  │  next slide (SlideView)│
│   current slide (SlideView,      ├──────────────────────┤
│   interactive: pointer/draw/links│  NotesPane            │
│                                  │                       │
├──────────────────────────────────┴──────────────────────┤
│ [label 3 / 40] [elapsed 12:34 ▶] [remaining 07:26] [clock 14:03] [status msg]│
│ ████████████░░░░░░░░░░░░ progress (talk time)                                  │
└─────────────────────────────────────────────────────────┘
```
- Horizontal `QSplitter` (proportions from `config.slide_ratio`), right side vertical
  splitter. Splitter sizes saved/restored via `QSettings("prez", "prez")`.
- Toolbar (small, icons from `QStyle.StandardPixmap` or text): open, prev, next, blank,
  freeze, pointer, pen, overview, swap screens, timer pause/reset, talk time.
- Overview is a `QStackedWidget` page over the whole central area (toggle with `overview`
  action); choosing a slide returns to the normal page.
- Goto page: clicking the slide label or `goto-page` action shows an inline `QLineEdit`
  in the status bar; Enter validates (page number or label via `doc.label_index`),
  Escape cancels. `jump-label` does the same but matches labels first.
- Timer label: green normally, orange when `fraction ≥ 0.9`, red + "−mm:ss" when overtime;
  clicking it toggles pause; paused shows "⏸". Progress bar reflects `fraction` (hidden
  when no talk time). Status bar also shows `state.message` texts for 3 s.
- Ticks from a 250 ms `QTimer` in app.py calling `presenter.update_time(timer)`.
- Current-slide view: left click → next, right click → prev, wheel → next/prev,
  internal links navigate, uri links open with `QDesktopServices`.
- Drag-and-drop of a .pdf opens it.
- Window title: `"<file name> — prez"`.

## `prez/screens.py` (core-ui)

```python
def choose_screens(app: QGuiApplication, preferred_content: str | None) -> tuple[QScreen, QScreen]:
    """(content, presenter). One screen → both the same. preferred_content matched by
    QScreen.name(); else content = a screen that is not the primary (prefer the largest
    non-primary), presenter = primary."""
def place(window: QWidget, screen: QScreen, fullscreen: bool) -> None:
    """windowHandle().setScreen(screen) (create handle first), move to screen.geometry()
    top-left, showFullScreen() or showMaximized(). Works on Wayland (no absolute positioning
    there; rely on setScreen before show)."""
```
`PrezApp` connects `QGuiApplication.screenAdded/screenRemoved` → re-choose and re-place
(content fullscreen on the new external screen when one appears, if it was fullscreen).

## `prez/app.py` (core-ui)

```python
class PrezApp(QObject):
    def __init__(self, argv: list[str], config: Config, path: str | None, *, notes_mode: str | None,
                 talk_time: int | None, content_screen: str | None, fullscreen: bool | None,
                 start_slide: int = 0): ...
    def run(self) -> int: ...      # exec() the QApplication
    def open(self, path: str): ... # load_document (render stopped), render.set_document, state.set_document
    def reload(self): ...          # same path, keep slide; triggered by QFileSystemWatcher (debounced 300 ms)
    def dispatch(self, action: str) -> bool: ...   # execute by name; returns False if unknown
```
- Key handling: `QApplication.installEventFilter`. On `KeyPress` for any of our two windows,
  build `QKeySequence(event.keyCombination())` and compare `toString(PortableText)` with the
  configured sequences (`config.shortcuts`, case-insensitive). Ignore when the focus
  widget is a `QLineEdit`/`QPlainTextEdit`, except `validate`/`cancel`. Also handle
  pending digit input for goto.
- Prerender policy on `slide_changed(s)`: `render.cancel_below(1)`; request current
  content key & presenter keys (prio 0, the views do this themselves), then for
  `s+1 .. s+config.prerender` and `s-1`: content size, presenter current size, next-preview
  size at prio 1 (s+1) / 2 (others). Sizes come from the views' `current_key()` (width/height).
- Timer auto-starts on the first `next`/`goto` that changes the slide, unless paused by
  the user explicitly before.
- `swap-screens`: swap which QScreen each window is on, keep fullscreen state.
- `notes-mode` cycles NONE → RIGHT → LEFT → TOP → BOTTOM → AFTER → NONE (reload doc with mode),
  message shows the new mode.
- `pointer` toggles laser mode: presenter current view emits `pointer_moved` → state →
  both views draw. `highlight` toggles draw mode with `config.active_pen`; `highlight-pen-N`
  selects pen; `highlight-eraser` sets eraser; strokes live in `state.strokes[slide]`.
- `blank` toggles black, `blank-white` toggles white. `freeze` toggles freeze.
- `quit` flushes notes, saves QSettings, stops render, quits.
- Dark palette: Fusion style, background #202124, text #e8eaed, highlight #8ab4f8.

## `prez/cli.py` (core-ui)

```
prez [FILE] [--notes {auto,none,right,left,top,bottom,after}] [--talk-time MM[:SS]]
     [--content-screen NAME] [--list-screens] [--fullscreen/--no-fullscreen]
     [--page N] [--blank] [--log-level LEVEL]
```
`--list-screens` prints `name  WxH@x,y  primary?` and exits 0. No FILE → start with an
empty presenter window and the open-file dialog.

## Performance checklist (everyone)

- Never render on the GUI thread. Never block the GUI thread on the cache.
- `QPainter.drawPixmap` with the exact-size pixmap; `SmoothTransformation` only for the
  temporary "nearest" scaled fallback.
- Cache keys use device pixels; thumbnails ≤ 300 px wide.
- Measure: `tests/test_render.py` includes one test asserting a 1920×1080 render of a
  fixture page takes < 200 ms on the worker (skip-if-slow-CI is fine, but keep it).

## Implementation notes (post-integration, 2026-10-03)

Deviations from the contract above that are now the de-facto interface:

- `RenderService(doc: DocumentInfo | None)`: `start()`/`request()` are no-ops without a
  document (empty presenter window at startup). Extra read-only helpers: `is_cached`,
  `is_running`, `pending_count`, `cache_bytes`, `cache_count`, `doc`.
- `DocumentInfo.next_label` / `prev_label` follow **pympress**, not the first draft above:
  next-label lands on the *last* slide of the next label group (fully built Beamer slide;
  from inside a group, on that group's last slide), prev-label on the last slide of the
  previous group.
- Page labels are decoded with `document.decode_label`: Beamer/hyperref emits UTF-16BE hex
  strings (`<FEFF0031>`) which PyMuPDF returns verbatim.
- `DEFAULT_SHORTCUTS["cancel"] = ["Escape", "Esc"]`; app.py normalises both sides through
  `QKeySequence(...).toString(PortableText)` lowercased.
- `Config.eraser_width` (default 0.05) added; `config.parse_color("#rrggbbaa")` returns
  `(r, g, b, a)` since `QColor` only understands `#AARRGGBB`.
- SlideView emits `pointer_moved(None)` over the letterbox band instead of clamping.
- On a single screen the content window is only fullscreen with an explicit `--fullscreen`.
- User notes are saved at `<pdf path>.notes.json` with atomic writes.

### Presenter rework (2026-10-03, second pass)

- Toolbar is text only (no icons) with a "Panes" menu at the right.
- The presenter is a `QMainWindow` whose central widget is the current slide / overview
  stack; **Next slide**, **Notes** (`NotesPane(with_editor=False)`) and **My notes**
  (`UserNotesEditor`) are `QDockWidget`s (movable, floatable, closable, nestable, tabbable),
  layout persisted with `saveState(LAYOUT_VERSION)`; `reset_layout()` restores the default.
  The status widgets live in a `QStatusBar`.
- `NotesPane` keeps its old API; `with_editor=True` (default) embeds a `UserNotesEditor`.
- `SlideView.set_background(color)` sets the letterbox colour (presenter uses `theme.CRUST`).
- `Config.pointer_on` (default `true`); `PrezApp.set_pointer_mode(on, announce=...)`.
- `PresenterWindow.release_text_focus()` is the first step of the `cancel` action.
- `prez/theme.py`: Catppuccin Macchiato (foot) palette + Claude orange accent, applied as
  `QPalette` + stylesheet by `apply_theme`; all widget colours import from it.

### Third pass (2026-10-04)

- The user-notes editor (`UserNotesEditor`, `<pdf>.notes.json`) is gone: notes come from
  the PDF only. `NotesPane(render)` shows the notes region, else annotations, else a
  placeholder. Docks are **Next slide** and **Notes** (`LAYOUT_VERSION = 3`).
- Toolbar no longer has Open / Prev / Next; those remain keyboard actions (`O`, arrows).
- `prez/desktop.py` + `prez --install-desktop-file`: writes `prez.desktop`; `PrezApp` silences
  `qt.qpa.services` and logs a hint when it is missing.
