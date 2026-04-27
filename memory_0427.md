已给你的项目加好了 `memory` 记忆压缩模块，并接入了现有会话保存流程。

主要改动：

- 新增 `toyclaw/memory.py`
  - 增加 `MemoryManager`，负责在 `sessions` 数量达到阈值时触发压缩归档
  - 将旧会话压缩写入 `memory/memory_YYYYMMDD_HHMMSS.jsonl`
  - 每条归档记录包含：
    - `key`、`created_at`、`updated_at`、`archived_at`
    - 原始消息数 `message_count`
    - 压缩后的 `messages`（只保留 user/assistant/tool，且做长度截断）
  - 归档成功后会删除被压缩的旧 session 文件（当前正在写入的会话会被排除）
- 修改 `toyclaw/session.py`
  - `SessionManager` 中集成 `MemoryManager`
  - 每次 `save()` 后自动调用压缩逻辑
  - 支持参数化：
    - `memory_trigger_count`（触发阈值，默认 20）
    - `memory_compact_batch_size`（每次压缩数量，默认 10）
- 修改 `toyclaw/config.py`
  - 新增配置项：
    - `memory_trigger_count: int = 20`
    - `memory_compact_batch_size: int = 10`
  - `load_config()` 已支持读取这两个字段
- 修改 `toyclaw/cli.py`
  - 在构建 `SessionManager` 时传入上述配置项

验证情况：

- 已做语法编译检查（`compileall`）通过
- 已检查 lints，无新增错误
- 做了一个最小冒烟测试：当 session 数达到阈值时，会生成 `memory/*.jsonl` 并清理旧 sessions，行为正常

如果你愿意，我下一步可以帮你再加两点：

- 在归档文件里写一条 `_type: "metadata"` 的头部（便于后续解析）
- 增加一个 `memory` 查询/回灌接口（让 agent 真正“用上”归档记忆，而不是只归档）