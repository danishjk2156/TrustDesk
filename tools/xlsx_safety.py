"""Spreadsheet safety for customer questionnaires: what may come in, what may go out.

A questionnaire is attacker-controlled input and the completed workbook is sent back out, so both
directions are checked here.

  Input  (`check_file`)   refuses macros, external links, embedded objects, ActiveX, oversized or
                          zip-bomb files and path-traversing zip entries before openpyxl reads a byte.
  Input  (`clean_text`, `flags_for`, `valid_id`)
                          strips control characters, caps length, and flags text that looks like an
                          instruction to the model or a spreadsheet formula. Flags are reported, never obeyed.
  Output (`safe_cell`)    a cell that would start a formula (= + - @ tab CR) is written as plain text.

Everything raises `UnsafeWorkbook` (a ValueError) so callers fail closed.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 200
MAX_RATIO = 100
MAX_ROWS = 2000
MAX_QUESTION_CHARS = 2000
MAX_CELL_CHARS = 32000  # Excel's own limit is 32767

FORBIDDEN_ENTRY = re.compile(r"(^|/)(vbaProject\.bin|externalLinks/|embeddings/|activeX/|oleObject\d*\.bin)", re.I)
FORBIDDEN_SUFFIX = {".xlsm", ".xlsb", ".xltm", ".xls"}
FORBIDDEN_TYPE = re.compile(r"macroEnabled|vbaProject|oleObject|activeX", re.I)
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")  # incl. bidi overrides
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|rules)|"
    r"disregard (all |any |the )?(previous|prior|above|your) |"
    r"(mark|label|set|treat) (everything|all|every answer|this)\b.{0,40}\b(verified|approved)|"
    r"skip (the )?(approval|review|gate|check)|bypass (the )?(gate|approval|review)|"
    r"you are now\b|new instructions?:|system prompt|\bas (the )?(security[- ]lead|legal|admin)\b.{0,20}\bapprove",
    re.I)


class UnsafeWorkbook(ValueError):
    pass


def check_file(path: str | Path) -> None:
    """Reject a workbook that carries active content or could exhaust memory. Reads only the zip index."""
    path = Path(path)
    if path.suffix.lower() in FORBIDDEN_SUFFIX:
        raise UnsafeWorkbook(f"{path.suffix} files are not accepted; send a plain .xlsx")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise UnsafeWorkbook(f"file is {size} bytes; the limit is {MAX_FILE_BYTES}")
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                raise UnsafeWorkbook(f"{len(infos)} zip entries; the limit is {MAX_ENTRIES}")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise UnsafeWorkbook("uncompressed size is over the limit (possible zip bomb)")
            for info in infos:
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise UnsafeWorkbook(f"unsafe zip entry name {name!r}")
                if FORBIDDEN_ENTRY.search(name):
                    raise UnsafeWorkbook(f"workbook contains {name!r} (macro, external link or embedded object)")
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 1_000_000:
                    raise UnsafeWorkbook(f"{name!r} is compressed {info.file_size // info.compress_size}:1 (possible zip bomb)")
            if (types := "[Content_Types].xml") in z.namelist() and FORBIDDEN_TYPE.search(z.read(types).decode("utf-8", "replace")):
                raise UnsafeWorkbook("workbook declares macro-enabled or embedded-object content")
    except zipfile.BadZipFile:
        raise UnsafeWorkbook("not a valid .xlsx file") from None


def valid_id(value) -> str:
    text = "" if value is None else str(value).strip()
    if not ID_PATTERN.match(text):
        raise UnsafeWorkbook(f"question ID {text[:40]!r} must be 1-32 letters, digits, '.', '_' or '-'")
    return text


def clean_text(value, limit: int = MAX_QUESTION_CHARS) -> str:
    text = CONTROL_CHARS.sub("", str(value)).strip()
    if len(text) > limit:
        raise UnsafeWorkbook(f"cell text is {len(text)} characters; the limit is {limit}")
    return text


def flags_for(raw) -> list[str]:
    """Why this question text needs a person's eye. Never changes the text."""
    text = str(raw)
    flags = []
    if text.lstrip().startswith(("=", "+", "@")):
        flags.append("formula")
    if INJECTION.search(text):
        flags.append("instruction-like text")
    if CONTROL_CHARS.search(text):
        flags.append("control or bidi characters removed")
    return flags


def safe_cell(value):
    """What may be written to an output cell: text, never a formula, within Excel's limit."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = CONTROL_CHARS.sub("", str(value))[:MAX_CELL_CHARS]
    return "'" + text if text.startswith(FORMULA_START) else text
