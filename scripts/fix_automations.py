#!/usr/bin/env python3
"""Fix automation prompts by replacing gbrain sync with memory-hub.sh index."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".codex" / "sqlite" / "codex-dev.db"
BACKUP_DB = "/tmp/codex-dev-before-memoryhub-1786439386.db"
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


def get_automation_from_db(db_path: str, auto_id: str) -> dict:
    con = sqlite3.connect(db_path)
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


def get_automation_from_current_db(auto_id: str) -> dict:
    return get_automation_from_db(DB_PATH, auto_id)


def set_prompt(data: dict, prompt: str) -> dict:
    data["prompt"] = prompt
    data["updated_at"] = now_ms()
    return data


def update_db_and_file(data: dict):
    # Write file
    path = AUTO_DIR / data["id"] / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(data), encoding="utf-8")
    print(f"wrote file: {data['id']} ({len(data['prompt'])} chars)")

    # Write DB
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


def create_db_and_file(data: dict):
    path = AUTO_DIR / data["id"] / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(data), encoding="utf-8")
    print(f"wrote file: {data['id']} ({len(data['prompt'])} chars)")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id FROM automations WHERE id=?", (data["id"],))
    if cur.fetchone():
        cur.execute("""UPDATE automations SET
            name=?, prompt=?, status=?, rrule=?, model=?, reasoning_effort=?,
            cwds=?, target_type=?, project_id=?, updated_at=?
            WHERE id=?""", (
            data["name"], data["prompt"], data["status"], data["rrule"],
            data.get("model"), data.get("reasoning_effort"),
            json.dumps(data.get("cwds", [])),
            data["target"]["type"], data["target"]["project_id"],
            data["updated_at"], data["id"]))
    else:
        cur.execute("""INSERT INTO automations
            (id, name, prompt, status, rrule, model, reasoning_effort,
             cwds, target_type, project_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            data["id"], data["name"], data["prompt"], data["status"], data["rrule"],
            data.get("model"), data.get("reasoning_effort"),
            json.dumps(data.get("cwds", [])),
            data["target"]["type"], data["target"]["project_id"],
            data["created_at"], data["updated_at"]))
    con.commit()
    con.close()
    print(f"created/updated DB: {data['id']}")


MEMORY_HUB_PROMPT = """跑 /Users/earan/Documents/memory-hub/memory-hub.sh run --apply --llm --commit，完成一次全链路记忆治理：采集 -> 蒸馏 -> 发布 -> 提交 -> 索引。

步骤：
1. cd /Users/earan/Documents/memory-hub
2. ./memory-hub.sh run --apply --llm --commit
3. 检查退出码：非 0 则报告错误并停止。
4. 输出简洁中文摘要：采集条目数、蒸馏页数、发布到 llm-wiki 的页数、commit hash、索引状态。

边界：只动 memory-hub 和 llm-wiki 相关文件；不做任何对外发布。"""


def main():
    # Backup current DB
    ts = int(datetime.now(timezone.utc).timestamp())
    backup_path = Path(f"/tmp/codex-dev-before-fix-{ts}.db")
    con = sqlite3.connect(DB_PATH)
    con.execute(f"VACUUM INTO '{backup_path}'")
    con.close()
    print(f"backup: {backup_path}")

    # Read original prompts from backup before our repair script touched them
    d_distill = get_automation_from_db(BACKUP_DB, "wiki-distill")
    d_review = get_automation_from_db(BACKUP_DB, "wiki-review")
    d_curation = get_automation_from_db(BACKUP_DB, "llm-wiki-curation")
    d_gbrain = get_automation_from_db(BACKUP_DB, "gbrain")

    # Replace gbrain sync with memory-hub.sh index
    d_distill["prompt"] = d_distill["prompt"].replace(
        'PATH="$HOME/.bun/bin:$PATH" gbrain sync',
        '/Users/earan/Documents/memory-hub/memory-hub.sh index'
    )
    d_review["prompt"] = d_review["prompt"].replace(
        'PATH="$HOME/.bun/bin:$PATH" gbrain sync',
        '/Users/earan/Documents/memory-hub/memory-hub.sh index'
    )
    d_curation["prompt"] = d_curation["prompt"].replace(
        'PATH="$HOME/.bun/bin:$PATH" gbrain sync',
        '/Users/earan/Documents/memory-hub/memory-hub.sh index'
    )

    # Update cwds for the three wiki automations to include memory-hub
    d_distill["cwds"] = ["/Users/earan/Documents/memory-hub", "/Users/earan/llm-wiki"]
    d_review["cwds"] = ["/Users/earan/Documents/memory-hub", "/Users/earan/llm-wiki"]
    d_curation["cwds"] = ["/Users/earan/Documents/memory-hub", "/Users/earan/llm-wiki"]

    # Update gbrain automation to be memory-hub health check
    d_gbrain["name"] = "memory-hub 每周健康摘要"
    d_gbrain["prompt"] = """对 ~/llm-wiki + memory-hub 做每周健康检查并生成中文摘要。memory-hub 是新的记忆系统，gbrain 已下线。

步骤：
1. 跑 `/Users/earan/Documents/memory-hub/memory-hub.sh status`，读取采集游标、staging 观察数、wiki 页面数、claude-mem 记录数、LLM 代理连通性。
2. 跑 `/Users/earan/Documents/memory-hub/memory-hub.sh index`（增量重建 FTS5 索引），确认索引无报错。
3. 跑 `cd ~/llm-wiki && git status -s`，检查 work-tree 是否有未提交文件（memory-hub 发布不 commit 的会留在这里）。
4. 读 `ls /Users/earan/Documents/memory-hub/staging/pages/ | wc -l`，确认 staging/pages 无积压。
5. 读 `ls /Users/earan/Documents/memory-hub/staging/published/ | wc -l`，确认已发布页计数。
6. 读 `grep -c "memoryhub" /Users/earan/llm-wiki/index.md`，确认索引引用正常。

输出格式（简洁中文）：

memory-hub 健康摘要 (YYYY-MM-DD)

  - 采集游标: <最新时间>
  - staging 观察: <N> 文件
  - wiki 页面: <N>
  - claude-mem 记录: <N>
  - LLM 代理: <连通/不可用>
  - 索引状态: <正常/报错>
  - 未提交文件: <清单或"无">
  - staging 积压: <N> 页

趋势提醒: (staging 积压是否上涨？索引是否变慢？)

建议操作: (需人工处理的事项)

边界：只做只读检查，不修改 memory-hub 数据或配置。"""
    d_gbrain["cwds"] = ["/Users/earan/Documents/memory-hub", "/Users/earan/llm-wiki"]

    update_db_and_file(set_prompt(d_distill, d_distill["prompt"]))
    update_db_and_file(set_prompt(d_review, d_review["prompt"]))
    update_db_and_file(set_prompt(d_curation, d_curation["prompt"]))
    update_db_and_file(set_prompt(d_gbrain, d_gbrain["prompt"]))

    # Create memory-hub daily automation
    d_memory_hub = {
        "id": "memory-hub",
        "name": "memory-hub 每日全链路",
        "prompt": MEMORY_HUB_PROMPT,
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
    create_db_and_file(d_memory_hub)

    return 0


if __name__ == "__main__":
    sys.exit(main())
