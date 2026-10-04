"""The ``prez.desktop`` entry.

On Wayland the window's app id is the desktop-file name (``prez``). xdg-desktop-portal
looks that name up in the XDG applications directories when Qt registers the process,
and complains ("App info not found for 'prez'") when nothing is installed. Installing the
file also gives the compositor a name and icon for window rules and task bars.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "prez"
FILE_NAME = f"{APP_ID}.desktop"

TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=prez
GenericName=PDF presenter
Comment=Dual-screen PDF presenter with speaker notes
Exec={exec_path} %f
TryExec={try_exec}
Terminal=false
Categories=Office;Presentation;Viewer;
MimeType=application/pdf;
StartupWMClass=prez
StartupNotify=false
"""


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def data_dirs() -> list[Path]:
    """``$XDG_DATA_HOME`` first, then ``$XDG_DATA_DIRS`` (default /usr/local/share:/usr/share)."""
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [data_home()] + [Path(d) for d in raw.split(":") if d]


def user_desktop_path() -> Path:
    return data_home() / "applications" / FILE_NAME


def installed_path() -> Path | None:
    """Where ``prez.desktop`` is installed, if anywhere the portal would look."""
    for base in data_dirs():
        candidate = base / "applications" / FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def is_installed() -> bool:
    return installed_path() is not None


def default_exec() -> str:
    """The ``prez`` launcher of the running environment, as an absolute path."""
    here = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if here is not None and here.name == APP_ID and here.is_file():
        return str(here)
    found = shutil.which(APP_ID)
    if found:
        return str(Path(found).resolve())
    return f"{sys.executable} -m prez"


def render(exec_path: str | None = None) -> str:
    exec_path = exec_path or default_exec()
    return TEMPLATE.format(exec_path=exec_path, try_exec=exec_path.split()[0])


def install(exec_path: str | None = None, path: Path | None = None) -> Path:
    """Write the desktop file (default ``~/.local/share/applications/prez.desktop``)."""
    path = path or user_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(exec_path), encoding="utf-8")
    updater = shutil.which("update-desktop-database")
    if updater:
        try:
            subprocess.run(
                [updater, str(path.parent)], check=False, capture_output=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("update-desktop-database failed: %s", exc)
    return path
