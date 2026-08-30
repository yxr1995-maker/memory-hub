# memory-hub B 激进全自动路线：正式设计规格

**状态：** 已批准的设计基线
**日期：** 2026-08-31
**决策：** 采用 B 激进全自动路线。自动推断、自动扩词、自动 successor、自动聚类与自动发布是默认行为；本规格不引入“候选后等待人工确认”的隐性替代路径。

## 1. 目标、边界与术语

### 1.1 目标

在不删除历史页面、不越过受控路径、也不把凭据或原始私密观察写入审计日志的前提下，memory-hub 必须实现下列自动闭环：

1. 为新页面与约 **12,900** 个存量 Markdown 页面自动归属 `user`、`project` 或 `agent` scope；
2. 让融合检索默认执行「L0 初召回 → 智能扩词 → 最终融合排序」两阶段查询；
3. 当新事实取代旧事实时，原子地建立 successor 链并让所有查询表面优先返回有效的新页；
4. 在跨日期的 observations 中自动聚类、生成合并页、发布、索引并作精确提交；
5. `run` 与 `maintain` 的无参数调用默认完成该自动化闭环。

### 1.2 明确非目标

- scope 是检索与注入命名空间，**不是** ACL、用户授权或租户隔离机制；
- 不删除、不截断、不改写任一历史页面正文；
- 不同步其他 wiki 仓库，不增加常驻服务，不修改外部 automation 的调度；
- 不在实现、测试或验收时读取、写入、暂存或提交真实 `~/llm-wiki`，除非操作者在独立运行时显式将它作为 `WIKI_PATH`；
- 不记录令牌、Cookie、Authorization 头、LLM 请求正文或未脱敏 observations。

### 1.3 规范术语

- **MUST/必须**：违反即为实现缺陷。
- **自动**：在满足本规格的机器可验规则后直接执行；不是生成待人工确认的 candidate。
- **安全退出**：因锁、路径、解析、完整性或网络问题停止写入，并保留可审计报告；不是吞错后继续发布。
- **受控 wiki**：由绝对化、符号链接解析后的 `WIKI_PATH` 表示的唯一根目录。
- **operation id**：`UTC 时间 + 随机 UUID`；同一次 `run`/`maintain` 的所有报告、journal 与提交清单共享该值。

## 2. 现有入口与统一契约

当前实现的命令入口是 `memory-hub.sh`，CLI 搜索经 `scripts/search.sh`/`scripts/fuse.py`，REST 经 `scripts/server.py`，MCP 经 `mcp/server.py`。B 路线必须将语义集中到可复用的 Python 服务层（建议 `scripts/automation_core/`），shell、REST 和 MCP 只负责参数解析、边界校验和展示，不能各自复制排序或推断规则。

### 2.1 所有表面必须一致的参数

| 语义 | CLI | MCP | REST |
|---|---|---|---|
| 融合检索 | `search QUERY --fuse`；默认等价于 `--fuse` | `memory_search(..., fuse=True)` | `GET /search?fuse=1`；缺省为 `1` |
| 智能扩词 | `--expand`；`--fuse` 下缺省启用 | `expand=True` 为默认值 | `expand=1` 为默认值 |
| 关闭扩词 | `--no-expand` | `expand=False` | `expand=0` |
| scope 过滤 | `--scope S [--scope-id ID]` | `scope`, `scope_id` 参数 | `scope=S&scope_id=ID` |
| 解释输出 | `--explain` | `explain=True` | `explain=1` |
| ask 的检索 | `ask QUERY` 复用同一 planner/ranker | `memory_ask` 复用同一 planner/ranker | `GET /ask` 复用同一 planner/ranker |

`search`、`ask`、MCP `memory_search`/`memory_ask` 与 REST `/search`/`/ask` 缺省均为 `fuse=on, expand=on`。`--no-fuse`（或 `fuse=0`）是兼容诊断开关；它不会使 `--no-expand` 隐式生效。输入限制沿用现有上限：query 最长 500 字符，`top` 夹在 1–50，scope 枚举值只能为三个合法值。

所有可见结果都必须带上可机器读取的 `path`、最终 `score`、`status`、`scope`、`scope_id` 与 `rank_reason`。CLI 的人类可读行可继续以 `[score] path.md` 开头，`--json`/REST/MCP 的结构化形式不得丢字段。

## 3. Scope 自动归属与 12,900 页回填

### 3.1 新数据模型

每个新蒸馏页、合并页及索引记录使用下列兼容 frontmatter：

```yaml
scope: user | project | agent
scope_id: stable-nonempty-id
scope_confidence: high | medium | low
scope_source: explicit | session_meta | path | content | fallback
scope_conflict: false
project: optional-project-name
agent_id: optional-stable-agent-id
```

`scope_id` MUST 非空。`user` 使用可配置的 `MEMORY_HUB_USER_SCOPE_ID`，未配置时使用稳定字面量 `default-user`；禁止从用户名、主目录或凭据推导。`project` 的 ID 是经 Unicode NFKC、trim、lowercase、非 `[a-z0-9._-]` 替换为 `-` 后的项目键；`agent` 的 ID 来自会话元数据的稳定 agent id，同样规范化。

### 3.2 确定性推断与冲突处理

推断器对每页收集候选及证据并按如下优先级选择，永远得到一个结果：

| 优先级 | 条件 | 结果 | 置信度/来源 |
|---:|---|---|---|
| 1 | frontmatter 已有合法 `scope` 与非空 `scope_id` | 保留原值 | `high/explicit` |
| 2 | 有合法稳定 `agent_id`，且页面或 session 明确标记 agent 规则 | `agent` | `high/session_meta` |
| 3 | 有明确全局用户偏好标记、受控 `user/` 路径或 `MEMORY_HUB_USER_SCOPE_ID` 引用 | `user` | `high/path` 或 `high/content` |
| 4 | `project` 字段、session `cwd` 或 source URI 同意同一个项目键 | `project` | `high/session_meta` |
| 5 | 多个项目证据中最高分唯一，且分差至少 0.20 | 该 `project` | `medium/path` |
| 6 | 其他所有情况 | `project/default-project` | `low/fallback` |

冲突的定义是两个不同 scope 或不同 scope_id 的强证据同时存在。冲突时 MUST 选更高优先级且设置 `scope_conflict: true`，在 `reports/scope-<operation-id>.jsonl` 记录每个候选、分数、胜者和原因；它不会暂停、不会生成待确认页面，也不会覆盖已有显式合法字段。若两个同优先级强证据并列，则稳定地选按规范化 ID 字典序最小者，标 `medium`，以确保重跑一致。

### 3.3 回填迁移

新增 `scope-backfill` 子命令，唯一数据写入目标为受控 wiki 内的 Markdown frontmatter：

```text
memory-hub.sh scope-backfill                 # 默认 dry-run，扫描并输出计划
memory-hub.sh scope-backfill --apply         # 自动回填
memory-hub.sh scope-backfill --apply --limit N --cursor C
```

实现必须按稳定相对路径排序并以 cursor 分批，支持约 12,900 页的中断恢复。每批在写入前重新读取并比较 inode/mtime/content hash；发现并发改动时跳过该页、记录 `concurrent_change`，继续其余页。已具有合法 scope 的页严格跳过。对文件外路径、符号链接逃逸、frontmatter 解析失败、超出受控根的目标必须拒绝写入并在报告中记录，不得尝试修复正文。

回填以临时同目录文件 + `fsync` + 原子 rename 写入；每个成功写入的前状态放入 `$MEMORY_HUB_DATA/transactions/<operation-id>/scope-before/`，并在 journal 记录 hash。批内失败时反向原子还原本批已写入页，退出非零。`--apply` 可安全重跑：相同字段与值不再写入。

### 3.4 索引、inject 与 export

`index.sh` 的原子新库必须扩展为：

```sql
pages(path, title, type, tags, abstract, content, scope, scope_id, scope_confidence, status)
meta(path, updated, last_verified, valid_at, invalid_at)
```

索引读取缺少 scope 的旧页时，仅在数据库中填 `project/default-project`, `low`, `fallback`，不改 wiki 文件；回填完成后重建索引即可收敛。`inject` 和 `export.py` 接受上表定义的 scope 参数，默认跨 scope，以保持旧调用的召回范围。`export` 的 JSONL、JSON 与 Markdown 都要透传 scope/status/validity 字段。

## 4. 默认两阶段 L0 + 智能扩词融合检索

### 4.1 查询计划

对任意 `fuse=on` 查询，执行器 MUST 采用如下顺序：

```text
原始 query
  -> L0 初召回（FTS5 + vector，各取最多 12 个候选）
  -> 读取最多 6 个去重候选的 abstract；缺 abstract 时只读取正文首个非空段，最多 320 字符/页
  -> LLM 扩词（首选）或本地扩词（故障降级）
  -> 原 query 与扩展 query 分别召回
  -> RRF 融合、时间/类型/生命周期调权、scope 筛选、去重
```

L0 是查询规划资料，不是最终命中保证。扩词最多 4 个，每项 2–64 字符，必须去重、不得包含控制字符、URL、疑似密钥或原 query 的大小写重复。LLM 提示只收到已截断、脱敏的 L0 abstract，不接触完整页面、raw observations 或环境变量。

原 query 的 RRF 权重为 0.70；所有扩词合计权重为 0.30，按扩词置信度归一。没有合格扩词时权重自动归于原 query。最终排序的同分打破顺序为：有效状态优先、较高 scope confidence、较新 `valid_at/updated`、稳定 `path` 字典序。

### 4.2 智能与本地降级

默认尝试通过已配置的 OpenAI-compatible proxy 请求 LLM 关键词规划，连接/读取超时分别不超过 2 秒/6 秒。HTTP 401、403、429、5xx、无效 JSON、越界输出、空输出和任何网络异常均为**预期可观测降级**：不重试风暴，不泄露响应体，改用本地 L0 统计扩词。

本地算法按 BM25/词频与 query 共现度从 L0 abstracts 提取中文 2–8 字符 n-gram 与英文词干，滤掉停用词、仅数字 token、隐私模式 token，并选择最高的最多 4 项。FTS5 与向量任一不可用时仍使用另一源；两源均无 L0 时直接以原 query 进行既有检索回退。扩词失败从不改变索引、不会导致空结果覆盖原始结果。

审计仅保存 query hash、候选页相对路径、扩词数组、`planner=llm|local|original-only`、降级原因代码、耗时与最终命中数，写入 `$MEMORY_HUB_DATA/reports/query-plan-<operation-id>.jsonl`。`--explain` 返回同一信息（不含原始敏感文本）。

### 4.3 生命周期一致的排序

融合器先从 index meta 得到 validity 与 successor 图，再排分：

- `status=deprecated` 不得占据一个最终名额；若有效 successor 存在，替换为该页并标记 `via=successor`；
- successor 链循环、断链或指向缺失页时，保留原页但标记 `lifecycle_error` 并发送指标；不能猜测另一个替代页；
- 有效页按基线分数参与 RRF；仅当有效页不足 `top` 时才填入带 `lifecycle_error` 的旧页；
- `ask` 获取的上下文页与 CLI/REST/MCP 的最终结果必须来自相同 `rank_results()`，所以 top-1 与路径序列一致。

## 5. 自动 successor 生命周期

### 5.1 字段与不变量

当页面 B 替代页面 A 时，A 的 frontmatter 必须新增或更新：

```yaml
status: deprecated
deprecated_by: '[[relative/path/to/B]]'
invalid_at: '2026-08-31T12:34:56Z'
```

同时 B 必须包含：

```yaml
status: active
supersedes: '[[relative/path/to/A]]'
valid_at: '2026-08-31T12:34:56Z'
```

`deprecated_by` 和 `supersedes` 必须是相对于受控 wiki 的规范 wikilink，禁止绝对路径、`..`、外部 URI 与符号链接目标。历史正文不可修改；只允许替换/插入 frontmatter 键。一个活跃页可以 supersede 多页，一页在同一时刻只能有一个 `deprecated_by`。新增 successor 不得使图产生环。

### 5.2 全自动判定

distill/publish 在每个新页发布前执行 `successor_plan()`：先以同 `scope + scope_id`、同 project 和主题 candidate 集合缩小范围，再比较标题/标签、L0 abstract、关键结论和 embedding。

| 条件 | 自动动作 |
|---|---|
| 语义相似度 ≥ 0.88，关键实体一致，且至少一个可比较结论或 freshness 字段发生变化 | 自动建立 successor |
| 0.72–0.88 或关键实体不完全一致 | 自动发布为独立 active 页，并记录 `relation=related-not-successor` |
| < 0.72 | 自动发布为独立 active 页 |
| 缺 embedding | 使用词项/标题/实体规则；规则分 < 0.92 时发布独立 active 页 |

“不确定”在 B 路线中并不转为人工 candidate：它意味着**不废弃任何历史页、自动发布独立 active 页、保留风险审计**。这将误判的破坏面限制为零，同时保持整条流水线无人值守。现有的普通同名发布冲突仍遵守“永不覆盖”规则，自动安全重命名新页；不得借生命周期逻辑覆盖一个无关文件。

### 5.3 原子 journal、回滚与索引

发布顺序必须是：

```text
validate all paths and cycle-free plan
-> write operation journal PREPARED
-> write/verify B temp (active, supersedes, valid_at)
-> write/verify A temp (deprecated, deprecated_by, invalid_at)
-> atomic rename both pages, recording each completed step
-> rebuild/swap index atomically
-> mark COMMITTED
```

两个文件系统 rename 无法成为单一内核事务，因此 journal 是恢复真相源。任何步骤失败、验证 hash/frontmatter 失败、索引重建失败或进程重启时，recovery 必须按 journal 恢复操作前的 A/B 内容并重建上一个有效索引；绝不留下半对生命周期字段。每个 `operation-id` 的 before images、plan、完成步骤、rollback 结果和精简路径清单存于 `$MEMORY_HUB_DATA/transactions/<operation-id>/`，文件权限 0700。

同一 successor plan 的重跑必须无写入；若当前 A/B 已具有互为对应的字段和同一 hash 语义，输出 `idempotent_skip`。若 A 后来指向第三页 C，旧 A→B 请求不能覆盖该状态，记录 `concurrent_successor` 并跳过。

## 6. `maintain` 的跨日自动聚类、发布与精确提交

### 6.1 输入选择与聚类

`maintain` 自动扫描所有日期命名 `staging/observations-*.jsonl`，而非仅最新文件；排除 `realtime`、`test` 与已经由 manifest 消费的 observation id。只接受已通过 schema、长度 20–2,000 字符、包含项目键和非空 id 的 observations。日志、命令输出、疑似密钥、私有目录片段先经 `sanitize_text` 去除或掩码。

按 `project scope_id` 分桶，再采用确定性凝聚聚类：优先 embedding cosine ≥ 0.80，embedding 不可用时使用字符 3-gram Jaccard ≥ 0.52。簇必须至少包含 3 条 observations、跨至少 2 个 UTC 日期、时间跨度不超过 45 天；不满足的 observation 保留给后续轮次。稳定 cluster key 为成员 id 的排序 SHA-256 前缀，禁止以运行时间作为幂等键。

每个簇自动生成一张 active 合并页，含：cluster key、成员数、首末观测时间、方法、来源 id 的 hash 列表、L0 摘要、明确的不确定性标签和 `valid_at`。它先经过第 5 节 successor planner，再按 normal publish 的“永不覆盖”规则发布。没有任何人工候选闸门。

### 6.2 锁、manifest 与阶段顺序

全自动写流程共用 `$MEMORY_HUB_DATA/locks/automation.lock`。锁用原子 `mkdir` 创建，内容含 PID、host、operation id、启动 monotonic/UTC 时间；取得失败时必须退出码 75 并报告持锁 operation。仅在 PID 不存在且超过保守 TTL 30 分钟后，才允许抢救为 stale lock，且必须把旧锁证据归档；永不删除仍存活进程的锁。

在锁内，`maintain` 必须严格按以下顺序执行，并在每阶段写 journal checkpoint：

```text
preflight/path+git baseline
-> fix deadlinks
-> backfill timestamps
-> backfill links
-> scan all eligible observations
-> deterministic clustering
-> write merge-page staging
-> successor transaction + publish
-> atomically update cluster manifest
-> rebuild/swap index
-> lint
-> exact stage + commit
```

manifest 路径为 `$MEMORY_HUB_DATA/manifests/cluster-observations-v1.json`，键为 cluster key，值包含已消费 observation-id hashes、生成页面相对路径、生成内容 hash、operation id 与成功阶段。manifest 的更新同样使用 temp + fsync + rename，且只在新页发布、索引与 lint 都成功后提交。重跑必须对已有 cluster key 报告 `manifest_skip`，不得再发布或再次消费同一组 observation。

### 6.3 失败回滚与精确提交

任一阶段非零必须停止。发布前失败不会写 wiki；发布后、manifest 前失败必须依据 lifecycle journal 删除**仅本 operation 新建且 hash 相同**的合并页、还原修改过的 frontmatter、还原 index，并保留失败报告。拒绝执行宽泛的 `git reset --hard`、`git add -A`、`git commit -a` 或删目录清理。

预检记录 wiki 的初始 unstaged/staged 路径集合。成功时允许暂存的精确白名单仅包括本 operation 创建或验证修改过的：合并页、旧/新 successor 页、`index.md`、`log.md`；它们必须先逐个比较 operation journal hash。`git diff --cached --name-only` 必须与 whitelist 完全相等，否则先执行 `git restore --staged` 仅对本 operation 已 stage 的路径、保留用户已有 staged 改动，并失败退出。提交信息为 `chore(wiki): memory-hub maintain <operation-id>`。若 wiki 不在 Git 仓库，自动发布仍可完成但报告 `commit=not-a-repository`，不得伪称已提交。

## 7. 无参数默认、退出开关与兼容迁移

### 7.1 默认行为

`memory-hub.sh run` 的正式默认值变为 `auto=on, apply=on, commit=on`：capture、distill、scope 回填、autolink、successor、publish、index、archive 均按上述事务执行。`memory-hub.sh maintain` 的无参数默认值同样为 `auto=on, apply=on, commit=on`，并执行第 6 节全流程。这是 B 路线有意改变的行为，必须在 `--help` 与 release note 显示。

两个明确的退出开关不是默认值：

- `--safe`：仍运行 parse、plan、L0、校验与审计，但不写受控 wiki、不更新 manifest、不 archive、不 stage/commit；返回可用于复现的 operation plan。
- `--no-auto`：禁用 B 路线的 scope 回填、智能关联、successor、跨日聚类和自动发布。对 `run`，没有显式 `--apply` 时退回旧式 dry-run；对 `maintain`，只运行旧的死链/时间戳/链接修复，且仍需显式 `--apply` 才写入。它不影响手动 `search --fuse` 的基础检索。

`--safe` 与 `--apply`、`--commit` 互斥；参数解析器必须以退出码 2 拒绝矛盾组合。调用输出首行必须打印 `mode=auto|safe|no-auto`、`apply=true|false` 和 operation id。

### 7.2 数据迁移兼容矩阵

| 表面/数据 | 旧状态 | 升级后的默认 | 兼容保证 | 回退方式 |
|---|---|---|---|---|
| 旧 wiki 页面 | 无 scope/validity | 读时 fallback，回填后显式字段 | 无字段页仍可检索 | 不删字段；safe 模式只报告 |
| FTS5 `pages/meta` | 旧列 | 新库原子 swap 增列 | 读取器先检测列，缺列降级 | 保留旧 DB 到验证完成 |
| CLI search/ask | fuse/expand 可选 | fuse+expand 默认 | `--no-fuse --no-expand` 可复现实验 | 参数级关闭 |
| MCP | `expand=False` 默认 | `expand=True` 默认并新增 scope | 显式 False 保持关闭 | 客户端传 `expand=False` |
| REST | `expand=0` 默认 | 缺省 `expand=1` | `expand=0&fuse=0` 保持旧方式 | query 参数关闭 |
| 历史冲突页 | candidate 需确认 | successor 使用独立全自动判断 | 既有 candidate 不自动覆盖 | 原 publish 语义原样保留 |
| `run` | 默认 dry-run | 默认 apply/commit/auto | `--safe` 与 `--no-auto` 显式可用 | 不存在隐式回退 |
| `maintain` | 仅 wiki 修复 | 含跨日聚类/发布/commit | 旧修复可通过 `--no-auto --apply` 执行 | journal 恢复本次写入 |

部署先发布“可读新 schema”的代码，再执行 index migration，再开启默认 auto；每阶段均可运行 `--safe` 验证。任何既有客户端在未发送新参数时得到新默认；需要旧结果的调用方必须显式发送关闭参数，避免隐式、不可观测的行为漂移。

## 8. 自动化误判控制（不退回人工候选）

自动化误判通过可验证的非破坏性控制收敛：

1. scope 冲突采用确定性优先级和全量 evidence，而不是阻塞；
2. successor 只有高阈值和实体一致才废弃旧页；中低置信度自动并存为 active 页；
3. 生命周期写入先做图环检测、路径解析、journal、before image 和前后 hash 验证；
4. 聚类要求最小成员数、跨日、时窗、项目隔离与稳定 key；弱簇留待后续 observation 自动累积；
5. 所有默认写路径受单写锁、受控路径白名单、精确 Git 白名单和原子 index swap 保护；
6. 每次自动决策都输出 reason code、阈值、模型/本地算法版本和可重放输入 hash；异常率越阈值时自动将该 operation 切换为 `safe` 终止，而非继续写入或派出人工 candidate；
7. 人可在后续编辑历史页面或新页纠正事实；下一次索引和检索会反映该真实内容，但流水线不会擅自改写正文来“修复”判断。

## 9. 观测、审计与告警

必须新增 Prometheus 指标和每 operation 的 JSONL/Markdown 报告，至少包括：

- `memory_hub_scope_backfill_total{scope,confidence,result}`、`memory_hub_scope_conflict_total`；
- `memory_hub_query_plan_total{planner,fallback_reason}`、`memory_hub_query_expand_terms`、L0/LLM/local 各阶段延迟；
- `memory_hub_successor_total{decision,result}`、`memory_hub_lifecycle_cycle_total`、`memory_hub_transaction_rollback_total`；
- `memory_hub_cluster_total{method,result}`、`memory_hub_cluster_members`、`memory_hub_manifest_skip_total`；
- `memory_hub_auto_operation_total{command,mode,result}`、锁等待/拒绝、精确 staged 路径数和提交结果。

报告根为 `$MEMORY_HUB_DATA/reports/`：`scope-<operation-id>.jsonl`、`query-plan-<operation-id>.jsonl`、`lifecycle-<operation-id>.jsonl`、`cluster-<operation-id>.md`、`operation-<operation-id>.json`。日志仅保存安全的相对路径、hash、计数、错误类别和截断长度；不得保存 token、绝对家目录、raw text、完整提示词或 HTTP 响应。

下列情形必须显式告警并使本次 operation 安全失败：锁不可取得、路径越界、journal 恢复失败、frontmatter 无法解析、index swap 失败、successor 图有环、manifest 原子写失败、Git staged 集合不精确。LLM/proxy 失败是可降级的 `warning`，不是失败。

## 10. 实现切分与测试要求

### 10.1 模块职责

| 模块 | 责任 |
|---|---|
| `scope.py` | frontmatter 解析、推断、回填计划、稳定 cursor 与冲突 evidence |
| `query_planner.py` | L0 截断、LLM/local 扩词、审计与故障降级 |
| `ranker.py` | RRF、scope filter、validity/successor 图、稳定排序，供 CLI/MCP/REST 共用 |
| `lifecycle.py` | successor 判定、环检测、journal、原子成对写、恢复 |
| `cluster.py` | 跨日输入、确定性聚类、合并页和 manifest |
| `operation.py` | 单写锁、路径验证、operation journal、精确 Git stage/commit 与回滚协调 |

shell 入口必须保持薄层。所有路径经 `Path.resolve()` 后验证在受控根内；对 symlink、相对 `..`、NUL、不可解析 frontmatter 和非 UTF-8 内容均有明确测试。任何网络调用必须设超时并有结构化降级码。

### 10.2 单元、集成与回归测试

新增测试不得依赖真实 home、真实 wiki、网络、模型或 Git 用户配置；每个 fixture 使用临时 `WIKI_PATH`、`MEMORY_HUB_DATA` 和单独 init 的 fixture Git repo。

| 测试组 | 必测场景与二元可观察结果 |
|---|---|
| `test_scope_backfill.py` | 6 级推断表、并列冲突、12,900 页分批 cursor、apply 幂等、并发改动跳过、路径逃逸拒绝；断言字段/报告/退出码 |
| `test_index_scope.py` | 旧 schema 读兼容、新列完整、原子重建故障不替换旧 DB；断言 SQLite 列与 DB hash |
| `test_query_planner.py` | L0 限额、LLM 成功、429/超时/无效 JSON 本地降级、原 query 无扩词；断言 planner 与 audit reason |
| `test_surface_parity.py` | CLI、MCP、REST 对相同 fixture query/scope 的 path 顺序和 top-1 一致；断言 JSON 响应 |
| `test_successor_lifecycle.py` | 高/中/低阈值、成对字段、图环拒绝、跨进程恢复、重复运行无写入；断言前后 hash 和 journal 状态 |
| `test_cluster_maintain.py` | 跨日 3 成员簇、单日/弱簇不发布、embedding/local 两法、manifest 幂等、发布失败回滚；断言页/manifest/Git 状态 |
| `test_operation_safety.py` | 活锁、stale lock、路径白名单、用户预先 staged 文件、whitelist mismatch；断言退出码 75/非零及未污染 staged 集 |
| 既有 tests | `pytest tests/`、`MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py`、`./memory-hub.sh verify` 保持通过 |

### 10.3 真实 CLI 验收（隔离 fixture，不触碰 live wiki）

CI 之外必须执行一个临时目录端到端场景。下面每条命令的 stdout/stderr、退出码、fixture tree、Git staged/commit 清单和报告文件都保存到 `.omo/evidence/roadmap-full-auto-cli/`：

```bash
fixture_root="$(mktemp -d)"
export WIKI_PATH="$fixture_root/wiki"
export MEMORY_HUB_DATA="$fixture_root/data"
export CODEX_SESSIONS_DIR="$fixture_root/sessions"

./memory-hub.sh scope-backfill --apply
./memory-hub.sh index
./memory-hub.sh search 'fixture lifecycle' --fuse --explain --scope project --scope-id fixture-project
./memory-hub.sh run --safe
./memory-hub.sh maintain --safe
pytest tests/
./memory-hub.sh verify
```

验收必须验证以下二元事实：scope-backfill 退出 0 且每个 fixture 页有合法 scope；搜索退出 0 且 explain 的 planner 为 `llm` 或 `local`；`run --safe`/`maintain --safe` 退出 0 且 fixture wiki Git diff 为空；全自动 fixture 操作退出 0 后有一页 cluster、正确 successor 双向字段、manifest 与 atomic index、并且 `git diff --cached --name-only` 等于 operation whitelist；第二次相同 maintain 退出 0 且 cluster 页数不增加。任何一项失败均不得宣布 B 路线完成。

## 11. 发布门槛

实现变更只有同时满足下列条件才能发布：

1. 全部第 10 节测试通过，`pytest tests/`、`./memory-hub.sh verify` 与真实 CLI fixture 均 exit 0；
2. 代码审查确认没有使 `--safe` 写 wiki/manifest/Git，没有宽泛 stage/commit，没有访问 live wiki 的测试；
3. 以一次 `--safe` 迁移报告确认 scope 计数总和与扫描页面数一致，约 12,900 存量页全部可分类或带显式 fallback；
4. query expand on/off 基准同时保存；开启扩词的 hit@5 不得低于关闭扩词的 90%，否则 operation 不自动推广 LLM planner；
5. 生命周期、聚类、锁、rollback、REST/MCP parity 的证据均位于 CI artifact 和 `.omo/evidence/roadmap-full-auto-cli/`；
6. 文档、`--help`、REST/MCP schema 已写清楚 B 路线默认值与 `--safe`/`--no-auto` 退出语义。

该门槛控制的是实现发布质量，不是重新引入人工确认路线：在门槛未满足时，自动流程以 `safe` 失败并输出证据，直到缺陷修复后重新运行。
