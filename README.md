# memory-hub — All-in-One Agent 记忆 CLI

**目标：完全替换 gbrain 和 claude-mem。** 自包含（bash + jq + sqlite FTS5），零常驻进程，markdown 为源，索引可随时重建。

自动采集 Codex 会话 → 分层蒸馏 → 发布到 `~/llm-wiki`（Obsidian 浏览 + gbrain 检索）→ 检索 / 注入 / 状态，一个 CLI 全闭环，本地运行、零外部依赖。

```
~/.codex/sessions/**/*.jsonl  ──capture──▶  staging/observations-*.jsonl
                                               │  (id/project/role/type/text/时间)
                                               ▼ distill（L0摘要 → L1概述 → L2明细）
                                         staging/pages/*.md
                                               │  publish（frontmatter校验 + type→目录映射）
                                               ▼
                                         ~/llm-wiki/   ← Obsidian 浏览 / gbrain 检索
                                               │
              ┌────────────────┬───────────────┼───────────────┐
              ▼                ▼               ▼               ▼
          search (rg/gbrain)  inject (AGENTS.md)  status       watch（定时）
```

## 快速开始

```bash
./memory-hub.sh run              # 全链路 dry-run（安全预览，不写知识库）
./memory-hub.sh run --apply      # 真正发布到 ~/llm-wiki + 更新 index/log
./memory-hub.sh run --apply --llm  # 发布时用本地免费模型生成 AI 摘要
```

## 命令

| 命令 | 说明 |
|------|------|
| `capture` | 解析 `~/.codex/sessions/**/*.jsonl` → `staging/observations-*.jsonl`。自动兼容新旧会话格式；用户消息剔除系统注入噪音（`<recommended_plugins>/<environment_context>` 等）；提取 tool 事件为 `type=tool` 观察；增量游标 `staging/.since` + 内容去重 `staging/.seen`。`--all` 全量 / `--since <ms>` / `--watch` 每 60 秒循环 / `--source claude-mem` 旧 SQLite 兼容  / `--source claude-code` 解析 Claude Code 会话 / `--source workbuddy` 解析 WorkBuddy 会话（`~/.workbuddy/projects`，id 前缀 `w`，独立游标）|
| `distill [--llm]` | 按 project 分组生成合规 wiki 页 → `staging/pages/`。frontmatter 含 `title/type/created/updated/abstract/tags/sources/confidence/contested/status/last_verified`；type 智能映射（决策→decision、失败→failure、对比→comparison、默认 concept）；正文 L0 摘要 + L1 概述 + L2 明细（>60 条自动分页）；AI 摘要标 ⚠️待核实；出链 `[[index]] [[log]]` + 同项目分页互链 |
| `index [--with-raw]` | 把 ~/llm-wiki 索引进 SQLite FTS5（trigram 中文分词）→ `~/.memory-hub/index.db`（3663 页 ~3 分钟，全量重建） |
| `ask "问题" [--top N]` | 知识库问答（替代 gbrain ask）：FTS5 检索 + 免费模型生成（默认 sensenova/sensenova-6.8-flash-lite） |
| `publish [--apply]` | `staging/pages` → `~/llm-wiki` 按 type 映射目录（decision→decisions/ 等）；默认 dry-run；frontmatter 五字段校验；永不覆盖已存在页面；`--apply` 更新 index.md/log.md（log 防重复） |
| `search "词" [--top N] [--raw] [--all] [--gbrain]` | rg 全文检索（默认排除 raw/_legacy-para）；`--gbrain` 优先 gbrain 混合检索，失败回退 rg |
| `inject [--apply --file X]` | 记忆上下文注入（对齐 claude-mem AGENTS.md 注入）：默认输出 Markdown 到 stdout（知识库最近 5 页 + 最新观察 10 条 + 统计）；`--apply` 写入指定 AGENTS.md 的 MARKER 区段，重复运行只替换区段 |
| `status` | 健康检查：Codex 会话数/最新会话、staging 观察与游标、llm-wiki 页面数/最近更新、claude-mem DB、本地 LLM 代理可达性 |
| `verify` | 静态漂移校验（CI 用）：49 个 automation.toml 全量 tomllib 解析、hooks.json 引用脚本存在、config.toml MCP 指向真实文件、memory-hub 相关 automation 与 DB 记录一一对应。全过 exit 0 |
| `metrics` | Prometheus 文本输出（兼容 node_exporter textfile）：即时统计 + `~/.memory-hub/timings.tsv` 阶段耗时（capture/distill/publish/index/embed 的 count/sum/last） |
| `serve [--port 8787]` | REST 查询服务（stdlib，零依赖）：`/health` `/status` `/search?q=&top=&expand=` `/ask?q=` `/metrics`，供外部 agent 查询知识库/向量索引 |
| `watch` | 每 60 秒 capture → distill 循环，Ctrl-C 退出 |
| `run [--apply] [--llm]` | 一键全链路 |

## 特性来源（2026-08-10 调研）

| 来源项目 | 借鉴特性 |
|----------|----------|
| claude-mem（本地 v13.12.4） | transcript watch 事件模型、observations 字段（role/type/text）、AGENTS.md 注入、status 健康检查 |
| gbrain（本地 0.42.73.2） | 5 维知识结构、frontmatter 规范、双标注（✅/⚠️/❓）、出链、index+log |
| langchain-ai/openwiki（14.8k★） | 确定性采集→合成两阶段、OKF 风格 frontmatter、connector 扩展 |
| volcengine/OpenViking（28.1k★） | L0/L1/L2 三层加载省 token、会话→长期记忆 |
| TencentDB-Agent-Memory（19.2k★） | 会话→原子→场景分层蒸馏、资产与框架解耦 |
| agent-memory-vault（233★） | Markdown 为源 + 索引可重建、可验证写入 |

## 依赖

- macOS bash 3.2、`jq`、`rg`（必选）
- `curl`（可选，`--llm` 摘要用）
- `sqlite3`（可选，`--source claude-mem` 兼容用）
- 本地 LLM 代理 `127.0.0.1:10100`（可选，`--llm` 默认用 sensenova/sensenova-6.8-flash-lite 免费模型（稳定），可用 `CLAUDE_MEM_MODEL` 环境变量覆盖；失败自动降级为统计描述）
- `gbrain`（可选，`search --gbrain` 混合检索用）

## 安全边界

- 发布默认 dry-run；永不覆盖已存在页面；不删除任何知识库文件。
- AI 生成内容一律标 ⚠️待核实；`inject` 默认只输出 stdout，不写任何文件。
- 只写 `queries/` 等时间戳页，人工策展的 entities/concepts 页面不受管道影响。
