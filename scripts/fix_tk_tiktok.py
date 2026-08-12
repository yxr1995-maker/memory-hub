#!/usr/bin/env python3
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".codex" / "sqlite" / "codex-dev.db"
AUTO_DIR = Path.home() / ".codex" / "automations"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def toml_escape(s: str) -> str:
    s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return s


def render_toml(data: dict) -> str:
    lines = [
        'version = 1',
        f'id = "{data["id"]}"',
        'kind = "cron"',
        f'name = "{data["name"]}"',
        f'prompt = "{toml_escape(data["prompt"])}"',
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


def get_automation_from_db(auto_id: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, name, prompt, status, rrule, model, reasoning_effort, "
                "cwds, target_type, project_id, created_at, updated_at "
                "FROM automations WHERE id=?", (auto_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    d = dict(zip(["id", "name", "prompt", "status", "rrule", "model",
                  "reasoning_effort", "cwds", "target_type", "project_id",
                  "created_at", "updated_at"], row))
    if isinstance(d["cwds"], str):
        d["cwds"] = json.loads(d["cwds"])
    d["target"] = {"type": d["target_type"], "project_id": d["project_id"]}
    del d["target_type"]
    del d["project_id"]
    return d


def update_db_and_file(data: dict):
    path = AUTO_DIR / data["id"] / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(data), encoding="utf-8")
    print(f"wrote file: {data['id']} ({len(data['prompt'])} chars)")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""UPDATE automations SET
        name=?, prompt=?, status=?, rrule=?, model=?, reasoning_effort=?,
        cwds=?, target_type=?, project_id=?, updated_at=?
        WHERE id=?""", (
        data["name"], data["prompt"], data["status"], data["rrule"],
        data.get("model"), data.get("reasoning_effort"),
        json.dumps(data.get("cwds", [])),
        data["target"]["type"], data["target"]["project_id"],
        data["updated_at"], data["id"]))
    con.commit()
    con.close()
    print(f"updated DB: {data['id']}")


def main():
    # Backup
    ts = int(datetime.now(timezone.utc).timestamp())
    backup_path = Path(f"/tmp/codex-dev-before-tk-tiktok-{ts}.db")
    con = sqlite3.connect(DB_PATH)
    con.execute(f"VACUUM INTO '{backup_path}'")
    con.close()
    print(f"backup: {backup_path}")

    # tk-w-6
    d = get_automation_from_db("tk-w-6")
    d["prompt"] = d["prompt"].replace(
        "linkfox 或 gbrain 记忆",
        "linkfox 或 memory-hub 搜索"
    )
    d["updated_at"] = now_ms()
    update_db_and_file(d)

    # tiktok
    d = get_automation_from_db("tiktok")
    d["prompt"] = d["prompt"].replace(
        "## 查重(gbrain 优先)",
        "## 查重(memory-hub 优先)"
    )
    d["prompt"] = d["prompt"].replace(
        "先调 gbrain query 工具搜索该主题是否已有 wiki 页面覆盖",
        "先调 /Users/earan/Documents/memory-hub/memory-hub.sh search 工具搜索该主题是否已有 wiki 页面覆盖"
    )
    d["prompt"] = d["prompt"].replace(
        'PATH="$HOME/.bun/bin:$PATH" gbrain sync',
        '/Users/earan/Documents/memory-hub/memory-hub.sh index'
    )
    d["updated_at"] = now_ms()
    update_db_and_file(d)

    return 0


if __name__ == "__main__":
    sys.exit(main())
