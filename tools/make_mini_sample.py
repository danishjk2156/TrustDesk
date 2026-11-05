"""Build samples/acme_mini.xlsx: a 5-question stand-in until the 40-question sample lands.

Covers each path the drafter must handle:
  Q1-Q3 factual (provable once acmecloud/ exists), Q4 likely NEEDS-EVIDENCE,
  Q5 a COMMITMENT that goes to Legal.

    python tools/make_mini_sample.py
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

QUESTIONS = [
    ("Q1", "Is customer data encrypted at rest?", "Encryption"),
    ("Q2", "Is all data encrypted in transit using TLS 1.2 or higher?", "Encryption"),
    ("Q3", "Do you enforce multi-factor authentication for all administrative access?", "Access control"),
    ("Q4", "Are backups tested by performing a restore at least annually?", "Business continuity"),
    ("Q5", "Within how many hours will you notify us after confirming a data breach?", "Incident response"),
]

out = Path(__file__).resolve().parent.parent / "samples" / "acme_mini.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)

wb = Workbook()
ws = wb.active
ws.title = "Security Questionnaire"
ws.append(["ID", "Question", "Category"])
for cell in ws[1]:
    cell.font = Font(bold=True)
for row in QUESTIONS:
    ws.append(row)
ws.column_dimensions["A"].width = 6
ws.column_dimensions["B"].width = 70
ws.column_dimensions["C"].width = 22
for r in ws.iter_rows(min_row=2):
    for cell in r:
        cell.alignment = Alignment(wrap_text=True, vertical="top")

wb.save(out)
print(out)
