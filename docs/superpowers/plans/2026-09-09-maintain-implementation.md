# maintain 实施与验收计划

日期：2026-09-09。范围：本地工作区独立 `maintain` 命令；基于 2026-08-31 full-auto design 第 6 节。

目标：让独立维护完成可验证的页面维护、跨日聚类、索引更新、消费记录、归档和精确提交；失败时明确记录已恢复状态或恢复冲突。

## 工作包

1. CLI 使用真实 `MaintainStageRunner`。`run` 与 `maintain` 共用 automation lock，并在持锁后捕获 Git 基线。默认应用并提交，`--safe` 与无 `--apply` 的 `--no-auto` 只预览。
2. 固定阶段：`validate -> publish_pages_lifecycle -> index_swap -> lint -> atomic_manifest_commit -> archive -> exact_stage_commit`。复用现有模块，仅在编排处补齐实际执行和校验。
3. 所有待写路径先登记 before images；索引只切换一次；通过 lint 才更新 manifest；仅归档已完整消费的观察文件。无变化重跑不生成重复页面或空提交。
4. 收窄 Git 回滚：不使用 `reset --hard`；仅撤销操作拥有的提交/暂存与文件。遇到后续外部提交或修改冲突时保留外部状态，报告恢复未完成。
5. 更新 CLI help、README 和验收报告，准确区分独立维护与 `run` 内仍明确跳过的维护阶段。

## 验收

- 真实 shell CLI + 临时 Git wiki/data/staging/home，合成跨日 3 条观察，不调用生产数据或真实 LLM。
- safe/no-auto preview 前后 wiki、Git、索引、manifest、staging 内容一致；允许写操作报告与 journal。
- 默认执行产生合并页、有效 SQLite 索引、消费 manifest 和精确 Git 提交；重跑页数、manifest、HEAD 不变。
- 索引、lint、manifest、archive、stage/commit 注入失败，检查页面、索引、消费状态和归档的恢复。
- 锁占用退出 75；预先 staged 改动退出非零且原样保留；非法 safe/apply/commit 组合退出 2。
- Git 回滚保留无关 dirty 内容；后续外部提交的文件不得被 before images 覆盖；提交 hook 失败不遗留本操作暂存。
- 原相关 93 项基线测试本轮已通过；实现完成后运行新增及受影响测试，并独立复核最终工作区。

## 边界

本轮不修改真实 `llm-wiki`，不执行真实全链路，不改定时任务、全局 Codex 配置、插件部署或外部发布。`run` 内维护保持明确跳过，避免引入第二次索引切换和嵌套事务。完整 roadmap 中的 embedding 质量、跨进程崩溃恢复等能力需按实际证据分别验收，不由本轮测试推定完成。
