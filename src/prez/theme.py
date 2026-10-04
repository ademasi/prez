"""Colour theme: Catppuccin Macchiato (the user's foot palette) with a Claude-orange accent.

Every hard-coded colour in the GUI comes from here so the whole app reads as one surface.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Catppuccin Macchiato, as in ~/.config/foot/foot.ini
CRUST = "#181926"
MANTLE = "#1e2030"
BASE = "#24273a"
SURFACE0 = "#363a4f"
SURFACE1 = "#494d64"
SURFACE2 = "#5b6078"
OVERLAY0 = "#6e738d"
OVERLAY1 = "#8087a2"
SUBTEXT0 = "#a5adcb"
SUBTEXT1 = "#b8c0e0"
TEXT = "#cad3f5"
ROSEWATER = "#f4dbd6"
PEACH = "#f5a97f"  # foot colour slot 16
RED = "#ed8796"
GREEN = "#a6da95"
YELLOW = "#eed49f"
BLUE = "#8aadf4"
PINK = "#f5bde6"
TEAL = "#8bd5ca"
MAUVE = "#c6a0f6"
LAVENDER = "#b7bdf8"
SELECTION = "#454a5f"

# Claude Code's terracotta accent.
CLAUDE = "#da7756"

ACCENT = PEACH
ACCENT_STRONG = CLAUDE

FONT_FAMILY = "FiraCode Nerd Font, Fira Code, monospace"

STYLESHEET = f"""
QMainWindow, QDialog, QStatusBar {{ background: {BASE}; }}
QWidget {{ color: {TEXT}; selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QToolTip {{ background: {MANTLE}; color: {TEXT}; border: 1px solid {SURFACE1}; padding: 4px; }}

QToolBar {{ background: {MANTLE}; border: none; padding: 2px 6px; spacing: 2px; }}
QToolBar::separator {{ background: {SURFACE1}; width: 1px; margin: 6px 6px; }}
QToolButton {{ background: transparent; color: {SUBTEXT1}; border: 1px solid transparent;
               border-radius: 4px; padding: 3px 9px; }}
QToolButton:hover {{ background: {SURFACE0}; color: {TEXT}; }}
QToolButton:pressed {{ background: {SURFACE1}; }}
QToolButton:checked {{ background: {SURFACE0}; color: {ACCENT}; border-color: {ACCENT}; }}
QToolButton::menu-indicator {{ image: none; }}
QMenu {{ background: {MANTLE}; color: {TEXT}; border: 1px solid {SURFACE1}; padding: 4px; }}
QMenu::item {{ padding: 4px 18px 4px 12px; border-radius: 3px; }}
QMenu::item:selected {{ background: {SURFACE0}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {SURFACE1}; margin: 4px 6px; }}
QMenu::indicator {{ width: 12px; height: 12px; }}

QDockWidget {{ color: {SUBTEXT0}; titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {MANTLE}; padding: 3px 8px; text-align: left;
                      border-bottom: 1px solid {SURFACE0}; }}
QDockWidget::close-button, QDockWidget::float-button {{ background: {SURFACE0};
    border-radius: 3px; width: 10px; height: 10px; subcontrol-position: right; padding: 1px; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {ACCENT}; }}
QMainWindow::separator {{ background: {MANTLE}; width: 5px; height: 5px; }}
QMainWindow::separator:hover {{ background: {SURFACE1}; }}
QSplitter::handle {{ background: {MANTLE}; }}
QSplitter::handle:hover {{ background: {SURFACE1}; }}

QStatusBar {{ background: {MANTLE}; border-top: 1px solid {SURFACE0}; }}
QStatusBar::item {{ border: none; }}
QLineEdit {{ background: {CRUST}; color: {TEXT}; border: 1px solid {ACCENT}; border-radius: 4px;
             padding: 1px 6px; }}
QPlainTextEdit, QTextEdit {{ background: {BASE}; color: {TEXT}; border: none; }}
QProgressBar {{ background: {SURFACE0}; border: none; border-radius: 0; }}
QProgressBar::chunk {{ background: {ACCENT_STRONG}; }}

QScrollBar:vertical {{ background: {BASE}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SURFACE1}; min-height: 24px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {SURFACE2}; }}
QScrollBar:horizontal {{ background: {BASE}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SURFACE1}; min-width: 24px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; }}
"""


def palette() -> QPalette:
    pal = QPalette()
    role = QPalette.ColorRole
    pal.setColor(role.Window, QColor(BASE))
    pal.setColor(role.WindowText, QColor(TEXT))
    pal.setColor(role.Base, QColor(MANTLE))
    pal.setColor(role.AlternateBase, QColor(BASE))
    pal.setColor(role.ToolTipBase, QColor(MANTLE))
    pal.setColor(role.ToolTipText, QColor(TEXT))
    pal.setColor(role.PlaceholderText, QColor(OVERLAY0))
    pal.setColor(role.Text, QColor(TEXT))
    pal.setColor(role.Button, QColor(SURFACE0))
    pal.setColor(role.ButtonText, QColor(TEXT))
    pal.setColor(role.BrightText, QColor(RED))
    pal.setColor(role.Highlight, QColor(ACCENT))
    pal.setColor(role.HighlightedText, QColor(CRUST))
    pal.setColor(role.Link, QColor(BLUE))
    pal.setColor(role.LinkVisited, QColor(MAUVE))
    pal.setColor(role.Light, QColor(SURFACE2))
    pal.setColor(role.Midlight, QColor(SURFACE1))
    pal.setColor(role.Mid, QColor(SURFACE0))
    pal.setColor(role.Dark, QColor(CRUST))
    pal.setColor(role.Shadow, QColor(CRUST))
    disabled = QPalette.ColorGroup.Disabled
    pal.setColor(disabled, role.Text, QColor(OVERLAY0))
    pal.setColor(disabled, role.ButtonText, QColor(OVERLAY0))
    pal.setColor(disabled, role.WindowText, QColor(OVERLAY0))
    return pal


def apply_theme(qapp: QApplication) -> None:
    """Fusion style + Macchiato palette + stylesheet."""
    qapp.setStyle("Fusion")
    qapp.setPalette(palette())
    qapp.setStyleSheet(STYLESHEET)
