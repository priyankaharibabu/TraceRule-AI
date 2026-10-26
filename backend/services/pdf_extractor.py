
from pathlib import Path
import fitz
import json
import hashlib

PROJECT = Path(__file__).resolve().parents[2]

PDF_FILES = [
    PROJECT / "data/raw/current/RBI_Commercial_Banks_Credit_Facilities_2025.pdf",
    PROJECT / "data/raw/historical/RBI_Digital_Lending_Directions_May_2025.pdf",
    PROJECT / "data/raw/historical/RBI_Digital_Lending_Guidelines_September_2022.pdf",
]

OUTPUT = PROJECT / "data/processed"
OUTPUT.mkdir(parents=True, exist_ok=True)


def extract_pdf(pdf_path):
    if not pdf_path.exists():
        print("MISSING:", pdf_path)
        return

    file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    document = fitz.open(pdf_path)

    pages = []

    for page_number, page in enumerate(document, start=1):
        text = page.get_text("text")

        pages.append({
            "page": page_number,
            "text": text
        })

    result = {
        "filename": pdf_path.name,
        "sha256": file_hash,
        "page_count": len(document),
        "pages": pages
    }

    output_file = OUTPUT / f"{pdf_path.stem}.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    extracted_chars = sum(len(p["text"]) for p in pages)

    print("FILE:", pdf_path.name)
    print("PAGES:", len(document))
    print("EXTRACTED CHARACTERS:", extracted_chars)
    print("SHA-256:", file_hash)
    print("SAVED:", output_file)
    print("-" * 50)


if __name__ == "__main__":
    for pdf in PDF_FILES:
        extract_pdf(pdf)
