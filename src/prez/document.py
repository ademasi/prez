"""Document model: notes modes, slide geometry, labels, links, outline and rendering.

Pure Python plus PyMuPDF; this module never imports Qt. PyMuPDF is not thread-safe, so it
is used only by :func:`load_document` (main thread, while no render worker is running) and
by :class:`PageRenderer` (created, used and closed on the render worker thread only).

Coordinates: PDF regions are in points. Link rectangles are normalized to 0..1 relative to
the slide region of their slide.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pymupdf as fitz

logger = logging.getLogger(__name__)

#: Pages wider than this many times their height are taken to be ``[slide | notes]`` pages.
NOTES_ASPECT_THRESHOLD = 2.4


class NotesMode(str, Enum):  # noqa: UP042 - (str, Enum) is the base class pair the spec fixes
    """Where the speaker notes live relative to the slide content."""

    NONE = "none"  # whole page is the slide
    RIGHT = "right"  # page = [slide | notes]
    LEFT = "left"  # page = [notes | slide]
    TOP = "top"  # page = [notes / slide]
    BOTTOM = "bottom"  # page = [slide / notes]
    AFTER = "after"  # pages alternate: slide page, then its notes page


class Region(str, Enum):  # noqa: UP042 - see NotesMode
    """Which part of a slide to address."""

    FULL = "full"  # the whole PDF page
    SLIDE = "slide"  # slide part
    NOTES = "notes"  # notes part (only meaningful if has_notes)


Rect = tuple[float, float, float, float]
"""``(x0, y0, x1, y1)``: points for PDF regions, 0..1 for normalized link rectangles."""

_SIDE_BY_SIDE = (NotesMode.RIGHT, NotesMode.LEFT, NotesMode.TOP, NotesMode.BOTTOM)


@dataclass(frozen=True)
class Link:
    """A clickable area on a slide."""

    rect: Rect  # normalized 0..1 within the SLIDE region
    target: int | None  # 0-based slide index for internal links
    uri: str | None = None  # external links


@dataclass(frozen=True)
class OutlineEntry:
    """One bookmark of the PDF outline (table of contents)."""

    level: int
    title: str
    slide: int  # 0-based slide index


@dataclass
class DocumentInfo:
    """Everything the UI needs to know about a document, as plain picklable data."""

    path: str
    page_count: int  # PDF pages
    page_sizes: list[tuple[float, float]]  # (w, h) points per PDF page
    labels: list[str]  # per slide; falls back to the PDF page number
    links: list[list[Link]]  # per slide
    annotations: list[list[str]]  # per slide: text of Text/FreeText/popup annotations
    outline: list[OutlineEntry]
    notes_mode: NotesMode
    mtime: float  # os.stat mtime at load, for reload detection

    def __post_init__(self) -> None:
        self.notes_mode = _coerce_notes_mode(self.notes_mode)

    # -- counting and page mapping ---------------------------------------------------------

    @property
    def slide_count(self) -> int:
        if self.notes_mode == NotesMode.AFTER:
            return math.ceil(self.page_count / 2)
        return self.page_count

    def has_notes(self) -> bool:
        return self.notes_mode != NotesMode.NONE

    def _check_slide(self, slide: int) -> None:
        if not 0 <= slide < self.slide_count:
            raise IndexError(f"slide {slide} out of range (document has {self.slide_count})")

    def page_for_slide(self, slide: int) -> int:
        """PDF page index holding the slide content of ``slide``."""
        self._check_slide(slide)
        if self.notes_mode == NotesMode.AFTER:
            return 2 * slide
        return slide

    def slide_for_page(self, page: int) -> int:
        """Slide index that PDF page ``page`` belongs to (its notes page maps to the slide)."""
        if not 0 <= page < self.page_count:
            raise IndexError(f"page {page} out of range (document has {self.page_count})")
        if self.notes_mode == NotesMode.AFTER:
            return page // 2
        return page

    def notes_page_for_slide(self, slide: int) -> int | None:
        """PDF page index holding the notes of ``slide``, or None when there are none."""
        self._check_slide(slide)
        if self.notes_mode == NotesMode.NONE:
            return None
        if self.notes_mode == NotesMode.AFTER:
            page = 2 * slide + 1
            return page if page < self.page_count else None
        return slide

    # -- geometry --------------------------------------------------------------------------

    def region(self, slide: int, region: Region) -> tuple[int, Rect]:
        """(PDF page index, clip rect in points) for ``region`` of ``slide``.

        NOTES when the document has no notes raises ValueError. In AFTER mode SLIDE is
        ``(2*slide, full page)`` and NOTES is ``(2*slide+1, full page)``, or the slide page
        itself when the notes page is missing (odd page count).
        """
        region = Region(region)
        page = self.page_for_slide(slide)
        width, height = self.page_sizes[page]
        full: Rect = (0.0, 0.0, width, height)
        if region == Region.FULL:
            return page, full
        if region == Region.NOTES and not self.has_notes():
            raise ValueError("document has no notes")
        if self.notes_mode == NotesMode.AFTER:
            if region == Region.NOTES:
                notes_page = self.notes_page_for_slide(slide)
                if notes_page is not None:
                    notes_w, notes_h = self.page_sizes[notes_page]
                    return notes_page, (0.0, 0.0, notes_w, notes_h)
            return page, full
        if self.notes_mode == NotesMode.NONE:
            return page, full
        slide_rect, notes_rect = _split_page(self.notes_mode, width, height)
        return page, slide_rect if region == Region.SLIDE else notes_rect

    def region_size(self, slide: int, region: Region) -> tuple[float, float]:
        """Size in points of ``region`` of ``slide``."""
        _, (x0, y0, x1, y1) = self.region(slide, region)
        return x1 - x0, y1 - y0

    def aspect(self, slide: int, region: Region) -> float:
        """Width / height of ``region`` of ``slide`` (1.0 for a degenerate region)."""
        width, height = self.region_size(slide, region)
        if width <= 0 or height <= 0:
            return 1.0
        return width / height

    # -- labels ----------------------------------------------------------------------------

    def _clamp_slide(self, slide: int) -> int:
        return max(0, min(slide, self.slide_count - 1))

    def _group_start(self, slide: int) -> int:
        """First slide of the run of identical labels containing ``slide``."""
        label = self.labels[slide]
        while slide > 0 and self.labels[slide - 1] == label:
            slide -= 1
        return slide

    def next_label(self, slide: int) -> int:
        """Last slide of the next label group, like pympress.

        From the middle of a group of overlays this lands on the group's last (fully
        built) slide; from a group's last slide it lands on the last slide of the
        following group. Clamped to the last slide.
        """
        if self.slide_count == 0:
            return 0
        slide = self._clamp_slide(slide)
        if slide + 1 >= self.slide_count:
            return slide
        return self._group_end(slide + 1)

    def prev_label(self, slide: int) -> int:
        """Nearest earlier slide with a different label (last slide of the previous
        group), like pympress; 0 if none."""
        if self.slide_count == 0:
            return 0
        slide = self._clamp_slide(slide)
        return max(self._group_start(slide) - 1, 0)

    def _group_end(self, slide: int) -> int:
        """Last slide of the run of identical labels containing ``slide``."""
        label = self.labels[slide]
        while slide + 1 < self.slide_count and self.labels[slide + 1] == label:
            slide += 1
        return slide

    def label_index(self, label: str) -> int | None:
        """First slide whose label equals ``label`` (exact, then case-insensitive)."""
        label = label.strip()
        for index, candidate in enumerate(self.labels):
            if candidate == label:
                return index
        folded = label.casefold()
        for index, candidate in enumerate(self.labels):
            if candidate.casefold() == folded:
                return index
        return None


def _split_page(mode: NotesMode, width: float, height: float) -> tuple[Rect, Rect]:
    """(slide rect, notes rect) for a side-by-side notes mode."""
    half_w, half_h = width / 2, height / 2
    if mode == NotesMode.RIGHT:
        return (0.0, 0.0, half_w, height), (half_w, 0.0, width, height)
    if mode == NotesMode.LEFT:
        return (half_w, 0.0, width, height), (0.0, 0.0, half_w, height)
    if mode == NotesMode.TOP:
        return (0.0, half_h, width, height), (0.0, 0.0, width, half_h)
    if mode == NotesMode.BOTTOM:
        return (0.0, 0.0, width, half_h), (0.0, half_h, width, height)
    raise ValueError(f"{mode!r} is not a side-by-side notes mode")


def _coerce_notes_mode(value: NotesMode | str) -> NotesMode:
    if isinstance(value, NotesMode):
        return value
    try:
        return NotesMode(str(value).strip().lower())
    except ValueError:
        choices = ", ".join(mode.value for mode in NotesMode)
        raise ValueError(
            f"unknown notes mode {value!r}; expected auto or one of {choices}"
        ) from None


def detect_notes_mode(page_sizes: list[tuple[float, float]]) -> NotesMode:
    """RIGHT if every page's w/h > 2.4 (two landscape slides side by side), else NONE."""
    if not page_sizes:
        return NotesMode.NONE
    for width, height in page_sizes:
        if height <= 0 or width / height <= NOTES_ASPECT_THRESHOLD:
            return NotesMode.NONE
    return NotesMode.RIGHT


def resolve_notes_mode(
    notes_mode: NotesMode | str, page_sizes: list[tuple[float, float]]
) -> NotesMode:
    """Turn a NotesMode, its value string, or ``"auto"`` into a concrete NotesMode."""
    if isinstance(notes_mode, str) and notes_mode.strip().lower() in ("", "auto"):
        return detect_notes_mode(page_sizes)
    return _coerce_notes_mode(notes_mode)


def load_document(path: str, notes_mode: NotesMode | str = "auto") -> DocumentInfo:
    """Open ``path`` with PyMuPDF, extract everything, and close it before returning.

    ``notes_mode`` accepts a NotesMode, its value string, or ``"auto"`` (detection by
    page aspect ratio). Links are kept only when they intersect the slide region and are
    normalized relative to it. Labels come from the PDF page labels, falling back to the
    1-based PDF page number of the slide page.
    """
    path = os.fspath(path)
    mtime = os.stat(path).st_mtime
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            raise ValueError(f"{path}: password-protected documents are not supported")
        page_sizes = [(float(page.rect.width), float(page.rect.height)) for page in doc]
        mode = resolve_notes_mode(notes_mode, page_sizes)
        info = DocumentInfo(
            path=path,
            page_count=len(page_sizes),
            page_sizes=page_sizes,
            labels=[],
            links=[],
            annotations=[],
            outline=[],
            notes_mode=mode,
            mtime=mtime,
        )
        for slide in range(info.slide_count):
            page_index = info.page_for_slide(slide)
            page = doc[page_index]
            _, clip = info.region(slide, Region.SLIDE)
            info.labels.append(_page_label(page) or str(slide + 1))
            info.links.append(_extract_links(page, clip, info))
            info.annotations.append(_extract_annotations(page))
        info.outline = _extract_outline(doc, info)
    finally:
        doc.close()
    return info


_HEX_STRING = re.compile(r"^<([0-9A-Fa-f\s]*)>$")


def decode_label(raw: str) -> str:
    """Decode a page label as PyMuPDF returns it.

    Beamer (hyperref) writes label prefixes as PDF hex strings in UTF-16BE with a BOM,
    e.g. ``<FEFF0031>`` for "1"; PyMuPDF hands that back verbatim. Hex strings are
    decoded (UTF-16 with BOM, else PDFDocEncoding/latin-1); anything else is returned
    unchanged.
    """
    raw = raw.strip()
    m = _HEX_STRING.match(raw)
    if not m:
        return raw
    digits = re.sub(r"\s+", "", m.group(1))
    if len(digits) % 2:
        digits += "0"
    try:
        data = bytes.fromhex(digits)
    except ValueError:
        return raw
    if data[:2] in (b"\xfe\xff", b"\xff\xfe"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return raw
    return data.decode("latin-1")


def _page_label(page: fitz.Page) -> str:
    try:
        return decode_label(page.get_label() or "")
    except Exception:  # noqa: BLE001 - non-PDF formats have no label tree
        logger.debug("no page label for page %d", page.number, exc_info=True)
        return ""


_EXTERNAL_LINK_KINDS = frozenset({fitz.LINK_URI, fitz.LINK_LAUNCH, fitz.LINK_GOTOR})


def _extract_links(page: fitz.Page, clip: Rect, info: DocumentInfo) -> list[Link]:
    clip_rect = fitz.Rect(*clip)
    clip_w, clip_h = clip_rect.width, clip_rect.height
    if clip_w <= 0 or clip_h <= 0:
        return []
    try:
        raw_links: Iterable[Mapping[str, Any]] = page.get_links()
    except Exception:  # noqa: BLE001 - a broken link annotation must not sink the document
        logger.warning("cannot read links of page %d", page.number, exc_info=True)
        return []

    links: list[Link] = []
    for raw in raw_links:
        try:
            area = fitz.Rect(raw["from"]) & clip_rect
        except (KeyError, TypeError, ValueError):
            continue
        if area.is_empty or area.is_infinite:
            continue
        kind = raw.get("kind")
        target: int | None = None
        uri: str | None = None
        if kind == fitz.LINK_URI:
            uri = raw.get("uri") or None
            if uri is None:
                continue
        elif kind in _EXTERNAL_LINK_KINDS:
            continue  # launch / remote-goto links are not supported
        else:
            page_no = raw.get("page")
            if not isinstance(page_no, int) or not 0 <= page_no < info.page_count:
                continue
            target = info.slide_for_page(page_no)
        rect: Rect = (
            (area.x0 - clip_rect.x0) / clip_w,
            (area.y0 - clip_rect.y0) / clip_h,
            (area.x1 - clip_rect.x0) / clip_w,
            (area.y1 - clip_rect.y0) / clip_h,
        )
        links.append(Link(rect=rect, target=target, uri=uri))
    return links


_TEXT_ANNOT_TYPES = (fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_POPUP)


def _extract_annotations(page: fitz.Page) -> list[str]:
    texts: list[str] = []
    try:
        annots = list(page.annots(types=_TEXT_ANNOT_TYPES))
    except Exception:  # noqa: BLE001 - annotations are optional information
        logger.warning("cannot read annotations of page %d", page.number, exc_info=True)
        return texts
    for annot in annots:
        content = (annot.info.get("content") or "").strip()
        if content and content not in texts:
            texts.append(content)
    return texts


def _extract_outline(doc: fitz.Document, info: DocumentInfo) -> list[OutlineEntry]:
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # noqa: BLE001 - outline is optional information
        logger.warning("cannot read outline of %s", info.path, exc_info=True)
        return []
    outline: list[OutlineEntry] = []
    for item in toc:
        if len(item) < 3:
            continue
        level, title, page_no = item[0], item[1], item[2]
        if not isinstance(page_no, int) or not 1 <= page_no <= info.page_count:
            continue  # unresolved destination
        outline.append(
            OutlineEntry(level=int(level), title=str(title), slide=info.slide_for_page(page_no - 1))
        )
    return outline


@dataclass(frozen=True)
class RawImage:
    """A rendered region: tightly packed RGB888 pixels, no alpha."""

    width: int
    height: int
    stride: int
    data: bytes


class PageRenderer:
    """Owns a fitz.Document for rendering. Create, use and close it on ONE thread only."""

    #: Display lists cached per page so re-rendering a page at another size skips parsing.
    MAX_CACHED_PAGES = 8

    def __init__(self, path: str) -> None:
        self._doc: fitz.Document | None = fitz.open(os.fspath(path))
        self._display_lists: OrderedDict[int, fitz.DisplayList] = OrderedDict()

    def render(self, page: int, clip: Rect, width_px: int, height_px: int) -> RawImage:
        """Render ``clip`` (points) of PDF page ``page`` as exactly ``width_px × height_px``.

        The matrix is ``Matrix(width_px / clip_w, height_px / clip_h)`` with the clip origin
        translated to (0, 0) so MuPDF's rounding cannot add or drop a pixel row/column.
        """
        if self._doc is None:
            raise RuntimeError("PageRenderer is closed")
        width_px = max(1, int(width_px))
        height_px = max(1, int(height_px))
        x0, y0, x1, y1 = (float(v) for v in clip)
        clip_w, clip_h = x1 - x0, y1 - y0
        if clip_w <= 0 or clip_h <= 0:
            raise ValueError(f"empty clip rectangle {clip!r}")

        matrix = fitz.Matrix(width_px / clip_w, height_px / clip_h).pretranslate(-x0, -y0)
        pix = self._display_list(page).get_pixmap(
            matrix=matrix, colorspace=fitz.csRGB, alpha=False, clip=fitz.Rect(x0, y0, x1, y1)
        )
        if pix.n != 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        data: bytes = pix.samples
        if pix.width != width_px or pix.height != height_px:
            data = _fit_rgb(data, pix.stride, pix.width, pix.height, width_px, height_px)
        return RawImage(width=width_px, height=height_px, stride=3 * width_px, data=data)

    def _display_list(self, page: int) -> fitz.DisplayList:
        assert self._doc is not None
        cached = self._display_lists.get(page)
        if cached is not None:
            self._display_lists.move_to_end(page)
            return cached
        display_list = self._doc[page].get_displaylist()
        self._display_lists[page] = display_list
        while len(self._display_lists) > self.MAX_CACHED_PAGES:
            self._display_lists.popitem(last=False)
        return display_list

    def close(self) -> None:
        """Release the document. Safe to call more than once."""
        self._display_lists.clear()
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def __enter__(self) -> PageRenderer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _fit_rgb(data: bytes, stride: int, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    """Crop or pad (with white) tightly packed RGB rows to exactly ``dst_w × dst_h``."""
    copy_bytes = 3 * min(src_w, dst_w)
    pad = b"\xff" * (3 * dst_w - copy_bytes)
    blank_row = b"\xff" * (3 * dst_w)
    rows = []
    for row in range(dst_h):
        if row < src_h:
            start = row * stride
            rows.append(data[start : start + copy_bytes] + pad)
        else:
            rows.append(blank_row)
    return b"".join(rows)
