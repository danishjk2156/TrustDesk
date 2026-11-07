import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import preflight  # noqa: E402


class Preflight(unittest.TestCase):
    def test_fine_grained_and_app_tokens_pass(self):
        self.assertEqual(preflight.judge("github_pat_abc", None), [])
        self.assertEqual(preflight.judge("ghs_abc", None), [])

    def test_classic_tokens_are_refused(self):
        self.assertTrue(preflight.judge("ghp_abc", "repo, read:org"))

    def test_broad_scopes_are_named(self):
        out = preflight.judge("github_pat_abc", "repo, workflow, admin:org, read:user")
        self.assertEqual(len(out), 3)

    def test_unknown_format_is_refused(self):
        self.assertTrue(preflight.judge("abc123", None))

    def test_token_is_never_in_the_messages(self):
        self.assertFalse(any("SECRET" in p for p in preflight.judge("ghp_SECRET", "repo")))


if __name__ == "__main__":
    unittest.main()
