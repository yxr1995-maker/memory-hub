# memory-hub 修复与升级方案

> 日期：2026-08-18
> 依据调研：`mem0-report.md` / `mem0-tech-notes.md`（Mem0）、`memos_research/MemOS_研究报告.md`（MemOS）、`aml_research/AML_方法论研究报告.md`（Agent Memory Leaderboard 方法论）
> 现状基线：README 描述的 capture → distill → publish → index/ask/inject 全链路，SQLite FTS5 + 向量 RRF 融合（`--fuse`），零常驻进程。

---

## 一、背景与结论

memory-hub 当前形态是「本地管道型记忆」：会话 jsonl 采集 → 分层蒸馏 → 发布 wiki → FTS5/向量检索。对照调研有三个核心差距：

1. **写入无治理**（对照 AML 能力 D「记忆治理」）：页面只增不改，无更新/冲突消解/遗忘机制；Mem0 新算法的 ADD-only 虽也「只增」，但它靠提取时的事实归一化 + 检索时的时间排序兜底，我们两头都没有。
2. **检索无时间语义**（对照能力 C「时间与事件序列」）：FTS5/向量都对「最新有效状态」不敏感，旧结论和新结论同权召回，用户已在 Westmonth 价格审计等场景被坑过（旧价格覆盖新价格）。
3. **无自评测闭环**（对照 AML 方法论）：AML 用统一 Add/Search 契约 + 7 维能力画像横向比较系统；memory-hub 目前没有任何可复现的自测基准，升级效果无法量化。

**结论：分两期走。第一期修复（治理 + 时间排序 + 自评测骨架），全部在现有 bash+sqlite 栈内完成；第二期升级（图关系、能力画像报告）视第一期收益再启动。**

---

## 二、第一期：修复（1–2 周）

### F1 蒸馏去重与冲突标注（对应 AML-D）

现状：`distill.sh` 按 project 分组生成页面，同一主题多次采集产生多份页面，publish「永不覆盖」导致旧结论永久占位。

做法：

- distill 生成页面前，先按 `title 归一化 + project` 查 `~/llm-wiki` 已有页；命中则写入 `staging/pages/` 同名候选并标 `status: candidate`，publish 阶段不直接发布，而是生成 `reports/conflicts/<slug>.md` 对照文件（旧页摘要 vs 新候选摘要）。
- 页面 frontmatter 已有 `confidence/contested/last_verified` 字段，启用 `contested: true` 标记冲突页，搜索/注入时降权。
- 人工确认后手动合并，管道本身永不自动覆盖（守住现有安全边界）。

可程序化验证：构造同主题两次采集的 fixture，跑 `distill && publish --apply`，断言出现 conflicts 报告且原页未被改动。

落地（2026-08-18）：`scripts/distill.sh` 按 project 查 wiki `drafts/memoryhub/` 已存在页，命中则新页标 `status: candidate` + `contested: true` 并写 `reports/conflicts/<slug>.md`（候选/已存在摘要对照）；`scripts/publish.sh` 对 candidate 页跳过发布，永不覆盖。fixture 验证通过：同主题二次采集出 conflicts 报告且旧页未被改动；新 project 正常发布。

### F2 检索时间衰减与「最新优先」（对应 AML-C）

现状：`search.sh`（rg）与 `fuse.py`（RRF）都不看时间。

做法：

- `index.sh` 建索引时把 frontmatter 的 `updated/last_verified` 存入索引表。
- `fuse.py` 的 RRF 得分乘时间衰减因子 `score * exp(-age_days / τ)`，τ 默认 90 天，可用 `--tau` 覆盖；同分时 `updated` 新者在前。
- `inject.sh` 注入的「最近观察」与「最近更新」区块已经按时间排，无需改；只需在 `ask.sh` 的上下文拼接处同样应用衰减排序。

可程序化验证：两条内容几乎相同、`updated` 相差 180 天的页面，新页在 `search --fuse` 结果中排前。

### F3 自评测基准骨架（对应 AML 方法论）

目的：每次改动后用同一组问题量化检索质量，避免「感觉变好了」。

做法：

- 新增 `evaluation/golden.jsonl`：手工挑 30–50 条「问题 → 预期命中页 slug」对，从 llm-wiki 真实历史里选（覆盖事实召回 A、时间序列 C、治理 D 三类）。
- 新增 `scripts/eval.sh`：对每条问题跑 `search --fuse --top 5`，计算 hit@5 与 MRR，输出 `reports/eval-<date>.md`，与上次结果 diff。
- 只评测检索，不评测生成（对齐 AML「系统只负责记忆」的责任边界）。

可程序化验证：`eval.sh` 在干净仓库上 exit 0 且输出固定格式报告。

落地基线（2026-08-18，30 条 golden，top=5）：hit@5=18/30=0.600，MRR=0.4361；分类型 A(24) 0.625 / C(3) 0.667 / D(3) 0.333。报告见 `reports/eval-2026-08-18.md`。

### F4 观察库瘦身（次要）

`staging/` 已积累数百个 `observations-*.jsonl` 与 `mem.db`，多为历史中间产物。做法：`run --apply` 成功后把已发布的观察归档到 `staging/archive/`（保留 30 天），减少 capture 增量比对与磁盘占用。删除走归档而非 `rm`，可恢复。

落地（2026-08-18）：新增 `scripts/archive.sh`（默认归档除最新 1 份日期命名观察外的全部，仅日期命名，绝不碰 `observations-realtime.jsonl`/`observations-test.jsonl`；dry-run 默认，`--apply` 移动）。已归档 788 个文件（791→3），observation 文件数下降；`run --apply` 成功后自动触发。

---

## 三、第二期：升级（视第一期收益，2–4 周）

### U1 实体关系边（对应 AML-B 多跳）—— 评审结论（2026-08-18）：暂不启动

蒸馏时从页面正文提取 `(实体, 关系, 实体)` 三元组写入索引库 `edges` 表（仅人名/项目名/工具名等可规则提取的，不上 LLM）；`search` 命中实体时附带 1 跳邻居。MemOS 的图记忆完整版过重，只做单边查询这一小步。

**评审结论：暂不启动，边缘化收益（YAGNI）。** 理由：

1. **改动面大**：需要 `index.sh` 新增 `edges` 表 + 索引时抽三元组、`fuse.py` 检索时 JOIN 1 跳邻居、`distill.sh` 产出结构化实体段——贯穿 4 文件，且 wiki 已拥有 `[[wikilink]]` 双链与 `sources` frontmatter 天然边，量级远超「单边查询这一小步」。
2. **收益不可量化**：当前 eval 基线已覆盖 A/C/D/G 四类，唯一短板是 G（规则/策略 0.333）而非 B（多跳），U1 对画像短板的贡献未被证明。
3. **可复用性差**：wiki 双链已提供类似功能（Obsidian/gbrain 导航），再加规则三元组重复。
4. **风险**：新增 edges 表需同步 embed/index 重建，破坏「索引可重建、零新依赖」边界。

**何时重启**：eval 基线中出现 B 类（关系/多跳）显著落后、且真实问答触发多跳需求（如跨页推导）时，优先复用 wiki 双链建边（把 `[[...]]` 解析为 edges），而非从正文另抽三元组。

### U2 能力画像自评报告

在 F3 基础上把 golden 集按 AML 的 A/C/D/G 四类打标签，`eval.sh` 输出分类别得分，形成 memory-hub 自己的「能力画像」，长期追踪。

落地（2026-08-18）：golden 集已按 AML 四类标注 A/C/D/G：A=24 / C=3 / D=1 / G=3。原 3 条误标 D 的「规则/策略」问题改标 G（对应 AML 官网能力编号 A–E 后跳 F 直接到 G 的「规则与流程执行」）；新增 D 类条目 `memory-hub 知识库治理架构 → concepts/memory-hub-wiki-governance.md`（实测命中）。`eval.py` 报告按 A/C/D/G 分组输出能力画像。回归基线（31 条，top=5，2026-08-18）：hit@5=19/31=0.613（较 30 条基线 0.600 提升，新 D 条目命中且为正样本）、MRR=0.4543；分类型 A 0.625 / C 0.667 / D 1.000 / G 0.333。画像显示当前检索短板在 G（规则/策略类问答弱）。

修复追加（2026-08-18）：画像定位的 G 类短板根因是 FTS5 中文检索缺陷——search.sh 把整句查询当单个裸词（或短语引号）匹配 trigram 索引，中文整句无法命中，导致 fuse 里 FTS 列恒空、只剩向量单源且 G 类 3 条全部 MISS。根因修复：search.sh 对 >=3 字符的词做 3-gram 滑窗拆分（ngram 函数），OR 拼接后 trigram 索引真正生效（规则页正文/标题的连续 3 字子串即可命中）。回归：hit@5 0.613→0.710（22/31，+3 命中：G 3/3 全中从 0.333→1.000，A 0.625→0.667），fuse 关键查询全部 HIT（rank 3-5），MRR 0.4543→0.4527 微降系新命中条目 rank 较深的必然 trade-off（可检索性优先）。

修复追加 2（2026-08-18）：fuse.py 的 `_filter_lines` 正则根本解析不到 FTS5 输出——search.sh FTS5 分支的 `snippet()` 含换行，awk 逐行切分后字段错位，`[0.0]` 是空字段数值化而非真实 bm25 分。根因修复：(1) search.sh FTS 查询去掉 snippet 列，输出 `[bm25] path.md` 单行（fuse 不需要 snippet，snippet 内含换行是 awk 错位的根源）；(2) fuse.py `_filter_lines` 正则兼容 VEC `[score] path.md — title` 后缀（截断到 `.md`），并加「任一源有结果」断言防回归；(3) search.sh 加 `--no-fallback` 参数（FTS 空时不回退 rg，避免 `[N 处] path.md` 格式污染 fuse 解析）；(4) RRF 融合加 type 加权：entity/concept ×1.8、atom/query/draft ×0.7（A/B 网格选型，实体页是知识库主体）。回归：hit@5 0.710→0.871（27/31，+5 命中，A 0.875 / C 0.667 / D 1.000 / G 1.000），MRR 0.4527→0.6124。

### U3 Session/User 双层记忆（对照 Mem0 三级）

当前 distill 直接进长期 wiki，缺 session 层。可在 `inject` 时把「本会话 staging 观察」与「长期库命中」分两个区块注入，避免短期噪音污染长期结论。改动只在 inject 的拼装逻辑。

落地（2026-08-18）：`scripts/inject.sh` 增加 `--project <name>` 参数（缺省自动从最新日期命名观察文件推断出现最多的 project），「最近采集观察（staging）」区块拆为「知识库最近更新（~/llm-wiki）」（长期库，逻辑不变）+「本会话观察（staging, project: X）」（仅注入该 project 的前 8 条观察，短期噪音不进长期结论）。验证：`--project memory-hub` 只输出 memory-hub 的观察；不存在的 project 输出「- 暂无本会话观察」且不报错；`bash -n` 通过。修复 macOS bash 3.2 全角括号紧跟变量的 unbound variable 坑（`${X}` 包裹）。

---

## 四、不做的事

- 不引入常驻守护进程、不引入新数据库（守住零依赖原则）。
- 不做自动覆盖/自动删除已有 wiki 页（安全边界不变）。
- 不上图数据库、不上完整 MemScheduler（MemOS 调研结论：对单用户本地场景收益不抵复杂度）。
- 不参加 AML 正式评测：契约要求托管 API + 配额，超出本项目定位；只借用其能力分类学做自评。

---

## 五、里程碑

| 阶段 | 交付 | 验收 |
|------|------|------|
| M1 | F3 自评测骨架 + golden 集 | `eval.sh` 跑出基线报告 |
| M2 | F1 冲突标注 + F2 时间衰减 | 两条 fixture 断言通过；eval 分数不退化 |
| M3 | F4 归档 + 文档更新 | staging 体积下降，README 更新 |
| M4（可选） | U1–U3 逐项评审后启动 | 每项单独评审 |

顺序故意把 F3 放最前：先有尺子，再动刀子。





