# prez

Dual-screen PDF presenter for Beamer decks, written in Python with PySide6 and PyMuPDF.
It does what [pympress](https://github.com/Cimbali/pympress) does, with the same keys,
and renders fast enough that slide changes feel instant: MuPDF rasterises on a worker
thread and the next slides are prepared while you talk.

The content window goes fullscreen on the projector. The presenter window shows the current
slide, the next one, the notes, a talk timer and a clock.

## Run

```sh
uv sync
uv run prez talk.pdf --talk-time 20
uv run prez --list-screens
uv run prez --install-desktop-file   # once, for Wayland window rules and the desktop portal
```

Beamer's `show notes on second screen` layout is detected from the page ratio. Use
`--notes left|top|bottom|after|none` for other layouts. Without notes the pane shows the
slide's PDF annotations.

## Keys

| | |
|---|---|
| `Right` `Space` / `Left` | next / previous slide |
| `Ctrl+Right` / `Ctrl+Left` | next / previous frame, skipping overlays |
| `G` or digits, then `Enter` | go to a page |
| `D` | overview grid |
| `B` / `W` | blank the projector black / white |
| `Z` | freeze the projector while you browse |
| `L` | laser pointer (on at startup) |
| `H`, `1`-`9`, `0`, `Ctrl+Z` | pen, pen colour, eraser, undo |
| `S` | swap the two screens |
| `P` / `R` / `T` | pause, reset, set the talk time |
| `F11` | content fullscreen |
| `Q` | quit |

Left click on the current slide goes forward, right click back. The PDF is reloaded when it
changes on disk, so recompiling your deck mid-rehearsal is fine.

The next-slide and notes panes can be dragged, floated or closed; the Panes menu brings
them back. Shortcuts, colours and defaults live in `~/.config/prez/config.toml`.

## Development

```sh
uv run pytest
uv run ruff check src tests
```

Architecture notes are in `docs/SPEC.md`. prez does not play embedded video or zoom.
