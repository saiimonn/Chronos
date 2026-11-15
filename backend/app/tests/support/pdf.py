from matplotlib.backends.backend_pdf import PdfPages


def capture_pdf_pages(monkeypatch) -> list[list[str]]:
    """Record the text of every page build_pdf actually saves, in draw order.

    Page text is compressed inside the PDF, so this inspects the saved figures
    instead of depending on an external text extractor.
    """
    pages: list[list[str]] = []
    savefig = PdfPages.savefig

    def recording(self, figure=None, **kwargs):
        pages.append([text.get_text() for text in figure.axes[0].texts])
        return savefig(self, figure, **kwargs)

    monkeypatch.setattr(PdfPages, "savefig", recording)
    return pages
