"""Strict, body-preserving frontmatter parsing and patching."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .schema import PageDocument

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$")
_ITEM = re.compile(r"^([ \t]*)-(?:[ \t]+(.*))?$")
_BLOCK_SCALAR = frozenset({"|", ">", "|-", "|+", ">-", ">+"})


def _block_indicator(value: str) -> str | None:
    """Return the block scalar indicator, tolerating a YAML trailing comment."""
    token = value.strip().split("#", 1)[0].strip()
    return token if token in _BLOCK_SCALAR else None


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


def _read_block(
    lines: tuple[str, ...], cursor: int, indicator: str, base_indent: int
) -> tuple[object, int]:
    """Consume an indented YAML block scalar; stop at the first shallower line."""
    literal = indicator.startswith("|")
    block: list[str] = []
    while cursor < len(lines):
        candidate = lines[cursor]
        if not candidate.strip():
            block.append("")
            cursor += 1
            continue
        indent = len(candidate) - len(candidate.lstrip())
        if indent <= base_indent:
            break
        block.append(candidate.strip())
        cursor += 1
    if not indicator.endswith("+"):
        while block and not block[-1]:
            block.pop()
    folded = "\n".join(block) if literal else " ".join(part for part in block if part)
    return folded, cursor


def _read_items(lines: tuple[str, ...], cursor: int) -> tuple[list[object], int]:
    """Consume sequence items, including item values written as block scalars."""
    values: list[object] = []
    while cursor < len(lines):
        item = _ITEM.fullmatch(lines[cursor])
        if not item:
            break
        item_indent = len(item.group(1))
        item_text = item.group(2) or ""
        indicator = _block_indicator(item_text)
        if indicator:
            value, cursor = _read_block(lines, cursor + 1, indicator, item_indent)
            values.append(value)
        else:
            values.append(_decode_scalar(item_text))
            cursor += 1
    return values, cursor


def _read_mapping(lines: tuple[str, ...], cursor: int, base_indent: int) -> tuple[dict, int]:
    """Consume a nested mapping block (sub-keys indented under an empty key)."""
    mapping: dict[str, object] = {}
    while cursor < len(lines):
        line = lines[cursor]
        if not line.strip():
            cursor += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        match = _KEY.fullmatch(line.strip())
        if not match:
            break
        key = match.group(1)
        raw = match.group(2) or ""
        if key in mapping:
            raise ValueError(f"duplicate frontmatter key: {key}")
        cursor += 1
        indicator = _block_indicator(raw)
        if indicator:
            mapping[key], cursor = _read_block(lines, cursor, indicator, indent)
            continue
        values, next_cursor = _read_items(lines, cursor)
        if values:
            mapping[key] = values
            cursor = next_cursor
            continue
        nested, next_cursor = _read_mapping(lines, cursor, indent)
        if nested:
            mapping[key] = nested
            cursor = next_cursor
            continue
        mapping[key] = _decode_scalar(raw)
    return mapping, cursor


def parse_page(path: Path) -> PageDocument:
    content = path.read_bytes()
    if not content.startswith(b"---\n"):
        raise ValueError(f"missing opening frontmatter delimiter: {path}")
    if content.startswith(b"---\n", 4):
        header = b""
        body = content[8:]
    else:
        close = content.find(b"\n---\n", 4)
        if close < 0:
            raise ValueError(f"missing closing frontmatter delimiter: {path}")
        header = content[4:close]
        body = content[close + 5 :]
    lines = tuple(header.decode("utf-8", errors="strict").splitlines())

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
        key_indent = len(line) - len(line.lstrip())
        indicator = _block_indicator(raw)
        if indicator:
            frontmatter[key], cursor = _read_block(lines, cursor, indicator, key_indent)
        else:
            values, cursor = _read_items(lines, cursor)
            if values:
                if raw.strip():
                    raise ValueError(f"frontmatter key mixes scalar and list: {key}")
                frontmatter[key] = values
            else:
                nested, next_cursor = _read_mapping(lines, cursor, key_indent)
                if nested:
                    frontmatter[key] = nested
                    cursor = next_cursor
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


def _scan_indented(lines: tuple[str, ...], cursor: int, base_indent: int) -> int:
    """Cursor after the deeper-indented lines that belong to one key (no decoding)."""
    end = cursor
    probe = cursor
    while probe < len(lines):
        candidate = lines[probe]
        if not candidate.strip():
            probe += 1
            continue
        if len(candidate) - len(candidate.lstrip()) > base_indent:
            probe += 1
            end = probe
            continue
        break
    return end


def _scan_items(lines: tuple[str, ...], cursor: int) -> int:
    """Cursor after the sequence items that belong to one key (no decoding)."""
    while cursor < len(lines):
        item = _ITEM.fullmatch(lines[cursor])
        if not item:
            break
        item_indent = len(item.group(1))
        item_text = item.group(2) or ""
        cursor += 1
        if _block_indicator(item_text):
            cursor = _scan_indented(lines, cursor, item_indent)
    return cursor


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
        raw_value = match.group(2) or ""
        indent = len(line) - len(line.lstrip())
        cursor = index + 1
        if _block_indicator(raw_value):
            cursor = _scan_indented(lines, cursor, indent)
        else:
            items_end = _scan_items(lines, cursor)
            if items_end > index + 1:
                cursor = items_end
            elif not raw_value.strip():
                cursor = _scan_indented(lines, cursor, indent)
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
