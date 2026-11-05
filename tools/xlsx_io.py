"""Read and write TrustDesk questionnaires (SIG Lite-style single-sheet XLSX).

Expected layout: one sheet, a header row with at least `ID` and `Question`
(optional `Category`), then one question per row. Header names are matched
case-insensitively but must be exactly ID / Question / Category.

Python API (used by the drafter and packager):
    read_questions(path) -> list of {"id", "question", "category", "row"}
    write_answers(path, answers, out_path=None) -> out_path

CLI (handy for agents):
    python tools/xlsx_io.py read  samples/acme_mini.xlsx
    python tools/xlsx_io.py write samples/acme_mini.xlsx work/answers.json [out.xlsx]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

REQUIRED = ("id", "question")
OUTPUT_COLUMNS = ("Answer", "Label", "Evidence", "Approved by")


def _header_map(ws) -> dict[str, int]:
    """Map lower-cased header name -> 1-based column index from row 1."""
    headers = {}
    for cell in ws[1]:
        if cell.value is not None:
            headers[str(cell.value).strip().lower()] = cell.column
    missing = [h for h in REQUIRED if h not in headers]
    if missing:
        raise ValueError(
            f"{ws.title!r} row 1 is missing column(s) {missing}; found {list(headers)}"
        )
    return headers


def read_questions(path: str | Path) -> list[dict]:
    """Return every non-empty question row, in sheet order."""
    ws = load_workbook(path, read_only=True).active
    cols = _header_map(ws)
    questions = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        qid = row[cols["id"] - 1]
        text = row[cols["question"] - 1]
        if qid is None and text is None:
            continue
        if qid is None or text is None:
            raise ValueError(f"row {row_idx}: ID and Question must both be filled")
        category = row[cols["category"] - 1] if "category" in cols else None
        questions.append(
            {
                "id": str(qid).strip(),
                "question": str(text).strip(),
                "category": str(category).strip() if category else None,
                "row": row_idx,
            }
        )
    return questions


def write_answers(path: str | Path, answers: list[dict], out_path: str | Path | None = None) -> Path:
    """Copy the questionnaire and fill Answer / Label / Evidence / Approved by.

    `answers` are TrustDesk answer records (see agents/drafter/CLAUDE.md); matched to
    rows by `id`. Records without a matching row are an error, rows without a record
    are left blank. Keeps the customer's own columns and row order.
    """
    path = Path(path)
    out_path = Path(out_path) if out_path else path.with_name(f"{path.stem}_completed.xlsx")
    wb = load_workbook(path)
    ws = wb.active
    cols = _header_map(ws)

    next_col = ws.max_column + 1
    out_cols = {}
    for name in OUTPUT_COLUMNS:
        key = name.lower()
        if key not in cols:
            cell = ws.cell(row=1, column=next_col, value=name)
            cell.font = Font(bold=True)
            cols[key] = next_col
            next_col += 1
        out_cols[name] = cols[key]

    rows_by_id = {}
    for r in range(2, ws.max_row + 1):
        qid = ws.cell(row=r, column=cols["id"]).value
        if qid is not None:
            rows_by_id[str(qid).strip()] = r

    unknown = [a.get("id") for a in answers if str(a.get("id")) not in rows_by_id]
    if unknown:
        raise ValueError(f"answers for unknown question IDs: {unknown}")

    wrap = Alignment(wrap_text=True, vertical="top")
    for a in answers:
        r = rows_by_id[str(a["id"])]
        evidence = a.get("evidence") or []
        values = {
            "Answer": a.get("answer"),
            "Label": a.get("label"),
            "Evidence": "\n".join(evidence) if evidence else None,
            "Approved by": a.get("approved_by"),
        }
        for name, value in values.items():
            cell = ws.cell(row=r, column=out_cols[name], value=value)
            cell.alignment = wrap

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "read":
        print(json.dumps(read_questions(argv[1]), indent=2, ensure_ascii=False))
        return 0
    if len(argv) >= 3 and argv[0] == "write":
        answers = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        out = write_answers(argv[1], answers, argv[3] if len(argv) > 3 else None)
        print(out)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
