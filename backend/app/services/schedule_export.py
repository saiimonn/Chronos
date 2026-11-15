import csv
import re
import textwrap
import unicodedata
from functools import cache
from io import BytesIO, StringIO
from typing import Any, Iterator, Sequence

from matplotlib import font_manager, rc_context
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from openpyxl import Workbook


CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def build_csv(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows([_safe_cell(value) for value in row] for row in rows)
    return output.getvalue().encode("utf-8-sig")


def build_xlsx(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Schedule")
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append([_safe_cell(value) for value in row])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_pdf(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    metadata: Sequence[tuple[str, str]],
) -> bytes:
    """Render the rows as an A4-landscape table, `metadata` printed on every page.

    Catalog text is drawn as literal glyphs: mathtext (`$...$`) and TeX are off,
    and the PDF backend escapes string operands, so no value can alter layout.
    """
    output = BytesIO()
    summary = "\n".join(f"{label}: {_pdf_text(value)}" for label, value in metadata)
    with rc_context({"text.usetex": False}), PdfPages(
        output,
        metadata={"Title": "Chronos schedule export", "Subject": summary, "Creator": "Chronos"},
    ) as pdf:
        for figure in _pdf_figures(headers, rows, metadata):
            pdf.savefig(figure)
    return output.getvalue()


# Page geometry in points (A4 landscape). Cells use a monospace font, so a
# column's character budget is an exact width and wrapped text cannot overflow.
_PAGE_W, _PAGE_H, _MARGIN = 11.69 * 72, 8.27 * 72, 18.0
_FONT, _META_FONT = 6.0, 8.0
_CHAR_W = 0.61  # DejaVu Sans Mono advance is 0.602 em
_LINE_H = 1.25  # em, above matplotlib's 1.2 linespacing
_CELL_PAD = 3.0
# Character budgets sized to each column's String(n) maximum or wrapped within it.
_PDF_COLUMN_CHARS = {
    "subject_code": 20,
    "subject_title": 44,
    "group_label": 10,
    "block_name": 14,
    "program_code": 10,
    "year_level": 5,
    "section_letter": 7,
    "capacity": 8,
    "faculty_name": 34,
    "room_code": 10,
    "meeting_days": 10,
    "time_slot": 14,
}
# Six metadata entries at three lines each still leave over 40 table lines a page.
_META_MAX_LINES = 3
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x1f\x7f-\x9f\s]+")


@cache
def _mono_charmap() -> frozenset[int]:
    path = font_manager.findfont(font_manager.FontProperties(family="monospace"))
    return frozenset(font_manager.get_font(path).get_charmap())


def _pdf_text(value: Any) -> str:
    # Newlines and control characters would break row heights or draw as boxes.
    # NFKC folds full-width/compatibility forms; any glyph the mono font lacks
    # would fall back to a wider font and overflow its cell, so it becomes "?".
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _CONTROL_OR_SPACE.sub(" ", text).strip()
    charmap = _mono_charmap()
    return "".join(ch if ord(ch) in charmap else "?" for ch in text)


def _wrap(value: Any, chars: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(_pdf_text(value), chars) or [""]
    if len(lines) > max_lines:
        # NFKC can expand one character into many (U+FDFA is 18), so a cell
        # could otherwise outgrow a page; keep what fits and mark the cut.
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: chars - 1] + "…"
    return lines


def _pdf_figures(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    metadata: Sequence[tuple[str, str]],
) -> Iterator[Figure]:
    """Yield one page at a time; only the cheap pagination is held for all rows."""
    chars = [_PDF_COLUMN_CHARS.get(header, 10) for header in headers]
    widths = [c * _CHAR_W * _FONT + 2 * _CELL_PAD for c in chars]
    # 10% slack: the PDF lays text out unhinted, but hinted rasterisers (Agg
    # previews, the containment tests) round each 8pt advance up by ~5%.
    meta_chars = int((_PAGE_W - 2 * _MARGIN) / (_CHAR_W * _META_FONT * 1.1))
    meta_lines = [
        line
        for label, value in metadata
        for line in _wrap(f"{label}: {_pdf_text(value)}", meta_chars, _META_MAX_LINES)
    ]
    table_top = _PAGE_H - _MARGIN - (len(meta_lines) + 1) * _LINE_H * _META_FONT - 8

    def cells(values: Sequence[Any], max_lines: int) -> tuple[list[list[str]], float]:
        wrapped = [_wrap(v, c, max_lines) for v, c in zip(values, chars)]
        lines = max(len(cell) for cell in wrapped)
        return wrapped, lines * _LINE_H * _FONT + 2 * _CELL_PAD

    header_cells = cells([h.replace("_", " ") for h in headers], 3)
    page_room = table_top - header_cells[1] - _MARGIN
    # The tallest row a page can hold; capping cells here means every row fits.
    max_lines = int((page_room - 2 * _CELL_PAD) / (_LINE_H * _FONT))
    assert max_lines >= 1, "PDF metadata leaves no room for the table"
    pages: list[list[tuple[list[list[str]], float]]] = [[]]
    room = page_room
    for row in rows:
        row_cells = cells(row, max_lines)
        if row_cells[1] > room and pages[-1]:
            pages.append([])
            room = page_room
        pages[-1].append(row_cells)
        room -= row_cells[1]

    for number, page in enumerate(pages, start=1):
        figure = Figure(figsize=(11.69, 8.27))
        axes = figure.add_axes((0, 0, 1, 1))
        axes.set_xlim(0, _PAGE_W)
        axes.set_ylim(0, _PAGE_H)
        axes.axis("off")
        axes.text(
            _MARGIN, _PAGE_H - _MARGIN,
            "\n".join([*meta_lines, f"Page: {number} of {len(pages)}"]),
            va="top", fontsize=_META_FONT, family="monospace", parse_math=False,
        )
        y = table_top
        if not page:
            axes.text(_MARGIN, y, "No assignments in this scope.", va="top", fontsize=9)
        for index, (wrapped, height) in enumerate([header_cells, *page] if page else []):
            x = _MARGIN
            y -= height
            for lines, width in zip(wrapped, widths):
                axes.add_patch(Rectangle(
                    (x, y), width, height, linewidth=0.4, edgecolor="black",
                    facecolor="#e6e6e6" if index == 0 else "white",
                ))
                axes.text(
                    x + _CELL_PAD, y + height - _CELL_PAD, "\n".join(lines),
                    va="top", ha="left", fontsize=_FONT, family="monospace",
                    linespacing=1.2, parse_math=False,
                )
                x += width
        yield figure
