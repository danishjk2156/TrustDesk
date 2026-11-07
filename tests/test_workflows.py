"""CI hardening: actions pinned by commit SHA, read-only tokens, no privileged triggers.

    python -m unittest discover -s tests -p "test_workflows.py" -v
"""
import re
import unittest
from pathlib import Path

import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def load(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))  # YAML reads a bare `on:` as True
    return doc


class Workflows(unittest.TestCase):
    def test_there_are_workflows(self):
        self.assertTrue(WORKFLOWS)

    def test_every_action_is_pinned_to_a_commit_sha(self):
        for path in WORKFLOWS:
            for job in load(path)["jobs"].values():
                for step in job["steps"]:
                    if "uses" in step:
                        with self.subTest(path.name, uses=step["uses"]):
                            self.assertRegex(step["uses"], SHA_PIN)

    def test_tokens_are_read_only_and_checkout_keeps_no_credentials(self):
        for path in WORKFLOWS:
            doc = load(path)
            self.assertFalse(doc.get("permissions"), f"{path.name}: set permissions per job")
            for name, job in doc["jobs"].items():
                with self.subTest(path.name, job=name):
                    self.assertEqual(job["permissions"], {"contents": "read"})
                    self.assertIn("timeout-minutes", job)
                    for step in job["steps"]:
                        if step.get("uses", "").startswith("actions/checkout@"):
                            self.assertIs(step["with"]["persist-credentials"], False)

    def test_no_privileged_triggers_or_untrusted_expressions(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("workflow_run", text)
                for expr in re.findall(r"\$\{\{([^}]*)\}\}", text):
                    self.assertNotRegex(expr, r"github\.event\.(pull_request|issue|comment|head_commit)|github\.head_ref")


if __name__ == "__main__":
    unittest.main()
