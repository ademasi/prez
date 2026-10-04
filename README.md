# prez

A fast dual-screen PDF presenter, reimplementing [pympress](https://github.com/Cimbali/pympress)
on **Qt 6 (PySide6)** and **MuPDF (PyMuPDF)**.

Two windows: the **content** window goes fullscreen on the projector, the **presenter**
window stays on your laptop with the current slide, the next slide, your notes, a talk
timer and a clock. Same keyboard shortcuts as pympress.

Why it is fast: slides are rasterised by MuPDF on a worker thread (a 1080p slide takes a
few milliseconds), cached as GPU-ready pixmaps, and the neighbouring slides are
pre-rendered as soon as you land on a slide. The GUI thread never renders. If an exact-size
image is not ready yet, a cached one at another size is shown scaled and replaced as soon as
the exact one arrives.

## Install and run

```sh
cd ~/git/prez
uv sync
uv run prez talk.pdf                     # auto-detects Beamer "notes on second screen"
uv run prez talk.pdf --talk-time 20      # 20 minutes, countdown + progress bar
uv run prez talk.pdf --notes after       # slide pages alternate with note pages
uv run prez --list-screens
uv run prez talk.pdf --content-screen HDMI-A-1
```

Or install it as a tool: `uv tool install --editable .` then `prez talk.pdf`.

## Notes modes

| `--notes` | page layout |
|-----------|-------------|
| `auto`    | `right` when every page is wider than 2.4:1, otherwise `none` |
| `none`    | the whole page is the slide |
| `right` / `left` / `top` / `bottom` | page is split, notes on that side (Beamer `\setbeameroption{show notes on second screen=right}`) |
| `after`   | slide page, then its notes page, alternating |

When the PDF has no notes, the notes pane shows the PDF annotations of the slide and a text
editor; what you type is saved next to the PDF in `talk.pdf.notes.json`.

## Keys (pympress defaults)

| Action | Keys |
|--------|------|
| Next / previous slide | `Right` `Down` `PgDown` `Space` / `Left` `Up` `PgUp` |
| First / last | `Home` / `End` |
| Next / previous label (skip Beamer overlays) | `Ctrl+Right` / `Ctrl+Left` |
| History back / forward | `Alt+Left` / `Alt+Right` |
| Go to page / jump to label | `G` / `J`, or just type digits, then `Enter` |
| Overview grid | `D` |
| Blank black / white | `B` / `W` |
| Freeze content window | `Z` |
| Laser pointer | `L` |
| Highlighter / pen | `H` toggles; `1`-`9` pick a pen while drawing, `0` eraser, `Ctrl+Z` undo, `Ctrl+R` redo, `Ctrl+Del` clear |
| Notes mode cycle | `N` |
| Content fullscreen / presenter fullscreen | `F11` `F` `F5` / `Ctrl+F` |
| Swap screens | `S` |
| Timer pause / reset / set talk time | `P` / `R` / `T` |
| Open file / reload | `O` / `Ctrl+Shift+R` (the file is also reloaded automatically when it changes) |
| Quit | `Q` |

Mouse on the presenter's current slide: left click next, right click previous, wheel
navigates, internal PDF links are clickable. Double-click the content window to toggle
fullscreen.

## Configuration

`~/.config/prez/config.toml` is created with commented defaults on first run. It holds
shortcuts (`[shortcuts] next = "Right Down PgDown Space"`), the default notes mode, talk
time, cache size, preferred content screen, pointer colour and size, and the nine
highlight pens.

## Development

```sh
uv run pytest            # ~300 tests, runs offscreen
uv run ruff check src tests && uv run ruff format src tests
```

Architecture is documented in `docs/SPEC.md`. Modules:

- `document.py` document model (regions, labels, links, annotations) and the MuPDF renderer
- `render.py` worker thread, priority queue, LRU pixmap cache
- `state.py` presentation state and signals (slide, blank, freeze, pointer, strokes, history)
- `widgets/` slide view with overlays, overview grid, notes pane
- `windows/` content and presenter windows
- `app.py` wiring, keyboard dispatch, prerender policy, screen handling; `cli.py` entry point

Not implemented (yet): embedded video, zoom.
