"""Evidence engine tests: no model, no network.

    python -m unittest discover -s tests -v
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

KEY = json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8"))
CHECKED = [q for q in KEY if q["control"]]


def claims_for(questions):
    return {"type": "claims", "claims": [{"id": q["id"], "control": q["control"], "claim": q["question"]} for q in questions]}


class AnswerKey(unittest.TestCase):
    def setUp(self):
        self.results = {r["id"]: r for r in search.run(claims_for(CHECKED))["results"]}

    def test_every_expected_outcome(self):
        for q in CHECKED:
            with self.subTest(q["id"], control=q["control"]):
                self.assertEqual(self.results[q["id"]]["status"], q["expected"], self.results[q["id"]])

    def test_counts(self):
        statuses = [r["status"] for r in self.results.values()]
        self.assertEqual(statuses.count("found"), 30)
        self.assertEqual(statuses.count("missing"), 6)
        self.assertEqual(statuses.count("contradicted"), 2)

    def test_found_citations_point_at_real_lines(self):
        for r in self.results.values():
            for cite in r.get("evidence", []):
                with self.subTest(r["id"], cite=cite):
                    loc, text = cite.split("  ", 1)
                    path, line = loc.rsplit(":", 1)
                    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
                    self.assertEqual(lines[int(line) - 1].strip(), text)

    def test_contradictions_name_the_problem(self):
        self.assertIn("exports", self.results["Q3"]["reason"])
        self.assertIn("configs/auth.yaml", self.results["Q9"]["evidence"][0])


class Contract(unittest.TestCase):
    def test_every_key_control_is_known(self):
        known = search.controls()
        for q in CHECKED:
            self.assertIn(q["control"], known)

    def test_unknown_control_is_missing_not_an_error(self):
        out = search.run({"type": "claims", "claims": [{"id": "X", "control": "made_up", "claim": "?"}]})
        self.assertEqual(out["results"][0]["status"], "missing")

    def test_rejects_wrong_message_type(self):
        with self.assertRaises(ValueError):
            search.run({"type": "evidence"})

    def test_deterministic(self):
        self.assertEqual(search.run(claims_for(CHECKED)), search.run(claims_for(CHECKED)))

    def test_cli_round_trip(self):
        proc = subprocess.run([sys.executable, str(ROOT / "agents" / "evidence" / "search.py")],
                              input=json.dumps(claims_for(CHECKED[:2])), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proc.stdout)["type"], "evidence")


if __name__ == "__main__":
    unittest.main()
