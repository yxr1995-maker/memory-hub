# Portable Memory Hub MCP Wrapper

## 概述

这是一个独立可移植的 MCP 服务器包装脚本，可以从任何位置运行 memory-hub MCP 服务器。

## 文件位置

- 主脚本: `~/.local/bin/memory-hub-mcp`
- 项目根目录: `~/Documents/memory-hub` 或 `~/memory-hub`

## 工作原理

脚本会自动在以下位置查找 memory-hub 项目:
1. 脚本目录的上一级 (`~/.local/bin/../memory-hub`)
2. 脚本目录的上两级 (`~/.local/bin/../../memory-hub`)
3. `~/Documents/memory-hub`
4. `~/memory-hub`

找到项目后，将项目根目录添加到 Python 路径，然后加载并运行 `mcp/server.py`。

## 使用方式

直接运行:
```bash
memory-hub-mcp
```

或从任意目录运行:
```bash
~/.local/bin/memory-hub-mcp
```

## 与原有 mcp-wrapper.py 的区别

- **原有版本**: 位于项目根目录，使用 `importlib.util.spec_from_file_location` 加载，存在模块命名冲突问题
- **新便携版本**: 位于 `~/.local/bin/`，自动定位项目根目录，使用正确的模块加载方式

## 注意事项

- 确保 `mcp` Python 包已安装: `pip install mcp`
- 脚本在找到项目后会改变工作目录到项目根目录
