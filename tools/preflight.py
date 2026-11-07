"""Check the GitHub credential TrustDesk is about to use is narrow enough.

    python tools/preflight.py [--role remediator|verifier]

Never prints the token: it looks at the token's prefix and the scopes GitHub reports for it.
  * classic personal access tokens (`ghp_`) carry broad scopes (`repo` is read/write on every repo
    the person can reach): refused
  * fine-grained tokens (`github_pat_`) and GitHub App installation tokens (`ghs_`) are repository
    scoped: accepted
  * a token whose reported OAuth scopes include admin, delete, workflow or package-write: refused
Exit 0 = acceptable, 1 = too broad, 2 = could not check.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

DANGEROUS = re.compile(r"^(repo|delete_repo|workflow|admin:.*|write:packages|delete:packages|write:org|site_admin|codespace)$")
PREFIX = {"ghp_": "classic personal access token", "gho_": "OAuth app token", "ghu_": "GitHub App user token",
          "ghs_": "GitHub App installation token", "github_pat_": "fine-grained personal access token"}
ACCEPTED = {"ghs_", "github_pat_"}


def token_kind(token: str) -> tuple[str, str]:
    for prefix, name in PREFIX.items():
        if token.startswith(prefix):
            return prefix, name
    return "", "unrecognised token format"


def judge(token: str, scopes_header: str | None) -> list[str]:
    """Reasons this credential is too broad (empty = fine)."""
    prefix, name = token_kind(token)
    problems = []
    if prefix not in ACCEPTED:
        problems.append(f"{name}: use a fine-grained token or a GitHub App installation token limited to this repository")
    scopes = [s.strip() for s in (scopes_header or "").split(",") if s.strip()]
    problems += [f"token has broad scope {s!r}" for s in scopes if DANGEROUS.match(s)]
    return problems


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30, check=True).stdout


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    try:
        token = _gh("auth", "token").strip()
        headers = _gh("api", "-i", "user")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"preflight: cannot check the GitHub credential ({type(exc).__name__})", file=sys.stderr)
        return 2
    scopes = next((line.split(":", 1)[1] for line in headers.splitlines() if line.lower().startswith("x-oauth-scopes:")), None)
    problems = judge(token, scopes)
    for p in problems:
        print(f"TOO BROAD: {p}")
    print(f"credential: {token_kind(token)[1]}" + ("" if problems else " (acceptable)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
