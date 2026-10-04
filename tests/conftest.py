"""Shared test setup: offscreen Qt platform and generated fixture PDFs.

The environment variable must be set before anything imports PySide6, hence the early
assignment. The PDFs are built once per session with PyMuPDF (imported lazily so that the
app modules keep the "fitz only in document.py" rule).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from prez.document import DocumentInfo, load_document  # noqa: E402

PAGE_W, PAGE_H = 960.0, 540.0  # 16:9 slide in points

#: Page labels of the deck fixture (beamer-like: pages 2 and 3 are overlays of label "2").
DECK_LABELS = ["1", "2", "2", "3", "4", "5"]
#: Internal link on deck page 1 (0-based 0): rect in points and 0-based target page.
DECK_GOTO_RECT = (100.0, 400.0, 300.0, 500.0)
DECK_GOTO_TARGET = 3
#: URI link on deck page 2 (0-based 1).
DECK_URI_RECT = (600.0, 60.0, 900.0, 160.0)
DECK_URI = "https://example.org"
DECK_ANNOTATION = "Note on page 3"
DECK_OUTLINE = [[1, "Intro", 1], [1, "Middle", 3], [2, "Detail", 5]]  # 1-based pages

#: notes_pdf page 1 carries two links: one in the slide half, one in the notes half.
NOTES_SLIDE_LINK_RECT = (100.0, 400.0, 300.0, 500.0)
NOTES_SLIDE_LINK_TARGET = 2
NOTES_NOTES_LINK_RECT = (1100.0, 400.0, 1300.0, 500.0)
NOTES_NOTES_LINK_URI = "https://example.org/notes"


def _draw_slide(page, number: int, x_offset: float = 0.0) -> None:
    """A coloured header bar and a big page number, so renders are visibly non-blank."""
    import pymupdf

    page.draw_rect(
        pymupdf.Rect(x_offset, 0, x_offset + PAGE_W, PAGE_H * 0.08),
        color=None,
        fill=(0.2, 0.4, 0.8),
    )
    page.insert_text(
        (x_offset + PAGE_W * 0.3, PAGE_H * 0.78),
        str(number),
        fontsize=PAGE_H * 0.6,
        fontname="helv",
        color=(0.1, 0.1, 0.5),
    )
    page.insert_text(
        (x_offset + 24, PAGE_H * 0.06),
        f"Slide {number}",
        fontsize=20,
        fontname="helv",
        color=(1, 1, 1),
    )


def _draw_notes(page, number: int, x_offset: float = 0.0) -> None:
    import pymupdf

    page.insert_text(
        (x_offset + 40, 80),
        f"Notes for {number}",
        fontsize=36,
        fontname="helv",
        color=(0, 0, 0),
    )
    page.draw_line(
        pymupdf.Point(x_offset, 0), pymupdf.Point(x_offset, PAGE_H), color=(0.5, 0.5, 0.5)
    )


def build_deck_pdf(path: Path) -> None:
    """6 pages 960×540, labels 1,2,2,3,4,5, links, an annotation and an outline."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(6):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        _draw_slide(page, index + 1)
    doc.set_page_labels(
        [
            {"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 2},
        ]
    )
    doc[0].insert_link(
        {
            "kind": pymupdf.LINK_GOTO,
            "from": pymupdf.Rect(*DECK_GOTO_RECT),
            "page": DECK_GOTO_TARGET,
        }
    )
    doc[1].insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(*DECK_URI_RECT), "uri": DECK_URI}
    )
    doc[2].add_text_annot((50, 50), DECK_ANNOTATION)
    doc.set_toc(DECK_OUTLINE)
    doc.save(str(path))
    doc.close()


def build_notes_pdf(path: Path) -> None:
    """5 pages 1920×540: slide with number on the left half, notes text on the right."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(5):
        page = doc.new_page(width=2 * PAGE_W, height=PAGE_H)
        _draw_slide(page, index + 1)
        _draw_notes(page, index + 1, x_offset=PAGE_W)
    doc[0].insert_link(
        {
            "kind": pymupdf.LINK_GOTO,
            "from": pymupdf.Rect(*NOTES_SLIDE_LINK_RECT),
            "page": NOTES_SLIDE_LINK_TARGET,
        }
    )
    doc[0].insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(*NOTES_NOTES_LINK_RECT),
            "uri": NOTES_NOTES_LINK_URI,
        }
    )
    doc.save(str(path))
    doc.close()


def build_after_pdf(path: Path, page_count: int = 6) -> None:
    """Pages alternate slide / notes (same 960×540 size); 6 pages → 3 slides."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if index % 2 == 0:
            _draw_slide(page, index // 2 + 1)
        else:
            _draw_notes(page, index // 2 + 1)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pdfs")


@pytest.fixture(scope="session")
def deck_pdf(fixture_dir: Path) -> str:
    path = fixture_dir / "deck.pdf"
    build_deck_pdf(path)
    return str(path)


@pytest.fixture(scope="session")
def notes_pdf(fixture_dir: Path) -> str:
    path = fixture_dir / "notes.pdf"
    build_notes_pdf(path)
    return str(path)


@pytest.fixture(scope="session")
def after_pdf(fixture_dir: Path) -> str:
    path = fixture_dir / "after.pdf"
    build_after_pdf(path)
    return str(path)


@pytest.fixture(scope="session")
def deck_doc(deck_pdf: str) -> DocumentInfo:
    return load_document(deck_pdf)


@pytest.fixture(scope="session")
def notes_doc(notes_pdf: str) -> DocumentInfo:
    return load_document(notes_pdf)
