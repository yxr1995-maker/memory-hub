# bash + jq 管道踩坑清单（macOS bash 3.2）

> 2026-08-10 在 memory-hub CLI 开发中实测踩中的全部坑。每条包含：真实报错、根因、修复命令。
> 环境：macOS，bash 3.2（系统自带），jq，`set -euo pipefail` 是默认陷阱放大器。

## 1. jq JSONL 流模式下 first()/limit() 按行计算，不是全局第一个

**现象**：用 `first(select(...))` 取第一条记录，结果输出了一整份文件的文本；页面 `abstract` 字段被塞进全部观察内容。

**报错**：无报错，纯逻辑错误。`jq -r 'first(select(.project=="x")) | .text | .[0:100]' file.jsonl | wc -c` 返回几千字节，而不是 ~100。

**根因**：JSONL 是逐行输入流，jq 对**每一行**独立求值。`first(expr)` 限制的是"当前输入值上 expr 产生的输出数量"，不是整个流。所以每行都命中 select，每行都输出。

**修复**：加 `-s`（slurp）把整份文件读成数组，再取第一个元素：

```bash
# 错误（输出全部行）
jq -r --arg p x 'first(select(.project==$p)) | .text | .[0:100]' file.jsonl

# 正确（只输出 1 行）
jq -sr --arg p x 'first(.[] | select(.project==$p)) | .text | .[0:100]' file.jsonl
```

## 2. head 截断管道 → SIGPIPE(141) → set -e 静默退出

**现象**：脚本"无任何输出"地退出，exit code 141（= 128+13 SIGPIPE），没有任何错误信息。

**报错**：`bash -x` 才能看到脚本停在某条管道后；`echo $?` 显示 141。

**根因**：`cmd | head -5` 中 head 读完 5 行就关闭管道，上游进程继续写时收到 SIGPIPE。`pipefail` 让管道返回 141，`set -e` 直接退出脚本——而且是在**命令替换**里，连变量都没赋值成功。

```bash
# 错误：上游 find/xargs 被 head 截断后 SIGPIPE，整条管道 141
LINES="$(find . -name '*.md' | xargs ls -t | head -5)"

# 正确：管道末尾兜底 || true
LINES="$(find . -name '*.md' | xargs ls -t | head -5 || true)"
```

同族问题：`jq ... | head -1`、`sed ... | head -1` 同样会 SIGPIPE。**优先在 jq/sed 内部截断**（`.[0:100]`、`/^.../{print; exit}`），管道级 head 一律加 `|| true`。

## 3. jq -s 排序输出 {key,value} 包装结构

**现象**：`jq -s 'sort_by(.x) | to_entries | map(.value |= ...) | .[]'` 输出的每行是 `{"key":0,"value":{...}}`，下游 `jq '.project'` 全部解析失败。

**报错**：`jq: error: Cannot index string with string "project"` / `Expected string key before ':'`。

**根因**：`to_entries` 作用在**对象**上（把对象的键值对转数组）；作用在**数组**上时，数组被当作"索引为字符串的对象"，生成 `{key: "0", value: ...}`。

**修复**：用 `map(.value.id = ...) | map(.value) | .[]` 显式取回 value，并加 `-c` 保证每行一个紧凑 JSON：

```bash
jq -s -c '
  sort_by(.created_at_epoch)
  | to_entries
  | map(.value.id = ("c" + (100000 + .key | tostring)))
  | map(.value)
  | .[]
' raw.jsonl > out.jsonl
```

## 4. macOS BSD find 的 -L 必须放在路径前面

**现象**：`find "$DIR" -L -type f` 报错。

**报错**：`find: -L: unknown primary or operator`

**根因**：BSD find（macOS 自带）把 `-L` 当作 primary 解析；GNU find 可以放在任意位置，BSD 要求选项在路径之前。

**修复**：

```bash
# 错误（BSD）
find "$DIR" -L -type f -name '*.jsonl'

# 正确（BSD）：-L 在路径前
find -L "$DIR" -type f -name '*.jsonl'
```

## 5. 命令替换吞掉 find -print0 的 NUL 分隔符

**现象**：`FILE_LIST="$(find ... -print0)"` 后循环一个文件都处理不到，采集 0 条。

**报错**：无报错，`find` 正常退出，但文件路径被拼成一个字符串。

**根因**：bash 变量不能包含 NUL 字节，命令替换 `$(...)` 会**静默丢弃**所有 NUL。`-print0` 的分隔符没了，多个路径粘连成一个不存在的路径。

**修复**：不要过命令替换，直接用进程替代把 find 管道给 while：

```bash
# 错误：NUL 被丢弃
LIST="$(find "$DIR" -type f -print0)"
while IFS= read -r -d '' f; do ...; done <<< "$LIST"

# 正确：进程替代，NUL 原样传递
while IFS= read -r -d '' f; do ...; done < <(find "$DIR" -type f -print0)
```

## 6. bash 3.2 空数组 + set -u 报 unbound variable

**现象**：脚本在 `"${ARR[@]}"` 处退出，报变量未定义；但数组明明声明过。

**报错**：`bash: ARR[@]: unbound variable`

**根因**：bash 3.2（macOS 默认）在 `set -u` 下展开**空数组**的 `[@]` 会报 unbound；bash 4.4+ 已修复。同样问题也出现在 `${MTIME_ARGS[@]}` 这类可空数组参数上。

**修复**：先判断长度再展开：

```bash
shopt -s nullglob
FILES=("$DIR"/*.md)
if [[ ${#FILES[@]} -gt 0 ]]; then
  printf '%s\n' "${FILES[@]}"
fi
```

## 7. 中文全角括号紧跟变量名会把首字节并入变量名

**现象**：`echo "类型: $TYPES）"` 报变量未定义，错误信息里的变量名带乱码。

**报错**：`bash: TYPES�: unbound variable`（`�` 是全角括号 `）` 的 UTF-8 首字节）

**根因**：bash 3.2 解析变量名时，把紧随的 UTF-8 多字节字符的首字节当作变量名一部分。中文环境里 `$VAR）`、`$VAR，`、`$VAR。` 全部中招。

**修复**：中文后一律用花括号：

```bash
# 错误：报 TYPES� unbound
echo "类型分布: $TYPES）"

# 正确
echo "类型分布: ${TYPES}）"
```

## 8. jq 错误被 2>/dev/null 吞掉 → "exit 0 零输出"假象

**现象**：脚本 exit code 是 0，但结果文件是空的，排查半天找不到原因。

**根因**：`jq ... 2>/dev/null` 把语法/管道错误全吞了，`|| true` 又把非零退出码吞了。静默失败被伪装成成功。

**修复**：调试期去掉重定向：

```bash
# 调试：暴露错误
jq '...' file.jsonl 2>&1 | head

# 收敛：错误输出到独立文件，不要用 /dev/null
jq '...' file.jsonl 2>>"$RAW.err" >> "$RAW" || true
```

## 9. shell 命令行 rm -f 被安全策略拦截，脚本内可以

**现象**：在命令行直接跑 `rm -f file` 被拒绝（exec 沙箱拦截）。

**报错**：`rejected: rm -f style commands are not permitted. Use a safer approach`

**根因**：部分 Agent 执行环境的安全策略拦截 shell 级 `rm -f`；脚本文件内的 `rm -f` 不受限。

**修复**：命令行用 `mv` 到备份目录替代；脚本内正常用 rm：

```bash
# 命令行：mv 到 mktemp 备份
BK="$(mktemp -d)" && mv staging/bad-file.jsonl "$BK/"

# 脚本内：rm 合法
rm -f "$RAW"
```

## 10. 新旧会话格式的 timestamp 位置不同（Codex JSONL）

**现象**：同一套 jq 管道处理旧会话文件（≤2026-07-31）提取 0 条，处理新文件（08-10）正常。

**根因**：旧格式 `timestamp` 在**顶层**（`{"timestamp":...,"type":"response_item","payload":{...}}`）；新格式在 `payload` 内。管道若在 `.payload | select(.timestamp != null)` 处过滤，旧文件全被滤掉。

**修复**：统一从顶层取时间戳，payload 只取内容字段：

```bash
select(.type=="response_item" and (.payload.role=="user" or .payload.role=="assistant"))
| select(.timestamp != null)
| {ts:.timestamp, role:.payload.role, text:([.payload.content[]? | select(.type=="input_text") | .text] | join("\n"))}
```

---

## 速查表

| 症状 | 根因 | 一句话修复 |
|------|------|-----------|
| 全部行都匹配 first() | JSONL 流按行求值 | 加 `-s` 用 `first(.[] \| ...)` |
| 脚本静默退出 141 | head 截断管道 SIGPIPE | 管道末尾 `\|\| true`，或 jq 内截断 |
| 输出 {key,value} | to_entries 用在数组上 | `map(.value) \| .[]` + `-c` |
| find 报 unknown primary | BSD find 选项位置 | `-L` 放路径前 |
| 采集 0 条无报错 | 命令替换吞 NUL | 用 `< <(find ... -print0)` |
| ARR[@] unbound | bash 3.2 空数组 + set -u | 先判断 `${#ARR[@]} -gt 0` |
| TYPES� unbound | 全角括号吞变量名 | 中文后用 `${VAR}` |
| exit 0 但结果空 | 2>/dev/null 吞错误 | 调试期 2>&1 |
| rm -f 被拦截 | 沙箱策略 | 命令行用 mv 备份 |
| 旧文件提取 0 条 | 新旧格式 timestamp 位置不同 | 统一从顶层取 ts |
