import os
from pathlib import Path
from pypdf import PdfReader

UPLOADS_DIR = Path(__file__).parent / "uploads"


def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def get_tutor_context() -> str:
    if not UPLOADS_DIR.exists():
        return ""

    pdfs = sorted(UPLOADS_DIR.glob("*.pdf"))
    if not pdfs:
        return ""

    sections = []
    for pdf in pdfs:
        text = extract_text_from_pdf(pdf)
        if text:
            sections.append(f"--- {pdf.name} ---\n{text}")

    return "\n\n".join(sections)
