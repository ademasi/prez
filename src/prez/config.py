"""Configuration: TOML file, keyboard shortcuts and highlight pens. No Qt imports.

The file lives at ``$XDG_CONFIG_HOME/prez/config.toml`` (``~/.config/prez/config.toml``).
When it is missing a commented default file is written. Unknown keys are ignored; values
of the wrong type or out of range fall back to the default with a logged warning. Loading
never raises because of the file's content.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from prez.timer import TalkTimer

logger = logging.getLogger(__name__)

#: Every action name the key dispatcher knows, in menu order.
ACTIONS: tuple[str, ...] = (
    "next",
    "prev",
    "first",
    "last",
    "next-label",
    "prev-label",
    "hist-back",
    "hist-forward",
    "goto-page",
    "jump-label",
    "content-fullscreen",
    "presenter-fullscreen",
    "notes-mode",
    "overview",
    "swap-screens",
    "blank",
    "blank-white",
    "freeze",
    "quit",
    "pointer",
    "pause-timer",
    "reset-timer",
    "edit-talk-time",
    "highlight",
    "highlight-clear",
    "highlight-undo",
    "highlight-redo",
    *(f"highlight-pen-{n}" for n in range(1, 10)),
    "highlight-eraser",
    "pick-file",
    "reload",
    "validate",
    "cancel",
)

#: Default bindings as Qt portable QKeySequence strings, mirroring pympress.
#: Note that Qt prints Key_Escape as "Esc"; both spellings are listed so that either a
#: string comparison or a QKeySequence comparison matches.
DEFAULT_SHORTCUTS: dict[str, list[str]] = {
    "next": ["Right", "Down", "PgDown", "Space"],
    "prev": ["Left", "Up", "PgUp"],
    "first": ["Home"],
    "last": ["End"],
    "next-label": ["Ctrl+Right", "Ctrl+Down", "Ctrl+PgDown", "Ctrl+Space"],
    "prev-label": ["Ctrl+Left", "Ctrl+Up", "Ctrl+PgUp"],
    "hist-back": ["Alt+Left"],
    "hist-forward": ["Alt+Right"],
    "goto-page": ["G"],
    "jump-label": ["J"],
    "content-fullscreen": ["F11", "F", "F5", "Ctrl+L"],
    "presenter-fullscreen": ["Ctrl+F"],
    "notes-mode": ["N"],
    "overview": ["D"],
    "swap-screens": ["S"],
    "blank": ["B"],
    "blank-white": ["W"],
    "freeze": ["Z"],
    "quit": ["Q", "Ctrl+Q"],
    "pointer": ["L"],
    "pause-timer": ["P", "Pause"],
    "reset-timer": ["R"],
    "edit-talk-time": ["T"],
    "highlight": ["H"],
    "highlight-clear": ["Ctrl+Del"],
    "highlight-undo": ["Ctrl+Z"],
    "highlight-redo": ["Ctrl+R"],
    **{f"highlight-pen-{n}": [str(n)] for n in range(1, 10)},
    "highlight-eraser": ["0"],
    "pick-file": ["O"],
    "reload": ["Ctrl+Shift+R"],
    "validate": ["Return", "Enter"],
    "cancel": ["Escape", "Esc"],
}

#: Accepted values of ``notes_mode`` ("auto" plus every NotesMode value).
NOTES_MODES: tuple[str, ...] = ("auto", "none", "right", "left", "top", "bottom", "after")

_COLOR_RE = re.compile(r"\A#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")


@dataclass
class Pen:
    color: str  # "#rrggbbaa" or "#rrggbb"
    width: float  # normalized to the slide width (e.g. 0.01)


def default_pens() -> list[Pen]:
    """pympress's nine highlight pens: four translucent wide highlighters, four opaque thin
    pens and one wide grey pen, with widths as fractions of the slide width."""
    highlighter, pen, grey = 0.05, 0.004, 0.03
    return [
        Pen("#ffff0080", highlighter),
        Pen("#80ff0080", highlighter),
        Pen("#ff008080", highlighter),
        Pen("#3030ff80", highlighter),
        Pen("#ef2929", pen),
        Pen("#0060ff", pen),
        Pen("#00bb00", pen),
        Pen("#000000", pen),
        Pen("#888888", grey),
    ]


def _default_shortcuts() -> dict[str, list[str]]:
    return {action: list(keys) for action, keys in DEFAULT_SHORTCUTS.items()}


@dataclass
class Config:
    shortcuts: dict[str, list[str]] = field(default_factory=_default_shortcuts)
    notes_mode: str = "auto"  # "auto" | NotesMode value
    talk_time: int | None = None  # seconds
    cache_max_mb: int = 512
    prerender: int = 2  # slides ahead to prerender (1 behind always)
    content_screen: str | None = None  # QScreen.name() to put content on
    start_fullscreen: bool = True  # content fullscreen on start
    start_blanked: bool = False
    pointer_color: str = "#ff0000"
    pointer_size: float = 0.02  # fraction of slide width
    pens: list[Pen] = field(default_factory=default_pens)  # 9 pens
    active_pen: int = 1  # 1..9
    next_count: int = 1  # next-slide previews (v1 uses 1; keep the field)
    slide_ratio: float = 0.6  # presenter: current-slide pane fraction
    show_clock: bool = True
    eraser_width: float = 0.05  # eraser stroke width, fraction of slide width


def _default_config_text() -> str:
    shortcut_lines = "\n".join(
        f'# {action} = "{" ".join(keys)}"' for action, keys in DEFAULT_SHORTCUTS.items()
    )
    pen_lines = "\n".join(
        f'# {index} = {{ color = "{pen.color}", width = {pen.width} }}'
        for index, pen in enumerate(default_pens(), 1)
    )
    return f"""\
# prez configuration (TOML). Every key is optional: a missing key keeps its default,
# which is the value shown here. Lines starting with "#" are comments.

# Where the speaker notes are: "auto" detects wide [slide | notes] pages, otherwise
# one of "none", "right", "left", "top", "bottom", "after" (slide page then notes page).
notes_mode = "auto"

# Expected talk duration, in seconds or as "MM", "MM:SS" or "H:MM:SS". Unset: no limit.
# talk_time = "20:00"

# Memory budget of the rendered-slide cache, in megabytes.
cache_max_mb = 512

# Slides ahead of the current one to pre-render (one slide behind is always kept).
prerender = 2

# Screen for the content window, by name (see `prez --list-screens`). Empty: automatic.
# content_screen = "HDMI-1"

# Content window fullscreen at startup; start with the content blanked.
start_fullscreen = true
start_blanked = false

# Laser pointer: colour ("#rrggbb" or "#rrggbbaa") and diameter as a fraction of the
# slide width.
pointer_color = "#ff0000"
pointer_size = 0.02

# Highlighting: pen selected at startup (1-9), eraser width (fraction of the slide width).
active_pen = 1
eraser_width = 0.05

# Presenter window: share of the width given to the current slide, number of next-slide
# previews, wall clock in the status bar.
slide_ratio = 0.6
next_count = 1
show_clock = true

# Keyboard shortcuts: Qt key names separated by spaces (e.g. "Ctrl+Right PgDown").
# Uncomment a line to replace the default binding of that action; "" unbinds it.
[shortcuts]
{shortcut_lines}

# Highlight pens 1-9: colour ("#rrggbb", or "#rrggbbaa" for a translucent highlighter)
# and stroke width as a fraction of the slide width. Pens left out keep their defaults.
# [pens]
{pen_lines}
"""


#: Contents written to the config file when it does not exist yet.
DEFAULT_CONFIG_TEXT: str = _default_config_text()


def config_path() -> Path:
    """``$XDG_CONFIG_HOME/prez/config.toml``, defaulting to ``~/.config/prez/config.toml``."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".config"
    return root / "prez" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load the configuration from ``path`` (default :func:`config_path`).

    A missing file is created with :data:`DEFAULT_CONFIG_TEXT`. Unreadable or malformed
    files log a warning and yield the defaults; this never raises because of the file.
    """
    file = Path(path) if path is not None else config_path()
    if not file.exists():
        _write_default(file)
        return Config()
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("config: cannot read %s (%s); using defaults", file, exc)
        return Config()
    return config_from_mapping(raw)


def _write_default(file: Path) -> None:
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    except OSError as exc:
        logger.warning("config: cannot write default configuration to %s: %s", file, exc)
    else:
        logger.info("config: wrote default configuration to %s", file)


def parse_shortcuts(table: Mapping[str, str | list[str]]) -> dict[str, list[str]]:
    """``{"next": "Right Down"}`` → ``{"next": ["Right", "Down"]}``.

    Values may be a whitespace-separated string or a list of such strings; an empty string
    gives an empty list (unbound). Other values are skipped with a warning. Action names
    are not validated here (see :func:`config_from_mapping`).
    """
    result: dict[str, list[str]] = {}
    for action, value in table.items():
        if isinstance(value, str):
            keys = value.split()
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            keys = [key for item in value for key in item.split()]
        else:
            logger.warning(
                "config: ignoring shortcut %s = %r (expected a string or a list of strings)",
                action,
                value,
            )
            continue
        result[str(action)] = keys
    return result


def parse_color(text: str) -> tuple[int, int, int, int]:
    """``"#rrggbb"`` or ``"#rrggbbaa"`` → ``(r, g, b, a)`` with 0..255 components.

    Qt's 8-digit ``#AARRGGBB`` order differs from this file format, so UI code should use
    this helper rather than ``QColor(text)``.
    """
    if not isinstance(text, str) or not _COLOR_RE.match(text):
        raise ValueError(f"invalid colour {text!r}; expected #rrggbb or #rrggbbaa")
    digits = text[1:]
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    alpha = int(digits[6:8], 16) if len(digits) == 8 else 255
    return red, green, blue, alpha


def config_from_mapping(raw: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from a parsed TOML table, validating every value."""
    cfg = Config()
    known = {f.name for f in fields(Config)}
    for key in raw:
        if key not in known:
            logger.info("config: ignoring unknown key %r", key)

    cfg.notes_mode = _choice(raw, "notes_mode", cfg.notes_mode, NOTES_MODES)
    cfg.talk_time = _talk_time(raw, cfg.talk_time)
    cfg.cache_max_mb = _int(raw, "cache_max_mb", cfg.cache_max_mb, minimum=1)
    cfg.prerender = _int(raw, "prerender", cfg.prerender, minimum=0)
    cfg.content_screen = _optional_str(raw, "content_screen", cfg.content_screen)
    cfg.start_fullscreen = _bool(raw, "start_fullscreen", cfg.start_fullscreen)
    cfg.start_blanked = _bool(raw, "start_blanked", cfg.start_blanked)
    cfg.pointer_color = _color(raw, "pointer_color", cfg.pointer_color)
    cfg.pointer_size = _float(raw, "pointer_size", cfg.pointer_size, minimum=0.001, maximum=1.0)
    cfg.active_pen = _int(raw, "active_pen", cfg.active_pen, minimum=1, maximum=len(cfg.pens))
    cfg.next_count = _int(raw, "next_count", cfg.next_count, minimum=0)
    cfg.slide_ratio = _float(raw, "slide_ratio", cfg.slide_ratio, minimum=0.1, maximum=0.9)
    cfg.show_clock = _bool(raw, "show_clock", cfg.show_clock)
    cfg.eraser_width = _float(raw, "eraser_width", cfg.eraser_width, minimum=0.0005, maximum=1.0)

    shortcuts = raw.get("shortcuts")
    if isinstance(shortcuts, Mapping):
        for action, keys in parse_shortcuts(shortcuts).items():
            if action not in ACTIONS:
                logger.warning("config: ignoring shortcut for unknown action %r", action)
                continue
            cfg.shortcuts[action] = keys
        _warn_shortcut_conflicts(cfg.shortcuts)
    elif shortcuts is not None:
        _warn("shortcuts", shortcuts, "a table of action = keys")

    _apply_pens(raw.get("pens"), cfg.pens)
    return cfg


# -- value validation ----------------------------------------------------------------------


def _warn(key: str, value: Any, expected: str) -> None:
    logger.warning(
        "config: ignoring %s = %r (expected %s); using the default", key, value, expected
    )


def _bool(table: Mapping[str, Any], key: str, default: bool) -> bool:
    value = table.get(key, default)
    if isinstance(value, bool):
        return value
    _warn(key, value, "true or false")
    return default


def _int(
    table: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = table.get(key, default)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and _in_range(value, minimum, maximum)
    ):
        return value
    _warn(key, value, _range_text("an integer", minimum, maximum))
    return default


def _float(
    table: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = table.get(key, default)
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and _in_range(float(value), minimum, maximum)
    ):
        return float(value)
    _warn(key, value, _range_text("a number", minimum, maximum))
    return default


def _in_range(value: float, minimum: float | None, maximum: float | None) -> bool:
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _range_text(kind: str, minimum: float | None, maximum: float | None) -> str:
    if minimum is not None and maximum is not None:
        return f"{kind} between {minimum} and {maximum}"
    if minimum is not None:
        return f"{kind} >= {minimum}"
    if maximum is not None:
        return f"{kind} <= {maximum}"
    return kind


def _choice(table: Mapping[str, Any], key: str, default: str, choices: tuple[str, ...]) -> str:
    value = table.get(key, default)
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    _warn(key, value, "one of " + ", ".join(repr(choice) for choice in choices))
    return default


def _optional_str(table: Mapping[str, Any], key: str, default: str | None) -> str | None:
    value = table.get(key, default)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    _warn(key, value, "a string")
    return default


def _color(table: Mapping[str, Any], key: str, default: str) -> str:
    value = table.get(key, default)
    if isinstance(value, str) and _COLOR_RE.match(value.strip()):
        return value.strip().lower()
    _warn(key, value, '"#rrggbb" or "#rrggbbaa"')
    return default


def _talk_time(table: Mapping[str, Any], default: int | None) -> int | None:
    value = table.get("talk_time", default)
    if value is None:
        return None
    if isinstance(value, str):
        try:
            seconds = TalkTimer.parse(value)
        except ValueError:
            _warn("talk_time", value, 'seconds or "MM", "MM:SS", "H:MM:SS"')
            return default
    elif isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        seconds = int(value)
    else:
        _warn("talk_time", value, 'non-negative seconds or "MM", "MM:SS", "H:MM:SS"')
        return default
    return seconds or None


def _warn_shortcut_conflicts(shortcuts: Mapping[str, list[str]]) -> None:
    seen: dict[str, str] = {}
    for action in ACTIONS:
        for key in shortcuts.get(action, []):
            folded = key.casefold()
            if folded in seen and seen[folded] != action:
                logger.warning(
                    "config: key %r is bound to both %r and %r", key, seen[folded], action
                )
            seen.setdefault(folded, action)


def _apply_pens(value: Any, pens: list[Pen]) -> None:
    """Override pens in place from a ``[pens]`` table keyed 1..9 or a ``[[pens]]`` array."""
    if value is None:
        return
    if isinstance(value, list):
        entries: list[tuple[Any, Any]] = list(enumerate(value, 1))
    elif isinstance(value, Mapping):
        entries = list(value.items())
    else:
        _warn("pens", value, "a table of pens keyed 1-9")
        return
    for key, entry in entries:
        try:
            index = int(key)
        except (TypeError, ValueError):
            index = 0
        if not 1 <= index <= len(pens):
            _warn(f"pens.{key}", entry, f"a pen number between 1 and {len(pens)}")
            continue
        if not isinstance(entry, Mapping):
            _warn(f"pens.{key}", entry, "a table with color and width")
            continue
        current = pens[index - 1]
        color = _color(entry, "color", current.color)
        width = _float(entry, "width", current.width, minimum=0.0001, maximum=1.0)
        pens[index - 1] = Pen(color=color, width=width)
