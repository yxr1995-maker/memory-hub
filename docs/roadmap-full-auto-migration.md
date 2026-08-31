# Roadmap B 激进全自动路线迁移指南

## 1. 核心变化与新默认值

- `run` 与 `maintain` 的默认行为变为 `auto=on, apply=on, commit=on`。
- `search` 与 `ask` 融合检索默认开启智能扩词 (`expand=1, fuse=1`)。
- 存量 ~12,900 页面支持确定性 scope 推断与断点续跑回填 (`scope-backfill`)。
- 知识生命周期引入全自动 successor 替代与时态废弃调权 (`deprecated_by`)。
- 跨日碎片观察自动聚类与合并蒸馏 (`maintain`)。

## 2. 退出开关与回退方式

- `--safe`：只读模式，运行解析与查询规划，不修改 wiki 文件或 manifest，不提交 Git。
- `--no-auto`：禁用自动 scope 回填、智能关联、successor 与跨日聚类。

## 3. 三阶段迁移步骤

1. 部署代码：读取器兼容新旧 schema (旧库自动填充 project/default-project 与 low confidence)。
2. 原子索引重建：运行 `memory-hub.sh index`。
3. 验证与全自动执行：运行 `memory-hub.sh run --safe` 验证，然后执行 `memory-hub.sh run`。
