# B 激进全自动路线设计规格：验证记录

日期：2026-08-31  
工作区：`/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto`  
分支：`codex/roadmap-full-auto`

| 成功准则 | 实际场景 | 调用 | 二元可观察结果 | 捕获工件 |
|---|---|---|---|---|
| 八项 B 路线要求、退出开关与无占位 | 对正式规格执行确定性结构/关键词/禁止占位断言 | `python3` 内联断言，配合 `git diff --no-index --check /dev/null docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md` | exit 0；输出 `SPEC_LINT_OK lines=356 required=18 placeholders=0`；diff check 无输出 | 本文件；规格 `docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md` |
| 现有标准库测试集 | 在隔离工作区发现并执行 `tests/` | `python3 -m unittest discover -s tests -v` | exit 0；`Ran 12 tests ... OK` | 本文件（命令输出摘要） |
| 仓库静态运行态一致性 | 执行现有 verify 入口 | `./memory-hub.sh verify` | exit 0；输出 `== verify: 全部通过 ==` | 本文件（命令输出摘要） |
| 融合检索自检 | 执行内置 fuse 自检 | `MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py` | exit 0；输出 `fuse._selfcheck OK` | 本文件（命令输出摘要） |
| 主工作区保护 | 在主工作区复查状态 | `git -C /Users/earan/Documents/memory-hub status --short` | 仅既有 ` M scripts/autolink.py`；本任务未修改、暂存、回滚该文件 | 本文件（范围证据） |

## 验证边界

`pytest tests/` 未执行测试，二进制结果为 exit 127、`pytest: command not found`；本工作区没有安装 pytest，且未为纯规格任务安装依赖。仓库内可由标准库发现的 12 个 `unittest` 已实际通过。真实 B 路线 CLI 场景是本规格为后续实现规定的验收项；本次没有实现或运行尚不存在的 `scope-backfill`、`--safe`/`--no-auto` 自动化功能，也没有触碰 live `~/llm-wiki`。

## 提交后补充

- 完整提交 SHA：`893b688f466c451128ce91d8e9b47f386062a50a`
- 提交内容：仅 `docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md`（356 行新增）。
- 本证据文件位于被忽略的 `.omo/evidence/`，不进入规格提交。

## 完成钩子复核：直接命令与原始可观察结果

本节在完成钩子要求后重新执行。下列输出为实际命令的精简原始 stdout/stderr；没有凭据、请求体或用户数据。

```text
CMD: git status --porcelain=v1
EXIT_STATUS=0

CMD: git rev-parse HEAD && git diff-tree --no-commit-id --name-only -r HEAD
893b688f466c451128ce91d8e9b47f386062a50a
docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md
EXIT_STATUS=0

CMD: python3 spec assertions
SPEC_ASSERTIONS_OK lines=356 required=18
EXIT_STATUS=0

CMD: git diff --check HEAD^ HEAD -- docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md
EXIT_STATUS=0

CMD: git status --porcelain=v1 | wc -l
       0
EXIT_STATUS=0

CMD: test -s .omo/evidence/roadmap-full-auto-design-validation.md
EXIT_STATUS=0
```

```text
CMD: python3 -m unittest discover -s tests -v
Ran 12 tests in 0.515s
OK
EXIT_STATUS=0

CMD: ./memory-hub.sh verify
PASS  toml 解析通过（49 个 automation.toml 均合法）
PASS  hook 健康（events=4 commands=4，引用脚本均存在）
PASS  MCP server 指向真实文件（/Users/earan/Documents/memory-hub/mcp/server.py）；hook_states=4
PASS  DB 一致性通过（db_rows=7 toml_files=49）
PASS  wiki 非归档区 token 扫描通过（0 命中）
PASS  非 raw 区死链 0 <= 20（raw 区 0）
PASS  concepts/queries 下无 memoryhub 页
== verify: 全部通过 ==
EXIT_STATUS=0

CMD: MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py
fuse._selfcheck OK
EXIT_STATUS=0
```

```text
CMD: git -C /Users/earan/Documents/memory-hub status --porcelain=v1
 M scripts/autolink.py
EXIT_STATUS=0

CMD: git -C /Users/earan/Documents/memory-hub diff --name-only
scripts/autolink.py
EXIT_STATUS=0
```

### 更正

早先记录的 `git diff --no-index --check /dev/null <新文件>` 会因 `--no-index` 将“新增文件存在”编码为退出码 1，即使没有空白错误，不能作为 exit 0 断言。复核已经使用已提交范围的 `git diff --check HEAD^ HEAD -- <file>`；它实际 exit 0 且无输出，因此规格提交不存在 Git whitespace error。
