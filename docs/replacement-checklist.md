# memory-hub 完全替换 gbrain / claude-mem 清单

> 目标：用 memory-hub（bash + jq + sqlite FTS5，零常驻进程）替换 gbrain（postgres+pgvector）和 claude-mem（worker+MCP）。
> 原则：markdown 为源（~/llm-wiki），索引可随时重建；替换过程可回滚。

## 1. 功能对比矩阵

| 能力 | gbrain | claude-mem | memory-hub 替代实现 | 状态 |
|------|--------|-----------|---------------------|------|
| 会话采集 | ✗（不采集） | ✓ transcript watch | `capture`（解析 Codex/Claude Code JSONL） | ✅ |
| 观察记录 | ✗ | ✓ SQLite observations | staging/observations-*.jsonl（JSONL 格式） | ✅ |
| 分层蒸馏 | ✗ | ✗（原始观察，无分层） | `distill`（L0 摘要/L1 概述/L2 明细） | ✅ 独有 |
| 知识页发布 | ✓ put_page | ✗ | `publish`（type→目录映射，dry-run 默认） | ✅ |
| 全文检索 | ✓ tsvector | ✓ mem-search | `search`（SQLite FTS5 trigram + rg 回退） | ✅ |
| 语义/向量检索 | ✓ pgvector + RRF | ✓ 嵌入 | trigram FTS5（子串匹配近似，无向量） | ⚠️ 近似 |
| 问答 | ✓ ask/query | ✗ | `ask`（FTS5 检索 + 免费模型生成） | ✅ |
| AGENTS.md 注入 | ✗ | ✓ 自动注入 | `inject`（MARKER 区段） | ✅ |
| 索引索引管线 | ✓ import/sync/autopilot | ✓ worker 常驻 | `index`（手动/定时全量重建，3663 页 ~3 分钟） | ✅ |
| 多 IDE 采集 | ✗ | ✓ Claude Code/Cursor 等 | `capture --source codex` / `--source claude-code` | ⚠️ 2 源 |
| Web UI 浏览 | ✓ gbrain UI | ✓ localhost:37703 | Obsidian（同 vault，更强） | ✅ 替代 |
| 云同步 | ✗ | ✓ cmem.ai Pro | ✗（本地 markdown，可用 git） | ⚠️ 无 |
| 数据迁移成本 | — | — | 零（markdown 是源，索引重建即可） | ✅ |

## 2. 能力缺口与替代方案

| 缺口 | 替代方案 | 影响评估 |
|------|----------|----------|
| pgvector 语义向量 | trigram FTS5 子串匹配 + `ask` 用 LLM 理解意图后检索 | 关键词/短语检索基本等价；纯语义相似度（"找类似主题的页"）弱于向量 |
| 多 IDE（Cursor/Windsurf） | 目前只支持 Codex + Claude Code JSONL | 如只用这两个 IDE 则无影响；其他 IDE 需扩展 capture 源 |
| Web UI | Obsidian（同 llm-wiki vault，双向链接/图谱更强） | 替代后体验更好 |
| 云同步 | llm-wiki 用 git 备份（`gbrain export` 已有 markdown，git 更通用） | 本地 git 比云同步更可控 |
| 实时捕获（worker 常驻） | `capture --watch` 定时循环 / launchd 定时 | 30-60 秒延迟 vs 实时，可接受 |

## 3. 替换步骤（建议按序执行）

```bash
# 第 0 步：并行验证（不动现有插件，先对比一周）
cd /Users/earan/Documents/memory-hub
./memory-hub.sh index          # 建 FTS5 索引（3663 页）
./memory-hub.sh search "关键词" # 与 gbrain query 对比结果
./memory-hub.sh ask "问题"      # 与 gbrain ask 对比回答

# 第 1 步：备份 gbrain 数据（可回滚的前提）
PATH="$HOME/.bun/bin:$PATH" gbrain export --dir ~/gbrain-backup-$(date +%Y%m%d)/

# 第 2 步：停用 claude-mem（保留数据，先观察）
npx claude-mem stop            # 停 worker（37703）
# 如需完全卸载（确认稳定后再做）：
# npx claude-mem uninstall     # 待验证：确认此命令保留 ~/.claude-mem 数据

# 第 3 步：停用 gbrain MCP（编辑 ~/.codex/config.toml 注释掉 gbrain 段）
# 手动备份 postgres 数据目录（gbrain 的数据在 ~/.gbrain/ 或 postgres 实例）

# 第 4 步：把 memory-hub 加入日常流程
# launchd 定时跑 index + run（示例见第 5 节）
```

## 4. 回滚方案

如果 memory-hub 验证不达标：

```bash
# 恢复 gbrain：重新 import 备份的 markdown
PATH="$HOME/.bun/bin:$PATH" gbrain import ~/gbrain-backup-YYYYMMDD/

# 恢复 claude-mem：重新启动 worker（数据未动）
npx claude-mem start

# memory-hub 是纯脚本，不产生需要清理的常驻状态；~/.memory-hub/index.db 可随时删除重建
```

## 5. 定时化（launchd 示例，待验证）

```bash
# ~/Library/LaunchAgents/com.user.memory-hub.plist
# 每日 09:00 跑 capture → distill → publish → index
# 命令：cd .../memory-hub && ./memory-hub.sh run --apply --llm && ./memory-hub.sh index
```

## 6. 验证标准（替换前必须全部通过）

- [ ] `search "memoryhub"` FTS5 命中数 ≥ gbrain search 命中数的 80%
- [ ] `ask "memory-hub 踩坑"` 回答内容包含真实页面信息（非编造）
- [ ] `capture --source codex` 与 `capture --source claude-code` 均产出非零观察
- [ ] `run --apply --llm` 发布后 index.md / log.md 正确更新
- [ ] 索引全量重建时间 < 5 分钟（3663 页）
