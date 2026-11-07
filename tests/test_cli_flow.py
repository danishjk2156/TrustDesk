"""The approver's real flow through the command line: keygen, roster, sign, gate, revoke, scrub.

    python -m unittest discover -s tests -p "test_cli_flow.py" -v
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support
from support import ROOT
from xlsx_io import read_questions

TOOLS = ROOT / "tools"


def run(script, *args, env=None):
    return subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)], capture_output=True, text=True, env=env)


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _ = support.draft(read_questions(support.SHEET))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.deal = self.tmp / "acme"
        self.deal.mkdir()
        self.answers = self.deal / "answers.json"
        self.answers.write_text(json.dumps(self.records), encoding="utf-8")
        keys = {}
        for who in ("sam", "lee"):
            out = run("approvals.py", "keygen", "--out", self.tmp / f"{who}.key")
            self.assertEqual(out.returncode, 0, out.stderr)
            keys[who] = out.stdout.split("public_key:")[1].strip()
        self.roster = self.tmp / "approvers.yaml"
        self.roster.write_text(
            "approvers:\n"
            f"  - {{id: sam, github: sam-gh, roles: [security-lead], public_key: '{keys['sam']}'}}\n"
            f"  - {{id: lee, github: lee-gh, roles: [legal], public_key: '{keys['lee']}'}}\n", encoding="utf-8")
        self.pin = hashlib.sha256(self.roster.read_bytes()).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sign(self, who, role, *extra, cmd="sign"):
        return run("approvals.py", cmd, "--key", self.tmp / f"{who}.key", "--approver", who, "--role", role, "--deal", "acme",
                   "--answers", self.answers, "--yes", *extra)

    def gate(self, *extra):
        return run("check_gates.py", self.answers, support.SHEET, "--deal", "acme", "--roster", self.roster,
                   "--roster-sha256", self.pin, *extra)

    def test_full_flow(self):
        self.assertEqual(self.gate().returncode, 1)  # nothing signed yet
        self.assertEqual(self.sign("sam", "security-lead").returncode, 0)
        self.assertEqual(self.gate().returncode, 1)  # commitments still need legal
        self.assertEqual(self.sign("lee", "legal").returncode, 0)
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(json.loads(done.stdout)["approvals"]["Q39"]["approver"], "lee")
        # a security lead's key cannot sign a commitment: the CLI skips it
        self.assertIn("skip Q39", self.sign("sam", "security-lead").stdout)
        # revoke one answer: the gate blocks again
        self.assertEqual(self.sign("sam", "security-lead", "--ids", "Q1", cmd="revoke").returncode, 0)
        blocked = self.gate()
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("Q1", blocked.stdout)

    def test_wrong_pin_blocks_even_with_valid_signatures(self):
        self.sign("sam", "security-lead")
        self.sign("lee", "legal")
        out = run("check_gates.py", self.answers, support.SHEET, "--deal", "acme", "--roster", self.roster, "--roster-sha256", "0" * 64)
        self.assertEqual(out.returncode, 1)
        self.assertIn("pinned hash", out.stdout)

    def test_edit_after_signing_requires_reapproval(self):
        self.sign("sam", "security-lead")
        self.sign("lee", "legal")
        edited = json.loads(self.answers.read_text(encoding="utf-8"))
        edited[0]["answer"] += " Also, we guarantee it."
        self.answers.write_text(json.dumps(edited), encoding="utf-8")
        out = self.gate()
        self.assertEqual(out.returncode, 1)
        self.assertIn("different version", out.stdout)

    def test_bad_deal_id_is_refused_by_the_signer(self):
        out = run("approvals.py", "sign", "--key", self.tmp / "sam.key", "--approver", "sam", "--role", "security-lead",
                  "--deal", "../x", "--answers", self.answers, "--yes")
        self.assertEqual(out.returncode, 2)

    def test_scrub_cli(self):
        entries = self.tmp / "entries.json"
        entries.write_text(json.dumps([{"question": "Is it encrypted?", "answer": "Yes.", "label": "VERIFIED", "source_deal": "acme"}]))
        ok = run("scrub.py", entries, "--customer", "Globex")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("deal-", ok.stdout)
        entries.write_text(json.dumps([{"question": "Does Globex need it?", "answer": "Yes.", "label": "VERIFIED"}]))
        self.assertEqual(run("scrub.py", entries, "--customer", "Globex").returncode, 1)


if __name__ == "__main__":
    unittest.main()
