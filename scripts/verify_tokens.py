#!/usr/bin/env python3
"""Fail when non-archived wiki/session/staging files contain unredacted credential-shaped text."""
import json
import pathlib
import re
import sys


BEARER = re.compile(r"Bearer\s+(\$\([^)]*\)|\S+)")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*")
SK = re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}")
DOC_BEARER_WORDS = {"token", "tokens", "令牌", "凭据"}
def is_safe_keychain(value: str) -> bool:
    """True if value is a query-only macOS keychain lookup: $(security find-generic-password ... -w)."""
    if not (value.startswith("$(") and value.endswith(")")):
        return False
    inner = value[2:-1].strip()
    if not inner.startswith("security find-generic-password"):
        return False
    tail = inner[len("security find-generic-password"):].strip()
    # Reject pipes, semicolons, &&, ||.  Allow only stderr redirection 2>/dev/null (or /tmp/foo) at the very end.
    if any(sep in tail for sep in ("|", ";", "&&", "||")):
        return False
    # Strip an optional trailing stderr redirect (e.g. 2>/dev/null).
    tail = re.sub(r"\s+2>\S+\s*$", "", tail)
    return tail.endswith("-w")


def _relative_parts(path: pathlib.Path) -> tuple:
    """Return path parts relative to root, equivalent to Path.relative_parts (3.12+)."""
    return path.parts[1:] if path.is_absolute() else path.parts


def has_bearer_credential(text: str) -> bool:
    for match in BEARER.finditer(text):
        value = match.group(1)
        if value == "[REDACTED_BEARER]":
            continue
        if is_safe_keychain(value) or re.fullmatch(
            r"\$[A-Z_][A-Z0-9_]*[;)}\\\\\"']*", value
        ):
            continue
        if not value.strip(".,;:!?*`"):
            continue
        if value.strip(".,;:!?*`" ).lower() in DOC_BEARER_WORDS:
            continue
        return True
    return False


def is_scanned(path: pathlib.Path) -> bool:
    parts = _relative_parts(path)
    return "raw" not in parts and not any(part.startswith("_" ) for part in parts)


def scan_text(text: str) -> bool:
    return has_bearer_credential(text) or bool(JWT.search(text)) or bool(SK.search(text))


def scan_file(path: pathlib.Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_text(text)


def main(wiki: pathlib.Path, *, strict: bool = False, fmt: str = "text") -> int:
    bad_paths = []
    for pattern in ("*.md", "*.jsonl"):
        for path in wiki.rglob(pattern):
            relative = path.relative_to(wiki)
            if not is_scanned(relative):
                continue
            if scan_file(path):
                bad_paths.append(str(relative))
    hits = len(bad_paths)
    if fmt == "json":
        print(json.dumps({"hits": hits, "strict": strict}, ensure_ascii=False, sort_keys=True))
    else:
        for relative in bad_paths:
            print("BAD", relative)
        print(f"token_hits={hits}")
    return 1 if hits else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    strict = "--strict" in args
    fmt = "text"
    if "--json" in args:
        fmt = "json"
        args = [a for a in args if a != "--json"]
    if "--format" in args:
        idx = args.index("--format")
        if idx + 1 >= len(args):
            print("error: --format requires text or json", file=sys.stderr)
            sys.exit(2)
        fmt = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    if fmt not in ("text", "json"):
        print(f"error: --format must be text or json (got {fmt!r})", file=sys.stderr)
        sys.exit(2)
    args = [a for a in args if a not in ("--strict",)]
    if len(args) != 1:
        raise SystemExit("usage: verify_tokens.py [--strict] [--format text|json] [--json] WIKI_DIR")
    raise SystemExit(main(pathlib.Path(args[0]), strict=strict, fmt=fmt))
