
from pathlib import Path
import json

PROJECT = Path(__file__).resolve().parents[2]

JSON_FILE = (
    PROJECT
    / "data"
    / "processed"
    / "RBI_Commercial_Banks_Credit_Facilities_2025.json"
)

with open(JSON_FILE, "r", encoding="utf-8") as file:
    document = json.load(file)

pages = document["pages"]

print("Total PDF pages:", len(pages))

keywords = [
    "Chapter III",
    "Digital Lending Guidelines",
    "General Requirements for bank-LSP",
    "Conduct and Customer Protection",
    "Technology and Data Requirement",
    "Reporting of Credit Information",
    "Loss sharing arrangement",
]

for page in pages:
    text = page.get("text", "")
    matching = [
        keyword for keyword in keywords
        if keyword.lower() in text.lower()
    ]

    if matching:
        print("\n" + "=" * 75)
        print("PDF PAGE INDEX:", page.get("page"))
        print("MATCHED HEADINGS:", matching)
        print("-" * 75)
        print(text[:2500])
