"""TrustDesk MCP server: one deterministic evidence engine for every agent.

Switch governs *who* may act in the deal room; this server gives every agent the *same* evidence
engine, release gate, drift check and red team, so no agent judges evidence on its own.
All tools are read-only: nothing here edits answers, code or the library.

Run it over stdio (the default for Claude Code and most MCP clients):

    python tools/mcp_server.py

Register it for Claude Code from the repo root:

    claude mcp add trustdesk -- python tools/mcp_server.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import check_gates as _gates  # noqa: E402
import deals as _deals  # noqa: E402
import drift_watch as _drift  # noqa: E402
import frameworks as _frameworks  # noqa: E402
import red_team as _red_team  # noqa: E402
import search as _search  # noqa: E402
import trust_score as _trust  # noqa: E402
from xlsx_io import read_questions  # noqa: E402

LIBRARY = ROOT / "room-docs" / "answer-library.yaml"

mcp = FastMCP("trustdesk")


def _deal_answers(answers_path: str | None, deal_id: str | None) -> tuple[Path, str | None]:
    """The answers file for one deal. With a deal id the file must live in that deal's own folder, so a
    caller working on one customer cannot read another's."""
    if deal_id and not answers_path:
        return _deals.resolve_in(deal_id, "answers.json"), deal_id
    path = _in_repo(answers_path or "work/answers.json")
    owner = _deals.deal_of(path)
    if deal_id and owner != deal_id:
        raise ValueError(f"{answers_path!r} does not belong to deal {deal_id!r}")
    return path, deal_id or owner


def _evaluate(answers_path: str | None, questionnaire_path: str, deal_id: str | None) -> tuple[dict, list[dict]]:
    path, deal = _deal_answers(answers_path, deal_id)
    return _gates.evaluate(path, _in_repo(questionnaire_path), deal)


def _in_repo(path: str) -> Path:
    """Resolve a path the caller gives us, refusing anything outside the TrustDesk repo."""
    p = (ROOT / path).resolve()
    if ROOT.resolve() not in [p, *p.parents]:
        raise ValueError(f"{path!r} is outside the TrustDesk repo")
    return p


@mcp.tool()
def list_controls() -> dict:
    """Every control tag the evidence engine knows, with what it checks."""
    return _search.controls()


@mcp.tool()
def search_evidence(control: str) -> dict:
    """Prove one control against the product repo: found (with file:line citations and commit
    permalinks), missing, or contradicted (the code says the opposite)."""
    return {"control": control, **_search.check(control), "frameworks": _frameworks.frameworks(control)}


@mcp.tool()
def check_claims(claims: list[dict]) -> dict:
    """Run a batch of claims, each {"id", "control", "claim"}, exactly as the evidence-checker does."""
    return _search.run({"type": "claims", "claims": claims})


def _words(text: str) -> set[str]:
    stop = {"the", "a", "an", "is", "are", "do", "you", "your", "of", "to", "in", "for", "and", "or", "on", "all", "with", "by"}
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop and len(w) > 2}


@mcp.tool()
def lookup_library(question: str, limit: int = 3) -> list[dict]:
    """Approved answers from the library closest to a question, best first, with a 0-1 overlap
    score. A match is a starting point: the answer must still be re-proven and re-approved."""
    entries = (yaml.safe_load(LIBRARY.read_text(encoding="utf-8")) or {}).get("answers") or []
    want = _words(question)
    scored = []
    for e in entries:
        have = _words(e.get("question", ""))
        overlap = len(want & have) / len(want | have) if want | have else 0
        if overlap:
            scored.append({"score": round(overlap, 2), **{k: e.get(k) for k in ("question", "answer", "label", "control", "evidence")}})
    return sorted(scored, key=lambda s: -s["score"])[: max(1, min(limit, 10))]


@mcp.tool()
def map_framework(control: str) -> dict:
    """SOC 2 criteria and ISO/IEC 27001:2022 Annex A controls for a control tag (indicative)."""
    return {"control": control, **_frameworks.frameworks(control), "label": _frameworks.label(control)}


@mcp.tool()
def check_release_gate(answers_path: str | None = None, questionnaire_path: str = "samples/acme_questionnaire.xlsx",
                       deal_id: str | None = None) -> dict:
    """The packager's release gate: may this deal be packaged? Paths are relative to the repo. Approvals
    come only from the signed ledger (approvals.jsonl next to the answers), never from the answers."""
    report, _ = _evaluate(answers_path, questionnaire_path, deal_id)
    return report


@mcp.tool()
def drift_check() -> dict:
    """Re-prove every approved library answer against today's code: OK, MOVED or STALE."""
    entries = (yaml.safe_load(LIBRARY.read_text(encoding="utf-8")) or {}).get("answers") or []
    for i, e in enumerate(entries, start=1):
        e.setdefault("id", f"L{i}")
    return _drift.watch(entries)


@mcp.tool()
def red_team_review(answers_path: str | None = None, questionnaire_path: str = "samples/acme_questionnaire.xlsx",
                    deal_id: str | None = None) -> dict:
    """Answers whose figures disagree with the room's own documents, and whether the owner approved
    (judged from the signed ledger)."""
    report, answers = _evaluate(answers_path, questionnaire_path, deal_id)
    return _red_team.review(answers, {k: {v["role"]} for k, v in report["approvals"].items()})


@mcp.tool()
def deal_trust_score(answers_path: str | None = None, questionnaire_path: str = "samples/acme_questionnaire.xlsx",
                     deal_id: str | None = None) -> dict:
    """How much of a deal's questionnaire is proven from code, with stale, conflict and approved counts."""
    report, answers = _evaluate(answers_path, questionnaire_path, deal_id)
    s = _trust.score(answers, report["approvals"])
    return {**s, "summary": _trust.line(s)}


if __name__ == "__main__":
    mcp.run()
