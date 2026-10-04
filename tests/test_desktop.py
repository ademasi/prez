"""prez.desktop: the prez.desktop entry used for the Wayland app id."""

from __future__ import annotations

import os

from prez import desktop
from prez.cli import main


def test_render_has_exec_and_mime():
    text = desktop.render("/opt/prez/bin/prez")
    assert "Exec=/opt/prez/bin/prez %f" in text
    assert "TryExec=/opt/prez/bin/prez" in text
    assert "MimeType=application/pdf;" in text
    assert "StartupWMClass=prez" in text
    assert text.startswith("[Desktop Entry]\n")


def test_render_module_fallback_splits_try_exec():
    text = desktop.render("/usr/bin/python3 -m prez")
    assert "Exec=/usr/bin/python3 -m prez %f" in text
    assert "TryExec=/usr/bin/python3\n" in text


def test_install_and_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "empty"))
    assert not desktop.is_installed()
    path = desktop.install("/x/prez")
    assert path == tmp_path / "share" / "applications" / "prez.desktop"
    assert path.is_file()
    assert desktop.installed_path() == path
    assert desktop.is_installed()


def test_lookup_in_system_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    system = tmp_path / "usr" / "share" / "applications"
    system.mkdir(parents=True)
    (system / "prez.desktop").write_text("[Desktop Entry]\n")
    monkeypatch.setenv("XDG_DATA_DIRS", f"{tmp_path / 'usr' / 'share'}:{tmp_path / 'other'}")
    assert desktop.installed_path() == system / "prez.desktop"


def test_cli_install_desktop_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert main(["--install-desktop-file"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out
    assert os.path.isfile(tmp_path / "share" / "applications" / "prez.desktop")
