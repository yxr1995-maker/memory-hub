#!/usr/bin/env python3
"""脱敏 jsonl 中 .text 字段：JWT/Bearer/API key/token/password。"""
import re, sys, json

_PATTERNS = [
    (re.compile(r'Bearer\s+\S+'), '[REDACTED_BEARER]'),
    (re.compile(r'eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*'), '[REDACTED_JWT]'),
    (re.compile(r'sk-ant-[A-Za-z0-9_\\-]{16,}'), '[REDACTED_SK]'),
    (re.compile(r'sk-[A-Za-z0-9]{48}'), '[REDACTED_SK]'),
    (re.compile(r'(api_key\s*=\s*)\S+', re.I), r'\1[REDACTED]'),
    (re.compile(r'(password\s*=\s*)\S+', re.I), r'\1[REDACTED]'),
    (re.compile(r'(token\s*=\s*)\S+', re.I), r'\1[REDACTED]'),
]

def sanitize_text(text):
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text

for line in sys.stdin:
    line = line.rstrip('\n')
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        print(line)
        continue
    if isinstance(obj.get('text'), str):
        obj['text'] = sanitize_text(obj['text'])
    print(json.dumps(obj, ensure_ascii=False))
