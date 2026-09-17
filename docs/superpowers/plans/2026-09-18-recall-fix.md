# memory-hub 调用率与信噪比修复计划

## 概要

四项相互独立的修复：A 挂 MCP 让 Codex 能主动查；B 向量索引从 0 行空转变为生效并进管道；C 钩子召回从裸 FTS 升级为混合检索（含看门狗/排序链路修复）；D atom/note 碎片页降权。改动约 5 个源码文件 + 1 处全局配置 + 1 处插件缓存同步。

## 全局约束

- 不动 MAINTAIN_ORDER/MAINTAIN_CHECKPOINTS；不动存量 wiki 文件；不动蒸馏端与 UI
- embed 只走 subprocess（python3），显式透传 WIKI_PATH 与 MEMORY_HUB_DATA；--safe 严禁触发；失败只 warn 不阻断
- CI 无 fastembed：涉及 embed 的测试必须 mock subprocess
- 全量 pytest 不得低于 669 passed 基线

## Task A: config.toml 挂 MCP（主 agent 已执行完毕）

- 备份 config.toml.bak-20260918；追加 [mcp_servers.memory-hub] command="bash" args=["/Users/earan/Documents/memory-hub/plugins/memory-hub/mcp/launch.sh"]
- 验证：tomllib 解析通过 + verify.sh 第 3 节 PASS（已完成）。重启会话生效。

## Task B: 向量索引进管道 + 存量回填

- scripts/automation_cli.py：run 成功末尾与 maintain 成功返回后各触发一次增量嵌入（subprocess 调 scripts/embed.py index，env 透传 WIKI_PATH/MEMORY_HUB_DATA，timeout=60，safe 不触发，失败仅 stderr warn）。
- 一次性回填：./memory-hub.sh embed index（13,322 页，主 agent 后台跑）。
- 验收：vec 行数 == pages 行数；search --fuse 返回向量候选；新测试：run apply 触发 embed（mock subprocess）、safe 不触发。

## Task C: 钩子召回升级（三处联动）

- scripts/automation_core/codex_memory.py：_recall 的 SearchRequest 改 fuse=True；L249 注入预算 1200→3000 字节；L265 看门狗 setitimer 0.8s→5.0s；_load_pages 加载 FTS+向量双路候选（去重）；L226 重排改为信任 service.search 的 RRF 融合序，仅附加当前项目 scope 优先。
- plugins/memory-hub/hooks/hooks.json：UserPromptSubmit timeout 3s→8s；同步覆盖 ~/.codex/plugins/cache/personal/memory-hub/2.0.0+codex.20260912143857/hooks/hooks.json。
- 测试适配：tests/test_codex_hooks.py L222（放行本地向量）、L269（预算断言改 3000）、L86（超时用例适配 5.0s）。
- 验收：手动跑 codex_hook_entry.py UserPromptSubmit，「WeKnora 是什么」「OpenCodex 补丁」等查询有带来源标注的注入；「继续」仍不注入；vec 为空时退化单通道不报错。

## Task D: 碎片页降权

- scripts/automation_core/ranker.py 打分循环：降权组 (atom, query, draft) ×0.7 改为 (atom, query, draft, note) ×0.3。
- tests/test_ranker_lifecycle.py 更新受影响断言 + 新增 note/atom 0.3 倍断言。

## 评测门控（B+D 落地后）

- 配对 eval（同 36 golden、top=5）写 reports/eval-2026-09-18-paired.md：hit@5 ≥ 9/36、MRR ≥ 0.1259、L 类保持 5/5。

## 不做

- 不动蒸馏端产量、不做 P3 主题页、不动 dashboard UI、不动存量 wiki 文件（回填只写 index.db 派生表）
- Obsidian 浏览层只给指引，不写代码
