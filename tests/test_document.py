"""Tests for prez.document: notes modes, geometry, labels, links, outline and rendering."""

import os
import pickle
from pathlib import Path

import pytest

from prez.document import (
    DocumentInfo,
    Link,
    NotesMode,
    OutlineEntry,
    PageRenderer,
    Region,
    detect_notes_mode,
    load_document,
    resolve_notes_mode,
)
from tests.conftest import (
    DECK_ANNOTATION,
    DECK_GOTO_RECT,
    DECK_GOTO_TARGET,
    DECK_LABELS,
    DECK_URI,
    DECK_URI_RECT,
    NOTES_NOTES_LINK_RECT,
    NOTES_NOTES_LINK_URI,
    NOTES_SLIDE_LINK_RECT,
    NOTES_SLIDE_LINK_TARGET,
    PAGE_H,
    PAGE_W,
    build_after_pdf,
)

FULL = (0.0, 0.0, PAGE_W, PAGE_H)
LEFT_HALF = (0.0, 0.0, PAGE_W / 2, PAGE_H)
RIGHT_HALF = (PAGE_W / 2, 0.0, PAGE_W, PAGE_H)
TOP_HALF = (0.0, 0.0, PAGE_W, PAGE_H / 2)
BOTTOM_HALF = (0.0, PAGE_H / 2, PAGE_W, PAGE_H)


def normalized(rect, clip):
    """Normalize a rect in points relative to ``clip`` (both x0, y0, x1, y1)."""
    cx0, cy0, cx1, cy1 = clip
    cw, ch = cx1 - cx0, cy1 - cy0
    return ((rect[0] - cx0) / cw, (rect[1] - cy0) / ch, (rect[2] - cx0) / cw, (rect[3] - cy0) / ch)


# -- detection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sizes", "expected"),
    [
        ([(1920.0, 540.0)] * 3, NotesMode.RIGHT),
        ([(960.0, 540.0)] * 3, NotesMode.NONE),
        ([(612.0, 792.0)], NotesMode.NONE),
        ([], NotesMode.NONE),
        ([(1920.0, 540.0), (960.0, 540.0)], NotesMode.NONE),  # one regular page spoils it
        ([(240.0, 100.0)], NotesMode.NONE),  # exactly 2.4 is not "> 2.4"
        ([(241.0, 100.0)], NotesMode.RIGHT),
        ([(100.0, 0.0)], NotesMode.NONE),  # degenerate page
    ],
)
def test_detect_notes_mode(sizes, expected):
    assert detect_notes_mode(sizes) is expected


def test_resolve_notes_mode_accepts_enum_string_and_auto():
    wide = [(1920.0, 540.0)]
    assert resolve_notes_mode("auto", wide) is NotesMode.RIGHT
    assert resolve_notes_mode("AUTO", []) is NotesMode.NONE
    assert resolve_notes_mode(NotesMode.LEFT, wide) is NotesMode.LEFT
    assert resolve_notes_mode("Bottom", wide) is NotesMode.BOTTOM
    with pytest.raises(ValueError, match="sideways"):
        resolve_notes_mode("sideways", wide)


# -- deck fixture (no notes) ----------------------------------------------------------------


class TestDeck:
    def test_counts_and_mode(self, deck_doc: DocumentInfo):
        assert deck_doc.page_count == 6
        assert deck_doc.slide_count == 6
        assert deck_doc.notes_mode is NotesMode.NONE
        assert not deck_doc.has_notes()
        assert deck_doc.labels == DECK_LABELS
        assert len(deck_doc.links) == 6
        assert len(deck_doc.annotations) == 6

    def test_page_sizes(self, deck_doc: DocumentInfo):
        assert len(deck_doc.page_sizes) == 6
        for width, height in deck_doc.page_sizes:
            assert width == pytest.approx(PAGE_W)
            assert height == pytest.approx(PAGE_H)

    def test_path_and_mtime(self, deck_doc: DocumentInfo, deck_pdf: str):
        assert deck_doc.path == deck_pdf
        assert deck_doc.mtime == os.stat(deck_pdf).st_mtime

    def test_internal_link(self, deck_doc: DocumentInfo):
        (link,) = deck_doc.links[0]
        assert link.target == DECK_GOTO_TARGET
        assert link.uri is None
        assert link.rect == pytest.approx(normalized(DECK_GOTO_RECT, FULL))
        assert all(0.0 <= v <= 1.0 for v in link.rect)

    def test_uri_link(self, deck_doc: DocumentInfo):
        (link,) = deck_doc.links[1]
        assert link.target is None
        assert link.uri == DECK_URI
        assert link.rect == pytest.approx(normalized(DECK_URI_RECT, FULL))

    def test_other_slides_have_no_links(self, deck_doc: DocumentInfo):
        assert deck_doc.links[2:] == [[], [], [], []]

    def test_annotations(self, deck_doc: DocumentInfo):
        assert deck_doc.annotations[2] == [DECK_ANNOTATION]
        assert all(deck_doc.annotations[i] == [] for i in (0, 1, 3, 4, 5))

    def test_outline(self, deck_doc: DocumentInfo):
        assert deck_doc.outline == [
            OutlineEntry(level=1, title="Intro", slide=0),
            OutlineEntry(level=1, title="Middle", slide=2),
            OutlineEntry(level=2, title="Detail", slide=4),
        ]

    def test_regions(self, deck_doc: DocumentInfo):
        assert deck_doc.region(0, Region.FULL) == (0, FULL)
        assert deck_doc.region(4, Region.SLIDE) == (4, FULL)
        assert deck_doc.region(4, "slide") == (4, FULL)
        with pytest.raises(ValueError):
            deck_doc.region(0, Region.NOTES)
        assert deck_doc.region_size(0, Region.SLIDE) == (PAGE_W, PAGE_H)
        assert deck_doc.aspect(0, Region.SLIDE) == pytest.approx(16 / 9)

    def test_page_mapping(self, deck_doc: DocumentInfo):
        assert [deck_doc.page_for_slide(s) for s in range(6)] == list(range(6))
        assert [deck_doc.slide_for_page(p) for p in range(6)] == list(range(6))
        assert deck_doc.notes_page_for_slide(3) is None

    @pytest.mark.parametrize("slide", [-1, 6, 100])
    def test_out_of_range_slide_raises(self, deck_doc: DocumentInfo, slide: int):
        with pytest.raises(IndexError):
            deck_doc.page_for_slide(slide)
        with pytest.raises(IndexError):
            deck_doc.region(slide, Region.SLIDE)
        with pytest.raises(IndexError):
            deck_doc.slide_for_page(slide)

    @pytest.mark.parametrize(
        ("slide", "expected"),
        [(0, 2), (1, 2), (2, 3), (3, 4), (4, 5), (5, 5), (-5, 2), (99, 5)],
    )
    def test_next_label(self, deck_doc: DocumentInfo, slide: int, expected: int):
        assert deck_doc.next_label(slide) == expected

    @pytest.mark.parametrize(
        ("slide", "expected"),
        [(0, 0), (1, 0), (2, 0), (3, 2), (4, 3), (5, 4), (-5, 0), (99, 4)],
    )
    def test_prev_label(self, deck_doc: DocumentInfo, slide: int, expected: int):
        assert deck_doc.prev_label(slide) == expected

    def test_label_index(self, deck_doc: DocumentInfo):
        assert deck_doc.label_index("1") == 0
        assert deck_doc.label_index("2") == 1  # first slide of the overlay group
        assert deck_doc.label_index(" 5 ") == 5
        assert deck_doc.label_index("9") is None
        assert deck_doc.label_index("") is None

    def test_pickle_roundtrip(self, deck_doc: DocumentInfo):
        clone = pickle.loads(pickle.dumps(deck_doc))
        assert clone == deck_doc
        assert clone.notes_mode is NotesMode.NONE
        assert clone.region(0, Region.SLIDE) == deck_doc.region(0, Region.SLIDE)


# -- notes fixture (RIGHT auto-detected) ----------------------------------------------------


class TestNotesRight:
    def test_autodetected(self, notes_doc: DocumentInfo):
        assert notes_doc.notes_mode is NotesMode.RIGHT
        assert notes_doc.has_notes()
        assert notes_doc.page_count == 5
        assert notes_doc.slide_count == 5
        assert notes_doc.labels == ["1", "2", "3", "4", "5"]

    def test_regions(self, notes_doc: DocumentInfo):
        assert notes_doc.region(0, Region.FULL) == (0, (0.0, 0.0, 2 * PAGE_W, PAGE_H))
        assert notes_doc.region(0, Region.SLIDE) == (0, (0.0, 0.0, PAGE_W, PAGE_H))
        assert notes_doc.region(0, Region.NOTES) == (0, (PAGE_W, 0.0, 2 * PAGE_W, PAGE_H))
        assert notes_doc.aspect(0, Region.SLIDE) == pytest.approx(16 / 9)
        assert notes_doc.aspect(0, Region.NOTES) == pytest.approx(16 / 9)
        assert notes_doc.aspect(0, Region.FULL) == pytest.approx(32 / 9)
        assert notes_doc.region_size(2, Region.NOTES) == (PAGE_W, PAGE_H)

    def test_page_mapping(self, notes_doc: DocumentInfo):
        assert notes_doc.page_for_slide(3) == 3
        assert notes_doc.notes_page_for_slide(3) == 3
        assert notes_doc.slide_for_page(4) == 4

    def test_links_limited_to_slide_region(self, notes_doc: DocumentInfo):
        (link,) = notes_doc.links[0]
        assert link.target == NOTES_SLIDE_LINK_TARGET
        assert link.uri is None
        slide_half = (0.0, 0.0, PAGE_W, PAGE_H)  # left half of the 1920 pt wide page
        assert link.rect == pytest.approx(normalized(NOTES_SLIDE_LINK_RECT, slide_half))

    def test_links_in_none_mode_cover_the_whole_page(self, notes_pdf: str):
        doc = load_document(notes_pdf, "none")
        assert doc.notes_mode is NotesMode.NONE
        full = (0.0, 0.0, 2 * PAGE_W, PAGE_H)
        by_uri = {link.uri: link for link in doc.links[0]}
        assert set(by_uri) == {None, NOTES_NOTES_LINK_URI}
        assert by_uri[None].target == NOTES_SLIDE_LINK_TARGET
        assert by_uri[None].rect == pytest.approx(normalized(NOTES_SLIDE_LINK_RECT, full))
        assert by_uri[NOTES_NOTES_LINK_URI].rect == pytest.approx(
            normalized(NOTES_NOTES_LINK_RECT, full)
        )

    def test_links_in_left_mode_keep_only_the_right_half(self, notes_pdf: str):
        doc = load_document(notes_pdf, NotesMode.LEFT)
        (link,) = doc.links[0]
        assert link.uri == NOTES_NOTES_LINK_URI
        right_half = (PAGE_W, 0.0, 2 * PAGE_W, PAGE_H)
        assert link.rect == pytest.approx(normalized(NOTES_NOTES_LINK_RECT, right_half))


# -- explicit side-by-side modes on the plain deck ------------------------------------------


@pytest.mark.parametrize(
    ("mode", "slide_rect", "notes_rect"),
    [
        ("right", LEFT_HALF, RIGHT_HALF),
        ("left", RIGHT_HALF, LEFT_HALF),
        ("top", BOTTOM_HALF, TOP_HALF),
        ("bottom", TOP_HALF, BOTTOM_HALF),
        (NotesMode.RIGHT, LEFT_HALF, RIGHT_HALF),
        ("TOP", BOTTOM_HALF, TOP_HALF),
    ],
)
def test_explicit_split_modes(deck_pdf: str, mode, slide_rect, notes_rect):
    doc = load_document(deck_pdf, mode)
    expected = mode if isinstance(mode, NotesMode) else NotesMode(mode.lower())
    assert doc.notes_mode is expected
    assert doc.has_notes()
    assert doc.slide_count == 6
    assert doc.region(1, Region.SLIDE) == (1, slide_rect)
    assert doc.region(1, Region.NOTES) == (1, notes_rect)
    assert doc.region(1, Region.FULL) == (1, FULL)
    assert doc.notes_page_for_slide(1) == 1
    assert doc.labels == DECK_LABELS


def test_split_mode_links_are_relative_to_the_slide_half(deck_pdf: str):
    # DECK_GOTO_RECT (100..300 × 400..500) lies in the left/bottom half of the page.
    doc = load_document(deck_pdf, "right")
    (link,) = doc.links[0]
    assert link.rect == pytest.approx(normalized(DECK_GOTO_RECT, LEFT_HALF))
    doc = load_document(deck_pdf, "left")  # slide is the right half: link dropped
    assert doc.links[0] == []
    doc = load_document(deck_pdf, "top")  # slide is the bottom half
    (link,) = doc.links[0]
    assert link.rect == pytest.approx(normalized(DECK_GOTO_RECT, BOTTOM_HALF))


def test_unknown_notes_mode_raises(deck_pdf: str):
    with pytest.raises(ValueError):
        load_document(deck_pdf, "sideways")


# -- AFTER mode -----------------------------------------------------------------------------


class TestAfter:
    def test_mapping(self, after_pdf: str):
        doc = load_document(after_pdf, "after")
        assert doc.notes_mode is NotesMode.AFTER
        assert doc.has_notes()
        assert doc.page_count == 6
        assert doc.slide_count == 3
        assert [doc.page_for_slide(s) for s in range(3)] == [0, 2, 4]
        assert [doc.notes_page_for_slide(s) for s in range(3)] == [1, 3, 5]
        assert [doc.slide_for_page(p) for p in range(6)] == [0, 0, 1, 1, 2, 2]
        assert doc.region(1, Region.SLIDE) == (2, FULL)
        assert doc.region(1, Region.NOTES) == (3, FULL)
        assert doc.region(1, Region.FULL) == (2, FULL)
        assert doc.aspect(1, Region.NOTES) == pytest.approx(16 / 9)

    def test_per_slide_lists(self, after_pdf: str):
        doc = load_document(after_pdf, "after")
        assert doc.labels == ["1", "2", "3"]  # no page labels: numbered by slide, not PDF page
        assert len(doc.links) == 3
        assert len(doc.annotations) == 3
        with pytest.raises(IndexError):
            doc.region(3, Region.SLIDE)

    def test_auto_does_not_pick_after(self, after_pdf: str):
        assert load_document(after_pdf).notes_mode is NotesMode.NONE

    def test_odd_page_count_falls_back_to_slide_page(self, tmp_path: Path):
        path = tmp_path / "odd.pdf"
        build_after_pdf(path, page_count=5)
        doc = load_document(str(path), NotesMode.AFTER)
        assert doc.slide_count == 3
        assert doc.notes_page_for_slide(1) == 3
        assert doc.notes_page_for_slide(2) is None
        assert doc.region(2, Region.SLIDE) == (4, FULL)
        assert doc.region(2, Region.NOTES) == (4, FULL)

    def test_outline_and_links_map_pages_to_slides(self, tmp_path: Path):
        import pymupdf

        path = tmp_path / "toc.pdf"
        doc = pymupdf.open()
        for _ in range(4):
            doc.new_page(width=PAGE_W, height=PAGE_H)
        doc.set_toc([[1, "Second slide", 3], [1, "Nowhere", -1]])
        doc[0].insert_link(
            {"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 50), "page": 3}
        )
        doc.save(str(path))
        doc.close()
        info = load_document(str(path), "after")
        assert info.outline == [OutlineEntry(level=1, title="Second slide", slide=1)]
        (link,) = info.links[0]
        assert isinstance(link, Link)
        assert link.target == 1  # PDF page 3 is the notes page of slide 1
        assert link.uri is None
        assert link.rect == pytest.approx((10 / 960, 10 / 540, 50 / 960, 50 / 540))


# -- error handling -------------------------------------------------------------------------


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_document(str(tmp_path / "nope.pdf"))


def test_garbage_file_raises(tmp_path: Path):
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(Exception):  # noqa: B017 - PyMuPDF's exact class is an implementation detail
        load_document(str(path))


@pytest.mark.parametrize("mode", ["none", NotesMode.AFTER])
def test_empty_document_info(mode):
    # PyMuPDF cannot save a zero-page PDF, so build the plain-data object directly.
    info = DocumentInfo(
        path="empty.pdf",
        page_count=0,
        page_sizes=[],
        labels=[],
        links=[],
        annotations=[],
        outline=[],
        notes_mode=mode,
        mtime=0.0,
    )
    assert isinstance(info.notes_mode, NotesMode)  # __post_init__ coerces the string
    assert info.slide_count == 0
    assert info.next_label(0) == 0
    assert info.prev_label(0) == 0
    assert info.label_index("1") is None
    with pytest.raises(IndexError):
        info.region(0, Region.SLIDE)


def test_links_with_unresolved_targets_are_dropped(tmp_path: Path):
    import pymupdf

    path = tmp_path / "badlink.pdf"
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc[0].insert_link(  # named destination that does not exist: no target page
        {"kind": pymupdf.LINK_NAMED, "from": pymupdf.Rect(10, 10, 50, 50), "name": "nowhere"}
    )
    doc[0].insert_link(  # valid URI link, but entirely outside the page
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(2000, 10, 2100, 50), "uri": "https://x"}
    )
    doc[0].insert_link(  # a good one, to make sure the page's links were read at all
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(60, 10, 90, 50), "uri": "https://ok"}
    )
    doc.save(str(path))
    doc.close()
    info = load_document(str(path))
    assert [link.uri for link in info.links[0]] == ["https://ok"]


# -- rendering ------------------------------------------------------------------------------


class TestPageRenderer:
    @pytest.mark.parametrize(
        ("clip", "size"),
        [
            (FULL, (320, 180)),
            (FULL, (333, 187)),
            (FULL, (1, 1)),
            (FULL, (1920, 1080)),
            (RIGHT_HALF, (333, 187)),
            (BOTTOM_HALF, (1001, 97)),
            ((100.3, 50.7, 700.9, 400.2), (257, 149)),
            ((0.0, 0.0, 10.0, 10.0), (3, 7)),  # stretched, tiny
        ],
    )
    def test_exact_output_size(self, deck_pdf: str, clip, size):
        width, height = size
        with PageRenderer(deck_pdf) as renderer:
            image = renderer.render(0, clip, width, height)
        assert (image.width, image.height) == (width, height)
        assert image.stride == 3 * width
        assert len(image.data) == 3 * width * height
        assert isinstance(image.data, bytes)

    def test_content_is_drawn_and_regions_differ(self, notes_pdf: str, notes_doc: DocumentInfo):
        _, slide_clip = notes_doc.region(0, Region.SLIDE)
        _, notes_clip = notes_doc.region(0, Region.NOTES)
        with PageRenderer(notes_pdf) as renderer:
            slide = renderer.render(0, slide_clip, 320, 180)
            notes = renderer.render(0, notes_clip, 320, 180)
            again = renderer.render(0, slide_clip, 320, 180)
        assert slide.data != notes.data
        assert slide.data == again.data  # deterministic, also exercises the display-list cache
        assert any(byte != 0xFF for byte in slide.data)  # not blank
        # The header bar fills the first rows of the slide half with a blue-ish colour.
        r, g, b = slide.data[0], slide.data[1], slide.data[2]
        assert b > r and b > g

    def test_minimum_size_is_one_pixel(self, deck_pdf: str):
        with PageRenderer(deck_pdf) as renderer:
            image = renderer.render(0, FULL, 0, -4)
        assert (image.width, image.height) == (1, 1)

    def test_invalid_clip_raises(self, deck_pdf: str):
        with PageRenderer(deck_pdf) as renderer, pytest.raises(ValueError):
            renderer.render(0, (10.0, 10.0, 10.0, 50.0), 10, 10)

    def test_bad_page_raises(self, deck_pdf: str):
        with PageRenderer(deck_pdf) as renderer, pytest.raises(Exception):  # noqa: B017
            renderer.render(42, FULL, 10, 10)

    def test_close_is_idempotent_and_render_after_close_raises(self, deck_pdf: str):
        renderer = PageRenderer(deck_pdf)
        renderer.render(0, FULL, 8, 8)
        renderer.close()
        renderer.close()
        with pytest.raises(RuntimeError):
            renderer.render(0, FULL, 8, 8)

    def test_render_many_pages_evicts_cached_display_lists(self, deck_pdf: str):
        with PageRenderer(deck_pdf) as renderer:
            renderer.MAX_CACHED_PAGES = 2
            for page in (0, 1, 2, 3, 0, 5):
                image = renderer.render(page, FULL, 16, 9)
                assert (image.width, image.height) == (16, 9)
            assert len(renderer._display_lists) <= 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<FEFF0031>", "1"),
        ("<FEFF00310030>", "10"),
        ("<FEFF 0041 0042>", "AB"),
        ("<41>", "A"),
        ("<FFFE3100>", "1"),
        ("12", "12"),
        ("A-1", "A-1"),
        ("", ""),
        ("<zz>", "<zz>"),
    ],
)
def test_decode_label(raw: str, expected: str):
    from prez.document import decode_label

    assert decode_label(raw) == expected
