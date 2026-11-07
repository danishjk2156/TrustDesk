"""Release blockers: one customer's data must not reach another customer's deal or the shared library.

    python -m unittest discover -s tests -p "test_adv_isolation.py" -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import deals  # noqa: E402
import scrub  # noqa: E402

try:
    import mcp_server  # noqa: E402
except ImportError:
    mcp_server = None


class Deals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = os.environ.get(deals.WORK_ENV)
        os.environ[deals.WORK_ENV] = str(self.tmp)

    def tearDown(self):
        os.environ.pop(deals.WORK_ENV, None) if self._old is None else os.environ.__setitem__(deals.WORK_ENV, self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_each_deal_gets_its_own_folder(self):
        a, b = deals.deal_dir("acme", create=True), deals.deal_dir("globex", create=True)
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent, b.parent)

    def test_hostile_deal_ids_are_refused(self):
        for bad in ("../x", "a/b", "A", "", ".", "..", "a b", "x" * 65, "acme\n", "/etc", None, 5):
            with self.subTest(bad), self.assertRaises(ValueError):
                deals.deal_dir(bad)

    def test_symlink_out_of_the_work_root_is_refused(self):
        outside = Path(tempfile.mkdtemp())
        try:
            (self.tmp / "evil").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                deals.deal_dir("evil")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_a_deal_cannot_reach_a_sibling_deal(self):
        (deals.deal_dir("globex", create=True) / "answers.json").write_text("[]")
        deals.deal_dir("acme", create=True)
        for rel in ("../globex/answers.json", str(self.tmp / "globex" / "answers.json"), "..", "."):
            with self.subTest(rel), self.assertRaises(ValueError):
                deals.resolve_in("acme", rel)

    def test_symlink_inside_a_deal_pointing_at_another_is_refused(self):
        other = deals.deal_dir("globex", create=True)
        (other / "answers.json").write_text("[]")
        (deals.deal_dir("acme", create=True) / "link.json").symlink_to(other / "answers.json")
        with self.assertRaises(ValueError):
            deals.resolve_in("acme", "link.json")

    def test_deal_of(self):
        self.assertEqual(deals.deal_of(self.tmp / "acme" / "answers.json"), "acme")
        self.assertIsNone(deals.deal_of(ROOT / "README.md"))

    @unittest.skipIf(mcp_server is None, "mcp package not installed")
    def test_mcp_refuses_another_deals_answers(self):
        (deals.deal_dir("globex", create=True) / "answers.json").write_text("[]")
        deals.deal_dir("acme", create=True)
        with self.assertRaises(ValueError):
            mcp_server._deal_answers(str(self.tmp / "globex" / "answers.json"), "acme")


class Scrub(unittest.TestCase):
    ENTRY = {"question": "Is data encrypted?", "answer": "Yes.", "label": "VERIFIED", "control": "db_encryption_at_rest",
             "evidence": ["acmecloud/terraform/rds.tf:18  storage_encrypted = true"], "approved_by": "security-lead",
             "approved_at": "2026-11-06T10:00:00+00:00", "source_deal": "globex-renewal"}

    def test_clean_entry_passes_with_the_deal_anonymised(self):
        clean, problems = scrub.scrub(self.ENTRY, ["Globex"])
        self.assertEqual(problems, [])
        self.assertNotIn("globex", json.dumps(clean).lower())
        self.assertTrue(clean["source_deal"].startswith("deal-"))

    def test_customer_name_split_by_invisible_characters_or_fullwidth_is_still_caught(self):
        for sneaky in ("Glo\u200bbex", "Glo\u00adbex", "\uff27lobex", "G\u2060lobex"):
            with self.subTest(sneaky):
                self.assertIsNone(scrub.scrub({**self.ENTRY, "answer": f"Agreed with {sneaky}."}, ["Globex"])[0])

    def test_control_characters_are_refused_and_cannot_hide_a_name(self):
        for sneaky in ("Glo\x00bex", "Glo\x07bex", "Glo\u200bbex"):
            with self.subTest(repr(sneaky)):
                clean, problems = scrub.scrub({**self.ENTRY, "evidence": [f"{sneaky}/main.tf:1  x"]}, ["Globex"])
                self.assertIsNone(clean)
                self.assertTrue(any("invisible" in p for p in problems))
                self.assertTrue(any("names the customer" in p for p in problems), problems)

    def test_every_free_text_field_is_checked_not_only_three(self):
        for field in ("approved_by", "control", "approved_at"):
            with self.subTest(field):
                self.assertIsNone(scrub.scrub({**self.ENTRY, field: "globex@globex.com"}, ["Globex"])[0])

    def test_source_deal_label_is_not_a_plain_hash_of_the_deal(self):
        import hashlib
        plain = "deal-" + hashlib.sha256(b"globex-renewal").hexdigest()[:8]
        os.environ.pop(scrub.KEY_ENV, None)
        a, b = (scrub.scrub(self.ENTRY, [])[0]["source_deal"] for _ in range(2))
        self.assertNotEqual(a, b)  # unkeyed: random, unlinkable
        self.assertNotIn(plain, (a, b))
        os.environ[scrub.KEY_ENV] = "test-secret"
        try:
            k1, k2 = (scrub.scrub(self.ENTRY, [])[0]["source_deal"] for _ in range(2))
        finally:
            os.environ.pop(scrub.KEY_ENV)
        self.assertEqual(k1, k2)  # keyed: stable
        self.assertNotIn(k1[:12], plain)

    def test_extra_fields_are_dropped(self):
        clean, _ = scrub.scrub({**self.ENTRY, "customer_notes": "Globex wants SOC2", "permalinks": ["x"], "reason": "r"}, [])
        self.assertEqual(set(clean) - set(scrub.ALLOWED), set())

    def test_customer_name_in_any_text_refuses_the_entry(self):
        for field, value in (("question", "Does Globex Corp require MFA?"), ("answer", "Yes, as agreed with globex."),
                             ("evidence", ["GLOBEX/main.tf:1  x"])):
            with self.subTest(field):
                clean, problems = scrub.scrub({**self.ENTRY, field: value}, ["Globex"])
                self.assertIsNone(clean)
                self.assertTrue(problems)

    def test_email_ip_and_foreign_url_are_refused(self):
        for text in ("Contact cto@globex.com", "Server 10.1.2.3 is hardened", "See https://globex.sharepoint.com/doc"):
            with self.subTest(text):
                self.assertIsNone(scrub.scrub({**self.ENTRY, "answer": text}, [])[0])

    def test_product_repo_permalink_is_allowed(self):
        entry = {**self.ENTRY, "answer": "Yes. https://github.com/o/r/blob/abc/x.tf#L1"}
        self.assertIsNotNone(scrub.scrub(entry, [])[0])

    def test_needs_evidence_never_enters_the_library(self):
        self.assertIsNone(scrub.scrub({**self.ENTRY, "label": "NEEDS-EVIDENCE"}, [])[0])

    def test_existing_library_has_no_pii(self):
        import yaml
        library = yaml.safe_load((ROOT / "room-docs" / "answer-library.yaml").read_text(encoding="utf-8"))["answers"]
        for entry in library:
            with self.subTest(entry["question"][:40]):
                self.assertEqual(scrub.scrub(entry, [])[1], [])


if __name__ == "__main__":
    unittest.main()
