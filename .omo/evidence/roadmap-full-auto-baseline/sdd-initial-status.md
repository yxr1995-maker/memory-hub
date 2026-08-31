# SDD Initial Status: roadmap-full-auto

**Date:** 2026-08-31  
**Base Commit:** `893b688`  
**Baseline Commit:** `26c3c8e`  

## Artifacts Committed

| Artifact | Path | Status |
|---|---|---|
| Spec (approved baseline) | `docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md` | ✅ existing, reviewed |
| Plan (14 tasks) | `docs/superpowers/plans/2026-08-31-roadmap-full-auto.md` | ✅ committed |
| Plan Review | `docs/superpowers/reviews/2026-08-31-roadmap-full-auto-plan-review.md` | ✅ committed |
| SDD Ledger | `SDD-ledger.md` | ✅ committed |

## Blocking Findings (P0)

### P0-1: `run_pipeline` stage 集合定义模糊
- **位置**: Task 11 Step 4
- **问题**: 计划描述了两套 pipeline：
  - `run_pipeline`: `capture → distill → scope backfill → autolink → successor page preparation → one index swap → lifecycle finalization → archive → exact commit`
  - `maintain_pipeline`: `validate → publish_pages_lifecycle → index_swap → lint → atomic_manifest_commit → archive → exact_stage_commit`
- **影响**: Task 11 实现时无法确定 `run_pipeline` 是否应复用 `MAINTAIN_ORDER` 或定义独立顺序
- **建议**: 在 Task 11 开始前添加一张对比表，明确两个 pipeline 的 stage 映射关系

### P0-2: `prepared` 对象传递机制缺失
- **位置**: Task 8 Step 4, Task 11 Step 4
- **问题**: 
  - Task 8 的 `publish_successor_once(plan, tx, rebuild_index)` wrapper 内部创建 `prepared`
  - Task 11 的 `maintain_pipeline` 通过 `stages.index_swap_once_and_finalize(tx)` 调用，但没有说明 `prepared` 如何传入
  - 如果 `prepared` 从外部传入，需在 `publish_pages_lifecycle` 阶段保存引用
  - 如果 `prepared` 在 `index_swap` 内部重新计算，那 `publish_pages_lifecycle` 的 prepare 调用就是多余的
- **影响**: Task 8 和 Task 11 的实现边界不明确
- **建议**: 明确 `index_swap_once_and_finalize(prepared, tx)` 签名，或在 `maintain_pipeline` 中添加 `prepared = stages.prepare_lifecycle(tx)` 步骤

## Non-Blocking Findings (P1)

| ID | 描述 | 解决阶段 |
|---|---|---|
| P1-1 | `validate` stage 检查内容未定义 | Task 11 |
| P1-2 | Spec L0 召回数量与计划可能不一致（Spec 无具体数字） | Task 4 |
| P1-3 | `run --no-auto` 的 stage 过滤逻辑未定义 | Task 11 |
| P1-4 | `rollback_transaction` 后 `prepared` 对象状态未定义 | Task 8 |

## Execution Readiness

- [x] Spec 文档完整且已批准
- [x] Plan 文档完整且已审阅
- [x] Plan review 记录已创建
- [x] SDD ledger 已初始化
- [ ] P0-1 澄清完成（阻塞 Task 11）
- [ ] P0-2 澄清完成（阻塞 Task 8/11）
- [ ] Task 1 实现准备就绪

## Recommended Next Steps

1. **立即解决 P0-2**: 在 Plan 中添加 `StageRunner` 接口定义，明确 `prepared` 传递机制
2. **开始 Task 1**: Schema/Frontmatter/Provenance 实现不依赖 P0 澄清
3. **Task 1-7 可并行启动**: 这些 Task 之间是顺序依赖，但 Task 1 完成后 Task 2-3 可并行
