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

When the PDF has no notes region, the notes pane shows the slide's PDF annotations.

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
| Open file / reload | `O` / `Ctrl+Shift+R` (the file is also reloaded automatically when it is recompiled) |
| Quit | `Q` |

The laser pointer is on from the start (it follows the mouse over the current slide and
shows on the projector); `L` turns it off, or set `pointer_on = false` in the config.

Mouse on the presenter's current slide: left click next, right click previous, wheel
navigates, internal PDF links are clickable. Double-click the content window to toggle
fullscreen.

## Presenter layout

The current slide is the centre of the presenter window. **Next slide** and **Notes** (the
Beamer notes half of the page, or the slide's PDF annotations) are panes you can drag to
any edge, stack as tabs, float as separate windows, resize or close. The **Panes** menu at
the right of the toolbar re-opens closed panes and has *Reset layout*. The arrangement is
remembered between runs. The toolbar only carries toggles and the timer; navigation and
opening files are keyboard and command-line matters.

Colours follow the Catppuccin Macchiato palette of your foot terminal, with Claude's orange
as the accent, in `src/prez/theme.py`.

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
- `theme.py` palette and stylesheet
- `windows/` content and presenter windows
- `app.py` wiring, keyboard dispatch, prerender policy, screen handling; `cli.py` entry point

Not implemented (yet): embedded video, zoom.

## Wayland and tiling compositors

With two screens the content window goes fullscreen on the non-primary screen (or
`--content-screen NAME`) and the presenter opens on the other one. Wayland does not let an
application choose where a *windowed* toplevel appears, so under sway or Hyprland the
presenter opens as a tile on the focused output. The windows have `app_id` `prez`; the
presenter's title is `<file> — prez` and the content window's is `prez — content`, so a
rule such as

```
for_window [app_id="prez" title="prez — content"] fullscreen enable
for_window [app_id="prez" title=" — prez$"] move workspace 9
```

puts them where you like. Pressing `S` swaps the two screens at any time: the slides go
fullscreen on the other monitor and the presenter takes the one they left.

Run `prez --install-desktop-file` once. It writes `~/.local/share/applications/prez.desktop`,
which the desktop portal and the compositor use to recognise the app (without it Qt
logs "Could not register app ID: App info not found for 'prez'" and prez prints a hint).
