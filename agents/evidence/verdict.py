"""What a control check concludes, and how per-resource outcomes combine into one verdict.

Each resource (or file) the check looks at yields one Item. The verdict is the strictest outcome:

  contradicted  any item is a literal value that breaks the requirement
  unverifiable  nothing contradicts, but a value is a variable, expression, `count`, module or
                unreadable file, so the code does not settle it
  missing       nothing contradicts, but a required setting or linked resource is not there
  found         every item passes, and there is at least one

So a gap in what the verifier can read never turns into a pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class State(Enum):
    PASS = "pass"
    FAIL = "fail"
    ABSENT = "absent"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Fact:
    """One value the verdict rests on, at the line it is written."""
    file: Path
    line: int
    subject: str  # e.g. aws_db_instance.main, or the config path
    attr: str  # e.g. storage_encrypted
    value: Any


@dataclass(frozen=True)
class Item:
    state: State
    subject: str
    fact: Fact | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Verdict:
    status: str
    facts: tuple[Fact, ...] = ()
    reason: str | None = None


def passed(fact: Fact) -> Item:
    return Item(State.PASS, fact.subject, fact)


def failed(fact: Fact, reason: str) -> Item:
    return Item(State.FAIL, fact.subject, fact, reason)


def absent(subject: str, reason: str) -> Item:
    return Item(State.ABSENT, subject, None, reason)


def unresolved(subject: str, reason: str, fact: Fact | None = None) -> Item:
    return Item(State.UNRESOLVED, subject, fact, reason)


def judge(items: list[Item], nothing_found: str, complete: bool = True) -> Verdict:
    """Combine items into a verdict. `complete` is False when part of the code could not be read."""
    def of(state):
        return [i for i in items if i.state is state]

    def facts(group):
        return tuple(i.fact for i in group if i.fact)

    def why(group):
        return "; ".join(i.reason for i in group if i.reason)

    if of(State.FAIL):
        return Verdict("contradicted", facts(of(State.FAIL)), why(of(State.FAIL)))
    if of(State.UNRESOLVED):
        return Verdict("unverifiable", facts(of(State.UNRESOLVED)), why(of(State.UNRESOLVED)))
    if not items:
        return Verdict("missing" if complete else "unverifiable", (), nothing_found)
    if of(State.ABSENT):
        return Verdict("missing", facts(of(State.PASS)), why(of(State.ABSENT)))
    if not complete:
        return Verdict("unverifiable", facts(of(State.PASS)),
                       "part of the configuration was not analysed (module or unreadable file), "
                       "so the checked resources may not be all of them")
    return Verdict("found", facts(of(State.PASS)))


@dataclass(frozen=True)
class Control:
    desc: str
    run: Callable[[Any], Verdict]  # takes a product.Product
