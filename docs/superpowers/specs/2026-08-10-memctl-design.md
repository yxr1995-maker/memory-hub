# memctl 设计：All-in-One Agent 记忆 CLI

## 定位

一句话：一个自包含的 Agent 记忆 CLI —— 自动采集 Codex 会话、分层蒸馏成知识页、内置检索、上下文注入，全部本地运行，免费模型可选加速。

## 设计依据（开源项目调研 2026-08-10）

| 来源 | 借鉴特性 |
|---|---|
| claude-mem (v13.12.4, 本地已装) | transcript-watch 事件模型、observations 字段（facts/narrative/concepts/files_read/files_modified）、mem-search、AGENTS.md 注入、status 健康检查 |
| gbrain (0.42.73.2, 本地已装) | 5 维知识结构、frontmatter 6 字段、双标注（✅/⚠️/❓）、出链、index.md/log.md、hybrid search |
| langchain-ai/openwiki (14.8k★) | 确定性采集→agent 合成两阶段、OKF 格式 frontmatter、connector 扩展、本地可视化 |
| volcengine/OpenViking (28.1k★) | L0 摘要/L1 概述/L2 详情三层加载、会话→长期记忆异步提取、可观测检索 |
| TencentDB-Agent-Memory (19.2k★) | 四类记忆资产（会话/技能/wiki/代码图）、L0-L3 分层蒸馏、资产与框架解耦 |
| vectorize-io/hindsight (19.4k★) | 学习型记忆（偏好/决策提取） |
| mcncarl/agent-memory-vault (233★) | Markdown 为源 + 轻量索引可重建、可验证写入、memoryctl CLI 形态 |

## 架构

```
~/.codex/sessions/**/*.jsonl  ──capture──▶  staging/observations-*.jsonl
                                              │  (id/project/role/text/type/created_at)
                                              ▼ distill (L0摘要→L1概述→L2明细)
                                        staging/pages/*.md
                                              │  publish (frontmatter校验+目录映射)
                                              ▼
                                        ~/llm-wiki/  (Obsidian vault, Markdown 为源)
                                              │
                ┌──────────────┬──────────────┼──────────────┐
                ▼              ▼              ▼              ▼
            search (rg)   inject (AGENTS.md) status        watch (定时)
```

## 命令设计

```
memctl capture [--all|--since ms|--watch]  采集 Codex 会话 → staging（噪音过滤 + tool 事件）
memctl distill [--llm]                     分层蒸馏 → staging/pages/
memctl publish [--apply]                   发布 → ~/llm-wiki（默认 dry-run，不覆盖已存在页）
memctl search "词" [--top N] [--raw|--all|--gbrain]  内置全文检索
memctl inject [--apply --file X]           记忆上下文注入（MARKER 区段）
memctl status                              健康检查/统计
memctl watch                               定时采集循环
memctl run [--apply] [--llm]               一键全链路
```

## 关键决策

1. **Markdown 为源**：知识库就是 ~/llm-wiki 普通 Markdown，Obsidian 纯浏览，索引可随时重建（agent-memory-vault 思路）。
2. **免费模型优先**：distill --llm 走本地 opencodex 代理 127.0.0.1:10100/v1（opencode-zen/deepseek-v4-flash-free），零 K2 费用；失败静默降级为统计描述。
3. **噪音过滤**：用户消息剔除系统注入块（recommended_plugins/environment_context/apps_instructions/permissions/skills 等 XML 风格标签）。
4. **分层蒸馏**：每页 frontmatter 带 abstract（L0），正文"概述"（L1）+ "观察明细"（L2），>60 条自动分页。
5. **类型映射**：观察内容关键词 → decision/failure/concept 等页面类型，对应 ~/llm-wiki 目录。
6. **不侵入**：publish 默认 dry-run、不覆盖已存在页；inject 默认 stdout。
