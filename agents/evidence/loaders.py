"""Parse a product repo into structured facts: Terraform resources, YAML and JSON documents.

The verifier asks questions of parsed data, never of raw text, so a commented-out setting, a
look-alike key or a value on the wrong resource cannot count as proof. Anything that cannot be
resolved to a literal (a variable, an expression, a module, `count`) is reported as unresolved so the
caller can fail closed.

Every value carries the line it was written on, so a citation is `file:line` of the real attribute.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2
import yaml
from hcl2.utils import SerializationOptions

_HCL_OPTIONS = SerializationOptions(strip_string_quotes=True)
_EXPR = re.compile(r"\$\{")


class Unresolved:
    """A value that is an expression or reference, not a literal (var.x, a function call, a splat)."""

    def __init__(self, expr: str):
        self.expr = expr

    def __repr__(self) -> str:
        return f"Unresolved({self.expr!r})"


def _literal(value: Any) -> Any:
    """Turn an hcl2 value into a literal, or Unresolved if any part is an expression."""
    if isinstance(value, str):
        return Unresolved(value) if _EXPR.search(value) else value
    if isinstance(value, list):
        return [_literal(v) for v in value]
    if isinstance(value, dict):
        return {k: _literal(v) for k, v in value.items() if not str(k).startswith("__")}
    return value


def refs(value: Any) -> set[str]:
    """Resource references like `aws_kms_key.data` inside an unresolved value or list of them."""
    if isinstance(value, Unresolved):
        return set(re.findall(r"\b([a-z][a-z0-9_]*\.[A-Za-z0-9_-]+)(?=[.\[]|$|\W)", value.expr))
    if isinstance(value, list):
        return set().union(*[refs(v) for v in value]) if value else set()
    return set()


@dataclass
class Block:
    """A resource body or nested block: attributes with lines, and nested blocks in file order."""
    attrs: dict[str, Any] = field(default_factory=dict)
    lines: dict[str, int] = field(default_factory=dict)
    blocks: dict[str, list["Block"]] = field(default_factory=dict)
    line: int = 0


@dataclass
class Resource:
    rtype: str
    name: str
    file: Path
    body: Block
    opaque: bool  # count, for_each or dynamic blocks: the real set of instances is not knowable
    line: int = 0  # the `resource "type" "name" {` line

    @property
    def address(self) -> str:
        return f"{self.rtype}.{self.name}"


@dataclass
class Terraform:
    resources: list[Resource] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)  # module blocks: their resources are not analysed
    errors: list[str] = field(default_factory=list)  # files that failed to parse

    def of_type(self, *rtypes: str) -> list[Resource]:
        return [r for r in self.resources if r.rtype in rtypes]

    def find(self, address: str) -> Resource | None:
        return next((r for r in self.resources if r.address == address), None)

    @property
    def complete(self) -> bool:
        """False when part of the configuration could not be read, so absence proves nothing."""
        return not self.modules and not self.errors


def _identifier(node) -> str:
    return str(node.children[0].children[0]) if hasattr(node.children[0], "children") else str(node.children[0])


def _build(body_node, values: dict) -> Block:
    """Pair the parse tree (for lines) with the evaluated dict (for values), by name and order."""
    block = Block(line=body_node.meta.line if not body_node.meta.empty else 0)
    seen: dict[str, int] = {}
    for child in body_node.children:
        data = getattr(child, "data", None)
        if data == "attribute":
            key = _identifier(child)
            block.attrs[key] = _literal(values.get(key))
            block.lines[key] = child.meta.line
        elif data == "block":
            key = _identifier(child)
            index = seen.get(key, 0)
            seen[key] = index + 1
            nested = values.get(key)
            if isinstance(nested, list) and index < len(nested) and isinstance(nested[index], dict):
                inner = next(c for c in child.children if getattr(c, "data", None) == "body")
                block.blocks.setdefault(key, []).append(_build(inner, nested[index]))
    return block


@functools.lru_cache(maxsize=256)
def _parse_hcl(text: str):
    """Keyed by the file's text, so an edited file is always re-parsed and an unchanged one is not."""
    return hcl2.parses_to_tree(text), hcl2.loads(text, serialization_options=_HCL_OPTIONS)


def load_terraform(directory: Path) -> Terraform:
    tf = Terraform()
    for path in sorted(directory.glob("*.tf")):
        if not path.is_file():
            continue
        try:
            tree, values = _parse_hcl(path.read_text(encoding="utf-8"))
        except Exception as exc:  # a parser can raise many error types; every one means "cannot read"
            tf.errors.append(f"{path.name}: {type(exc).__name__}")
            continue
        top = [n for n in tree.children[0].children if getattr(n, "data", None) == "block"]
        evaluated = [(kind, labels) for entry in values.get("resource", []) for kind, labels in entry.items()]
        resource_nodes = [n for n in top if _identifier(n) == "resource"]
        for node, (rtype, labels) in zip(resource_nodes, evaluated):
            for name, body in labels.items():
                inner = next(c for c in node.children if getattr(c, "data", None) == "body")
                block = _build(inner, body)
                opaque = any(k in block.attrs for k in ("count", "for_each")) or "dynamic" in body
                tf.resources.append(Resource(rtype, name, path, block, opaque, node.meta.line))
        tf.modules += [name for entry in values.get("module", []) for name in entry]
    return tf


@dataclass
class Document:
    """A YAML or JSON file: the data, plus the line of any path into it."""
    path: Path
    data: Any
    root: Any = None  # compose() node tree, used only for lines
    error: str | None = None

    def line(self, *path: str | int) -> int | None:
        node = self.root
        for step in path:
            node = _child(node, step)
            if node is None:
                return None
        return node[1]

    def get(self, *path: str | int) -> Any:
        value = self.data
        for step in path:
            try:
                value = value[step]
            except (KeyError, IndexError, TypeError):
                return None
        return value


def _child(node, step):
    """(value node, line) for a mapping key or list index; None if absent or ambiguous."""
    if node is None:
        return None
    if isinstance(node, tuple):
        node = node[0]
    if isinstance(node, yaml.MappingNode):
        matches = [(v, k.start_mark.line + 1) for k, v in node.value if getattr(k, "value", None) == step]
        return matches[0] if len(matches) == 1 else None  # a duplicated key is ambiguous
    if isinstance(node, yaml.SequenceNode) and isinstance(step, int) and 0 <= step < len(node.value):
        return node.value[step], node.value[step].start_mark.line + 1
    return None


def _duplicate_keys(node, seen=None) -> str | None:
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return None
    seen.add(id(node))
    if isinstance(node, yaml.MappingNode):
        keys = [k.value for k, _ in node.value if hasattr(k, "value")]
        for key in keys:
            if keys.count(key) > 1:
                return key
        for _, value in node.value:
            if (dup := _duplicate_keys(value, seen)):
                return dup
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            if (dup := _duplicate_keys(item, seen)):
                return dup
    return None


def load_document(path: Path) -> Document:
    """Parse YAML or JSON. A duplicate key or a parse failure is an error, never a guess."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            json.loads(text, object_pairs_hook=_reject_duplicates)
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is None:
            return Document(path, None, None, "empty document")
        if (dup := _duplicate_keys(root)):
            return Document(path, None, root, f"duplicate key {dup!r}")
        return Document(path, yaml.safe_load(text), (root, root.start_mark.line + 1), None)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return Document(path, None, None, f"{type(exc).__name__}: {exc}"[:120])


def _reject_duplicates(pairs):
    keys = [k for k, _ in pairs]
    for key in keys:
        if keys.count(key) > 1:
            raise ValueError(f"duplicate key {key!r}")
    return dict(pairs)


def strip_markdown_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
