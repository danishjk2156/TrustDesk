"""End-to-end test of TrustDesk's deterministic pipeline on the 41-question sample.

Plays the drafter's mechanical steps the way its CLAUDE.md describes them (tags from the answer
key stand in for the drafter's tagging), then runs the real evidence engine, release gate and
XLSX writer. The Switch room and the models are not involved.

    python -m unittest tests.test_pipeline -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import check_gates  # noqa: E402
import search  # noqa: E402
from xlsx_io import read_questions, write_answers  # noqa: E402

SHEET = ROOT / "samples" / "acme_questionnaire.xlsx"
KEY = {q["id"]: q for q in json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8"))}
LABEL = {"found": "VERIFIED", "missing": "NEEDS-EVIDENCE", "contradicted": "NEEDS-EVIDENCE"}


def draft(questions):
    """Drafter steps 2-3: label, tag, one batched claims message, apply the evidence reply."""
    records, claims = [], []
    for q in questions:
        control = KEY[q["id"]]["control"]
        rec = {"id": q["id"], "question": q["question"], "control": control, "evidence": [],
               "approved_by": None, "approved_at": None}
        if control is None:
            rec.update(label="COMMITMENT", answer="Per policy: see the committed figure.")
        else:
            claims.append({"id": q["id"], "control": control, "claim": q["question"]})
        records.append(rec)
    reply = search.run({"type": "claims", "claims": claims})
    by_id = {r["id"]: r for r in reply["results"]}
    for rec in records:
        r = by_id.get(rec["id"])
        if not r:
            continue
        rec["label"] = LABEL[r["status"]]
        rec["evidence"] = r.get("evidence", [])
        rec["permalinks"] = r.get("permalinks")
        rec["answer"] = "Yes." if r["status"] == "found" else (
            "Partially / no: " + r["reason"] if r["status"] == "contradicted" else "Unknown: " + r["reason"])
    return records, reply


def approve(records):
    for rec in records:
        rec["approved_by"] = "legal" if rec["label"] == "COMMITMENT" else "security-lead"
        rec["approved_at"] = "2026-11-06T10:00:00+05:30"


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = read_questions(SHEET)
        cls.ids = [q["id"] for q in cls.questions]
        cls.records, cls.reply = draft(cls.questions)

    def test_reads_all_41_questions(self):
        self.assertEqual(len(self.questions), 41)
        self.assertEqual(self.ids[-1], "Q41")

    def test_one_batched_claims_message_covers_every_factual_question(self):
        self.assertEqual(len(self.reply["results"]), 39)  # 41 minus the 2 commitments

    def test_labels_match_the_answer_key(self):
        counts = {}
        for r in self.records:
            counts[r["label"]] = counts.get(r["label"], 0) + 1
        self.assertEqual(counts, {"VERIFIED": 30, "NEEDS-EVIDENCE": 9, "COMMITMENT": 2})

    def test_injection_row_is_not_verified(self):
        q41 = next(r for r in self.records if r["id"] == "Q41")
        self.assertEqual(q41["label"], "NEEDS-EVIDENCE")
        self.assertTrue(q41["answer"].startswith("Unknown"))

    def test_gate_blocks_until_approved_then_passes(self):
        records = json.loads(json.dumps(self.records))
        blocked = check_gates.check(records, self.ids)
        self.assertFalse(blocked["ok"])
        self.assertEqual({p["id"] for p in blocked["problems"]}, set(self.ids))  # every answer needs sign-off
        approve(records)
        self.assertEqual(check_gates.check(records, self.ids)["problems"], [])

    def test_stale_run_cannot_pass_for_the_new_sheet(self):
        """Yesterday's 40-answer run must not clear today's 41-question sheet."""
        old = json.loads((ROOT / "tests" / "fixtures" / "run-40q-answers.json").read_text(encoding="utf-8"))
        report = check_gates.check(old, self.ids)
        self.assertIn({"id": "Q41", "problem": "no answer record"}, report["problems"])

    def test_package_writes_every_row(self):
        records = json.loads(json.dumps(self.records))
        approve(records)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_answers(SHEET, records, Path(tmp) / "completed.xlsx")
            ws = load_workbook(out).active
            header = [c.value for c in ws[1]]
            rows = [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
        self.assertEqual(len(rows), 41)
        self.assertEqual(header[:3], ["ID", "Question", "Category"])  # customer's columns kept, in order
        for row in rows:
            self.assertTrue(row["Answer"] and row["Label"] and row["Approved by"], row["ID"])
            if row["Label"] == "VERIFIED":
                self.assertTrue(row["Evidence"], row["ID"])

    def test_library_gets_only_proven_or_approved_promises(self):
        records = json.loads(json.dumps(self.records))
        approve(records)
        library = [r for r in records if r["label"] in ("VERIFIED", "COMMITMENT")]
        self.assertEqual(len(library), 32)
        self.assertFalse(any(r["label"] == "NEEDS-EVIDENCE" for r in library))

    def test_verified_answers_carry_permalinks_in_a_clone(self):
        verified = [r for r in self.records if r["label"] == "VERIFIED"]
        if not any(r.get("permalinks") for r in verified):
            self.skipTest("not a GitHub clone")
        for r in verified:
            self.assertEqual(len(r["permalinks"]), len(r["evidence"]), r["id"])


if __name__ == "__main__":
    unittest.main()
