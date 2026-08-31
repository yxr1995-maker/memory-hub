# roadmap-full-auto Superpowers 计划验证证据

- 验证时间（UTC）：`2026-08-30T18:20:56Z`
- 验证时间（本地）：`2026-08-31 02:20:56 CST`
- 隔离 worktree：`/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto`
- 被验计划：`docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`
- 权威规格：`docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md`
- 规格基线提交：`893b688`

## 验收标准、场景、调用和二元可观察结果

| 成功标准 | 实际场景 | 调用 | 二元可观察结果 |
|---|---|---|---|
| 禁止占位符并满足 writing-plans 任务结构 | 扫描计划全文，要求 14 个顺序任务；每任务含 Files、Interfaces、至少 5 个 checkbox 步骤、精确 git add/commit | 下方 `plan_static_validation` Python 验证器 | `result=PASS`，exit 0，禁用占位符命中 0 |
| 覆盖权威规格全部章节 | 解析计划中的 Spec Coverage Matrix | 同一验证器 | `spec_rows=11 sections=[1..11]`，exit 0 |
| 类型与接口一致 | 比较声明与伪代码实现的 `infer_scope`、`rank_results`、`MemoryService.search`、`MemoryService.ask_context`、`commit_manifest`；拒绝旧默认签名 | 同一验证器 | `interface_checks=5 shared_dataclasses=4`，exit 0 |
| 每任务有明确文件所有权 | 解析每个 Task 的 Files block 并拒绝重复 Create 所有者 | 同一验证器 | `tasks=14`、`unique_create_paths=32`，exit 0 |
| Markdown 与 diff 无格式错误 | 检查 fence 配对及 Git whitespace errors | 验证器；`git diff --check -- docs/superpowers/plans/2026-08-31-roadmap-full-auto.md` | `code_fences=118 balanced=True`；git diff check exit 0 |
| 规格未被改写且等同提交 893b688 | 比较 worktree 规格与提交对象的 SHA-256 | `shasum` 与 `git show ... | shasum` | 两者均为 `92cfece...ecba9d`；exit 0 |
| 仅计划文件为工作树变更 | 检查 `git status --short` 精确输出 | `git status --short` | 仅 `?? docs/superpowers/plans/`；exit 0 |
| 计划证据非空 | 文件大小检查 | `test -s docs/superpowers/plans/2026-08-31-roadmap-full-auto.md` | exit 0 |

## 实际验证器

执行了一个只读 Python 验证器，具体断言为：

```text
1. 禁止 TBD、TODO、implement later、fill in details、appropriate error handling、
   handle edge cases、write tests for the above、similar to Task、类似 Task、待补、
   待定、稍后实现、适当处理及以省略号结束的伪实现。
2. 必须恰有 14 个连续编号 Task。
3. 每个 Task 必须包含 Files、Interfaces、至少五个 checkbox Step、git add、git commit。
4. 每个 Task 必须拥有至少一个文件；同一路径不得在多个任务中声明 Create。
5. Spec Coverage Matrix 必须恰有 11 行并覆盖规格章节 1–11。
6. 五个关键接口的声明和实现签名必须同时存在；四个共享 dataclass 必须各定义一次。
7. 禁止旧签名 expand: bool = False 和 scope_id: str = ""。
8. Markdown code fence 数量必须为偶数。
```

## 捕获的原始输出

```text
CHECK=plan_static_validation
plan=/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto/docs/superpowers/plans/2026-08-31-roadmap-full-auto.md
lines=1248
tasks=14
task_file_counts=[(1, 9), (2, 5), (3, 4), (4, 2), (5, 3), (6, 5), (7, 6), (8, 3), (9, 2), (10, 2), (11, 4), (12, 7), (13, 6), (14, 3)]
unique_create_paths=32
spec_rows=11 sections=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
code_fences=118 balanced=True
forbidden_placeholder_hits=0
interface_checks=5 shared_dataclasses=4
result=PASS
EXIT_plan_static_validation=0
EXIT_git_diff_check=0
plan_sha256=3175e27904b3bc1f744f031596119161a32010f31c1c7848b602af9d52697882
spec_worktree_sha256=92cfece0317b8047bc5f213f407789e84bbe166dc5aafc7b5c71e17321ecba9d
spec_commit_893b688_sha256=92cfece0317b8047bc5f213f407789e84bbe166dc5aafc7b5c71e17321ecba9d
EXIT_spec_identity=0
git_status_begin
?? docs/superpowers/plans/
git_status_end
EXIT_scope_only_plan=0
EXIT_plan_nonempty=0
```

## 判断

`PASS`。计划文件满足本次计划编写验收标准；规格对象与提交 `893b688` 一致；未修改功能代码、规格或 live wiki。`.omo/evidence/` 被仓库 `.gitignore` 忽略，因此记录本证据后 `git status --short` 的可见工作树变更仍应只有未跟踪的计划目录。
