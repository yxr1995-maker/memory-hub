# Codex 本地插件源码准备与撤回

此入口属于分发前的隔离准备工具。它调整已明确指定的本地源码链接和 Memory Hub runtime/settings，不注册插件、不刷新 Desktop 缓存、不启动后台 worker，也不自动通过经历功能的 M2/M3/M5 门槛。

## 命令与作用域

沿用现有 `memory-hub.sh codex` 入口，显式提供 source/target 计划和数据目录。source 是含 `.codex-plugin/plugin.json` 的 Memory Hub 插件源码目录；target 是准备切换的本地路径。两个路径必须绝对、互不包含，不能覆盖普通文件。

```json
{
  "source": "/absolute/memory-hub/plugins/memory-hub",
  "target": "/absolute/isolated-home/plugin-source"
}
```

```bash
MEMORY_HUB_DATA=/absolute/isolated-data WIKI_PATH=/absolute/isolated-wiki \
  ./memory-hub.sh codex stage-install --plan /absolute/plan.json --home /absolute/isolated-home

./memory-hub.sh codex unstage-install --backup /absolute/isolated-data/backups/codex-install-RECEIPT
```

`stage-install` 输出备份回执目录以及 `registered=false`。它仍沿用当前 Python 解释器；真正启动 MCP 前，应使用拥有项目依赖的解释器执行入口。准备成功不能证明 MCP 已可启动，更不能证明 Desktop 已加载配置或自然使用了记忆。

## 撤回会处理什么

撤回以当前协议的完整回执为依据，仅处理本次安装准备实际修改的插件链接、`codex-runtime.json` 和 `codex-memory-settings.json`。原路径不存在时恢复为不存在；原路径是符号链接或目录时恢复原对象。原库和外部媒体不在撤回范围。

全局 `~/.codex/config.toml` 和 marketplace 不由此入口写入，也不会在撤回时用旧副本覆盖。手动注册、已安装的缓存副本、项目 override、信任条目和 LaunchAgent 是不同的状态，不能把源码撤回当成它们的卸载。

相关文件在准备后被用户修改时，撤回会报告冲突并保留修改。没有当前协议回执的旧备份不得自动推断恢复方法。保留回执目录以供核对，不要将含本机配置的备份目录作为公开分发材料。

## 验证与发布边界

本工作包只在隔离 HOME/data/wiki 路径执行。进程内异常的恢复、重复撤回、原目录/链接保真、用户后续修改保护由专项测试和实际 CLI 验收覆盖；不宣称经受过断电或任意外部进程并发修改的完整事务验证。

经历样例使用 `synthetic_fixture` 标记的合成材料；它们不是用户证言、受众反馈或已观测商业结果。公开分发仍需单独核对源码、依赖、样例许可和当前验收证据，不能打包本机 `docs/evidence` 中的原始会话、配置或真实知识库。
