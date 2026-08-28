# 分层上下文架构与借鉴落地规范 (Tiered Context Architecture)

## 1. 背景与借鉴来源

在大型语言模型与自主 Coding Agent（Codex、Claude Code、OpenClaw 等）的协作场景中，将完整的历史会话与全部知识库无差别注入上下文窗口会导致严重的 Token 膨胀与模型注意力分散（Lost in the Middle）。

`memory-hub` 吸收了业界主流 Agent Memory 系统的核心设计：

1. **OpenViking (volcengine/OpenViking)**:
   - **L0 (Abstract)**: 极轻量摘要（~100 tokens），用作索引路由与意图初筛。
   - **L1 (Overview)**: 结构化大纲与决策背景（~2,000 tokens），用作任务规划与上下文导航。
   - **L2 (Details)**: 底层完整观察记录与日志代码，仅在 Agent 明确需要深入调查时按需展开。

2. **claude-mem (thedotmack/claude-mem)**:
   - 生命周期 Hook 自动捕获与增量提炼。
   - 渐进式注入（Progressive Injection）：会话启动阶段仅注入高层索引，深层内容由 Agent 按需调用 MCP/CLI 检索。

3. **gbrain & OpenWiki**:
   - Markdown 为唯一事实源（Single Source of Truth）。
   - 强类型 YAML Frontmatter、可溯源标记（✅/⚠️/❓）与原子索引无损重建。

---

## 2. memory-hub 的分层数据流

```
Codex / Claude Code 会话流 (*.jsonl)
       │
       ▼ capture (增量游标 + 去重)
staging/observations-*.jsonl
       │
       ▼ distill (分层蒸馏)
~/llm-wiki/*.md (含 L0 Frontmatter + L1 概述 + L2 明细)
       │
       ├─▶ inject (只注入 L0/L1 轻量索引至 AGENTS.md, ~100 tokens)
       ├─▶ search / fuse (FTS5 + 向量 RRF 混合检索)
       └─▶ export [--tier l0|l1|l2|full] (按需抽取指定深度提供给 Agent/外部管道)
```

---

## 3. 分级抽取规范

| 级别 | 范围与内容 | 适用场景 | Token 开销估算 |
|---|---|---|---|
| **L0 (Abstract)** | Frontmatter 中的 \`abstract\` 字段或首段精炼摘要 | 系统 Prompt 启动常驻、多 Agent 广播、快速路由 | 50 ~ 150 tokens |
| **L1 (Overview)** | \`## 概述 (L1)\` 章节 + AI 总结 + 结构化元数据 | Agent 任务规划、背景调研、架构决策回顾 | 300 ~ 1,500 tokens |
| **L2 (Details)** | \`## 观察明细 (L2)\` 章节 + 原始日志/代码片段 | 根因排查、测试复现、代码重构深潜 | 2,000 ~ 10,000+ tokens |
| **Full** | 包含 Frontmatter 与完整 Markdown 正文 | 全量冷备份、人工 Obsidian 浏览阅读 | 全文 |

---

## 4. CLI 使用示例

```bash
# 仅导出 L0 摘要用于轻量提示词注入
./memory-hub.sh export --tier l0 --format jsonl --output /tmp/l0-summary.jsonl

# 仅导出指定项目的 L1 概述用于任务背景分析
./memory-hub.sh export --project my-project --tier l1 --format markdown

# 全量导出指定类型决策为 JSON 备份
./memory-hub.sh export --type decision --tier full --format json -o decisions.json
```
