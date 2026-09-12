# 独立 maintain 实现验收

日期：2026-09-09。基线 HEAD：`cdeaf2c53afa5152abe70d853c7a28d40cb54fa4`；验收对象为其上的本地未提交工作区，不能把 HEAD 本身视为本轮实现版本。

## 已交付行为

- 独立 `maintain` 使用真实 runner，固定七阶段，默认应用并精确提交。
- 页面修复仅补缺失 created/updated，按文件 mtime 推定；Git dirty/untracked、raw/archive 页面跳过。
- 本地跨日聚类去重 observation id、拒绝无项目键输入、复用敏感文本清洗、来源 ID 哈希化、摘要引号转义，并标记待核实。
- 页面只新增，单次索引切换，通过页面/hash/SQLite 检查后更新 manifest；只归档完整消费文件。
- safe/no-auto preview 保持 wiki/Git/index/manifest/staging 原样。诊断 report/journal 允许写入。
- run/maintain 共用锁；锁忙退出 75，非法模式退出 2，已有 staged 改动非零失败并保留原状态。
- 回滚不使用 reset --hard；恢复本操作文件，保留无关 dirty；外部修改冲突和 own commit 后的后续提交会报告恢复未完成。

## 实测记录

1. 变更前相关基线：93 passed。
2. 新 CLI 与 maintain pipeline：22 passed，8.49 秒。
3. 受影响原有测试与回滚新用例：99 passed，14.91 秒。
4. 全套 `uv run python -m pytest tests/ -q`：246 passed，6 subtests passed，61.89 秒。
5. 全套之后新增“own commit 后有外部提交时保留整棵已提交树并报告冲突”保护和测试；对应 `test_operation_rollback_ownership.py`、`test_operation_safety.py`、`test_maintain_pipeline.py`：22 passed，6.59 秒。未重复全套。
6. Bash 语法、四个改动 Python 模块编译、相关 diff whitespace 检查通过。

当前最终源码独立 shell 验收详见 [cli-acceptance.json](../evidence/maintain-2026-09-09/cli-acceptance.json)，包含逐文件 SHA256：safe 退出 0 且受控状态一致；3 条观察、2 个日期产生 1 张合并页；归档 2 个 JSONL；索引切换 1 次、2 页、integrity=ok；临时 Git 提交成功；replay 不改变 HEAD/manifest。所有数据均为合成临时 fixture，真实 llm-wiki 写入数为 0。

验收脚本首次使用系统 python3 因缺 pytest 而未运行，改用仓库 `uv run python` 后完整通过；未安装系统依赖。

## 独立复核与限制

- CLI 复核发现 run 非法模式抛 traceback，已修复并为 run/maintain 参数化覆盖。
- 回滚复核和 maintain 复核未返回阻塞项。复核发生于共享工作区实现过程，曾读取中间版本；最终可复现证据以附带源码哈希的 CLI 验收及主执行者最后的相关测试为准，不能当作已发布版本认证。
- 首轮代理误回旧摘要及推进过慢，未采纳其完成结论；重新隔离上下文分派后，由主执行者完成核心实现及最终集成。代理完成测试补充、部分回滚补丁和独立只读复核。
- 仍保留的范围限制：run 内维护明确 skipped；全库死链修复/链接回填、真实 embedding 聚类、一般语义 successor 判定、跨进程崩溃恢复及生产自动化部署未在本轮完成或认证。旧词法 successor 不足以安全废弃旧页，runner 采用保守候选选择。
- 未 commit/push 本仓库，未改定时任务或全局配置；工作区原有其他改动不在本轮验收范围。
