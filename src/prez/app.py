"""`PrezApp`: wires document, renderer, state and windows; dispatches named actions."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QEvent, QFileSystemWatcher, QKeyCombination, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QScreen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from prez import theme
from prez.config import Config, Pen
from prez.document import DocumentInfo, NotesMode, load_document
from prez.render import RenderKey, RenderService
from prez.screens import choose_screens, find_screen, place
from prez.state import PresentationState
from prez.timer import TalkTimer, TimerState
from prez.windows.content import ContentWindow
from prez.windows.presenter import PresenterWindow

log = logging.getLogger(__name__)

NOTES_CYCLE: tuple[NotesMode, ...] = (
    NotesMode.NONE,
    NotesMode.RIGHT,
    NotesMode.LEFT,
    NotesMode.TOP,
    NotesMode.BOTTOM,
    NotesMode.AFTER,
)
RELOAD_DEBOUNCE_MS = 300
RELOAD_MAX_ATTEMPTS = 20
SCREEN_SETTLE_MS = 500
TICK_MS = 250

TEXT_WIDGETS = (QLineEdit, QPlainTextEdit, QTextEdit)
MODIFIER_KEYS = frozenset(
    {
        Qt.Key.Key_Shift,
        Qt.Key.Key_Control,
        Qt.Key.Key_Alt,
        Qt.Key.Key_AltGr,
        Qt.Key.Key_Meta,
        Qt.Key.Key_Super_L,
        Qt.Key.Key_Super_R,
        Qt.Key.Key_Hyper_L,
        Qt.Key.Key_Hyper_R,
        Qt.Key.Key_CapsLock,
        Qt.Key.Key_NumLock,
        Qt.Key.Key_ScrollLock,
        Qt.Key.Key_unknown,
    }
)
# Actions still honoured while the overview page has the keyboard (it handles the rest).
OVERVIEW_ACTIONS = frozenset(
    {
        "overview",
        "cancel",
        "quit",
        "content-fullscreen",
        "presenter-fullscreen",
        "swap-screens",
        "blank",
        "blank-white",
        "freeze",
        "pause-timer",
        "reset-timer",
        "pick-file",
        "reload",
    }
)

PORTABLE = QKeySequence.SequenceFormat.PortableText


# ----------------------------------------------------------------------------- helpers


def apply_dark_theme(qapp: QApplication) -> None:
    """Catppuccin Macchiato palette with a Claude-orange accent (see prez.theme)."""
    theme.apply_theme(qapp)


def empty_document() -> DocumentInfo:
    """Placeholder document so that the render service and views exist before a file opens."""
    return DocumentInfo(
        path="",
        page_count=0,
        page_sizes=[],
        labels=[],
        links=[],
        annotations=[],
        outline=[],
        notes_mode=NotesMode.NONE,
        mtime=0.0,
    )


def normalize_sequence(text: str) -> str:
    """Canonical lowercase portable form of a key sequence string ("Escape" → "esc")."""
    return QKeySequence(text).toString(PORTABLE).lower()


def key_event_sequence(event: QKeyEvent) -> str:
    """Canonical lowercase portable form of the key combination in a key press event."""
    mods = event.modifiers() & ~(
        Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.GroupSwitchModifier
    )
    combo = QKeyCombination(Qt.KeyboardModifier(mods), Qt.Key(event.key()))
    return QKeySequence(combo).toString(PORTABLE).lower()


def build_shortcut_table(shortcuts: dict[str, list[str]]) -> dict[str, str]:
    """Map normalized key sequence → action name."""
    table: dict[str, str] = {}
    for action, sequences in shortcuts.items():
        for text in sequences:
            norm = normalize_sequence(text)
            if not norm:
                log.warning("ignoring unknown key sequence %r for action %s", text, action)
                continue
            if norm in table and table[norm] != action:
                log.warning("key %r bound to both %s and %s", text, table[norm], action)
            table[norm] = action
    return table


def _same_screen(a: QScreen | None, b: QScreen | None) -> bool:
    if a is None or b is None:
        return a is b
    return a is b or a.name() == b.name()


# ------------------------------------------------------------------------------ PrezApp


class PrezApp(QObject):
    """The application: owns the QApplication, the windows and all policy."""

    def __init__(
        self,
        argv: list[str],
        config: Config,
        path: str | None,
        *,
        notes_mode: str | None,
        talk_time: int | None,
        content_screen: str | None,
        fullscreen: bool | None,
        start_slide: int = 0,
    ) -> None:
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            self.qapp = existing
        else:
            self.qapp = QApplication(list(argv) or ["prez"])
        self.qapp.setApplicationName("prez")
        self.qapp.setDesktopFileName("prez")  # Wayland app_id, for compositor window rules
        self.qapp.setOrganizationName("prez")
        apply_dark_theme(self.qapp)
        super().__init__(self.qapp)

        self.config = config
        self.path: str | None = None
        self.doc: DocumentInfo | None = None
        self.notes_mode: str = notes_mode or config.notes_mode or "auto"
        self.timer = TalkTimer(talk_time if talk_time is not None else config.talk_time)
        self.state = PresentationState(self)
        self.render = RenderService(
            empty_document(), max_bytes=max(1, config.cache_max_mb) * 2**20, parent=self
        )

        self.pointer_mode = False
        self.draw_mode = False
        self.eraser = False
        self.active_pen = min(max(int(config.active_pen), 1), 9)
        self._timer_user_paused = False
        self._quitting = False
        self._loading = False
        self._explicit_fullscreen = fullscreen
        self._fullscreen = fullscreen if fullscreen is not None else config.start_fullscreen
        self._content_screen_name: str | None = content_screen or config.content_screen
        self._screen_names: tuple[str, str] | None = None
        self._shortcuts = build_shortcut_table(config.shortcuts)
        self._handlers = self._build_handlers()

        self.content = ContentWindow(self.render, self.state)
        self.content.view.set_pointer_style(config.pointer_color, config.pointer_size)
        self.presenter = PresenterWindow(self)
        self._wire_views()
        if config.pointer_on:
            self.set_pointer_mode(True, announce=False)

        self.state.slide_changed.connect(self._on_slide_changed)
        self.state.document_changed.connect(self._on_document_changed)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self.presenter.update_time(self.timer)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RELOAD_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self.reload)
        self._reload_attempts = 0

        self._screen_timer = QTimer(self)
        self._screen_timer.setSingleShot(True)
        self._screen_timer.setInterval(SCREEN_SETTLE_MS)
        self._screen_timer.timeout.connect(self._replace_windows)
        self.qapp.screenAdded.connect(self._on_screens_changed)
        self.qapp.screenRemoved.connect(self._on_screens_changed)

        self.qapp.installEventFilter(self)

        self._place_windows()
        if path and self.open(path) and start_slide:
            self._loading = True
            try:
                self.state.goto(start_slide)
            finally:
                self._loading = False
        if config.start_blanked:
            self.state.toggle_blank("black")

    # --------------------------------------------------------------- lifecycle

    def run(self) -> int:
        """Run the Qt event loop; returns the process exit code."""
        if self.doc is None and not self._quitting:
            QTimer.singleShot(0, self.pick_file)
        return int(self.qapp.exec())

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._tick.stop()
        self._reload_timer.stop()
        self._screen_timer.stop()
        try:
            self.presenter.flush_notes()
        except Exception:
            log.exception("flushing notes failed")
        try:
            self.presenter.save_settings()
        except Exception:
            log.exception("saving settings failed")
        self.render.stop()
        self.content.close()
        self.presenter.close()
        self.qapp.quit()

    # ---------------------------------------------------------------- document

    def open(self, path: str, *, keep_slide: bool = False) -> bool:
        """Load `path` (render worker stopped meanwhile) and install it everywhere."""
        path = os.path.abspath(path)
        self.render.stop()
        try:
            doc = load_document(path, self.notes_mode)
        except Exception as exc:
            log.exception("cannot open %s", path)
            self.state.notify(f"Cannot open {os.path.basename(path)}: {exc}")
            if self.doc is not None:
                self.render.start()
            return False
        same_file = self.path == path
        if self.path is not None and not same_file:
            try:
                self.presenter.notes.flush()
            except Exception:
                log.exception("flushing notes failed")
        self.path = path
        self.doc = doc
        self._watch(path)
        self.render.set_document(doc)
        self._loading = True
        try:
            self.state.set_document(doc, keep_slide=keep_slide or same_file)
        finally:
            self._loading = False
        log.info("opened %s: %d slides, notes=%s", path, doc.slide_count, doc.notes_mode.value)
        return True

    def reload(self) -> None:
        """Reload the current file in place, keeping the slide (debounced file watcher)."""
        if self._quitting or not self.path:
            return
        if not os.path.exists(self.path):
            # Editors replace files (delete + rename): give them a moment.
            self._reload_attempts += 1
            if self._reload_attempts < RELOAD_MAX_ATTEMPTS:
                self._reload_timer.start()
            else:
                self.state.notify(f"{os.path.basename(self.path)} disappeared")
            return
        self._reload_attempts = 0
        if self.open(self.path, keep_slide=True):
            self.state.notify(f"Reloaded {os.path.basename(self.path)}")
        else:
            self._watch(self.path)

    def _watch(self, path: str) -> None:
        old = self._watcher.files()
        if old:
            self._watcher.removePaths(old)
        if os.path.exists(path):
            self._watcher.addPath(path)

    def _on_file_changed(self, _path: str) -> None:
        self._reload_attempts = 0
        self._reload_timer.start()

    def pick_file(self) -> None:
        if self._quitting:
            return
        start_dir = os.path.dirname(self.path) if self.path else os.path.expanduser("~")
        path, _filter = QFileDialog.getOpenFileName(
            self.presenter, "Open PDF", start_dir, "PDF files (*.pdf);;All files (*)"
        )
        if path:
            self.open(path)

    def _on_document_changed(self, doc: object) -> None:
        if doc is not None:
            self._prerender(self.state.slide)

    # ----------------------------------------------------------------- actions

    def _build_handlers(self) -> dict[str, Callable[[], object]]:
        st = self.state
        handlers: dict[str, Callable[[], object]] = {
            "next": st.next,
            "prev": st.prev,
            "first": st.first,
            "last": st.last,
            "next-label": st.next_label,
            "prev-label": st.prev_label,
            "hist-back": st.back,
            "hist-forward": st.forward,
            "goto-page": lambda: self.presenter.show_goto(),
            "jump-label": lambda: self.presenter.show_goto(labels_first=True),
            "content-fullscreen": lambda: self.content.toggle_fullscreen(),
            "presenter-fullscreen": self.toggle_presenter_fullscreen,
            "notes-mode": self.cycle_notes_mode,
            "overview": lambda: self.presenter.toggle_overview(),
            "swap-screens": self.swap_screens,
            "blank": lambda: st.toggle_blank("black"),
            "blank-white": lambda: st.toggle_blank("white"),
            "freeze": st.toggle_freeze,
            "quit": self.quit,
            "pointer": lambda: self.set_pointer_mode(not self.pointer_mode),
            "pause-timer": self.toggle_timer,
            "reset-timer": self.reset_timer,
            "edit-talk-time": lambda: self.presenter.show_talk_time_edit(),
            "highlight": lambda: self.set_draw_mode(not self.draw_mode),
            "highlight-clear": lambda: st.clear_strokes(),
            "highlight-undo": lambda: st.undo_stroke(),
            "highlight-redo": lambda: st.redo_stroke(),
            "highlight-eraser": self.select_eraser,
            "pick-file": self.pick_file,
            "reload": self.reload,
            "validate": self.validate,
            "cancel": self.cancel,
        }
        for n in range(1, 10):
            handlers[f"highlight-pen-{n}"] = partial(self.select_pen, n)
        return handlers

    def dispatch(self, action: str) -> bool:
        """Execute an action by name. Returns False for unknown actions."""
        handler = self._handlers.get(action)
        if handler is None:
            log.warning("unknown action %r", action)
            return False
        log.debug("action %s", action)
        handler()
        return True

    def validate(self) -> None:
        self.presenter.validate_input()

    def cancel(self) -> None:
        presenter = self.presenter
        if presenter.cancel_input():
            return
        if presenter.release_text_focus():
            return
        if presenter.overview_visible():
            presenter.hide_overview()
            return
        if self.draw_mode:
            self.set_draw_mode(False)
            return
        if self.pointer_mode:
            self.set_pointer_mode(False)

    # ------------------------------------------------------------------ timer

    def _on_tick(self) -> None:
        self.presenter.update_time(self.timer)

    def _maybe_start_timer(self) -> None:
        if self._loading or self._timer_user_paused:
            return
        if self.timer.state == TimerState.IDLE:
            self.timer.start()
            self.presenter.update_time(self.timer)

    def toggle_timer(self) -> None:
        self.timer.toggle()
        self._timer_user_paused = self.timer.state == TimerState.PAUSED
        self.presenter.update_time(self.timer)
        self.state.notify("Timer paused" if self._timer_user_paused else "Timer running")

    def reset_timer(self) -> None:
        self.timer.reset()
        self.presenter.update_time(self.timer)
        self.state.notify("Timer reset")

    def set_talk_time(self, seconds: int | None) -> None:
        self.timer.set_talk_time(seconds)
        self.presenter.update_time(self.timer)
        if seconds is None:
            self.state.notify("Talk time cleared")
        else:
            self.state.notify(f"Talk time: {TalkTimer.format(seconds)}")

    # --------------------------------------------------------- pointer / pens

    def set_pointer_mode(self, on: bool, *, announce: bool = True) -> None:
        if on and self.draw_mode:
            self.set_draw_mode(False)
        self.pointer_mode = on
        if not on:
            self.state.set_pointer(None)
        self.presenter.set_checked("pointer", on)
        if announce:
            self.state.notify("Pointer on" if on else "Pointer off")

    def set_draw_mode(self, on: bool) -> None:
        if on and self.pointer_mode:
            self.set_pointer_mode(False)
        self.draw_mode = on
        self.presenter.current_view.set_draw_mode(on)
        self.presenter.set_checked("highlight", on)
        if on:
            tool = "eraser" if self.eraser else f"pen {self.active_pen}"
            self.state.notify(f"Highlight: {tool}")
        else:
            self.state.end_stroke()
            self.state.notify("Highlight off")

    def select_pen(self, number: int) -> None:
        self.active_pen = min(max(int(number), 1), 9)
        self.eraser = False
        if not self.draw_mode:
            self.set_draw_mode(True)
        else:
            self.state.notify(f"Pen {self.active_pen}")

    def select_eraser(self) -> None:
        self.eraser = True
        if not self.draw_mode:
            self.set_draw_mode(True)
        else:
            self.state.notify("Eraser")

    def current_pen(self) -> Pen:
        pens = self.config.pens
        if not pens:
            return Pen(color="#ffff0080", width=0.02)
        return pens[min(self.active_pen - 1, len(pens) - 1)]

    def _wire_views(self) -> None:
        view = self.presenter.current_view
        view.pointer_moved.connect(self._on_pointer_moved)
        view.stroke_started.connect(self._on_stroke_started)
        view.stroke_moved.connect(self._on_stroke_moved)
        view.stroke_ended.connect(self._on_stroke_ended)

    def _on_pointer_moved(self, pos: object) -> None:
        if self.pointer_mode:
            self.state.set_pointer(pos)  # type: ignore[arg-type]

    def _on_stroke_started(self, x: float, y: float) -> None:
        pen = self.current_pen()
        if self.eraser:
            width = float(getattr(self.config, "eraser_width", 0.0)) or max(pen.width * 2, 0.03)
            self.state.begin_stroke("#00000000", width, eraser=True)
        else:
            self.state.begin_stroke(pen.color, pen.width)
        self.state.add_point(x, y)

    def _on_stroke_moved(self, x: float, y: float) -> None:
        self.state.add_point(x, y)

    def _on_stroke_ended(self) -> None:
        self.state.end_stroke()

    # ------------------------------------------------------------- notes mode

    def cycle_notes_mode(self) -> None:
        if self.doc is None or not self.path:
            return
        current = self.doc.notes_mode
        index = NOTES_CYCLE.index(current) if current in NOTES_CYCLE else 0
        new_mode = NOTES_CYCLE[(index + 1) % len(NOTES_CYCLE)]
        self.notes_mode = new_mode.value
        if self.open(self.path, keep_slide=True):
            self.state.notify(f"Notes: {new_mode.value}")

    # ----------------------------------------------------------------- screens

    def _place_windows(self) -> None:
        try:
            content_screen, presenter_screen = choose_screens(self.qapp, self._content_screen_name)
        except RuntimeError:
            log.warning("no screens; showing windows unplaced")
            self.content.show()
            self.presenter.show()
            return
        self._screen_names = (content_screen.name(), presenter_screen.name())
        if _same_screen(content_screen, presenter_screen):
            # Single screen: never lock the user out behind a fullscreen content window
            # unless fullscreen was explicitly requested on the command line.
            if self._explicit_fullscreen is True:
                self.content.showFullScreen()
            else:
                self.content.show()
            self.presenter.show()
        else:
            place(self.content, content_screen, self._fullscreen)
            place(self.presenter, presenter_screen, False)
        self.presenter.raise_()
        self.presenter.activateWindow()

    def swap_screens(self) -> None:
        if self._screen_names is None:
            return
        content_name, presenter_name = self._screen_names
        content_screen = find_screen(self.qapp, content_name)
        presenter_screen = find_screen(self.qapp, presenter_name)
        if content_screen is None or presenter_screen is None:
            self._replace_windows()
            return
        if _same_screen(content_screen, presenter_screen):
            self.state.notify("Only one screen")
            return
        self._screen_names = (presenter_name, content_name)
        self._content_screen_name = presenter_name
        place(self.content, presenter_screen, self.content.is_fullscreen())
        place(self.presenter, content_screen, self.presenter.isFullScreen())
        self.state.notify(f"Content on {presenter_name}")

    def _on_screens_changed(self, *_args: object) -> None:
        if not self._quitting:
            self._screen_timer.start()

    def _replace_windows(self) -> None:
        """Screens were added/removed: re-choose and re-place the windows."""
        if self._quitting:
            return
        try:
            content_screen, presenter_screen = choose_screens(self.qapp, self._content_screen_name)
        except RuntimeError:
            return
        old = self._screen_names
        self._screen_names = (content_screen.name(), presenter_screen.name())
        if old is None:
            return
        single_now = _same_screen(content_screen, presenter_screen)
        single_before = old[0] == old[1]
        content_moved = content_screen.name() != old[0]
        presenter_moved = presenter_screen.name() != old[1]
        if single_now:
            if self.content.is_fullscreen() and self._explicit_fullscreen is not True:
                self.content.set_fullscreen(False)
            if not single_before:
                self.state.notify("External screen lost")
            return
        if content_moved or single_before:
            place(self.content, content_screen, self._fullscreen)
            self.state.notify(f"Content on {content_screen.name()}")
        if presenter_moved or single_before:
            place(self.presenter, presenter_screen, self.presenter.isFullScreen())
            self.presenter.activateWindow()

    def toggle_presenter_fullscreen(self) -> None:
        if self.presenter.isFullScreen():
            self.presenter.showMaximized()
        else:
            self.presenter.showFullScreen()

    # ------------------------------------------------------------- prerender

    def _on_slide_changed(self, slide: int) -> None:
        self._maybe_start_timer()
        self._prerender(slide)

    def _prerender(self, slide: int) -> None:
        """Queue neighbours at every size currently displayed (prio 1 for s+1, else 2)."""
        doc = self.state.doc
        if doc is None or doc.slide_count == 0:
            return
        self.render.cancel_below(1)
        sizes: set[tuple[object, int, int]] = set()
        views = [
            self.content.view,
            self.presenter.current_view,
            self.presenter.next_view,
            self.presenter.notes.slide_view(),
        ]
        for view in views:
            if view is None:
                continue
            key = view.current_key()
            if key is not None and key.width > 0 and key.height > 0:
                sizes.add((key.region, key.width, key.height))
        if not sizes:
            return
        targets = [(slide + 1, 1)]
        targets += [(slide + k, 2) for k in range(2, max(1, self.config.prerender) + 1)]
        targets.append((slide - 1, 2))
        for target, priority in targets:
            if 0 <= target < doc.slide_count:
                for region, width, height in sizes:
                    self.render.request(RenderKey(target, region, width, height), priority)

    # ------------------------------------------------------------- keyboard

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(obj, QWidget)
            and isinstance(event, QKeyEvent)
        ):
            try:
                if self._handle_key(obj, event):
                    event.accept()
                    return True
            except Exception:
                log.exception("key handling failed")
        return False

    def _handle_key(self, widget: QWidget, event: QKeyEvent) -> bool:
        window = widget.window()
        if window is not self.presenter and window is not self.content:
            return False
        if event.key() in MODIFIER_KEYS:
            return False
        action = self._shortcuts.get(key_event_sequence(event))

        focus = self.qapp.focusWidget()
        if isinstance(focus, TEXT_WIDGETS) or isinstance(widget, TEXT_WIDGETS):
            # Text entry owns the keyboard; the goto box validates itself on Return.
            if action == "cancel":
                return self.dispatch("cancel")
            return False

        if window is self.presenter and self.presenter.overview_visible():
            if action in OVERVIEW_ACTIONS:
                return self.dispatch(action)
            return False  # arrows / Return go to the overview widget

        if action is None:
            return False
        if (action.startswith("highlight-pen-") or action == "highlight-eraser") and (
            not self.draw_mode and event.text().isdigit()
        ):
            # Digits outside highlight mode start a "go to page" entry.
            self.presenter.show_goto(prefill=event.text())
            return True
        return self.dispatch(action)
