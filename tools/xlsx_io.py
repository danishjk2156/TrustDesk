"""Read and write TrustDesk questionnaires (SIG Lite-style single-sheet XLSX).

Expected layout: one sheet, a header row with at least `ID` and `Question`
(optional `Category`), then one question per row. Header names are matched
case-insensitively but must be exactly ID / Question / Category.

Python API (used by the drafter and packager):
    read_questions(path) -> list of {"id", "question", "category", "row"}
    write_answers(path, answers, out_path=None, approvals=None) -> out_path   # approvals: check_gates report["approvals"]

CLI (handy for agents):
    python tools/xlsx_io.py read  samples/acme_mini.xlsx
    python tools/xlsx_io.py write samples/acme_mini.xlsx work/answers.json [out.xlsx [gate-report.json]]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xlsx_safety  # noqa: E402

REQUIRED = ("id", "question")
OUTPUT_COLUMNS = ("Answer", "Label", "Evidence", "Approved by", "Frameworks")


def _framework_label(control):
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        from frameworks import label  # tools/frameworks.py, next to this file
    except ImportError:
        return None
    return label(control) or None


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
    """Return every non-empty question row, in sheet order.

    The file is checked first (macros, external links, zip bombs: tools/xlsx_safety.py). Each question
    carries `flags`: reasons a person should look (instruction-like text, a formula, hidden row).
    Duplicate or malformed IDs and oversized sheets are errors, never guesses.
    """
    xlsx_safety.check_file(path)
    xlsx_safety.check_workbook_shape(path, load_workbook)
    wb = load_workbook(path)  # not read-only: row and column visibility are needed
    try:
        ws = wb.active
        if ws.sheet_state != "visible":
            raise ValueError(f"the active sheet {ws.title!r} is hidden")
        if ws.max_row > xlsx_safety.MAX_ROWS + 1:
            raise ValueError(f"{ws.max_row} rows; the limit is {xlsx_safety.MAX_ROWS}")
        cols = _header_map(ws)
        questions, seen = [], set()
        for row_idx in range(2, ws.max_row + 1):
            raw_id = ws.cell(row=row_idx, column=cols["id"]).value
            raw_text = ws.cell(row=row_idx, column=cols["question"]).value
            if raw_id is None and raw_text is None:
                continue
            if raw_id is None or raw_text is None:
                raise ValueError(f"row {row_idx}: ID and Question must both be filled")
            qid = xlsx_safety.valid_id(raw_id)
            if qid in seen:
                raise ValueError(f"row {row_idx}: duplicate question ID {qid!r}")
            seen.add(qid)
            flags = xlsx_safety.flags_for(raw_text)
            if ws.row_dimensions[row_idx].hidden:
                flags.append("hidden row")
            category = ws.cell(row=row_idx, column=cols["category"]).value if "category" in cols else None
            flags += [f"category: {f}" for f in xlsx_safety.flags_for(category)] if category else []
            flags += [f"hidden column: {n}" for n, c in cols.items() if n in ("id", "question", "category") and _col_hidden(ws, c)]
            questions.append({"id": qid, "question": xlsx_safety.clean_text(raw_text),
                              "category": xlsx_safety.clean_text(category, 200) if category else None,
                              "row": row_idx, "flags": list(dict.fromkeys(flags))})
        return questions
    finally:
        wb.close()


def _defuse_workbook(wb) -> None:
    """The customer's own content goes back out too: formulas become text, and links, defined names,
    data validations and conditional formats (which can hold formulas) are removed."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f" or (isinstance(cell.value, str) and cell.value.startswith("=")):
                    cell.value = xlsx_safety.safe_cell(str(cell.value))
                    cell.data_type = "s"
                if cell.hyperlink:
                    cell.hyperlink = None
        ws.data_validations.dataValidation = []
        ws.conditional_formatting = ConditionalFormattingList()
    for name in list(wb.defined_names):
        del wb.defined_names[name]


def _col_hidden(ws, column: int) -> bool:
    dim = ws.column_dimensions.get(get_column_letter(column))
    return bool(dim and dim.hidden)


def _approver_label(approval: dict | None) -> str | None:
    return f"{approval['approver']} ({approval['role']}), {approval['at'][:10]}" if approval else None


def write_answers(path: str | Path, answers: list[dict], out_path: str | Path | None = None,
                  approvals: dict | None = None) -> Path:
    """Copy the questionnaire and fill Answer / Label / Evidence / Approved by.

    `answers` are TrustDesk answer records (see agents/drafter/CLAUDE.md); matched to
    rows by `id`. Records without a matching row are an error, rows without a record
    are left blank. Keeps the customer's own columns and row order.

    `approvals` is the release gate's `approvals` map ({id: {approver, role, at}}). "Approved by" is
    filled from it only; the record's own `approved_by` is never written out.
    """
    path = Path(path)
    xlsx_safety.check_file(path)
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
            "Approved by": _approver_label((approvals or {}).get(str(a["id"]))),
            "Frameworks": _framework_label(a.get("control")),
        }
        for name, value in values.items():
            cell = ws.cell(row=r, column=out_cols[name])
            cell.value = xlsx_safety.safe_cell(value)
            cell.data_type = "s" if isinstance(cell.value, str) else cell.data_type  # never a formula
            cell.alignment = wrap
    _defuse_workbook(wb)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    wb.close()
    return out_path


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "read":
        print(json.dumps(read_questions(argv[1]), indent=2, ensure_ascii=False))
        return 0
    if len(argv) >= 3 and argv[0] == "write":
        answers = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        gate_report = json.loads(Path(argv[4]).read_text(encoding="utf-8")) if len(argv) > 4 else {}
        out = write_answers(argv[1], answers, argv[3] if len(argv) > 3 else None, gate_report.get("approvals"))
        print(out)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
