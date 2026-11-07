"""A product repo as the verifier sees it: parsed on demand, never cached across calls.

Nothing is cached because remediation edits the repo and re-checks it straight away.
"""
from __future__ import annotations

from pathlib import Path

import loaders
from loaders import Document, Terraform


class Product:
    def __init__(self, root: Path):
        self.root = root

    @property
    def terraform(self) -> Terraform:
        return loaders.load_terraform(self.root / "terraform")

    def documents(self, *globs: str) -> list[Document]:
        """Every YAML/JSON file matching a glob, parsed; a file that fails to parse still appears."""
        seen: dict[Path, Document] = {}
        for pattern in globs:
            for path in sorted(self.root.glob(pattern)):
                if path.is_file() and path not in seen:
                    seen[path] = loaders.load_document(path)
        return list(seen.values())

    def text(self, relative: str) -> list[str] | None:
        path = self.root / relative
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else None
