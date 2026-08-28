# memory-hub — All-in-One Agent 记忆 CLI

**目标：完全替换 gbrain 和 claude-mem。** 以 Markdown 为源、索引可随时重建；核心 CLI 不需要常驻服务，但需要下列本地命令行依赖。

自动采集 Codex 会话 → 分层蒸馏 → 发布到 `~/llm-wiki`（Obsidian 浏览 + FTS5/rg/向量检索）→ 检索 / 注入 / 状态，一个 CLI 全闭环。本地数据默认不离开机器；只有显式使用 `--llm` 或其他可选集成时才会访问配置的服务。

```
~/.codex/sessions/**/*.jsonl  ──capture──▶  staging/observations-*.jsonl
                                               │  (id/project/role/type/text/时间)
                                               ▼ distill（L0摘要 → L1概述 → L2明细）
                                         staging/pages/*.md
                                               │  publish（frontmatter校验 + type→目录映射）
                                               ▼
                                         ~/llm-wiki/   ← Obsidian 浏览 / FTS5+rg+向量检索
                                               │
              ┌────────────────┬───────────────┼───────────────┐
              ▼                ▼               ▼               ▼
          search (FTS5/rg/fuse)  inject (AGENTS.md)  status       watch（定时）
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
| `distill` F1 冲突检测 | wiki `drafts/memoryhub/` 已存在同 project 页时，新页标 `status: candidate` + `contested: true`，并生成 `reports/conflicts/<slug>.md` 对照报告；publish 跳过 candidate 页，绝不覆盖已存在页 |
| `index [--with-raw]` | 把 ~/llm-wiki 索引进 SQLite FTS5（trigram 中文分词）→ `~/.memory-hub/index.db`（3663 页 ~3 分钟，全量重建） |
| `eval [--top N]` | 自评测基准(F3+U2): 跑 `evaluation/golden.jsonl` 的 31 条「问题→预期页」对,算 hit@N 与 MRR,按 AML 四类 A/C/D/G 给出能力画像,写 `reports/eval-<date>.md` |
| `archive [--keep N] [--apply]` | 归档已消费的观察文件(F4): `staging/observations-*.jsonl`(仅日期命名,不含 realtime/test) 除最新 N 份外全部移入 `staging/archive/`(可恢复,不 rm); 默认 dry-run; `run --apply` 成功后自动执行 |
| `ask "问题" [--top N]` | 知识库问答（替代 gbrain ask）：FTS5 检索 + 免费模型生成（默认 sensenova/sensenova-6.8-flash-lite） |
| `export [--project X] [--type Y] [--tier l0|l1|l2|full]` | 结构化分层导出知识库页面为 JSONL / JSON / 合并 Markdown 归档，支持 L0/L1/L2 上下文深度裁剪（详见 [分层架构说明](docs/architecture-tiered-context.md)） |
| `publish [--apply]` | `staging/pages` → `~/llm-wiki` 按 type 映射目录（decision→decisions/ 等）；默认 dry-run；frontmatter 五字段校验；永不覆盖已存在页面；`--apply` 更新 index.md/log.md（log 防重复） |
| `search "词" [--top N] [--raw] [--all] [--gbrain]` | rg 全文检索（默认排除 raw/_legacy-para）；`--gbrain` 优先 gbrain 混合检索，失败回退 rg |
| `search "词" --fuse [--top N] [--tau N]` | FTS5 bm25 + 向量 RRF 融合检索(F2)；中文 3-gram 滑窗拆词适配 trigram 索引；`--tau` 时间衰减天数(默认 90,`--tau 0` 关闭,新页优先)；type 加权: entity/concept ×1.8, atom/query/draft ×0.7 (hit@5 0.710→0.871) |
| `inject [--apply --file X] [--project NAME]` | 记忆上下文注入（对齐 claude-mem AGENTS.md 注入）：默认输出 Markdown 到 stdout（知识库最近 5 页[长期库] + 本会话观察[staging 按 project 分离，U3] + 统计）；`--project` 指定本会话 project（缺省自动推断）；`--apply` 写入指定 AGENTS.md 的 MARKER 区段，重复运行只替换区段 |
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
| volcengine/OpenViking（31.2k★，2026-08-21 实测） | L0/L1/L2 三层加载省 token、会话→长期记忆；定位"Self-evolving Context Database"，统一 Agent Memory / 知识 RAG / Skills |
| TencentDB-Agent-Memory（19.2k★） | 会话→原子→场景分层蒸馏、资产与框架解耦 |
| agent-memory-vault（233★） | Markdown 为源 + 索引可重建、可验证写入 |

## 依赖

核心命令需要以下本地工具：Bash、`jq`、`rg`、`curl`、Python 3（含标准库 `sqlite3`）和 `sqlite3` CLI（带 FTS5）。项目不会自动安装它们。

以下能力按需安装或配置：

- MCP 服务：Python 包 `mcp`（FastMCP）；不使用 MCP 时无需安装。
- 向量检索：Python 包 `fastembed` 和 `numpy`；不运行 `embed` 或 `search --fuse` 时无需安装。
- LLM 摘要/问答：可访问的 OpenAI-compatible 本地或远程端点；仅 `--llm`、`ask` 与查询扩展功能需要。请求会发送到你配置的端点。
- `gbrain`：仅 `search --gbrain` 的可选混合检索需要。

## 本地草稿与 launchd 模板

`~/llm-wiki/drafts/memoryhub/` 保存从本地会话生成、尚未人工公开整理的运维草稿。它不是公开内容区，`verify` 的凭据扫描会精确跳过该目录；其他 `drafts/` 子目录仍在扫描范围内。发布到公开仓库前，请单独审阅或排除该草稿目录。

`deploy/com.memctl.watch.plist` 是模板，不含个人路径。复制后将 `__MEMORY_HUB_ROOT__` 替换为本机仓库绝对路径，再按自己的 launchd 流程安装；不要直接加载未替换的模板。

## 安全边界

- 发布默认 dry-run；永不覆盖已存在页面；不删除任何知识库文件。
- AI 生成内容一律标 ⚠️待核实；`inject` 默认只输出 stdout，不写任何文件。
- 只写 `queries/` 等时间戳页，人工策展的 entities/concepts 页面不受管道影响。
