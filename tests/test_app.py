"""Tests for the core-ui modules: state, cli, key dispatch and an offscreen app smoke test."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402

from prez.state import PresentationState, Stroke  # noqa: E402

OTHER_MODULES = (
    "prez.document",
    "prez.timer",
    "prez.config",
    "prez.render",
    "prez.widgets.slide_view",
    "prez.widgets.overview",
    "prez.widgets.notes_view",
)


# ----------------------------------------------------------------------------- fakes


class FakeDoc:
    """Just enough of DocumentInfo for PresentationState."""

    def __init__(self, labels: list[str], path: str = "/tmp/fake.pdf") -> None:
        self.labels = labels
        self.path = path

    @property
    def slide_count(self) -> int:
        return len(self.labels)

    def next_label(self, slide: int) -> int:
        if slide + 1 >= self.slide_count:
            return slide
        i = slide + 1
        while i + 1 < self.slide_count and self.labels[i + 1] == self.labels[i]:
            i += 1
        return i

    def prev_label(self, slide: int) -> int:
        i = slide
        while i > 0 and self.labels[i - 1] == self.labels[slide]:
            i -= 1
        return max(i - 1, 0)

    def label_index(self, label: str) -> int | None:
        try:
            return self.labels.index(label)
        except ValueError:
            return None


DECK_LABELS = ["1", "2", "2", "3", "4", "5"]


class Recorder:
    """Collects signal emissions in order."""

    def __init__(self, state: PresentationState) -> None:
        self.events: list[tuple[str, object]] = []
        state.document_changed.connect(lambda d: self.events.append(("document", d)))
        state.slide_changed.connect(lambda s: self.events.append(("slide", s)))
        state.content_changed.connect(lambda s: self.events.append(("content", s)))
        state.blank_changed.connect(lambda k: self.events.append(("blank", k)))
        state.freeze_changed.connect(lambda f: self.events.append(("freeze", f)))
        state.pointer_changed.connect(lambda p: self.events.append(("pointer", p)))
        state.strokes_changed.connect(lambda s: self.events.append(("strokes", s)))
        state.message.connect(lambda m: self.events.append(("message", m)))

    def of(self, kind: str) -> list[object]:
        return [value for name, value in self.events if name == kind]

    def clear(self) -> None:
        self.events.clear()


@pytest.fixture
def state(qapp) -> PresentationState:
    return PresentationState()


@pytest.fixture
def loaded(state: PresentationState) -> tuple[PresentationState, Recorder]:
    rec = Recorder(state)
    state.set_document(FakeDoc(DECK_LABELS))
    rec.clear()
    return state, rec


# ------------------------------------------------------------------------ state: doc


def test_state_initial(state: PresentationState) -> None:
    assert state.doc is None
    assert state.slide == 0
    assert state.content_slide == 0
    assert state.blank is None
    assert state.frozen is False
    assert state.pointer is None
    assert state.strokes == {}
    assert state.slide_count == 0
    assert state.goto(3) is False
    assert state.next() is False


def test_set_document_emits_everything(state: PresentationState) -> None:
    rec = Recorder(state)
    doc = FakeDoc(DECK_LABELS)
    state.set_document(doc)
    assert rec.of("document") == [doc]
    assert rec.of("slide") == [0]
    assert rec.of("content") == [0]
    assert state.history == [0]
    assert state.history_pos == 0


def test_set_document_keep_slide_clamps(loaded) -> None:
    state, rec = loaded
    state.goto(5)
    state.set_document(FakeDoc(["1", "2", "3"]), keep_slide=True)
    assert state.slide == 2
    assert state.content_slide == 2
    state.set_document(FakeDoc(["1", "2", "3", "4"]), keep_slide=False)
    assert state.slide == 0


def test_set_document_none(loaded) -> None:
    state, rec = loaded
    state.set_document(None)
    assert state.doc is None
    assert rec.of("document") == [None]
    assert state.slide == 0


def test_reload_same_path_keeps_strokes_and_history(loaded) -> None:
    state, _ = loaded
    state.goto(2)
    state.begin_stroke("#ff0000", 0.01)
    state.add_point(0.1, 0.1)
    state.add_point(0.2, 0.2)
    state.end_stroke()
    state.set_document(FakeDoc(DECK_LABELS))  # same path
    assert state.slide == 2
    assert len(state.strokes[2]) == 1
    assert state.history == [0, 2]
    state.set_document(FakeDoc(DECK_LABELS, path="/tmp/other.pdf"))
    assert state.strokes == {}
    assert state.history == [2]  # restarted at the kept slide
    assert state.history_pos == 0


# ----------------------------------------------------------------- state: navigation


def test_goto_clamps_and_emits(loaded) -> None:
    state, rec = loaded
    assert state.goto(3) is True
    assert state.slide == 3
    assert state.content_slide == 3
    assert rec.of("slide") == [3]
    assert rec.of("content") == [3]
    rec.clear()
    assert state.goto(99) is True
    assert state.slide == 5
    assert state.goto(-4) is True
    assert state.slide == 0
    rec.clear()
    assert state.goto(0) is False  # no change → no signal
    assert rec.of("slide") == []


def test_next_prev_first_last(loaded) -> None:
    state, _ = loaded
    assert state.next() is True
    assert state.slide == 1
    assert state.prev() is True
    assert state.slide == 0
    assert state.prev() is False
    assert state.last() is True
    assert state.slide == 5
    assert state.next() is False
    assert state.first() is True
    assert state.slide == 0


def test_label_navigation(loaded) -> None:
    state, _ = loaded
    state.goto(1)  # label "2" (slides 1 and 2)
    assert state.next_label() is True
    assert state.slide == 2  # last overlay of label "2", like pympress
    assert state.next_label() is True
    assert state.slide == 3  # label "3"
    assert state.prev_label() is True
    assert state.slide == 2  # last slide of the previous label group
    assert state.prev_label() is True
    assert state.slide == 0
    assert state.prev_label() is False


def test_history_back_forward(loaded) -> None:
    state, _ = loaded
    state.goto(2)
    state.goto(4)
    assert state.history == [0, 2, 4]
    assert state.history_pos == 2
    assert state.back() is True
    assert state.slide == 2
    assert state.back() is True
    assert state.slide == 0
    assert state.back() is False
    assert state.forward() is True
    assert state.slide == 2
    # a new goto chops the forward history
    state.goto(5)
    assert state.history == [0, 2, 5]
    assert state.forward() is False
    # repeated goto of the same slide is not recorded twice
    state.goto(5)
    assert state.history == [0, 2, 5]


def test_history_not_recorded_when_asked(loaded) -> None:
    state, _ = loaded
    state.goto(3, record_history=False)
    assert state.history == [0]
    assert state.slide == 3


# ------------------------------------------------------------- state: blank / freeze


def test_toggle_blank(loaded) -> None:
    state, rec = loaded
    state.toggle_blank()
    assert state.blank == "black"
    state.toggle_blank("white")
    assert state.blank == "white"
    state.toggle_blank("white")
    assert state.blank is None
    assert rec.of("blank") == ["black", "white", None]


def test_freeze_keeps_content_slide(loaded) -> None:
    state, rec = loaded
    state.goto(1)
    rec.clear()
    state.toggle_freeze()
    assert state.frozen is True
    assert rec.of("freeze") == [True]
    state.next()
    state.next()
    assert state.slide == 3
    assert state.content_slide == 1
    assert rec.of("slide") == [2, 3]
    assert rec.of("content") == []
    state.toggle_freeze()
    assert state.frozen is False
    assert state.content_slide == 3
    assert rec.of("content") == [3]


def test_freeze_survives_reload(loaded) -> None:
    state, _ = loaded
    state.goto(1)
    state.toggle_freeze()
    state.goto(4)
    state.set_document(FakeDoc(DECK_LABELS))
    assert state.slide == 4
    assert state.content_slide == 1


# --------------------------------------------------------------------- state: pointer


def test_pointer(loaded) -> None:
    state, rec = loaded
    state.set_pointer((0.5, 0.25))
    assert state.pointer == (0.5, 0.25)
    state.set_pointer((0.5, 0.25))  # unchanged → no second emission
    state.set_pointer(None)
    assert state.pointer is None
    assert rec.of("pointer") == [(0.5, 0.25), None]


# --------------------------------------------------------------------- state: strokes


def _draw(state: PresentationState, color: str, pts: list[tuple[float, float]], w=0.01):
    state.begin_stroke(color, w)
    for x, y in pts:
        state.add_point(x, y)
    state.end_stroke()


def test_strokes_basic(loaded) -> None:
    state, rec = loaded
    _draw(state, "#ff0000", [(0.1, 0.1), (0.2, 0.2)])
    strokes = state.strokes_for(0)
    assert len(strokes) == 1
    assert isinstance(strokes[0], Stroke)
    assert strokes[0].color == "#ff0000"
    assert strokes[0].points == [(0.1, 0.1), (0.2, 0.2)]
    assert strokes[0].eraser is False
    assert rec.of("strokes") and all(s == 0 for s in rec.of("strokes"))
    # strokes are per slide
    state.goto(1)
    assert state.strokes_for(1) == []
    assert len(state.strokes_for(0)) == 1


def test_stroke_without_points_is_dropped(loaded) -> None:
    state, _ = loaded
    state.begin_stroke("#ff0000", 0.01)
    state.end_stroke()
    assert state.strokes_for(0) == []
    assert state.undo_stroke() is False


def test_undo_redo_clear(loaded) -> None:
    state, _ = loaded
    _draw(state, "#ff0000", [(0.1, 0.1), (0.2, 0.2)])
    _draw(state, "#00ff00", [(0.5, 0.5), (0.6, 0.6)])
    assert len(state.strokes_for(0)) == 2
    assert state.undo_stroke() is True
    assert len(state.strokes_for(0)) == 1
    assert state.redo_stroke() is True
    assert len(state.strokes_for(0)) == 2
    assert state.redo_stroke() is False
    assert state.clear_strokes() is True
    assert state.strokes_for(0) == []
    assert state.clear_strokes() is False
    assert state.undo_stroke() is True
    assert len(state.strokes_for(0)) == 2
    assert state.undo_stroke() is True
    assert state.undo_stroke() is True
    assert state.strokes_for(0) == []
    assert state.undo_stroke() is False
    # a new stroke after undo discards the redo stack
    _draw(state, "#0000ff", [(0.3, 0.3), (0.4, 0.4)])
    assert state.redo_stroke() is False


def test_eraser_removes_intersecting_strokes(loaded) -> None:
    state, _ = loaded
    _draw(state, "#ff0000", [(0.1, 0.5), (0.9, 0.5)])  # horizontal line
    _draw(state, "#00ff00", [(0.1, 0.9), (0.9, 0.9)])  # far away
    state.begin_stroke("#000000", 0.05, eraser=True)
    state.add_point(0.5, 0.2)
    state.add_point(0.5, 0.5)  # crosses the first line
    state.end_stroke()
    strokes = state.strokes_for(0)
    assert len(strokes) == 1
    assert strokes[0].color == "#00ff00"
    assert not any(s.eraser for s in strokes)
    # erasing is undoable
    assert state.undo_stroke() is True
    assert len(state.strokes_for(0)) == 2


def test_eraser_touching_nothing_is_not_undoable(loaded) -> None:
    state, _ = loaded
    _draw(state, "#ff0000", [(0.1, 0.1), (0.2, 0.1)])
    state.begin_stroke("#000000", 0.05, eraser=True)
    state.add_point(0.9, 0.9)
    state.end_stroke()
    assert len(state.strokes_for(0)) == 1
    assert state.undo_stroke() is True  # undoes the pen stroke, not the eraser
    assert state.strokes_for(0) == []
    assert state.undo_stroke() is False


def test_message(loaded) -> None:
    state, rec = loaded
    state.notify("hello")
    assert rec.of("message") == ["hello"]


# --------------------------------------------------------------------------------- cli


def test_cli_defaults() -> None:
    from prez.cli import parse_args

    args = parse_args([])
    assert args.file is None
    assert args.notes is None
    assert args.talk_time is None
    assert args.fullscreen is None
    assert args.page is None
    assert args.blank is False
    assert args.list_screens is False
    assert args.log_level == "WARNING"


def test_cli_options() -> None:
    from prez.cli import parse_args

    args = parse_args(
        [
            "deck.pdf",
            "--notes",
            "right",
            "--content-screen",
            "HDMI-1",
            "--no-fullscreen",
            "--page",
            "7",
            "--blank",
            "--log-level",
            "debug",
        ]
    )
    assert args.file == "deck.pdf"
    assert args.notes == "right"
    assert args.content_screen == "HDMI-1"
    assert args.fullscreen is False
    assert args.page == 7
    assert args.blank is True
    assert args.log_level == "DEBUG"
    assert parse_args(["--fullscreen"]).fullscreen is True


def test_cli_talk_time() -> None:
    pytest.importorskip("prez.timer")
    from prez.cli import parse_args

    assert parse_args(["--talk-time", "20:30"]).talk_time == 1230
    assert parse_args(["--talk-time", "20"]).talk_time == 1200
    assert parse_args(["--talk-time", "1:05:00"]).talk_time == 3900
    with pytest.raises(SystemExit):
        parse_args(["--talk-time", "abc"])


def test_cli_rejects_bad_values() -> None:
    from prez.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--page", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--notes", "sideways"])


def test_cli_list_screens(qapp, capsys) -> None:
    from prez.cli import main

    assert main(["--list-screens"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) >= 1
    assert "@" in out[0] and "x" in out[0]


def test_cli_missing_file(capsys) -> None:
    from prez.cli import main

    assert main(["/nonexistent/deck.pdf"]) == 2
    assert "no such file" in capsys.readouterr().err


# ------------------------------------------------------------------------ key handling


def _key_event(key: Qt.Key, mods=Qt.KeyboardModifier.NoModifier, text: str = "") -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, mods, text)


def test_shortcut_normalisation(qapp) -> None:
    for mod in OTHER_MODULES:
        pytest.importorskip(mod)
    from prez.app import build_shortcut_table, key_event_sequence, normalize_sequence

    table = build_shortcut_table(
        {
            "cancel": ["Escape"],
            "validate": ["Return", "Enter"],
            "next": ["Right", "Space", "PgDown"],
            "goto-page": ["G"],
            "reload": ["Ctrl+Shift+R"],
            "highlight-clear": ["Ctrl+Delete"],
        }
    )
    assert table[normalize_sequence("Esc")] == "cancel"
    assert table[key_event_sequence(_key_event(Qt.Key.Key_Escape))] == "cancel"
    assert table[key_event_sequence(_key_event(Qt.Key.Key_Return))] == "validate"
    keypad = Qt.KeyboardModifier.KeypadModifier
    assert table[key_event_sequence(_key_event(Qt.Key.Key_Enter, keypad))] == "validate"
    assert table[key_event_sequence(_key_event(Qt.Key.Key_PageDown))] == "next"
    assert table[key_event_sequence(_key_event(Qt.Key.Key_Space))] == "next"
    assert table[key_event_sequence(_key_event(Qt.Key.Key_G, text="g"))] == "goto-page"
    shift = Qt.KeyboardModifier.ShiftModifier
    assert key_event_sequence(_key_event(Qt.Key.Key_G, shift, "G")) not in table
    ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    assert table[key_event_sequence(_key_event(Qt.Key.Key_R, ctrl_shift))] == "reload"
    ctrl = Qt.KeyboardModifier.ControlModifier
    assert table[key_event_sequence(_key_event(Qt.Key.Key_Delete, ctrl))] == "highlight-clear"


# -------------------------------------------------------------------------- app smoke


@pytest.fixture
def prez_app(qtbot, tmp_path, request):
    for mod in OTHER_MODULES:
        pytest.importorskip(mod)
    try:
        deck_pdf = request.getfixturevalue("deck_pdf")
    except pytest.FixtureLookupError:
        pytest.skip("deck_pdf fixture (tests/conftest.py) not available yet")

    # Keep QSettings out of the real ~/.config.
    QSettings.setPath(
        QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "settings")
    )
    from prez.app import PrezApp
    from prez.config import Config

    config = Config()
    config.start_fullscreen = False
    app = PrezApp(
        [],
        config,
        deck_pdf,
        notes_mode=None,
        talk_time=None,
        content_screen=None,
        fullscreen=False,
    )
    qtbot.addWidget(app.content)
    qtbot.addWidget(app.presenter)
    yield app
    app.quit()
    app.qapp.removeEventFilter(app)


def _views(app):
    views = [app.content.view, app.presenter.current_view, app.presenter.next_view]
    notes_view = app.presenter.notes.slide_view()
    if notes_view is not None:
        views.append(notes_view)
    return views


def _all_rendered(app) -> bool:
    for view in _views(app):
        key = view.current_key()
        if key is not None and app.render.get(key) is None:
            return False
    return True


def test_app_smoke(prez_app, qtbot, tmp_path) -> None:
    from prez.timer import TimerState

    app = prez_app
    assert app.doc is not None
    assert app.doc.slide_count == 6
    assert app.state.slide == 0
    assert app.presenter.isVisible()
    assert app.content.isVisible()
    assert app.presenter.windowTitle().endswith("— prez")
    assert app.timer.state == TimerState.IDLE

    qtbot.waitSignal(app.render.rendered, timeout=5000).wait()

    assert app.dispatch("next") is True
    assert app.state.slide == 1
    assert app.timer.state == TimerState.RUNNING  # auto-started on first slide change
    assert app.dispatch("prev") is True
    assert app.state.slide == 0
    assert app.dispatch("blank") is True
    assert app.state.blank == "black"
    assert app.dispatch("blank") is True
    assert app.state.blank is None
    assert app.dispatch("freeze") is True
    assert app.state.frozen is True
    app.dispatch("next")
    assert app.state.slide == 1
    assert app.state.content_slide == 0
    assert app.content.view.slide == 0
    assert app.presenter.current_view.slide == 1
    assert app.dispatch("freeze") is True
    assert app.state.content_slide == 1
    assert app.pointer_mode is True  # on by default (config.pointer_on)
    app.presenter.current_view.pointer_moved.emit((0.5, 0.5))
    assert app.state.pointer == (0.5, 0.5)
    assert app.dispatch("pointer") is True
    assert app.pointer_mode is False
    assert app.state.pointer is None
    assert app.dispatch("pointer") is True
    assert app.pointer_mode is True
    assert app.dispatch("overview") is True
    assert app.presenter.overview_visible()
    assert app.dispatch("overview") is True
    assert not app.presenter.overview_visible()
    assert app.dispatch("last") is True
    assert app.state.slide == 5
    assert app.presenter.next_view.slide is None
    assert app.dispatch("first") is True
    assert app.state.slide == 0
    assert app.dispatch("no-such-action") is False

    qtbot.waitUntil(lambda: _all_rendered(app), timeout=5000)

    for name, widget in (("content", app.content), ("presenter", app.presenter)):
        path = tmp_path / f"{name}.png"
        pixmap = widget.grab()
        assert not pixmap.isNull()
        assert pixmap.save(str(path))
        assert path.stat().st_size > 0


def test_app_keyboard_dispatch(prez_app, qtbot) -> None:
    app = prez_app
    view = app.presenter.current_view
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert app.state.slide == 1
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert app.state.slide == 2
    qtbot.keyClick(view, Qt.Key.Key_Left)
    assert app.state.slide == 1
    qtbot.keyClick(view, Qt.Key.Key_End)
    assert app.state.slide == 5
    qtbot.keyClick(view, Qt.Key.Key_Home)
    assert app.state.slide == 0
    qtbot.keyClick(view, Qt.Key.Key_B)
    assert app.state.blank == "black"
    qtbot.keyClick(view, Qt.Key.Key_B)
    assert app.state.blank is None
    # the content window receives keys too
    qtbot.keyClick(app.content, Qt.Key.Key_Right)
    assert app.state.slide == 1


def test_app_goto_box(prez_app, qtbot) -> None:
    app = prez_app
    presenter = app.presenter
    view = presenter.current_view
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_G)
    assert presenter.input_active()
    qtbot.keyClicks(presenter.input_edit, "4")
    # while typing, navigation keys must not fire actions
    qtbot.keyClick(presenter.input_edit, Qt.Key.Key_Right)
    assert app.state.slide == 0
    qtbot.keyClick(presenter.input_edit, Qt.Key.Key_Return)
    assert not presenter.input_active()
    assert app.state.slide == 3
    # Escape cancels
    qtbot.keyClick(view, Qt.Key.Key_G)
    assert presenter.input_active()
    qtbot.keyClick(presenter.input_edit, Qt.Key.Key_Escape)
    assert not presenter.input_active()
    assert app.state.slide == 3
    # digits outside highlight mode start the goto entry prefilled
    qtbot.keyClicks(view, "2")
    assert presenter.input_active()
    assert presenter.input_edit.text() == "2"
    qtbot.keyClick(presenter.input_edit, Qt.Key.Key_Return)
    assert app.state.slide == 1
    # labels: "3" is the label of slide index 3
    app.dispatch("jump-label")
    qtbot.keyClicks(presenter.input_edit, "3")
    qtbot.keyClick(presenter.input_edit, Qt.Key.Key_Return)
    assert app.state.slide == 3


def test_app_highlight_and_pens(prez_app, qtbot) -> None:
    app = prez_app
    view = app.presenter.current_view
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_H)
    assert app.draw_mode is True
    qtbot.keyClicks(view, "5")
    assert app.active_pen == 5
    assert not app.presenter.input_active()
    view.stroke_started.emit(0.1, 0.1)
    view.stroke_moved.emit(0.3, 0.3)
    view.stroke_ended.emit()
    strokes = app.state.strokes_for(0)
    assert len(strokes) == 1
    assert strokes[0].color == app.config.pens[4].color
    qtbot.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert app.state.strokes_for(0) == []
    qtbot.keyClick(view, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert len(app.state.strokes_for(0)) == 1
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert app.draw_mode is False


def test_app_timer_controls(prez_app) -> None:
    from prez.timer import TimerState

    app = prez_app
    app.dispatch("pause-timer")  # IDLE → RUNNING
    assert app.timer.state == TimerState.RUNNING
    app.dispatch("pause-timer")
    assert app.timer.state == TimerState.PAUSED
    app.dispatch("next")
    assert app.timer.state == TimerState.PAUSED  # explicit pause wins over auto-start
    app.dispatch("reset-timer")
    assert app.timer.state == TimerState.IDLE
    app.set_talk_time(600)
    app.presenter.update_time(app.timer)
    assert app.presenter.progress.isVisibleTo(app.presenter)
    assert app.presenter.remaining_label.text() == app.timer.format(600)
    app.set_talk_time(None)
    app.presenter.update_time(app.timer)
    assert not app.presenter.progress.isVisibleTo(app.presenter)


def test_app_notes_mode_cycle_and_reload(prez_app) -> None:
    from prez.document import NotesMode

    app = prez_app
    app.dispatch("next")
    assert app.doc.notes_mode == NotesMode.NONE
    app.dispatch("notes-mode")
    assert app.doc.notes_mode == NotesMode.RIGHT
    assert app.state.slide == 1  # slide kept across the reload
    assert app.presenter.notes.slide_view() is not None
    app.reload()
    assert app.doc.notes_mode == NotesMode.RIGHT
    assert app.state.slide == 1


# ----------------------------------------------------------------------------- screens


def test_choose_screens_single(qapp) -> None:
    from prez.screens import choose_screens, describe_screen, find_screen

    content, presenter = choose_screens(qapp, None)
    assert content.name() == presenter.name()
    # an unknown preferred name falls back gracefully
    content, presenter = choose_screens(qapp, "no-such-screen")
    assert content.name() == presenter.name()
    if content.name():  # the offscreen platform's screen has no name
        assert find_screen(qapp, content.name()) is not None
    assert find_screen(qapp, "0") is not None
    assert find_screen(qapp, None) is None
    line = describe_screen(content, qapp.primaryScreen())
    geo = content.geometry()
    assert line.startswith(content.name())
    assert f"{geo.width()}x{geo.height()}@{geo.x()},{geo.y()}" in line
    assert line.endswith("primary")


# ------------------------------------------------------------------ presenter layout


def test_toolbar_has_no_icons(prez_app) -> None:
    for name, action in prez_app.presenter.actions.items():
        assert action.icon().isNull(), name


def test_docks_are_editable(prez_app, qtbot) -> None:
    from PySide6.QtWidgets import QDockWidget

    presenter = prez_app.presenter
    presenter.show()
    qtbot.waitExposed(presenter)
    assert set(presenter.docks) == {"next", "notes"}
    for dock in presenter.docks.values():
        features = dock.features()
        assert features & QDockWidget.DockWidgetFeature.DockWidgetMovable
        assert features & QDockWidget.DockWidgetFeature.DockWidgetFloatable
        assert features & QDockWidget.DockWidgetFeature.DockWidgetClosable
    # closing a dock and resetting the layout brings it back
    presenter.docks["notes"].close()
    assert not presenter.docks["notes"].isVisible()
    presenter.actions["reset-layout"].trigger()
    assert presenter.docks["notes"].isVisible()
    # the Panes menu lists a toggle per dock plus the reset entry
    texts = [a.text() for a in presenter.panes_menu.actions() if a.text()]
    assert texts == ["Next slide", "Notes", "Reset layout"]


def test_escape_leaves_text_view(prez_app, qtbot) -> None:
    presenter = prez_app.presenter
    presenter.show()
    qtbot.waitExposed(presenter)
    prez_app.state.goto(2)  # the fixture deck has an annotation on slide 3
    view = presenter.notes.annotations_view()
    assert view.isVisible()
    view.setFocus(Qt.FocusReason.OtherFocusReason)
    assert presenter.focusWidget() is view
    assert prez_app.dispatch("cancel") is True
    assert presenter.focusWidget() is presenter.current_view


def test_toolbar_has_no_open_prev_next(prez_app) -> None:
    names = set(prez_app.presenter.actions)
    assert not names & {"pick-file", "prev", "next"}
    assert prez_app.dispatch("next") is True  # keyboard/dispatch still work


def test_pointer_on_by_default_and_configurable(prez_app) -> None:
    from prez.config import Config

    assert Config().pointer_on is True
    assert prez_app.pointer_mode is True
    assert prez_app.presenter.actions["pointer"].isChecked()


def test_theme_uses_foot_palette(prez_app) -> None:
    from PySide6.QtGui import QPalette

    from prez import theme

    palette = prez_app.qapp.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == theme.BASE
    assert palette.color(QPalette.ColorRole.Highlight).name() == theme.PEACH
    assert theme.BASE == "#24273a"  # foot background
