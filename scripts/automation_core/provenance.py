"""Provenance normalization for capture records."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import IO, Mapping

from .schema import Observation, normalize_id


def parse_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _redact_private_paths(text: str, cwd: str) -> str:
    if cwd:
        text = text.replace(cwd, "[REDACTED_CWD]")
    return re.sub(r"/(?:Users|home)/[^/\s]+", "[REDACTED_HOME]", text)


def _record(observation: Observation) -> dict[str, object]:
    return asdict(observation)


def _controlled_project_id(value: object) -> str:
    rendered = str(value or "").strip().replace("\\", "/").rstrip("/")
    leaf = rendered.rsplit("/", 1)[-1]
    return normalize_id(leaf, "default-project")


def normalize_observation(
    raw: Mapping[str, object], source: str, session_meta: Mapping[str, object]
) -> Observation:
    project_id = _controlled_project_id(
        session_meta.get("project_id") or raw.get("project")
    )
    cwd = str(session_meta.get("cwd", ""))
    cwd_hash = hashlib.sha256(cwd.encode()).hexdigest()
    stable = json.dumps({"source": source, "source_id": str(raw["id"]),
                         "session_id": str(session_meta.get("session_id", "")),
                         "project_id": project_id, "cwd_hash": cwd_hash,
                         "created_at": str(raw["created_at"])}, sort_keys=True, separators=(",", ":"))
    session_id = str(session_meta.get("session_id", ""))
    agent_id_raw = session_meta.get("agent_id")
    observed_at = parse_utc(str(raw["created_at"]))
    epoch = raw.get("created_at_epoch")
    if epoch is None:
        epoch = int(observed_at.timestamp() * 1000)
    return Observation(
        id=str(raw["id"]),
        project_id=project_id,
        provenance_id=hashlib.sha256(stable.encode()).hexdigest(),
        source=source,
        source_uri=f"{source}://sessions/{session_id}",
        agent_id=normalize_id(str(agent_id_raw), "agent") if agent_id_raw else None,
        cwd_hash=cwd_hash,
        text=_redact_private_paths(str(raw["text"]), cwd),
        observed_at=observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        role=str(raw.get("role") or "unknown"),
        type=str(raw.get("type") or "message"),
        created_at_epoch=int(epoch),
    )


def render_observation_report(observation: Observation) -> str:
    return json.dumps(_record(observation), ensure_ascii=False, indent=2)


def normalize_jsonl(source: IO[str], sink: IO[str], source_name: str) -> int:
    count = 0
    for line in source:
        if not line.strip():
            continue
        raw = json.loads(line)
        session_meta = raw.get("session_meta", {})
        if not isinstance(session_meta, Mapping):
            raise ValueError("session_meta must be an object")
        record = normalize_observation(raw, source_name, session_meta)
        sink.write(json.dumps(_record(record), ensure_ascii=False) + "\n")
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Normalize captured observations")
    parser.add_argument("command", choices=("normalize-jsonl",))
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    normalize_jsonl(sys.stdin, sys.stdout, args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
