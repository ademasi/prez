"""Command line entry point: `prez [FILE] [options]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version

NOTES_CHOICES = ("auto", "none", "right", "left", "top", "bottom", "after")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _version() -> str:
    try:
        return version("prez")
    except PackageNotFoundError:
        return "0.0.0"


def _talk_time(text: str) -> int:
    """argparse type for `--talk-time MM[:SS]` → seconds (via `TalkTimer.parse`)."""
    from prez.timer import TalkTimer

    try:
        return TalkTimer.parse(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid talk time {text!r}: {exc}") from exc


def _page(text: str) -> int:
    try:
        page = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid page number {text!r}") from exc
    if page < 1:
        raise argparse.ArgumentTypeError("page numbers start at 1")
    return page


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prez",
        description="Fast dual-screen PDF presenter (content window + presenter window).",
    )
    parser.add_argument("file", nargs="?", metavar="FILE", help="PDF file to present")
    parser.add_argument(
        "--notes",
        choices=NOTES_CHOICES,
        default=None,
        help="where the notes are on each page (default: auto-detect)",
    )
    parser.add_argument(
        "--talk-time",
        type=_talk_time,
        default=None,
        metavar="MM[:SS]",
        help="planned talk duration, e.g. 20 or 20:30 or 1:05:00",
    )
    parser.add_argument(
        "--content-screen",
        default=None,
        metavar="NAME",
        help="screen for the content window (see --list-screens)",
    )
    parser.add_argument(
        "--list-screens",
        action="store_true",
        help="list the available screens and exit",
    )
    parser.add_argument(
        "--fullscreen",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="start the content window fullscreen (default from config)",
    )
    parser.add_argument(
        "--page",
        type=_page,
        default=None,
        metavar="N",
        help="start on page N (1-based)",
    )
    parser.add_argument(
        "--blank",
        action="store_true",
        help="start with the content window blanked (black)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        type=str.upper,
        choices=LOG_LEVELS,
        metavar="LEVEL",
        help="logging level (default: WARNING)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def list_screens() -> int:
    """Print `name  WxH@x,y  primary?` for every screen."""
    from PySide6.QtGui import QGuiApplication

    from prez.screens import describe_screen

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    primary = app.primaryScreen()
    for screen in app.screens():
        print(describe_screen(screen, primary))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.list_screens:
        return list_screens()

    if args.file is not None and not os.path.isfile(args.file):
        print(f"prez: no such file: {args.file}", file=sys.stderr)
        return 2

    from prez.app import PrezApp
    from prez.config import load_config

    config = load_config()
    if args.blank:
        config.start_blanked = True

    app = PrezApp(
        sys.argv[:1],
        config,
        args.file,
        notes_mode=args.notes,
        talk_time=args.talk_time,
        content_screen=args.content_screen,
        fullscreen=args.fullscreen,
        start_slide=(args.page - 1) if args.page else 0,
    )
    return app.run()
