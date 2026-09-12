"""PR-3 frozen holdout harness: snapshots, leakage check, dry-run plan.

Model execution lives in runner.py; execute_run here is only an
authorization gate. This module makes no network calls.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.automation_core.experience import AccessContext, ExperienceError, initialize, record_episode, recall_for_decision  # noqa: E402
from scripts.automation_core.experience.store import tokens  # noqa: E402

GROUPS = ("A", "B", "C")
ENGINEERING_SHAPE = {"decision": "non-empty string: the decision taken", "reason": "non-empty string: why"}

# Public task definitions. The correct strategy never appears here; the
# grading reference stays in holdout_keys.jsonl (mode 0600). Creative tasks
# embed synthetic facts so mechanical fact checks are possible.
HOLDOUT_TASKS = [
    {"task_id": "eng-cover-user-edits", "dev_index": 0,
     "task_text": "自动生成的巡检清单被用户手动调整过。程序现在要重新生成并写入这份清单文件。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "eng-migration-backup", "dev_index": 1,
     "task_text": "把本地 SQLite 数据库迁移到新服务器。给出迁移前的准备动作。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "eng-real-exit-code", "dev_index": 2,
     "task_text": "测试脚本的日志显示全部通过。给出判定测试通过还需要的证据。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "eng-fault-isolation", "dev_index": 3,
     "task_text": "在一个复制出来的环境里演练故障注入。给出必须遵守的边界。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "creative-companion-copy", "dev_index": 4,
     "task_text": "为独居老人的陪伴机器人「小伴」写一段晚间八点推送文案：温暖、有陪伴感，但不要让用户感到负担或愧疚。",
     "artifact": {"kind": "creative_copy", "min_length": 40, "facts": ["小伴", "晚间"],
                  "must_not_include": ["愧疚", "拖累", "负担"]}},
    {"task_id": "creative-keep-intent", "dev_index": 5,
     "task_text": "合成产品：保温杯「暖途」，容量350毫升，有提绳，未提供保温时长测试。旧稿为“把家的温暖握在手里”。为通勤用户写20至60字新稿，保留温暖语气但不要照抄旧句或虚构性能。",
     "artifact": {"kind": "creative_copy", "min_length": 20, "facts": ["暖途"],
                  "must_not_include": ["上一版", "原句照抄"]}},
    {"task_id": "creative-tone-down", "dev_index": 6,
     "task_text": "合成净水壶「净流」，容量1.5升，可更换滤芯，未提供净化率或实测报告。原稿“全网第一，立刻见效”。改写30至70字，具体可信，不虚构认证或测试结论。",
     "artifact": {"kind": "creative_copy", "min_length": 30, "facts": ["净流"],
                  "must_not_include": ["全网第一", "史上最强", "立刻见效"]}},
    {"task_id": "creative-shot-notes", "dev_index": 7,
     "task_text": "合成宠物零食广告：第3秒画面含包装、狗、三件背景道具。评审明确要求移除两件背景道具，包装放左侧三分之一，狗留在右侧。写修改记录，保留评审来源并将你的额外建议标为建议。",
     "artifact": {"kind": "creative_copy", "min_length": 25, "facts": ["第3秒", "构图"],
                  "must_not_include": ["大概", "随意"]}},
    {"task_id": "boundary-new-brief-wins", "dev_index": 8,
     "task_text": "客户这次明确要求更激进的促销风格，与之前偏柔和的文案经验冲突。给出这次的处理决定。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "boundary-no-reason", "dev_index": 9,
     "task_text": "一条历史记录只写了“用户选了B方案”，没有原因。给出这条记录的使用方式。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "boundary-disposable-dir", "dev_index": 10,
     "task_text": "在一次性容器目录里跑可丢弃的实验。给出是否需要完整备份恢复流程的决定。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
    {"task_id": "boundary-no-history", "dev_index": 11,
     "task_text": "接到一个与已有经历都无关的新任务。给出正确做法。",
     "artifact": {"kind": "engineering_json", "answer_shape": ENGINEERING_SHAPE}},
]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _norm(value):
    return "".join(str(value).split())


def load_holdout_tasks(dest):
    path = Path(dest) / "holdout_tasks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_holdout_keys(dest):
    path = Path(dest) / "holdout_keys.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def grade(task, answer):
    """Judge only the answer. No group distinction, no injection records.

    engineering_json: shape gate ({decision, reason}, both non-empty
    strings). Whether the decision is correct belongs to blind review
    against holdout_keys, not to this function.
    creative_copy: mechanical checks only (length, synthetic facts,
    forbidden phrases). No total score is produced here.
    """
    spec = task["artifact"]
    if spec["kind"] == "engineering_json":
        ok = (type(answer) is dict and set(answer) == {"decision", "reason"}
              and all(type(answer[k]) is str and answer[k].strip() for k in ("decision", "reason")))
        return {"passed": ok, "score": 1 if ok else 0, "failures": [] if ok else ["answer_shape"]}
    if spec["kind"] == "creative_copy":
        checks = {"min_length": False, "facts_missing": list(spec["facts"]), "forbidden_hits": [], "failures": []}
        if type(answer) is not str:
            checks["failures"] = ["missing_or_non_string"]
            return {"mechanical": checks}
        checks["min_length"] = len(answer) >= spec["min_length"]
        checks["facts_missing"] = [f for f in spec["facts"] if f not in answer]
        checks["forbidden_hits"] = [f for f in spec["must_not_include"] if f in answer]
        if not checks["min_length"]:
            checks["failures"].append("too_short")
        if checks["facts_missing"]:
            checks["failures"].append("facts_missing")
        if checks["forbidden_hits"]:
            checks["failures"].append("forbidden_hits")
        return {"mechanical": checks}
    raise ExperienceError("PAYLOAD_INVALID", "unknown artifact kind")


def check_leakage(tasks, dev_cases):
    """Flag verbatim reuse and heavy token overlap against development cases."""
    leaks = []
    dev_index = []
    for i, case in enumerate(dev_cases):
        phrases = [_norm(case["goal"])]
        if case.get("explicit_reason"):
            phrases.append(_norm(case["explicit_reason"]))
        dev_index.append((i, phrases, set(tokens(case["goal"] + " " + (case.get("explicit_reason") or "")))))
    for task in tasks:
        body = _norm(task["task_text"])
        task_tokens = set(tokens(task["task_text"]))
        for index, phrases, dev_tokens in dev_index:
            if any(p and p in body for p in phrases):
                leaks.append({"task_id": task["task_id"], "dev_index": index, "reason": "verbatim"})
                break
            shared = task_tokens & dev_tokens
            if len(dev_tokens) and len(shared) / len(dev_tokens) >= 0.6 and len(shared) >= 6:
                leaks.append({"task_id": task["task_id"], "dev_index": index, "reason": "token_overlap"})
                break
    return leaks


def _seed_snapshot(db_path, dev_cases, artifact_root):
    if db_path.exists():
        db_path.unlink()
    initialize(db_path)
    ctx = AccessContext(subject_id="eval-freezer", allowed_collections=("fixture",),
                        role="owner", artifact_roots=(str(Path(artifact_root).resolve()),))
    for i, payload in enumerate(dev_cases):
        record_episode(db_path, ctx, payload, idempotency_key=f"dev-{i:02d}")


def freeze_tasks(dest):
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ExperienceError('FREEZE_EXISTS','use a new empty destination; frozen evidence is immutable')
    dev_cases = [json.loads(line) for line in (EVAL_DIR / "development.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(dev_cases) != 12:
        raise ExperienceError("PAYLOAD_INVALID", "development set must hold exactly 12 cases")
    tasks = [{"task_id": t["task_id"], "task_text": t["task_text"], "artifact": t["artifact"]} for t in HOLDOUT_TASKS]
    leaks = check_leakage(tasks, dev_cases)
    if leaks:
        raise ExperienceError("LEAKAGE_DETECTED", json.dumps(leaks, ensure_ascii=False))
    (dest / "snapshots").mkdir(parents=True, exist_ok=True)
    snapshots = {}
    for group in GROUPS:
        db = dest / "snapshots" / group / "experience.sqlite3"
        _seed_snapshot(db, dev_cases, dest)
        snapshots[group] = {"events": 12, "sha256": sha256_file(db)}
    (dest / "holdout_tasks.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks), encoding="utf-8")
    keys_path = dest / "holdout_keys.jsonl"
    fd = os.open(keys_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for t in HOLDOUT_TASKS:
            handle.write(json.dumps({"task_id": t["task_id"], "expected_dev_index": t["dev_index"]}, ensure_ascii=False) + "\n")
    keys_path.chmod(0o600)
    config = {
        "protocol": "experience-v1-holdout-1",
        "groups": {
            "A": {"memory_access": "none", "note": "baseline without any history surface"},
            "B": {"memory_access": "plain_search", "note": "same snapshot, unstructured literal search"},
            "C": {"memory_access": "experience_recall", "note": "same snapshot, bounded recall_for_decision; no automatic evidence expansion in this pilot"},
        },
        "shared": {"tasks": "holdout_tasks.jsonl", "snapshot_source": "development.jsonl"},
    }
    (dest / "holdout_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest = {
        "protocol": config["protocol"],
        "development_sha256": sha256_file(EVAL_DIR / "development.jsonl"),
        "tasks_sha256": sha256_file(dest / "holdout_tasks.jsonl"),
        "keys_sha256": sha256_file(keys_path),
        "config_sha256": sha256_file(dest / "holdout_config.json"),
        "snapshots": snapshots,
        "leaks": [],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tasks_frozen": len(tasks), "leaks": leaks, "snapshots": {g: 12 for g in GROUPS},
            "manifest": str(dest / "manifest.json")}


def build_plan(dest):
    dest = Path(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    plan = {
        "protocol": manifest["protocol"],
        "groups": list(GROUPS),
        "tasks": len(HOLDOUT_TASKS),
        "runs": len(GROUPS) * len(HOLDOUT_TASKS),
        "status": "dry_run",
        "model_calls_made": 0,
        "group_configs": json.loads((dest / "holdout_config.json").read_text(encoding="utf-8"))["groups"],
        "budget": {
            "paid_cap": 0,
            "basis": "free_model_only_no_paid_calls",
            "confirmed": False,
            "note": "any paid model call is out of scope; run budget uses free-model call and token caps only",
            "free_limits": {"max_model_calls_per_group": 48, "max_tokens_per_group": 200000},
        },
        "manifest_sha256": sha256_file(dest / "manifest.json"),
        "notes": [
            "36 runs are task executions, not 36 single API calls",
            "per-group independent snapshots; no cross-group feedback",
            "model execution is implemented in runner.py, not in this harness",
        ],
    }
    (dest / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


def execute_run(dest, *, group, task_index=None):
    """Authorization gate only. Actual execution lives in runner.py."""
    if group not in GROUPS:
        raise ExperienceError("PAYLOAD_INVALID", f"unknown group {group!r}")
    auth = Path(dest) / "authorization.json"
    if not auth.is_file():
        raise ExperienceError("AUTHORIZATION_REQUIRED",
                              "requires authorization.json confirming free_only=true and paid_cap=0")
    payload = json.loads(auth.read_text(encoding="utf-8"))
    if not (payload.get("paid_cap") == 0 and payload.get("free_only") is True
            and payload.get("confirmed_by_owner") is True):
        raise ExperienceError("AUTHORIZATION_REQUIRED",
                              "authorization must confirm free_only=true and paid_cap=0")
    raise ExperienceError("NOT_IMPLEMENTED", "model execution is implemented in runner.py, not here")
