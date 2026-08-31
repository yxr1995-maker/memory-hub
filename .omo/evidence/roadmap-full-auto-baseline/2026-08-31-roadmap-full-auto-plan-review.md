# Plan Review: roadmap-full-auto

**Date:** 2026-08-31  
**Reviewer:** /root/sdd_setup_and_plan_commit  
**Plan:** docs/superpowers/plans/2026-08-31-roadmap-full-auto.md  
**Spec:** docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md  
**Worktree:** .worktrees/roadmap-full-auto  

---

## P0: 阻塞实现的关键问题

### P0-1: `run_pipeline` 与 `maintain_pipeline` 的 stage 集合定义模糊

**位置:** Task 11, Step 4 (lines ~1150-1165)

**问题:** 计划描述了两套 pipeline：
- `run_pipeline`: `capture → distill → scope backfill → autolink → successor page preparation → one index swap → lifecycle finalization → archive → exact commit`
- `maintain_pipeline` (`MAINTAIN_ORDER`): `validate → publish_pages_lifecycle → index_swap → lint → atomic_manifest_commit → archive → exact_stage_commit`

这两套 stage 集合不同，但计划没有明确定义：
1. `run_pipeline` 是否也受 `--safe`/`--no-auto` 模式影响？
2. `run_pipeline` 的 stage 名称是否与 `MAINTAIN_ORDER` 有对应关系？
3. `run_pipeline` 中 "successor page preparation" 和 "lifecycle finalization" 是否对应 `maintain_pipeline` 的 `publish_pages_lifecycle` 和 `index_swap`？

**建议:** 在 Task 11 中添加一张对比表，明确两个 pipeline 的 stage 映射关系，以及各自在 auto/safe/no-auto 模式下的行为差异。

---

### P0-2: `StageRunner.index_swap_once_and_finalize` 的 `prepared` 参数来源未定义

**位置:** Task 8 Step 4 (lines ~840-850), Task 11 Step 4 (lines ~1196-1200)

**问题:** 
- Task 8 的 `publish_successor_once` wrapper 签名是 `(plan, tx, rebuild_index)`，内部调用 `prepare_successor_pages(plan, tx)` 得到 `prepared`
- Task 11 的 `maintain_pipeline` 通过 `stages.index_swap_once_and_finalize(tx)` 调用，但没有说明 `prepared` 对象如何传入
- 如果 `prepared` 从外部传入，那 `maintain_pipeline` 必须在调用 `index_swap` 前保存 `prepared` 引用
- 如果 `prepared` 在 `index_swap` 内部重新计算，那 `publish_pages_lifecycle` 阶段的 `prepare_successor_pages` 调用就是多余的

**建议:** 明确 `index_swap_once_and_finalize` 的签名应该是 `(prepared, tx)` 还是从某个上下文获取 `prepared`。如果是前者，需在 `maintain_pipeline` 中添加 `prepared = stages.prepare_lifecycle(tx)` 的步骤。

---

### P0-3: `prepare_successor_pages` 中 `PREPARED` checkpoint 时机问题

**位置:** Task 8 Step 4 (lines ~820-830)

**问题:** 代码片段显示：
```python
tx.journal.checkpoint("PREPARED")
tx.journal.write_verified_temp(plan.new_path, active_patch(plan))
tx.journal.checkpoint("NEW_TEMP_VERIFIED")
```
`PREPARED` checkpoint 在写入 A/B 页之前调用，这意味着如果写入失败，checkpoint 已经是 `PREPARED` 但实际文件未写入。这与计划中 "prepare 完成后才允许 index_swap" 的语义可能冲突。

**建议:** 确认 `PREPARED` checkpoint 应在所有 temp 写入完成之后、rename 之前调用，或者明确 `PREPARED` 的含义是 "准备阶段开始" 而非 "准备阶段完成"。

---

### P0-4: `atomic_manifest_commit` 是否独立 checkpoint 的定义缺失

**位置:** Task 11 Step 4 (lines ~1196-1200)

**问题:** 计划说：
- `commit_manifest()` owns `MANIFEST_COMMITTED`
- `StageOutcome.ok(checkpoint_owned=True)` 用于 `index_swap` 和 `atomic_manifest_commit`

但 `atomic_manifest_commit` 的 adapter 实现没有给出，且计划说它 "returns `StageOutcome.ok(checkpoint_owned=True)` only after `commit_manifest()` itself writes `MANIFEST_COMMITTED`"。这暗示 `commit_manifest` 既写 checkpoint 又返回 `checkpoint_owned=True`，但通用 loop 会跳过已 owned 的 checkpoint。需要明确：
1. `commit_manifest` 是直接写 journal checkpoint 还是通过 `tx.journal.commit_manifest()` 方法？
2. `atomic_manifest_commit` adapter 的完整实现签名是什么？

**建议:** 补充 `atomic_manifest_commit` adapter 的伪代码，明确其与 `commit_manifest()` 的交互方式。

---

## P1: 设计模糊或潜在冲突

### P1-1: `validate` stage 的具体检查内容未定义

**位置:** Task 11 Step 4 (lines ~1157-1158)

**问题:** `validate` 被描述为 "contains baseline/planning checks but makes no controlled mutation"，但没有具体列出需要检查什么。这可能影响测试编写和实现。

**建议:** 补充 `validate` stage 的检查清单，例如：
- `WIKI_PATH` 解析后是否为符号链接目标内的绝对路径
- 是否存在并发 lock 且未过期
- `MEMORY_HUB_DATA` 目录是否存在且可写
- 当前 Git baseline staged 状态是否为空

---

### P1-2: Spec 第 4.1 节 L0 召回数量与计划描述不一致

**位置:** Spec 第 4.1 节 vs Task 4 Step 3

**问题:** Spec 说 "L0 初召回"，计划说 "at most 12 each"（FTS 和 vector 各最多 12 个）。需要确认：
1. Spec 是否有明确的数字限制？
2. 如果 Spec 没有，计划中的 "12" 是否是一个需要回填的约束？

**建议:** 检查 Spec 第 4.1 节是否有具体数字；如果没有，在计划中添加引用并说明该数字的来源（可能是经验值）。

---

### P1-3: `run_pipeline` 在 `--no-auto` 模式下的行为

**位置:** Task 11 Step 3 (lines ~1135-1140)

**问题:** 计划说：
- `maintain --no-auto`: 只运行 deadlink/timestamp/link stages，仅在显式 `--apply` 时写入
- `run --no-auto`: 保留旧的 dry-run 行为，除非显式 `--apply`

但没有说明 `run_pipeline` 在 `--no-auto` 模式下的具体 stage 跳过逻辑。`run_pipeline` 当前只有 `execute_run_order` 的框架，没有详细的 stage 条件判断。

**建议:** 补充 `run_pipeline` 在 no-auto 模式下的 stage 过滤逻辑伪代码。

---

### P1-4: `rollback_transaction` 对 `prepared` 对象的影响未定义

**位置:** Task 8 Step 4 (lines ~841-848), Task 11 Step 4

**问题:** 如果 `index_swap` 阶段失败并触发 `rollback_transaction`，`prepare_successor_pages` 创建的 `PreparedLifecycle` 对象会发生什么？
- 它的 `tx` 引用是否仍然有效？
- 是否应该丢弃该对象并重新调用 `prepare_successor_pages`？
- 或者 `prepare_successor_pages` 应该检测 journal 状态并跳过已完成的部分？

**建议:** 明确 `rollback_transaction` 后的 `prepared` 对象状态，以及调用方是否需要重新调用 `prepare_successor_pages`。

---

## 验证状态

- ✅ 规格文档存在且结构完整 (369 行)
- ✅ 计划文档存在且结构完整 (1499 行)
- ✅ 14 个 Task 都有 Files、Interfaces、Steps 定义
- ✅ Spec Coverage Matrix 覆盖 Section 1-11
- ⚠️ P0-1 到 P0-4 需要在 Task 1 开始前澄清
- ⚠️ P1-1 到 P1-4 建议在 Task 1 期间记录但不阻塞

---

## 建议的下一步

1. **立即解决 P0-1 和 P0-2**：这两个问题影响 Task 8 和 Task 11 的实现边界，必须在开始编码前澄清。
2. **记录 P0-3 和 P0-4**：可以在 Task 8 实现时作为 design decision 记录。
3. **P1 系列**：在对应 Task 实现时按需澄清。
4. **开始 Task 1**：Schema/Frontmatter/Provenance 的实现不依赖上述问题的澄清。
