# Task 1 修复回合代码复核

## 范围与输入

- 目标：仅复核既有五项 finding 在 `acf39c85bae75610b1889d160fcd00f838526859..c93d39449238693c3d7319262999cb204bd3a64a` 中的修复，不进行宽泛审查。
- 输入：`task-1-brief.md`、`task-1-implementer-report.md`、`review-acf39c8..c93d394.diff`、实施者验证记录 `task-1-schema-provenance-repair-validation.md`。
- ULW 状态：`omo ulw-loop status --json` 无法执行，原因是本机 `omo-ulw-loop` 的 `posix_spawn` 返回 `EACCES`；因此按无可用 ULW attempt 路径回退到本报告路径。
- 技能视角检查：`remove-ai-slops` 与 `programming` 不在本回合的可用技能清单，无法加载。已按其题面给定准则人工检查：新增测试真实驱动 `capture.sh` 的四来源成功/失败/重试分支，非删除式、非仅复述常量、非提示词测试；生产差异未增加无关的数据抽取、解析或归一化。唯一的测试注入开关见 Minor。

## CRITICAL

无。

## HIGH

无。

## MEDIUM

无。

## LOW

无。

## Finding Verdicts

1. **ADDRESSED — controlled project ID / home 身份泄露。**
   - `scripts/automation_core/provenance.py:32-43` 令 `session_meta.project_id` 优先于原始 `project`，将路径或反斜杠路径压缩为最后一级后才做 `normalize_id`；因此不会持久化 `/Users/<name>`。
   - 行 56-69 的 `Observation` 仅输出 controlled `project_id`、`cwd_hash`，正文在行 64 脱敏；未输出 session 元数据。
   - 行为证据：`tests/test_capture_provenance.py:286-301` 覆盖 session 优先和两类用户名排除；行 349-361 对 Codex、Claude Code、Claude-mem、WorkBuddy 真实 `capture.sh` 输出做同类断言。定向运行通过。

2. **ADDRESSED — normalizer 失败时游标/去重状态提前提交。**
   - `scripts/capture.sh:159-167`、`328-336`、`443-451` 均在 `normalize_capture_output` 成功后才移动 `.seen` 和写入 `.since`；失败分支仅清理临时文件并以非零返回。
   - Claude-mem 在 `scripts/capture.sh:204-208` 中先完成 normalizer，再写 `.since`；`set -e` 保证 normalizer 非零时不会抵达行 208。
   - 行为证据：`tests/test_capture_provenance.py:364-400` 对四来源注入 normalizer 失败，断言 `.since`（及适用 `.seen`）不变、无发布文件，并成功重试。定向运行通过。

3. **ADDRESSED — frontmatter closing delimiter 必须是严格独立行，空块仍合法。**
   - `scripts/automation_core/frontmatter.py:32-40` 只接受紧随 opening delimiter 的空块，或以精确 `\n---\n` 出现的 closing delimiter；`title: value---` 不能作为 delimiter。
   - 行为证据：`tests/test_capture_provenance.py:74-90` 覆盖畸形 `title: value---`，行 92-95 覆盖 `---\n---\n` 空 frontmatter；定向运行通过。

4. **ADDRESSED — 实现报告的测试证据表述。**
   - `task-1-implementer-report.md:13-19` 将“10 passed”明确标为“历史、不可作为当前验收证据”，并以当前 `11 passed` 等结果取代；行 29 明确 `test_status_verify_consistency.sh` 未运行，未虚报通过。
   - 产物证据：`.omo/evidence/task-1-schema-provenance-repair-validation.md:11-13,21,29` 分别记录每项可观察行为、当前 `11 passed`、以及该脚本未运行的边界。

5. **ADDRESSED — 四 source 与 normalizer failure 的真实行为测试。**
   - `tests/test_capture_provenance.py:349-361` 创建四类真实输入并通过 `bash scripts/capture.sh --source ... --all` 检查已发布的 normalized JSONL。
   - `tests/test_capture_provenance.py:364-400` 使用同样的真实 shell 入口验证失败和重试状态。
   - 本次复核实际命令：`PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py -k 'session_project_id_wins or parse_page_rejects_malformed_or_unsafe_frontmatter or parse_page_accepts_an_empty_frontmatter_block or all_capture_sources_publish_controlled_provenance or normalizer_failure_preserves_seen_and_since_for_retry'`，退出 0，`5 passed, 6 deselected in 2.66s`。

## New Breakage in the Fix Diff

- **Minor — 测试注入开关进入生产 shell 表面。** `scripts/capture.sh:61-64` 新增未在 usage 或注释中说明的 `MEMORY_HUB_NORMALIZER_MODULE`，允许环境变量替换 Python 模块。它使真实失败路径可被集成测试稳定触发，且可影响仅在已能控制进程环境时运行的命令；未构成当前 correctness/security 阻塞。不过该开关若作为长期接口保留，应在后续将其限制为测试机制或明确记录。

未发现 Critical 或 Important 的修复差异新破坏。

## Out-of-Scope Observations

- 修复前已存在的 capture 解析/临时文件策略、以及本任务外的 `tests/test_status_verify_consistency.sh` 早期失败，不属于此次 fix diff；未据此阻塞。

## 结论

- `codeQualityStatus`: **CLEAR**
- `recommendation`: **APPROVE**
- `blockers`: 无
- Verdict: **All findings addressed, no new Critical/Important breakage**
