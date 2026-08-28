#!/usr/bin/env python3
"""
memory-hub 导出工具 (export)

将 ~/llm-wiki 知识库中的页面按项目、类型、分层上下文 (L0/L1/L2) 结构化导出为 JSONL、JSON 或合并 Markdown 归档。
借鉴 OpenViking / claude-mem 分层架构设计。

用法:
  memory-hub.sh export [--project NAME] [--type TYPE] [--tier l0|l1|l2|full] [--format jsonl|json|markdown] [--output FILE]
"""

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional


def parse_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """解析 Markdown 文件头部的 YAML frontmatter。"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    fm_text = parts[1]
    body = parts[2].lstrip("\r\n")
    meta: Dict[str, Any] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"\'')
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
                meta[key] = items
            else:
                meta[key] = val
    return meta, body


def extract_tiered_content(frontmatter: Dict[str, Any], body: str, tier: str = "full") -> str:
    """按 L0 (Abstract) / L1 (Overview) / L2 (Full Details) 分级抽取正文。"""
    abstract = str(frontmatter.get("abstract", "")).strip()
    if tier == "l0":
        if abstract:
            return abstract
        clean_body = re.sub(r"^[#\>\-\*\s]+", "", body, flags=re.MULTILINE).strip()
        return clean_body[:160] + ("..." if len(clean_body) > 160 else "")

    if tier == "l1":
        l1_match = re.search(r"##\s*概述[^\n]*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
        if l1_match:
            l1_text = l1_match.group(1).strip()
            if abstract:
                return f"> **L0 Abstract**: {abstract}\n\n### L1 概述\n{l1_text}"
            return l1_text
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.startswith("#")]
        l1_fallback = "\n\n".join(paragraphs[:2]) if paragraphs else body[:400]
        if abstract:
            return f"> **L0 Abstract**: {abstract}\n\n### L1 概览\n{l1_fallback}"
        return l1_fallback

    if tier == "l2":
        l2_match = re.search(r"##\s*观察明细[^\n]*\n(.*)", body, re.DOTALL)
        if l2_match:
            return l2_match.group(1).strip()

    return body


def collect_pages(
    wiki_dir: pathlib.Path,
    project_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    tier: str = "full",
) -> List[Dict[str, Any]]:
    """遍历知识库并提取符合过滤条件的页面列表。"""
    pages: List[Dict[str, Any]] = []
    if not wiki_dir.exists() or not wiki_dir.is_dir():
        return pages

    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("raw", ".git", ".scripts")]
        for file in sorted(files):
            if not file.endswith(".md") or file in ("index.md", "log.md"):
                continue
            file_path = pathlib.Path(root) / file
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            rel_path = file_path.relative_to(wiki_dir).as_posix()
            frontmatter, body = parse_frontmatter(text)

            page_type = frontmatter.get("type", "")
            page_project = frontmatter.get("project", "")
            tags = frontmatter.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]

            if type_filter and page_type.lower() != type_filter.lower():
                continue
            if project_filter:
                pf = project_filter.lower()
                matches_project = (
                    page_project.lower() == pf
                    or any(pf in t.lower() for t in tags)
                    or pf in rel_path.lower()
                )
                if not matches_project:
                    continue

            slug = file_path.stem
            title = frontmatter.get("title", slug)
            extracted_body = extract_tiered_content(frontmatter, body, tier=tier)
            pages.append({
                "slug": slug,
                "title": title,
                "type": page_type,
                "project": page_project,
                "tier": tier,
                "path": rel_path,
                "frontmatter": frontmatter,
                "abstract": frontmatter.get("abstract", ""),
                "content": extracted_body,
                "body": body,
                "updated": frontmatter.get("updated", ""),
                "created": frontmatter.get("created", ""),
            })

    pages.sort(key=lambda p: (p["updated"] or p["created"] or "", p["path"]), reverse=True)
    return pages


def export_pages(
    pages: List[Dict[str, Any]],
    fmt: str = "jsonl",
) -> str:
    """将页面结构化转换为指定格式的文本。"""
    if fmt == "jsonl":
        lines = [json.dumps(p, ensure_ascii=False) for p in pages]
        return "\n".join(lines) + ("\n" if lines else "")
    elif fmt == "json":
        return json.dumps(pages, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "markdown":
        out_sections: List[str] = [
            f"# Memory Hub Knowledge Export ({len(pages)} pages)\n",
            "> 导出来源: memory-hub\n",
        ]
        for p in pages:
            title = p.get("title") or p.get("slug")
            meta_summary = []
            if p.get("type"):
                meta_summary.append(f"Type: {p['type']}")
            if p.get("project"):
                meta_summary.append(f"Project: {p['project']}")
            if p.get("updated"):
                meta_summary.append(f"Updated: {p['updated']}")
            meta_line = f" *({', '.join(meta_summary)})*" if meta_summary else ""
            
            out_sections.append(f"## {title}{meta_line}\n\n*Path: `{p['path']}`*\n")
            out_sections.append(p.get("content", p.get("body", "")).strip() + "\n\n---\n")
        return "\n".join(out_sections)
    else:
        raise ValueError(f"不支持的导出格式: {fmt}")


def main() -> int:
    default_wiki = os.environ.get("WIKI_PATH", os.path.expanduser("~/llm-wiki"))
    parser = argparse.ArgumentParser(description="memory-hub 知识库结构化导出工具")
    parser.add_argument("--wiki-dir", default=default_wiki, help=f"知识库根目录 (默认: {default_wiki})")
    parser.add_argument("--project", default=None, help="按项目名称过滤")
    parser.add_argument("--type", default=None, help="按页面类型过滤 (如 concept, decision, failure 等)")
    parser.add_argument("--tier", choices=["l0", "l1", "l2", "full"], default="full", help="分级上下文抽取深度 (L0摘要 / L1概述 / L2明细 / full全文)")
    parser.add_argument("--format", choices=["jsonl", "json", "markdown"], default="jsonl", help="导出格式 (默认: jsonl)")
    parser.add_argument("--output", "-o", default=None, help="输出文件路径 (缺省打印至 stdout)")
    parser.add_argument("--limit", type=int, default=None, help="最大导出条目数")

    args = parser.parse_args()
    wiki_path = pathlib.Path(args.wiki_dir).expanduser().resolve()

    pages = collect_pages(wiki_path, project_filter=args.project, type_filter=args.type, tier=args.tier)
    if args.limit and args.limit > 0:
        pages = pages[:args.limit]

    output_text = export_pages(pages, fmt=args.format)

    if args.output:
        out_file = pathlib.Path(args.output).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(output_text, encoding="utf-8")
        print(f"成功导出 {len(pages)} 个页面至: {out_file}", file=sys.stderr)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
