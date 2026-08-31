"""Strict, body-preserving frontmatter parsing and patching."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .schema import PageDocument

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$")


def _decode_scalar(value: str) -> object:
    value = value.strip()
    if not value:
        return ""
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_page(path: Path) -> PageDocument:
    content = path.read_bytes()
    if not content.startswith(b"---\n"):
        raise ValueError(f"missing opening frontmatter delimiter: {path}")
    close = content.find(b"---\n", 4)
    if close < 0:
        raise ValueError(f"missing closing frontmatter delimiter: {path}")
    header = content[4:close]
    lines = tuple(header.decode("utf-8", errors="strict").splitlines())
    body = content[close + 4 :]

    frontmatter: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        match = _KEY.fullmatch(line)
        if not match:
            raise ValueError(f"invalid frontmatter line {index + 1}: {line!r}")
        key, raw = match.group(1), match.group(2) or ""
        if key in frontmatter:
            raise ValueError(f"duplicate frontmatter key: {key}")
        values: list[object] = []
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("  - "):
            values.append(_decode_scalar(lines[cursor][4:]))
            cursor += 1
        if values:
            if raw.strip():
                raise ValueError(f"frontmatter key mixes scalar and list: {key}")
            frontmatter[key] = values
        else:
            frontmatter[key] = _decode_scalar(raw)
        index = cursor

    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list):
        raise ValueError("tags must be a scalar or list")
    return PageDocument(
        path=path,
        title=str(frontmatter.get("title", path.stem)),
        tags=[str(tag) for tag in tags],
        frontmatter=frontmatter,
        body=body,
        frontmatter_lines=lines,
    )


def _quote_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    rendered = str(value)
    if re.fullmatch(r"[A-Za-z0-9._/]+", rendered) and rendered.lower() not in {
        "true", "false", "null", "yes", "no",
    }:
        return rendered
    return "'" + rendered.replace("'", "''") + "'"


def _render_block(key: str, value: object) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [f"{key}:", *[f"  - {_quote_scalar(item)}" for item in value]]
    return [f"{key}: {_quote_scalar(value)}"]


def patch_frontmatter(document: PageDocument, updates: Mapping[str, object]) -> bytes:
    remaining = dict(updates)
    rendered: list[str] = []
    lines = document.frontmatter_lines
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _KEY.fullmatch(line) if line.strip() else None
        if not match:
            rendered.append(line)
            index += 1
            continue
        key = match.group(1)
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("  - "):
            cursor += 1
        if key in remaining:
            rendered.extend(_render_block(key, remaining.pop(key)))
        else:
            rendered.extend(lines[index:cursor])
        index = cursor
    for key, value in remaining.items():
        if not _KEY.fullmatch(f"{key}:"):
            raise ValueError(f"invalid frontmatter key: {key}")
        rendered.extend(_render_block(key, value))
    header = "\n".join(rendered)
    return b"---\n" + header.encode("utf-8") + b"\n---\n" + document.body

__all__ = ["parse_page", "patch_frontmatter"]
