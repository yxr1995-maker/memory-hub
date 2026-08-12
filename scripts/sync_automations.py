#!/usr/bin/env python3
"""Sync Codex automation file layer (.toml) with SQLite DB layer."""
import json
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".codex" / "sqlite" / "codex-dev.db"
AUTO_DIR = Path.home() / ".codex" / "automations"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def backup_db():
    ts = int(datetime.now(timezone.utc).timestamp())
    backup_path = Path(f"/tmp/codex-dev-before-memoryhub-{ts}.db")
    con = sqlite3.connect(DB_PATH)
    con.execute(f"VACUUM INTO '{backup_path}'")
    con.close()
    print(f"backup: {backup_path}")


def parse_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


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


MEMORY_HUB_TOML = '''version = 1
id = "memory-hub"
kind = "cron"
name = "memory-hub 每日全链路"
prompt = """跑 /Users/earan/Documents/memory-hub/memory-hub.sh run --apply --llm --commit，完成一次全链路记忆治理：采集 -> 蒸馏 -> 发布 -> 提交 -> 索引。

步骤：
1. cd /Users/earan/Documents/memory-hub
2. ./memory-hub.sh run --apply --llm --commit
3. 检查退出码：非 0 则报告错误并停止。
4. 输出简洁中文摘要：采集条目数、蒸馏页数、发布到 llm-wiki 的页数、commit hash、索引状态。

边界：只动 memory-hub 和 llm-wiki 相关文件；不做任何对外发布。"""
status = "ACTIVE"
rrule = "RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=0;BYSECOND=0"
model = "agnes/agnes-2.5-flash"
reasoning_effort = "high"
notification_policy = "failed_runs_only"
execution_environment = "local"
target = { type = "project", project_id = "33bba037-406a-46a2-a0bf-bede378cdb74" }
cwds = ["/Users/earan/Documents/memory-hub"]
'''


def main():
    backup_db()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    changed = []
    for auto_id in ["wiki-distill", "wiki-review", "llm-wiki-curation", "gbrain"]:
        toml_path = AUTO_DIR / auto_id / "automation.toml"
        if not toml_path.exists():
            print(f"missing file: {toml_path}")
            continue
        data = parse_toml(toml_path)
        data["id"] = auto_id
        db = get_db_automation(cur, auto_id)
        if not db:
            print(f"missing DB: {auto_id}")
            continue
        if db["prompt"] != data["prompt"] or db["name"] != data["name"]:
            changed.append(auto_id)
            update_or_create(cur, data)
        else:
            print(f"unchanged: {auto_id}")

    memory_hub_toml = AUTO_DIR / "memory-hub" / "automation.toml"
    if not memory_hub_toml.exists():
        memory_hub_toml.parent.mkdir(parents=True, exist_ok=True)
        memory_hub_toml.write_text(MEMORY_HUB_TOML, encoding="utf-8")
        print(f"created file: {memory_hub_toml}")
    data = parse_toml(memory_hub_toml)
    data["id"] = "memory-hub"
    if not get_db_automation(cur, "memory-hub"):
        changed.append("memory-hub")
        update_or_create(cur, data)
    else:
        print("memory-hub already exists in DB")

    con.commit()
    con.close()
    print(f"\nchanged: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
