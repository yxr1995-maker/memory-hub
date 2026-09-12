# memory-hub 原生 Codex 更新 Review

核验日期：2026-09-08。审查对象为当前未提交工作树，HEAD 为 `619f1727edfb2ee24ae43931d86791e17cacf266`；HEAD 并不包含本次所有实现。文件哈希见证据 JSON。本轮只读评审，未修改实现、真实队列、知识库或自动发布开关；仅保存审查报告。临时复现使用 TemporaryDirectory 并已清理。

## 结论

基本接入链路已有实际运行证据，但本次审查不通过无保留生产验收。优先修复脱敏缺口、草稿误召回及来源截断；随后改进无效批次处理和版本化交付。没有发现证据证明真实秘密已经泄露，不能把合成复现表述为真实泄露事件。

## 已复现问题

### P1：多行秘密能够进入模型请求

位置：`scripts/automation_core/codex_memory.py:47`（clean_text），`scripts/automation_core/memory_worker.py:263`（混合批次发模型）。

合成 PEM 块和多行 PRIVATE_KEY 值未完整脱敏。真实临时 Hook 采集 → 队列 → worker → 本地 urlopen 拦截复现确认：占位 marker 同时存在于队列和组装后的模型请求体中。请求在本地被拦截，实际网络请求为 0，未使用任何真实秘密。

建议：优先按敏感结构整块移除 PEM/SSH 私钥、多行赋值和嵌套凭据；采集与出站分别执行校验，发现无法可靠清洗的秘密则留本地隔离，不发送。一般非发布证据工具输出不要随一个合格用户偏好整批外送。现有清洗正则还会把 title/status 等普通字段误脱敏，需同时约束字段语义。

### P1：未审核草稿被当成自动记忆召回

位置：`scripts/automation_core/codex_memory.py:184`（OR 查询构造）、`:209`（只按状态排除）。

复制真实索引到隔离数据目录进行检索：`更新成功了吗` 的 5 个来源有 4 个位于 drafts/；第一个来源是旧视觉工具成功记录。当前 Review 提问命中旧 TikTok 草稿。草稿被索引为 active 就能通过排除列表，当前项目仅在分数相同时优先。历史资料标签并不能解决事实相关性问题。

建议：自动召回只使用明确允许的已审核状态和来源集合；候选/草稿保留主动 MCP 查询能力。给“继续、成功了吗、Review 一下”等弱语义提问增加弃答条件；按实体/项目/查询覆盖率设门槛，不能仅凭某个三元词命中即注入。保持前台本地 FTS 和一秒预算。

### P2：上下文预算会截断来源路径

位置：`scripts/automation_core/codex_memory.py:216`。

实现是 1,200 UTF-8 字节硬切，不是约 1,200 tokens 的条目预算。真实索引副本中，本轮 Review query 的最后来源被切为 `drafts/memo`，无法追溯。另一条 query 丢失末条状态字段。

建议：以完整条目为单位分配预算，先保证来源、项目、日期、状态，再缩短正文；装不下就不追加该条，不把整个拼接字符串硬切。

### P2：已判定无需模型的批次仍扫描整个知识库

位置：`scripts/automation_core/memory_worker.py:263` 至 `:273`。

`proposals=[]` 后仍无条件 rglob、read_text 和 parse_page。临时库 1 条不合格助手记录、50 个无关页面，复现 50 次全文读取。正式 15:39 快照最近一批 gateway_skipped=true、20 candidates，耗时 10.20 秒。记录中 5,803 条 pending 里 4,540 条为工具输出（约 78%）。

建议：无候选时直接完成本地候选落盘；其余批次从索引加载需要的元数据并缓存，避免每分钟扫全库。对不可能成为发布证据的纯日志建立独立低成本处理通道。当前每批 20 条的上限在持续新增时可能无法消化积压，应监控到达量与处理量，不仅监控进程存活。

### P2：doctor 历史验收标志未绑定当前部署

位置：`scripts/automation_core/codex_integration.py:98` 至 `:109`。

临时目录只有字段完整的 2000 年验收 JSON，没有安装记录、没有索引，doctor 仍返回 model_use_verified=true。它证明存在历史验收声明，不证明当前代码已被客户端使用。这不是身份认证漏洞。

建议：分开 historical_acceptance 与 current_build_verified；验收记录绑定插件版本、worker/Hook 哈希、实际客户端版本、测试会话和时间，部署改变后明确显示需复验。索引健康还应区分 SQLite 可读与已同步到最新发布 commit。

### P2：新增核心实现尚未形成可复现 Git 发布

`git ls-files` 对 codex_memory.py、memory_worker.py 和插件 hooks.json 返回空，`git status` 显示新模块、插件和测试为未跟踪文件。本机已安装运行与源码已提交发布是不同状态。

建议：下一次修复完成后，按本次所有权边界精确提交源码、测试和插件，使用干净检出执行安装/MCP/Hook 验收；保留现有无关工作树改动，勿将它们混入提交。

## 官方机制研究

核验来源：[OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)。这里只确认当前官方支持，不推断这些功能是今天新增的。

- SessionStart 的 source=compact 已能在压缩后、下一次模型请求前恢复上下文。当前无 matcher 的 SessionStart 已覆盖，宜补真实压缩验收，不必重复再加规则注入。
- Interrupt 可用于被用户打断轮次的轻量落盘，建议评估补充，继续复用去重与本地队列。
- additionalContextLimit 是可用的输出预算设置，但不能替代应用侧完整条目裁剪。
- mcp_tool Hook 可复用既有 MCP 连接；SessionEnd 不支持它。可做前台召回的性能对比实验，当前不宜直接替换已工作的接入。
- async Hook 在会话结束时可能被取消，因此不能替代当前持久队列和 LaunchAgent。

## 建议升级顺序与验收

1. 安全和召回：修复多行/嵌套脱敏；禁止草稿自动注入；按完整来源条目裁剪。验收要求合成秘密在采集后和请求体中均不存在，草稿不会自动注入，所有来源都能完整定位。
2. 队列和可观察性：无候选快速退出、按需读取、工具日志分流、积压年龄/处理率/失败原因统计。验收要求无候选批次不读取 wiki 全文，doctor 区分历史验收与当前版本。
3. 发布和兼容：精确 Git 提交、干净环境安装、CLI/Desktop 压缩和中断实验。以当前文件哈希绑定新证据。

## 证据边界

正式运行状态来自 15:39 的只读快照，后台仍可能继续变化。此前 13.64 秒万页发布和 CLI/Desktop 时延记录是历史成功证据，本轮没有重跑这些既有通过检查。部分独立子审查未返回可采信结论，未计为通过；主审已拒绝“版本戳必然阻塞 wiki 发布”“故意禁网端口必然造成 flaky”等未证实推断。主要问题由主审临时复现和当前源码交叉确认。

详细结构化证据：[review-20260908.json](../evidence/codex-native-2026-09-08/review-20260908.json)。

最终独立证据复核：review_evidence_1545 对同一源码哈希确认多行 PEM 脱敏缺口、草稿召回/来源截断、空候选全库扫描三项机制均成立；独立复核为只读源码验证，动态复现由主审完成。

## 修复跟进（2026-09-08 16:20 CST）

本节是审查后修复记录，前文保留原始发现，不作为当前未修复清单。

- 已修复：多行/嵌套脱敏、模型出站二次清洗；混合批次仅发送合格证据。
- 已修复：草稿目录自动召回、弱语义误召回、完整来源预算。额外修复未知生命周期被归一为 active：fresh 保持兼容映射，其余未知状态归入 candidate；既有索引需正常重建后生效。
- 已修复：空 proposals 跳过 wiki 全文扫描；公平性测试验证旧记录继续在本地处理。
- 已修复：doctor 分离 historical_acceptance/current_build_verified；后者要求源码哈希、插件版本、安装及索引匹配，model_use_verified 跟随当前验证。
- 验证：worker/install/protocol/relations 88 passed；capture/retrieval 36 passed；doctor 添加正向绑定与哈希失配测试后 11 passed；git diff --check 通过。
- 实际隔离运行：10,001 旧页，真实本机网关整理，新增 1 页，commit `dc97752b53ed5b2cd73ca1d7ad7db849733b94c4`，索引 ready，UserPromptSubmit 返回完整来源与银杉测试偏好；耗时 30.54 秒。这是直接 worker 到召回测试，不是新一轮 Desktop/CLI 客户端定时端到端验收。
- 当前只读生产快照：publish=false，recall/capture=true；index ready；historical_acceptance=true、current_build_verified=false；pending 5,389。没有将历史客户端验收重标为当前通过。
- 未做：本轮源码精确提交、当前构建的两端重新验收、生产自动发布重新启用。工作树仍包含多项历史未提交变更，不能把隔离 wiki commit 称为项目源码发布。

证据：`review-fixes-runtime.json`、`review-fixes-doctor.json`（位于同日 evidence 目录）。

## 源码交付与生产恢复（2026-09-08 16:38 CST）

- 精确源码提交 `cdeaf2c53afa5152abe70d853c7a28d40cb54fa4`，分支 `codex/native-memory-integration`，25 文件。其他检索/流水线/UI 改动仍保留在工作树，未混入提交；未推送远端。
- 干净含空格路径检出：118 原生测试通过；使用实际配置 Python 完成 MCP initialize/list/call 与非仓库 cwd Hook 调用。测试 venv 不含 MCP SDK，因此协议验证使用已配置解释器，未安装依赖。
- 当前 Desktop 新任务无工具正确回答银杏报告结尾三条行动项，完整来源；Stop 到索引 mtime 59.450 秒（LaunchAgent 在 Stop 后 bootstrap，含 RunAtLoad，不声称这是完整轮询等待实测）。
- 当前 CLI 独立会话无工具正确回答白鹭报告日期升序，完整来源；Stop 到索引 mtime 69.913 秒。首次网关 400、忽略配置导致 Hook 未触发两种失败均未计通过；最终使用隔离 CODEX_HOME 的正式插件注册通过。
- installed plugin 全文件哈希与源码一致；当前源码哈希绑定新 acceptance，doctor current_build_verified=true。独立 reviewer 复核 commit/哈希和 Desktop/clean 检出证据通过，CLI 最终证据由 root 核验。
- 索引已备份并在共用排他锁下重建，quick_check=ok。恢复生产 publish=true 后，首个定时 worker 于 08:38:16 UTC 正确 noop：20 条本地候选、gateway_skipped=true、366.26ms，未创建空提交。
- 证据见 current-acceptance.json、clean-release-protocol.json、release-review-ledger.json、release-index.json、release-activation.json。临时验收 LaunchAgents 已卸载，生产 60 秒 LaunchAgent 保留。
