# memory-hub UI

memory-hub 的记忆管理面板与 REST 服务说明（简体中文）。

## 架构

```
Codex 左侧边栏 (codex++)
  └─ 用户脚本 memory-hub-admin.js
       └─ fetch() → http://127.0.0.1:8787
            └─ scripts/server.py (Python stdlib，仅本机)
                 ├─ ~/llm-wiki       (知识库 .md)
                 ├─ memory-hub/staging/  (原始观察)
                 ├─ ~/.memory-hub/trash/ (回收站)
                 └─ scripts/{status,search,ask,metrics,obs_search}.sh
```

脚本市场里的 `memory-hub-admin.js` 不直接跑 shell，只走 REST。所有写操作（新建、删除页面）都在 `server.py` 内完成，shell 只做查询，路径穿越与权限校验由服务端统一兜底。

## 启动

两种方式，任选其一：

```bash
# 方式 A：走 wrapper（推荐）
bash memory-hub.sh serve

# 方式 B：直起 Python 服务，可指定端口
python3 scripts/server.py --port 8787 --host 127.0.0.1
```

默认地址 `http://127.0.0.1:8787`，所有响应带 CORS `Access-Control-Allow-Origin: *`。

## 载入方式

1. **脚本市场自动载入（推荐）**
   Codex 脚本市场会拉取 `ui/index.json`；市场页面出现 "Memory Hub Admin" 后点安装，
   脚本会被复制到 `~/Codex/Scripts/` 并在 codex++ 里显示入口。
2. **手动载入**
   把 `ui/memory-hub-admin.js` 拷到 Codex 的用户脚本目录
   （macOS: `~/Codex/Scripts/`），重启 Codex 或在 "Codex 助手 > 脚本" 里刷新列表。

脚本入口名：**Memory Hub Admin**。首次进入会自动检查 `/health`。

## API 速查表

| 方法   | 路径                       | 说明                                |
| ------ | -------------------------- | ----------------------------------- |
| GET    | `/health`                  | `{"status":"ok"}`，存活探针          |
| GET    | `/api/overview`            | 页面数、观察数、索引大小、近期会话数    |
| GET    | `/api/pages?type=&tag=&q=&offset=&limit=` | 分页 + 过滤，`items[]` 含 frontmatter 元数据 |
| GET    | `/api/page?path=<wiki 相对路径>` | 页面全文 + `meta{}`；不存在 404 |
| POST   | `/api/page`                | JSON `{path, content}`，仅 wiki 内 `.md`；非法 400 |
| DELETE | `/api/page?path=`          | 移入 `~/.memory-hub/trash/`（可恢复）；不存在 404 |
| GET    | `/api/tags`                | `tags[]`，每项 `{tag, count}`       |
| GET    | `/api/observations?q=&project=&offset=&limit=` | staging 观察，新 → 旧 |
| GET    | `/status` `/search` `/ask` `/metrics` | 兼容旧端点，返回 `text/plain` |

## 故障排查

**面板连不上 → 先看 `/health`**

```bash
curl -s http://127.0.0.1:8787/health
```

返回 `{"status":"ok"}` 说明服务端 OK，继续看浏览器 Console 的 CSP/CORS 报错。

**CSP / CORS**：`server.py` 已设置 `Access-Control-Allow-Origin: *`。
如果 Codex 脚本运行环境仍拦截，可尝试：

```bash
# 用 dev harness 手工跑（不依赖 codex++）
open ui/dev-harness.html
```

**"删除"不是真删**：`DELETE /api/page` 会把文件移到 `~/.memory-hub/trash/<date>/<path>`，
按原目录结构保留；`rm -rf ~/.memory-hub/trash` 前请三思。

**脚本市场不出现**：`bash memory-hub.sh index` 重新生成 `ui/index.json`，
然后去 codex++ 里点刷新。

## 测试

```bash
bash ui/test-api.sh        # 默认 8899
bash ui/test-api.sh 8899   # 指定端口
```

脚本会先等 `/health` 就绪，失败才自起服务；退出时清理自启进程。
