"""Article text source. Deliberately does nothing else: extraction stays
separate from interpretation so a bad source layout doesn't get silently
absorbed into the parsing logic that follows.

No OCR here by design (see project decision log): many NYT-style PDFs are
page-image screenshots with no embedded text layer, which pdfplumber
cannot read. Rather than guess text via OCR, this pipeline requires a
human-verified plain-text sidecar file for image-only PDFs and fails
loudly rather than silently ingesting an empty/garbled record.
"""
from pathlib import Path

import pdfplumber

MIN_PLAUSIBLE_CHARS = 100


def extract_pdf_text(pdf_path: str) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return "\n".join(pages)


def get_article_text(pdf_path: str, text_file: str = None) -> str:
    """Try the PDF's embedded text layer first. If it's empty/too short
    (image-only PDF), fall back to a human-supplied sidecar .txt file --
    either an explicit --text-file path, or <pdf_stem>.txt next to the PDF.
    Raises FileNotFoundError with clear next steps if neither works.
    """
    raw = extract_pdf_text(pdf_path)
    if len(raw.strip()) >= MIN_PLAUSIBLE_CHARS:
        return raw

    candidate = Path(text_file) if text_file else Path(pdf_path).with_suffix(".txt")
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")

    raise FileNotFoundError(
        f"'{pdf_path}' has no usable embedded text layer (extracted only "
        f"{len(raw.strip())} chars -- likely an image-only/screenshot PDF), "
        f"and no sidecar text file was found at '{candidate}'. "
        f"Save the article body as plain text to that path (or pass "
        f"--text-file) and re-run."
    )
