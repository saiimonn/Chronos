from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from openpyxl import load_workbook

from app.services.schedule_export import (
    _pdf_figures,
    _pdf_text,
    build_csv,
    build_pdf,
    build_xlsx,
)
from app.tests.support.pdf import capture_pdf_pages


def test_schedule_exports_escape_spreadsheet_formulas():
    headers = ("subject", "room")
    rows = (("=1+1", "@ROOM"),)

    csv_content = build_csv(headers, rows).decode("utf-8-sig")
    workbook = load_workbook(BytesIO(build_xlsx(headers, rows)), read_only=True)
    worksheet = workbook["Schedule"]

    assert csv_content == "subject,room\n'=1+1,'@ROOM\n"
    assert list(worksheet.values) == [headers, ("'=1+1", "'@ROOM")]


def test_pdf_export_draws_catalog_text_literally_and_carries_metadata(monkeypatch):
    headers = ("subject", "room")
    # Mathtext would raise on this markup; it must be drawn as plain text.
    rows = ((r"$\notacommand{x}$ <b>", "=1+1"),)
    metadata = (("Term", "2025-2026 Term 1"), ("Scope", "Block 1A"))
    saved = capture_pdf_pages(monkeypatch)

    content = build_pdf(headers, rows, metadata)

    assert content.startswith(b"%PDF")
    assert b"Scope: Block 1A" in content  # document Info /Subject
    [page] = saved
    assert page[0].splitlines() == [
        "Term: 2025-2026 Term 1",
        "Scope: Block 1A",
        "Page: 1 of 1",
    ]
    # Wrapped to the 10-character default budget, but otherwise verbatim.
    assert page[3].split("\n") == [r"$\notacomm", "and{x}$", "<b>"]


def test_pdf_export_renders_an_empty_scope():
    assert build_pdf(("subject",), (), ()).startswith(b"%PDF")


# The PDF table columns: EXPORT_HEADERS without academic_year/term_number.
PDF_HEADERS = (
    "subject_code", "subject_title", "group_label", "block_name", "program_code",
    "year_level", "section_letter", "capacity", "faculty_name", "room_code",
    "meeting_days", "time_slot",
)
METADATA = (("Term", "2025-2026 Term 1"), ("Scope", "All blocks; all rooms"))


def _row(title="Intro to Programming", faculty="Dela Cruz, Juan"):
    return ("S" * 20, title, "G" * 20, "B" * 20, "P" * 20, 4, "C" * 5, 40,
            faculty, "R" * 20, "M" * 10, "T" * 50)


def _cells(figure):
    """Pair each table rectangle with its text; the first text is the header block."""
    axes = figure.axes[0]
    return list(zip(axes.patches, axes.texts[1:]))


def _assert_cells_contained(figures):
    for figure in figures:
        renderer = FigureCanvasAgg(figure).get_renderer()
        page = figure.bbox
        widths = []
        for rect, text in _cells(figure):
            cell = rect.get_window_extent(renderer)
            drawn = text.get_window_extent(renderer)
            assert page.x0 <= cell.x0 and cell.x1 <= page.x1
            assert page.y0 <= cell.y0 and cell.y1 <= page.y1
            assert cell.x0 <= drawn.x0 and drawn.x1 <= cell.x1, text.get_text()
            assert cell.y0 <= drawn.y0 and drawn.y1 <= cell.y1, text.get_text()
            widths.append(rect.get_width())
        assert widths[: len(PDF_HEADERS)] == widths[len(PDF_HEADERS) : 2 * len(PDF_HEADERS)]


def test_pdf_cells_hold_maximum_length_values_inside_the_page():
    longest = _row(title="Title word " * 13 + "X" * 7, faculty="W" * 150)
    figures = list(_pdf_figures(PDF_HEADERS, [longest] * 40, METADATA))

    _assert_cells_contained(figures)
    # Every character of every value is drawn somewhere on the page.
    drawn = "".join(t.get_text() for _, t in _cells(figures[0])[len(PDF_HEADERS):][:12])
    assert drawn.replace("\n", "").count("W") == 150


def test_pdf_cells_hold_glyphs_outside_the_mono_font():
    # U+1E9E and U+01C5 are missing from DejaVu Sans Mono; full-width W is a
    # compatibility form. Drawn as-is, each falls back to a ~1.2 em glyph.
    wide = _row(title="ẞ" * 150, faculty="Ｗ" * 75 + "ǅ" * 75)

    _assert_cells_contained(_pdf_figures(PDF_HEADERS, [wide] * 3, METADATA))
    assert _pdf_text("ẞＷǅ Peña") == "?WDž Peña"


def test_pdf_caps_cells_that_nfkc_expands_past_a_page():
    # U+FDFA NFKC-expands to 18 in-font characters: 150 of them is ~90 lines.
    tall = _row(title="ﷺ" * 150, faculty="ﷺ" * 150)
    metadata = (*METADATA, ("Scope", "ﷺ" * 40), ("Schedule", "ﷺ" * 40))
    figures = list(_pdf_figures(PDF_HEADERS, [tall, _row(), tall], metadata))

    _assert_cells_contained(figures)
    for figure in figures:
        renderer = FigureCanvasAgg(figure).get_renderer()
        header = figure.axes[0].texts[0]
        top_cell = max(r.get_window_extent(renderer).y1 for r, _ in _cells(figure))
        assert header.get_window_extent(renderer).y0 >= top_cell
        assert header.get_window_extent(renderer).y1 <= figure.bbox.y1
        assert header.get_window_extent(renderer).x1 <= figure.bbox.x1
        assert header.get_text().count("…") == 2  # both long entries cut
    cells = [text.get_text() for figure in figures for _, text in _cells(figure)]
    assert sum(text.endswith("…") for text in cells) == 4  # 2 cells x 2 rows


def test_pdf_cell_text_collapses_newlines_and_control_characters():
    assert _pdf_text("Line1\nLine2\r\tA\x00b\x07c  ") == "Line1 Line2 A b c"
    figure = next(_pdf_figures(PDF_HEADERS, [_row(title="Line1\nLine2")], METADATA))

    texts = [text.get_text() for _, text in _cells(figure)]

    assert "Line1 Line2" in texts


def test_pdf_paginates_by_row_height_and_repeats_the_header():
    one = list(_pdf_figures(PDF_HEADERS, [_row()], METADATA))
    many = list(_pdf_figures(PDF_HEADERS, [_row()] * 200, METADATA))

    assert len(one) == 1
    assert len(many) > 1
    assert sum(len(_cells(f)) for f in many) == (200 + len(many)) * len(PDF_HEADERS)
    for number, figure in enumerate(many, start=1):
        cells = _cells(figure)
        assert cells[0][1].get_text() == "subject code"
        assert min(rect.get_y() for rect, _ in cells) >= 0
        assert f"Page: {number} of {len(many)}" in figure.axes[0].texts[0].get_text()
