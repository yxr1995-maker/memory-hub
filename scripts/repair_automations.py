#!/usr/bin/env python3
"""Repair corrupted automation.toml files and sync DB layer."""
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".codex" / "sqlite" / "codex-dev.db"
AUTO_DIR = Path.home() / ".codex" / "automations"

AUTOMATIONS = ["wiki-distill", "wiki-review", "llm-wiki-curation", "gbrain"]


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def backup_db():
    ts = int(datetime.now(timezone.utc).timestamp())
    backup_path = Path(f"/tmp/codex-dev-before-memoryhub-{ts}.db")
    con = sqlite3.connect(DB_PATH)
    con.execute(f"VACUUM INTO '{backup_path}'")
    con.close()
    print(f"backup: {backup_path}")


def extract_prompt(text: str) -> str:
    """Extract the prompt value from potentially corrupted .toml files."""
    # Find the prompt = " start
    m = re.search(r'^\s*prompt\s*=\s*"', text, re.MULTILINE)
    if not m:
        raise ValueError("No prompt key found")
    start = m.end()

    # Look for the corruption marker: escaped quote, newline, status = \"ACTIVE\"
    # This is where the first (corrupted) section ends.
    corr_m = re.search(r'\\"\n\s*status\s*=\s*\\\\"ACTIVE\\\\"', text[start:])
    if corr_m:
        # The prompt ends just before the corruption marker's escaped quote
        end = start + corr_m.start()
        return text[start:end]

    # No corruption: find the regular closing quote
    # Find next unescaped quote after start
    i = start
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            i += 2
            continue
        if text[i] == '"':
            return text[start:i]
        i += 1
    raise ValueError("Could not find end of prompt string")


def toml_escape_value(s: str) -> str:
    """Escape a string for TOML basic string on a single line."""
    s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return s


def repair_toml(auto_id: str) -> dict:
    path = AUTO_DIR / auto_id / "automation.toml"
    text = path.read_text(encoding="utf-8")

    prompt = extract_prompt(text)
    # In corrupted files, the prompt contains literal \n (backslash-n) sequences.
    # Keep them as-is in the repaired file; TOML basic strings will parse \n as newline.
    # Also un-escape TOML-escaped quotes that were written as \\" inside the prompt text.
    # Actually, if the file contained \\"memoryhub\\" that is TOML for "memoryhub".
    # We keep the raw string value that tomllib would have produced.

    # Extract the last (clean) occurrence of each metadata field
    fields = {}
    for key in ["name", "status", "rrule", "model", "reasoning_effort", "notification_policy", "execution_environment"]:
        matches = list(re.finditer(rf'^\s*{key}\s*=\s*"([^"\\n]+)"\s*$', text, re.MULTILINE))
        if matches:
            fields[key] = matches[-1].group(1)

    # target (last occurrence)
    matches = list(re.finditer(r'^\s*target\s*=\s*\{\s*type\s*=\s*"([^"]+)"\s*,\s*project_id\s*=\s*"([^"]+)"\s*\}', text, re.MULTILINE))
    if matches:
        fields["target"] = {"type": matches[-1].group(1), "project_id": matches[-1].group(2)}

    # cwds (last occurrence)
    matches = list(re.finditer(r'^\s*cwds\s*=\s*\[(.*?)\]', text, re.MULTILINE | re.DOTALL))
    if matches:
        fields["cwds"] = re.findall(r'"([^"]+)"', matches[-1].group(1))

    for key in ["created_at", "updated_at"]:
        matches = list(re.finditer(rf'^\s*{key}\s*=\s*(\d+)\s*$', text, re.MULTILINE))
        if matches:
            fields[key] = int(matches[-1].group(1))

    return {
        "id": auto_id,
        "version": 1,
        "kind": "cron",
        "name": fields.get("name", ""),
        "prompt": prompt,
        "status": fields.get("status", "ACTIVE"),
        "rrule": fields.get("rrule", ""),
        "model": fields.get("model", ""),
        "reasoning_effort": fields.get("reasoning_effort", ""),
        "notification_policy": fields.get("notification_policy", "failed_runs_only"),
        "execution_environment": fields.get("execution_environment", "local"),
        "target": fields.get("target", {"type": "project", "project_id": ""}),
        "cwds": fields.get("cwds", []),
        "created_at": fields.get("created_at", now_ms()),
        "updated_at": now_ms(),
    }


def render_toml(data: dict) -> str:
    lines = [
        'version = 1',
        f'id = "{data["id"]}"',
        'kind = "cron"',
        f'name = "{data["name"]}"',
        f'prompt = "{toml_escape_value(data["prompt"])}"',
        f'status = "{data["status"]}"',
        f'rrule = "{data["rrule"]}"',
    ]
    if data.get("model"):
        lines.append(f'model = "{data["model"]}"')
    if data.get("reasoning_effort"):
        lines.append(f'reasoning_effort = "{data["reasoning_effort"]}"')
    if data.get("notification_policy"):
        lines.append(f'notification_policy = "{data["notification_policy"]}"')
    if data.get("execution_environment"):
        lines.append(f'execution_environment = "{data["execution_environment"]}"')
    if data.get("target"):
        lines.append(f'target = {{ type = "{data["target"]["type"]}", project_id = "{data["target"]["project_id"]}" }}')
    if data.get("cwds"):
        lines.append(f'cwds = {json.dumps(data["cwds"])}')
    lines.append(f'created_at = {data["created_at"]}')
    lines.append(f'updated_at = {data["updated_at"]}')
    return "\n".join(lines) + "\n"


def get_db_automation(cur, auto_id: str):
    cur.execute("SELECT id, name, prompt, status, rrule, model, reasoning_effort, "
                "cwds, target_type, project_id, created_at, updated_at "
                "FROM automations WHERE id=?", (auto_id,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(["id", "name", "prompt", "status", "rrule", "model",
                     "reasoning_effort", "cwds", "target_type", "project_id",
                     "created_at", "updated_at"], row))


def update_or_create(cur, data: dict):
    existing = get_db_automation(cur, data["id"])
    ts = now_ms()
    target_type = data.get("target", {}).get("type", "project")
    project_id = data.get("target", {}).get("project_id", "")
    cwds = data.get("cwds", [])
    if isinstance(cwds, str):
        cwds = json.loads(cwds)
    if existing:
        cur.execute("""UPDATE automations SET
            name=?, prompt=?, status=?, rrule=?, model=?, reasoning_effort=?,
            cwds=?, target_type=?, project_id=?, updated_at=?
            WHERE id=?""", (
            data["name"], data["prompt"], data["status"], data["rrule"],
            data.get("model"), data.get("reasoning_effort"),
            json.dumps(cwds), target_type, project_id, ts, data["id"]))
        print(f"updated DB: {data['id']} ({len(data['prompt'])} chars)")
    else:
        created = data.get("created_at", ts)
        cur.execute("""INSERT INTO automations
            (id, name, prompt, status, rrule, model, reasoning_effort,
             cwds, target_type, project_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            data["id"], data["name"], data["prompt"], data["status"], data["rrule"],
            data.get("model"), data.get("reasoning_effort"),
            json.dumps(cwds), target_type, project_id, created, ts))
        print(f"created DB: {data['id']} ({len(data['prompt'])} chars)")


MEMORY_HUB_DATA = {
    "id": "memory-hub",
    "version": 1,
    "kind": "cron",
    "name": "memory-hub 每日全链路",
    "prompt": "跑 /Users/earan/Documents/memory-hub/memory-hub.sh run --apply --llm --commit，完成一次全链路记忆治理：采集 -> 蒸馏 -> 发布 -> 提交 -> 索引。\n\n步骤：\n1. cd /Users/earan/Documents/memory-hub\n2. ./memory-hub.sh run --apply --llm --commit\n3. 检查退出码：非 0 则报告错误并停止。\n4. 输出简洁中文摘要：采集条目数、蒸馏页数、发布到 llm-wiki 的页数、commit hash、索引状态。\n\n边界：只动 memory-hub 和 llm-wiki 相关文件；不做任何对外发布。",
    "status": "ACTIVE",
    "rrule": "RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=0;BYSECOND=0",
    "model": "agnes/agnes-2.5-flash",
    "reasoning_effort": "high",
    "notification_policy": "failed_runs_only",
    "execution_environment": "local",
    "target": {"type": "project", "project_id": "33bba037-406a-46a2-a0bf-bede378cdb74"},
    "cwds": ["/Users/earan/Documents/memory-hub"],
    "created_at": now_ms(),
    "updated_at": now_ms(),
}


def main():
    backup_db()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    changed_files = []
    changed_db = []

    for auto_id in AUTOMATIONS:
        path = AUTO_DIR / auto_id / "automation.toml"
        if not path.exists():
            print(f"missing file: {path}")
            continue
        data = repair_toml(auto_id)
        new_text = render_toml(data)
        old_text = path.read_text(encoding="utf-8")
        if new_text != old_text:
            path.write_text(new_text, encoding="utf-8")
            changed_files.append(auto_id)
            print(f"repaired file: {auto_id}")
        else:
            print(f"file already clean: {auto_id}")

        db = get_db_automation(cur, auto_id)
        if db and (db["prompt"] != data["prompt"] or db["name"] != data["name"] or db["rrule"] != data["rrule"]):
            update_or_create(cur, data)
            changed_db.append(auto_id)
        else:
            print(f"DB unchanged: {auto_id}")

    # memory-hub
    mh_path = AUTO_DIR / "memory-hub" / "automation.toml"
    if not mh_path.exists():
        mh_path.parent.mkdir(parents=True, exist_ok=True)
    new_text = render_toml(MEMORY_HUB_DATA)
    old_text = mh_path.read_text(encoding="utf-8") if mh_path.exists() else ""
    if new_text != old_text:
        mh_path.write_text(new_text, encoding="utf-8")
        changed_files.append("memory-hub")
        print(f"created/repaired file: memory-hub")
    else:
        print("file already clean: memory-hub")

    if not get_db_automation(cur, "memory-hub"):
        update_or_create(cur, MEMORY_HUB_DATA)
        changed_db.append("memory-hub")
    else:
        db = get_db_automation(cur, "memory-hub")
        if db["prompt"] != MEMORY_HUB_DATA["prompt"] or db["rrule"] != MEMORY_HUB_DATA["rrule"]:
            update_or_create(cur, MEMORY_HUB_DATA)
            changed_db.append("memory-hub")
        else:
            print("DB unchanged: memory-hub")

    con.commit()
    con.close()
    print(f"\nchanged files: {changed_files}")
    print(f"changed DB: {changed_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
