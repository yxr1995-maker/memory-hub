# Task 1 代码质量与规格符合性门禁

审查范围：`2b61e89601f0bb7b52e0c944b64f280f286edaed..acf39c85bae75610b1889d160fcd00f838526859`，仅限 Task 1 指定文件及实现报告。未审查整个分支。

## 结论

- codeQualityStatus: BLOCK
- recommendation: REQUEST_CHANGES
- blockers:
  1. Claude-mem 的未受控 `project` 能以含真实 home 用户名的 `project_id` 持久化，违反 provenance 仅允许受控 `project_id` 的绑定约束。
  2. Codex、Claude Code 和 WorkBuddy 在归一化成功前提交 `.seen`，归一化失败会永久跳过本批观察，违反“不破坏 cursor”的要求。
  3. 实现报告关于聚焦测试“10 项通过”的成功声明与当前审查包中的 7 项测试不一致，且未提供可核验的对应产物。

## Spec Compliance

- ✅ 四个 capture 分支都调用 `scripts.automation_core.provenance normalize-jsonl`，并在发布最终 `observations-*.jsonl` 前完成输出文件替换。
- ✅ `distill.sh` 在读取项目字段时使用 `project_id // project`，能继续读取 legacy observation；其新增 frontmatter 使用 provenance id、source URI、cwd hash 和 agent id，而没有直接写入 `session_meta`。
- ✅ Observation 的 `observed_at` 已序列化为 UTC `Z` 格式字符串，JSONL/报告路径不会再因 `datetime` 不能 JSON 序列化而失败。
- ✅ fixture 将 `HOME` 覆盖为 fixture root 下的 `home`，并将 session、automation、config、db、wiki 和 data 全部注入临时路径；本次聚焦 fixture 测试通过。
- ❌ `project_id` 并不始终是受控标识：raw `project` 优先于 `session_meta.project_id`，绝对路径经 normalize 后仍会保留真实用户段。
- ❌ 对有 `.seen` 的 capture 分支，游标状态没有与归一化/发布保持原子性；失败会丢失记录。
- ❌ frontmatter parser 没有严格验证 closing delimiter 位于独立 delimiter 行，畸形文档会被接受。

## Strengths

- 新增接口集中在 `scripts/automation_core`，`Observation`、`PageDocument`、`OperationContext` 等边界清晰，改动范围与 Task 1 文件清单一致。
- `normalize_jsonl()` 不转发 `session_meta`；正常输入中的 cwd 只以 SHA-256 hash 保存，正文中的精确 cwd 与 `/Users/<name>`、`/home/<name>` 前缀也会被替换。
- `patch_frontmatter()` 使用原始 `body: bytes` 回写，已有测试覆盖了 NUL 字节正文的 byte-exact 保持。
- 聚焦验证实际执行：`PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py`（7 passed）；`python3 -m py_compile scripts/automation_core/*.py tests/helpers/*.py`（exit 0）；`bash -n scripts/capture.sh scripts/distill.sh`（exit 0）。未重跑全套测试。

## Issues

### Critical

- 无。

### Important

- **HIGH — 未受控 raw project 会泄露真实 home 身份并违反受控 provenance 契约。** [scripts/automation_core/provenance.py:35](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/automation_core/provenance.py:35) 先选择 `raw["project"]`，再选择已由 capture 分支构造的 `session_meta.project_id`；[scripts/capture.sh:195](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:195) 又将 Claude-mem SQLite 的原始 `project` 无条件带入 normalizer。最小复现将 `raw.project=/Users/real-user/private/project` 归一化为持久化值 `users-real-user-private-project`。这不是不可逆 cwd hash，也不是 controlled project id，直接违背绑定隐私约束。

- **HIGH — 归一化失败后 `.seen` 已被提交，重跑会静默丢失观察。** [scripts/capture.sh:154](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:154) / [scripts/capture.sh:158](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:158)、[scripts/capture.sh:320](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:320) / [scripts/capture.sh:324](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:324)、[scripts/capture.sh:432](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:432) / [scripts/capture.sh:436](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/capture.sh:436)。`normalize_observation()` 仍可因无时区/非法时间戳、非整数 epoch 等输入抛错；此时 final JSONL 未发布，但 raw hash 已写到 `.seen`，同一记录后续被过滤。这正是 Task 1 要避免的 cursor 破坏。

- **HIGH — 关闭 frontmatter delimiter 的检查不是严格行级检查。** [scripts/automation_core/frontmatter.py:32](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/scripts/automation_core/frontmatter.py:32) 使用 `content.find(b"---\\n", 4)`，可匹配 `title: value---\\n` 末尾的字节串，不要求该 `---` 自成一行。复现输入 `---\\ntitle: value---\\nnot-a-delimiter\\n---\\nbody\\n` 被接受为 `{'title': 'value'}`，其余 header 内容被当作 body。该行为不满足 brief 对 malformed delimiter 的拒绝要求。

- **HIGH — 实现者成功证据与审查包不一致，且没有可核验的输出产物。** [task-1-implementer-report.md:19](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/.superpowers/sdd/2026-08-31-roadmap-full-auto/task-1-implementer-report.md:19) 声称聚焦文件“all 10 tests”通过；当前 commit 的 [tests/test_capture_provenance.py:30](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/tests/test_capture_provenance.py:30) 至 [tests/test_capture_provenance.py:161](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/tests/test_capture_provenance.py:161) 只有 7 个 `test_` 函数，本次同一命令也得到 `7 passed`。该不一致使成功声明不可采信；按门禁要求，误导性且无产物路径的成功输出是 blocker。

### Minor

- **MEDIUM — 测试没有覆盖四个 source 的隐私/游标失败路径。** [tests/test_capture_provenance.py:161](/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/tests/test_capture_provenance.py:161) 端到端测试只覆盖 Codex 正常路径；没有覆盖 Claude-mem raw `project`、Claude Code、WorkBuddy，也没有注入 normalizer 失败后检查 `.seen`/`.since` 不变。因此它对本任务两个高风险约束提供了错误的完成信号。应新增行为级场景测试，而不是只增加常量或实现镜像断言。

## Skill-perspective check

`remove-ai-slops` 与 `programming` 不在本会话可用的 skills 清单中，且本次无法加载其 `SKILL.md`；因此按任务指定的等价准则人工执行了该检查。

- remove-ai-slops：未发现 deletion-only test、仅验证“移除”的测试或纯常量镜像测试；但测试遗漏所有 source 的失败安全路径，导致正常路径绿灯无法证明 cursor/provenance 契约，见上述 MEDIUM finding。
- programming：未发现 untyped escape hatch 或为本任务额外引入的生产解析层；fixture 的延迟 transaction import 有明确跨任务所有权原因。frontmatter 的自写 parser 与本任务的严格解析边界相关，并非无关抽象，但其 delimiter 实现不够严格，见 HIGH finding。

## Verification notes

- 审阅了指定 brief、实现者报告和 review diff；diff 仅包含 Task 1 列出的 9 个文件，未见 legacy worktree 作为输入。
- 未重跑全套测试。仅在出现具体疑点后运行了 focused pytest、py_compile、bash syntax，以及两个无持久化 Python 最小复现。

