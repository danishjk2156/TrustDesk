"""MCP server, framework mapping and trust score tests.

    python -m unittest discover -s tests -p "test_mcp_extras.py" -v
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import frameworks  # noqa: E402
import search  # noqa: E402
import trust_score  # noqa: E402

RUN = json.loads((ROOT / "docs" / "example-run" / "answers.json").read_text(encoding="utf-8"))


class Frameworks(unittest.TestCase):
    def test_every_control_is_mapped_to_both_frameworks(self):
        for control in search.controls():
            with self.subTest(control):
                f = frameworks.frameworks(control)
                self.assertTrue(f["soc2"] and f["iso27001"])

    def test_label(self):
        self.assertEqual(frameworks.label("db_encryption_at_rest"), "SOC 2 CC6.1 · ISO 27001 A.8.24")
        self.assertEqual(frameworks.label("not_a_control"), "")


class TrustScore(unittest.TestCase):
    def test_published_run(self):
        recorded = {a["id"]: {"role": a["approved_by"]} for a in RUN}  # the recording's own claims, stood in for a ledger
        s = trust_score.score(RUN, recorded)
        self.assertEqual((s["verified"], s["factual"], s["verified_pct"]), (30, 39, 77))
        # The Nov 5 run was recorded with the text-search engine. The structured verifier cites every
        # resource a claim rests on, so 8 recorded citations are now narrower than what it would cite.
        self.assertEqual((s["contradicted"], s["stale"], s["open_conflicts"]), (2, 8, 0))
        self.assertEqual((s["approved"], s["total"]), (41, 41))
        self.assertIn("77% proven from code", trust_score.line(s))

    def test_self_asserted_approvals_do_not_count(self):
        self.assertEqual(trust_score.score(RUN)["approved"], 0)


try:
    import mcp_server  # noqa: E402
except ImportError:  # the mcp package is optional for the core pipeline
    mcp_server = None


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class McpServer(unittest.TestCase):
    def test_tools_are_registered(self):
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertEqual(names, {"list_controls", "search_evidence", "check_claims", "lookup_library", "map_framework",
                                 "check_release_gate", "drift_check", "red_team_review", "deal_trust_score"})

    def test_search_evidence_matches_the_engine(self):
        out = mcp_server.search_evidence("storage_encryption_all_buckets")
        self.assertEqual(out["status"], "contradicted")
        self.assertEqual(out["frameworks"]["soc2"], ["CC6.1"])

    def test_lookup_library_finds_the_approved_answer(self):
        best = mcp_server.lookup_library("Is customer data encrypted at rest in the database?")[0]
        self.assertEqual(best["control"], "db_encryption_at_rest")

    def test_release_gate_and_scores_over_mcp(self):
        # the recorded example run has no signed ledger, so the gate refuses it and nothing counts as approved
        gate = mcp_server.check_release_gate("docs/example-run/answers.json", deal_id="example-run")
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["approvals"], {})
        self.assertEqual(mcp_server.drift_check()["counts"]["STALE"], 0)
        score = mcp_server.deal_trust_score("docs/example-run/answers.json", deal_id="example-run")
        self.assertEqual((score["verified_pct"], score["approved"]), (77, 0))
        self.assertIn("conflicts", mcp_server.red_team_review("docs/example-run/answers.json", deal_id="example-run"))

    def test_paths_outside_the_repo_are_refused(self):
        with self.assertRaises(ValueError):
            mcp_server.check_release_gate("../../Windows/win.ini")


if __name__ == "__main__":
    unittest.main()
