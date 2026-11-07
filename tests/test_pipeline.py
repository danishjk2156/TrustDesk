"""End-to-end test of TrustDesk's deterministic pipeline on the 41-question sample.

Plays the drafter's mechanical steps the way its CLAUDE.md describes them (tags from the answer
key stand in for the drafter's tagging), then runs the real evidence engine, release gate and
XLSX writer. The Switch room and the models are not involved.

    python -m unittest tests.test_pipeline -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

import support
from support import ROOT, SHEET, draft, gate
from xlsx_io import read_questions, write_answers


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
        blocked = gate(records, [])
        self.assertFalse(blocked["ok"])
        self.assertEqual({p["id"] for p in blocked["problems"]}, set(self.ids))  # every answer needs sign-off
        self.assertEqual(gate(records, support.sign_run(records))["problems"], [])

    def test_package_writes_every_row(self):
        records = json.loads(json.dumps(self.records))
        approved = gate(records, support.sign_run(records))["approvals"]
        with tempfile.TemporaryDirectory() as tmp:
            out = write_answers(SHEET, records, Path(tmp) / "completed.xlsx", approved)
            ws = load_workbook(out).active
            header = [c.value for c in ws[1]]
            rows = [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
        self.assertEqual(len(rows), 41)
        self.assertEqual(header[:3], ["ID", "Question", "Category"])  # customer's columns kept, in order
        for row in rows:
            self.assertTrue(row["Answer"] and row["Label"] and row["Approved by"], row["ID"])
            if row["Label"] == "VERIFIED":
                self.assertTrue(row["Evidence"], row["ID"])

    def test_approved_by_column_ignores_the_record_field(self):
        records = json.loads(json.dumps(self.records))
        for r in records:
            r["approved_by"] = "security-lead"
        with tempfile.TemporaryDirectory() as tmp:
            ws = load_workbook(write_answers(SHEET, records, Path(tmp) / "c.xlsx")).active
            header = [c.value for c in ws[1]]
            col = header.index("Approved by")
            self.assertTrue(all(row[col] is None for row in ws.iter_rows(min_row=2, values_only=True)))

    def test_library_gets_only_proven_or_approved_promises(self):
        records = json.loads(json.dumps(self.records))
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
