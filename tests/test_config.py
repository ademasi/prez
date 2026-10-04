"""Tests for prez.config: actions, default shortcuts, TOML loading and validation."""

import logging
import tomllib
from pathlib import Path

import pytest

from prez.config import (
    ACTIONS,
    DEFAULT_CONFIG_TEXT,
    DEFAULT_SHORTCUTS,
    Config,
    Pen,
    config_from_mapping,
    config_path,
    default_pens,
    load_config,
    parse_color,
    parse_shortcuts,
)

# -- actions and default shortcuts ----------------------------------------------------------


def test_actions_are_complete_unique_and_ordered():
    assert len(ACTIONS) == 41
    assert len(set(ACTIONS)) == len(ACTIONS)
    assert ACTIONS[:8] == (
        "next",
        "prev",
        "first",
        "last",
        "next-label",
        "prev-label",
        "hist-back",
        "hist-forward",
    )
    pen_start = ACTIONS.index("highlight-pen-1")
    assert ACTIONS[pen_start : pen_start + 9] == tuple(f"highlight-pen-{n}" for n in range(1, 10))
    assert ACTIONS[pen_start + 9 :] == (
        "highlight-eraser",
        "pick-file",
        "reload",
        "validate",
        "cancel",
    )
    assert ACTIONS[ACTIONS.index("highlight-redo") + 1] == "highlight-pen-1"


def test_default_shortcuts_cover_every_action():
    assert set(DEFAULT_SHORTCUTS) == set(ACTIONS)
    for action, keys in DEFAULT_SHORTCUTS.items():
        assert isinstance(keys, list), action
        assert keys, f"{action} has no default binding"
        assert all(isinstance(key, str) and key and " " not in key for key in keys), action


def test_default_shortcuts_have_no_conflicts():
    owners: dict[str, str] = {}
    for action, keys in DEFAULT_SHORTCUTS.items():
        for key in keys:
            assert key.casefold() not in owners, (
                f"{key} bound to {owners.get(key.casefold())} and {action}"
            )
            owners[key.casefold()] = action


@pytest.mark.parametrize(
    ("action", "keys"),
    [
        ("next", ["Right", "Down", "PgDown", "Space"]),
        ("prev", ["Left", "Up", "PgUp"]),
        ("first", ["Home"]),
        ("last", ["End"]),
        ("next-label", ["Ctrl+Right", "Ctrl+Down", "Ctrl+PgDown", "Ctrl+Space"]),
        ("prev-label", ["Ctrl+Left", "Ctrl+Up", "Ctrl+PgUp"]),
        ("hist-back", ["Alt+Left"]),
        ("hist-forward", ["Alt+Right"]),
        ("goto-page", ["G"]),
        ("jump-label", ["J"]),
        ("content-fullscreen", ["F11", "F", "F5", "Ctrl+L"]),
        ("presenter-fullscreen", ["Ctrl+F"]),
        ("notes-mode", ["N"]),
        ("overview", ["D"]),
        ("swap-screens", ["S"]),
        ("blank", ["B"]),
        ("blank-white", ["W"]),
        ("freeze", ["Z"]),
        ("quit", ["Q", "Ctrl+Q"]),
        ("pointer", ["L"]),
        ("pause-timer", ["P", "Pause"]),
        ("reset-timer", ["R"]),
        ("edit-talk-time", ["T"]),
        ("highlight", ["H"]),
        ("highlight-clear", ["Ctrl+Del"]),
        ("highlight-undo", ["Ctrl+Z"]),
        ("highlight-redo", ["Ctrl+R"]),
        ("highlight-pen-1", ["1"]),
        ("highlight-pen-9", ["9"]),
        ("highlight-eraser", ["0"]),
        ("pick-file", ["O"]),
        ("reload", ["Ctrl+Shift+R"]),
        ("validate", ["Return", "Enter"]),
    ],
)
def test_default_shortcut_values(action, keys):
    assert DEFAULT_SHORTCUTS[action] == keys


def test_cancel_is_bound_to_escape():
    assert DEFAULT_SHORTCUTS["cancel"][0] == "Escape"
    assert "Esc" in DEFAULT_SHORTCUTS["cancel"]


def test_default_shortcuts_are_valid_qt_sequences():
    QKeySequence = pytest.importorskip("PySide6.QtGui").QKeySequence
    portable = QKeySequence.SequenceFormat.PortableText
    for action, keys in DEFAULT_SHORTCUTS.items():
        for key in keys:
            sequence = QKeySequence(key)
            assert sequence.count() == 1, f"{action}: {key!r} is not a single key"
            assert sequence.toString(portable), f"{action}: {key!r} is unknown to Qt"


# -- parse helpers --------------------------------------------------------------------------


def test_parse_shortcuts(caplog: pytest.LogCaptureFixture):
    table = {
        "next": "Right  Down\tPgDown",
        "prev": ["Left", "Up PgUp"],
        "quit": "",
        "blank": 7,
    }
    with caplog.at_level(logging.WARNING, logger="prez.config"):
        parsed = parse_shortcuts(table)
    assert parsed == {
        "next": ["Right", "Down", "PgDown"],
        "prev": ["Left", "Up", "PgUp"],
        "quit": [],
    }
    assert "blank" in caplog.text


@pytest.mark.parametrize(
    ("text", "rgba"),
    [
        ("#ff0000", (255, 0, 0, 255)),
        ("#FFFF0080", (255, 255, 0, 128)),
        ("#00bb00", (0, 187, 0, 255)),
        ("#00000000", (0, 0, 0, 0)),
    ],
)
def test_parse_color(text, rgba):
    assert parse_color(text) == rgba


@pytest.mark.parametrize("text", ["red", "#fff", "#ggGGgg", "ff0000", "#ff00000", "", None])
def test_parse_color_rejects(text):
    with pytest.raises(ValueError):
        parse_color(text)


# -- defaults -------------------------------------------------------------------------------


def test_config_defaults():
    cfg = Config()
    assert cfg.shortcuts == DEFAULT_SHORTCUTS
    assert cfg.shortcuts is not DEFAULT_SHORTCUTS
    cfg.shortcuts["next"].append("X")
    assert DEFAULT_SHORTCUTS["next"] == ["Right", "Down", "PgDown", "Space"]  # deep copy
    assert cfg.notes_mode == "auto"
    assert cfg.talk_time is None
    assert cfg.cache_max_mb == 512
    assert cfg.prerender == 2
    assert cfg.content_screen is None
    assert cfg.start_fullscreen is True
    assert cfg.start_blanked is False
    assert cfg.pointer_color == "#ff0000"
    assert cfg.pointer_size == 0.02
    assert cfg.active_pen == 1
    assert cfg.next_count == 1
    assert cfg.slide_ratio == 0.6
    assert cfg.show_clock is True


def test_default_pens_mirror_pympress():
    pens = default_pens()
    assert len(pens) == 9
    assert Config().pens == pens
    for pen in pens[:4]:  # translucent wide highlighters
        assert len(pen.color) == 9 and pen.color.endswith("80")
        assert pen.width == pytest.approx(0.05)
    for pen in pens[4:8]:  # opaque thin pens
        assert len(pen.color) == 7
        assert pen.width == pytest.approx(0.004)
    assert pens[0].color == "#ffff0080"
    assert pens[4].color == "#ef2929"
    assert pens[7].color == "#000000"
    assert pens[8] == Pen("#888888", 0.03)
    for pen in pens:
        parse_color(pen.color)  # all valid


# -- paths ----------------------------------------------------------------------------------


def test_config_path_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "prez" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")  # must be absolute per XDG
    assert config_path() == Path.home() / ".config" / "prez" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config_path() == Path.home() / ".config" / "prez" / "config.toml"


# -- loading --------------------------------------------------------------------------------


def test_missing_file_writes_defaults(tmp_path: Path):
    path = tmp_path / "deep" / "er" / "config.toml"
    cfg = load_config(path)
    assert cfg == Config()
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEXT
    assert load_config(path) == Config()  # the written file reproduces the defaults


def test_default_text_is_valid_toml_with_every_action_documented():
    table = tomllib.loads(DEFAULT_CONFIG_TEXT)
    assert config_from_mapping(table) == Config()
    for action in ACTIONS:
        assert f"# {action} = " in DEFAULT_CONFIG_TEXT
    for index in range(1, 10):
        assert f"# {index} = {{ color = " in DEFAULT_CONFIG_TEXT


def test_load_uses_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_config() == Config()
    assert (tmp_path / "prez" / "config.toml").is_file()


def test_load_overrides(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
notes_mode = "Right"
talk_time = 1500
cache_max_mb = 64
prerender = 5
content_screen = "HDMI-1"
start_fullscreen = false
start_blanked = true
pointer_color = "#00FF00"
pointer_size = 0.05
active_pen = 7
next_count = 2
slide_ratio = 0.5
show_clock = false
eraser_width = 0.1

[shortcuts]
next = "Space N"
quit = ["Ctrl+Q"]
blank = ""

[pens]
2 = { color = "#123456", width = 0.02 }
9 = { color = "#abcdef80" }
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.notes_mode == "right"
    assert cfg.talk_time == 1500
    assert cfg.cache_max_mb == 64
    assert cfg.prerender == 5
    assert cfg.content_screen == "HDMI-1"
    assert cfg.start_fullscreen is False
    assert cfg.start_blanked is True
    assert cfg.pointer_color == "#00ff00"
    assert cfg.pointer_size == 0.05
    assert cfg.active_pen == 7
    assert cfg.next_count == 2
    assert cfg.slide_ratio == 0.5
    assert cfg.show_clock is False
    assert cfg.eraser_width == 0.1
    assert cfg.shortcuts["next"] == ["Space", "N"]
    assert cfg.shortcuts["quit"] == ["Ctrl+Q"]
    assert cfg.shortcuts["blank"] == []
    assert cfg.shortcuts["prev"] == DEFAULT_SHORTCUTS["prev"]  # untouched actions keep defaults
    assert set(cfg.shortcuts) == set(ACTIONS)
    assert cfg.pens[1] == Pen("#123456", 0.02)
    assert cfg.pens[8] == Pen("#abcdef80", 0.03)  # width kept from the default
    assert cfg.pens[0] == default_pens()[0]


def test_pens_as_array_of_tables(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[[pens]]
color = "#111111"
width = 0.01

[[pens]]
color = "#222222"
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.pens[0] == Pen("#111111", 0.01)
    assert cfg.pens[1] == Pen("#222222", 0.05)
    assert cfg.pens[2:] == default_pens()[2:]


@pytest.mark.parametrize(
    ("value", "expected"),
    [('"20:30"', 1230), ('"45"', 2700), ("0", None), ('"0"', None), ("90.0", 90)],
)
def test_talk_time_forms(tmp_path: Path, value, expected):
    path = tmp_path / "config.toml"
    path.write_text(f"talk_time = {value}\n", encoding="utf-8")
    assert load_config(path).talk_time == expected


def test_malformed_file_falls_back_to_defaults(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "config.toml"
    path.write_text("this is = not [valid\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="prez.config"):
        cfg = load_config(path)
    assert cfg == Config()
    assert "cannot read" in caplog.text


def test_binary_garbage_falls_back_to_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert load_config(path) == Config()


def test_bad_values_fall_back_with_warnings(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "config.toml"
    path.write_text(
        """
notes_mode = "sideways"
talk_time = -3
cache_max_mb = 0
prerender = "two"
content_screen = 3
start_fullscreen = 1
pointer_color = "red"
pointer_size = 7
active_pen = 12
slide_ratio = 5
show_clock = "yes"
shortcuts = "Right"
pens = 3
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="prez.config"):
        cfg = load_config(path)
    assert cfg == Config()
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) >= 13


def test_bad_pen_entries_warn_and_keep_defaults(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[pens]
0 = { color = "#111111" }
10 = { color = "#111111" }
x = { color = "#111111" }
3 = "blue"
4 = { color = "blue", width = 7 }
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="prez.config"):
        cfg = load_config(path)
    assert cfg.pens == default_pens()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) >= 6


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('colour_scheme = "dark"\n[video]\nenabled = true\n', encoding="utf-8")
    assert load_config(path) == Config()


def test_unknown_action_and_conflicts_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "config.toml"
    path.write_text('[shortcuts]\nfly = "F"\nprev = "right"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="prez.config"):
        cfg = load_config(path)
    assert "fly" not in cfg.shortcuts
    assert cfg.shortcuts["prev"] == ["right"]
    assert "unknown action" in caplog.text
    assert "bound to both" in caplog.text  # "right" collides with next's "Right"


def test_unwritable_location_still_returns_defaults(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    cfg = load_config(blocker / "config.toml")
    assert cfg == Config()


def test_load_accepts_str_path(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("prerender = 3\n", encoding="utf-8")
    assert load_config(str(path)).prerender == 3
