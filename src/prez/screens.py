"""Screen selection and window placement (X11 and Wayland)."""

from __future__ import annotations

import logging

from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def _same(a: QScreen | None, b: QScreen | None) -> bool:
    if a is None or b is None:
        return a is b
    return a is b or (a.name() == b.name() and a.geometry() == b.geometry())


def _area(screen: QScreen) -> int:
    size = screen.size()
    return size.width() * size.height()


def find_screen(app: QGuiApplication, name: str | None) -> QScreen | None:
    """Find a screen by `QScreen.name()` (case-insensitive) or by 0-based index."""
    if not name:
        return None
    screens = app.screens()
    for screen in screens:
        if screen.name() == name:
            return screen
    for screen in screens:
        if screen.name().lower() == name.lower():
            return screen
    if name.isdigit() and int(name) < len(screens):
        return screens[int(name)]
    return None


def choose_screens(app: QGuiApplication, preferred_content: str | None) -> tuple[QScreen, QScreen]:
    """Return `(content_screen, presenter_screen)`.

    One screen → both the same. `preferred_content` is matched by `QScreen.name()`;
    otherwise the content goes to the largest non-primary screen and the presenter stays
    on the primary one.
    """
    screens = app.screens()
    if not screens:
        raise RuntimeError("no screens available")
    primary = app.primaryScreen() or screens[0]
    if len(screens) == 1:
        return screens[0], screens[0]

    content = find_screen(app, preferred_content)
    if preferred_content and content is None:
        log.warning(
            "content screen %r not found; available: %s",
            preferred_content,
            ", ".join(s.name() for s in screens),
        )
    non_primary = [s for s in screens if not _same(s, primary)]
    if content is None:
        content = max(non_primary, key=_area) if non_primary else primary

    if _same(content, primary):
        others = [s for s in screens if not _same(s, content)]
        presenter = max(others, key=_area) if others else primary
    else:
        presenter = primary
    return content, presenter


def assign_screen(window: QWidget, screen: QScreen) -> None:
    """Bind `window` to `screen` before showing it (the only thing that works on Wayland)."""
    window.winId()  # force creation of the native window handle
    handle = window.windowHandle()
    if handle is not None and not _same(handle.screen(), screen):
        handle.setScreen(screen)
    window.move(screen.geometry().topLeft())


def place(window: QWidget, screen: QScreen, fullscreen: bool) -> None:
    """Show `window` on `screen`, fullscreen or maximized.

    A window that is already visible is hidden first so that both X11 and Wayland pick up
    the new screen when it is shown again.
    """
    if window.isVisible():
        window.hide()
    assign_screen(window, screen)
    if fullscreen:
        window.showFullScreen()
    else:
        window.showMaximized()


def describe_screen(screen: QScreen, primary: QScreen | None) -> str:
    """One line for `--list-screens`: `name  WxH@x,y  primary?`."""
    geo = screen.geometry()
    flag = "primary" if _same(screen, primary) else ""
    return f"{screen.name()}  {geo.width()}x{geo.height()}@{geo.x()},{geo.y()}  {flag}".rstrip()
