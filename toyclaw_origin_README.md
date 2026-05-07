# 🐾 ToyClaw

**千行代码实现的 OpenClaw。**

ToyClaw 是一个极简但功能完整的 AI Agent 框架，参考 [nanobot](https://github.com/HKUDS/nanobot) 架构设计，用一千多行 Python 实现了 Agent Loop、Tool Use、Cron 定时任务、Heartbeat 心跳、Sub-agent 后台任务、Web Search 等核心能力。

---

## 目录

- [设计思路](#设计思路)
- [技术架构](#技术架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [内置工具](#内置工具)
- [Skills 扩展](#skills-扩展)
- [核心模块详解](#核心模块详解)
- [测试](#测试)

---

## 设计思路

### 为什么做这个？

OpenClaw 已经达到了惊人的40w+行的代码量，抽象层套抽象层，耦合程度堪比仙人排队跳———套里还有套。它看起来很复杂，各种功能：聊天软件支持、多 Provider、Skills、MCP、长Memory、Cron定时任务、Sub-agent……但核心能力其实很简单。Agent Loop 就是一个 while 循环：LLM 思考，调用工具，拿到结果，再思考，直到给出最终回复。其他一切都是生产级需求的包装。

我决定砍掉一切非核心复杂度，仅用 1000 行 Python 代码实现一个功能完整的OpenClaw。我把它命名为toyclaw。在openclaw自身的辅助下，总共花费时间不超过2小时，代码量千行出头，但是它能做的事情几乎和 nanobot 一样——支持工具调用、定时任务、skills扩展、Session 持久化等核心功能。

### 核心取舍

| 保留 | 砍掉 | 原因 |
|------|------|------|
| Agent Loop（LLM ↔ Tool） | MessageBus | CLI 直连 Agent，不需要消息总线 |
| Tool Registry + 9 个内置工具 | MCP 协议 | 内置工具直接注册，不需要外部协议 |
| Cron 定时任务 | 多 Channel（Telegram/Discord） | 只做 CLI 入口 |
| Heartbeat 心跳 | Memory Consolidation | Agent 自己用 write_file 管理记忆 |
| Sub-agent 后台任务 | 多 Provider / LiteLLM | 一个 OpenAI 兼容端点够用 |
| Skills 动态发现 | 多模态（图片/语音） | 纯文本交互 |
| Session 持久化 | OAuth / 权限系统 | 个人工具，无需鉴权 |

### 设计原则

1. **单文件可读** — 每个模块职责单一，打开就能读懂
2. **asyncio 原生** — 全链路 async，Tool 执行、LLM 调用、Cron 定时器都不阻塞
3. **依赖最小化** — 只用 4 个运行时依赖（openai / httpx / croniter / readability-lxml）
4. **组合优于继承** — 组件通过回调函数组合，不搞深层继承链

---

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│                      CLI REPL                       │
│              (cli.py — 交互/单次模式)                │
└─────────────┬───────────────────────────────────────┘
              │ user input
              ▼
┌─────────────────────────────────────────────────────┐
│                   Agent Loop                        │
│           (agent.py — 核心处理引擎)                  │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐  │
│  │ Context  │    │ Provider │    │ Tool Registry │  │
│  │ Builder  │    │ (OpenAI) │    │  (9 tools)    │  │
│  └──────────┘    └──────────┘    └───────────────┘  │
└──────┬──────────────────────────────────┬───────────┘
       │                                  │
       ▼                                  ▼
┌──────────────┐  ┌──────────┐  ┌─────────────────┐
│   Session    │  │  Skills  │  │  Background Svc │
│  (JSONL)     │  │  Loader  │  │                 │
└──────────────┘  └──────────┘  │  ┌───────────┐  │
                                │  │   Cron    │  │
                                │  └───────────┘  │
                                │  ┌───────────┐  │
                                │  │ Heartbeat │  │
                                │  └───────────┘  │
                                │  ┌───────────┐  │
                                │  │ Sub-agent │  │
                                │  └───────────┘  │
                                └─────────────────┘
```

### 数据流

```
User Input
  → ContextBuilder.build_messages()  (system prompt + history + skills + runtime)
    → Provider.chat()                (发送到 LLM)
      → LLM 返回 tool_calls?
        ├─ YES → ToolRegistry.execute() → 结果追加到 messages → 回到 Provider.chat()
        └─ NO  → 最终回复 → Session 持久化 → 返回给用户
```

---

## 项目结构

```
toyclaw/
├── pyproject.toml                     # 构建配置 + 依赖声明
├── README.md
├── toyclaw/
│   ├── __init__.py              (3)   # 版本号
│   ├── __main__.py              (5)   # python -m toyclaw 入口
│   ├── config.py               (43)   # JSON 配置加载
│   ├── provider.py             (98)   # OpenAI SDK 封装
│   ├── context.py             (129)   # System prompt + 消息组装
│   ├── session.py              (94)   # JSONL 会话持久化
│   ├── skills.py               (57)   # Skills 目录发现
│   ├── agent.py               (184)   # 核心 Agent Loop
│   ├── subagent.py            (125)   # 后台子 Agent
│   ├── cron.py                (241)   # 定时任务引擎
│   ├── heartbeat.py           (115)   # 心跳唤醒服务
│   ├── cli.py                 (174)   # CLI REPL + 组件装配
│   └── tools/
│       ├── __init__.py          (1)
│       ├── base.py             (77)   # Tool 抽象基类 + Registry
│       └── builtin.py         (452)   # 9 个内置工具
└── tests/
    ├── test_functional.py             # 12 项单元测试
    └── test_e2e.py                    # 4 项端到端测试
```


---

## 快速开始

### 环境要求

- Python ≥ 3.11
- 一个 OpenAI 兼容的 API 端点（OpenAI / DeepSeek / OpenRouter / 本地 vLLM 等）

### 安装

```bash
git clone https://github.com/yourname/toyclaw.git
cd toyclaw
pip install -e .
```

### 配置

创建配置文件 `~/.toyclaw/config.json`：

```json
{
  "api_key": "sk-your-api-key",
  "api_base": "https://api.openai.com/v1",
  "model": "gpt-4o-mini"
}
```

### 运行

```bash
# 交互模式
toyclaw

# 或者
python -m toyclaw

# 单次执行
toyclaw -m "帮我写一个 hello world"

# 指定配置文件
toyclaw -c /path/to/config.json
```

### REPL 命令

| 命令 | 说明 |
|------|------|
| `/new` | 清空当前会话，开始新对话 |
| `exit` / `quit` / `/exit` / `:q` | 退出 |

---

## 配置说明

`~/.toyclaw/config.json` 支持以下字段：

```json
{
  "api_key": "",                  // 必填 — LLM API Key
  "api_base": "https://api.openai.com/v1",  // API 端点
  "model": "gpt-4o",             // 模型名称
  "workspace": "~/.toyclaw/workspace",      // 工作区路径
  "brave_api_key": null,          // Brave Search API Key（可选）
  "max_iterations": 30,           // Agent 单轮最大迭代次数
  "heartbeat_interval": 1800,     // 心跳间隔（秒）
  "heartbeat_enabled": true       // 是否启用心跳
}
```

### 工作区目录结构

启动后自动创建：

```
~/.toyclaw/workspace/
├── memory/          # 长期记忆（MEMORY.md 等）
├── sessions/        # 会话历史（JSONL 格式）
├── skills/          # 技能扩展目录
├── cron/            # 定时任务持久化
├── AGENTS.md        # Agent 指令文件（可选）
├── SOUL.md          # 人格设定（可选）
├── HEARTBEAT.md     # 心跳任务清单（可选）
└── ...
```

---

## 内置工具

ToyClaw 提供 9 个内置工具，LLM 可自主决定何时调用：

| 工具 | 名称 | 功能 |
|------|------|------|
| 📖 ReadFile | `read_file` | 读取文件内容（128KB 上限，支持路径解析） |
| ✏️ WriteFile | `write_file` | 写入文件（自动创建父目录） |
| 🔧 EditFile | `edit_file` | 精确搜索替换（失败时给出 diff 提示） |
| 📁 ListDir | `list_dir` | 列出目录内容 |
| 💻 Shell | `exec` | 执行 Shell 命令（带安全拦截 + 60s 超时） |
| 🔍 WebSearch | `web_search` | Brave Search API 网页搜索 |
| 🌐 WebFetch | `web_fetch` | 抓取网页并提取正文（readability 算法） |
| ⏰ Cron | `cron` | 创建 / 列出 / 删除定时任务 |
| 🚀 Spawn | `spawn` | 派生后台子 Agent 执行耗时任务 |

### Shell 安全防护

内置危险命令拦截，以下模式会被自动阻止：

```
rm -rf, del /f, rmdir /s, format, mkfs, diskpart,
dd if=, shutdown, reboot, fork bomb
```

---

## Skills 扩展

在 `workspace/skills/` 下创建子目录，放入 `SKILL.md` 即可被自动发现：

```
workspace/skills/
└── my_skill/
    ├── SKILL.md          # 技能说明（必须存在）
    ├── script.py         # 任意辅助文件
    └── ...
```

Agent 的 system prompt 会自动包含所有已发现技能的摘要。LLM 可以通过 `read_file` 工具读取 `SKILL.md` 获取详细指令。

---

## 核心模块详解

### Agent Loop（agent.py）⭐

这是整个系统的心脏。核心逻辑只有一个循环：

```python
for _ in range(max_iterations):
    response = await provider.chat(messages, tools)
    if response.has_tool_calls:
        for tc in response.tool_calls:
            result = await tools.execute(tc.name, tc.arguments)
            messages.append(tool_result)
    else:
        return response.content  # 最终回复
```

关键设计：
- **最大迭代次数保护** — 防止 LLM 无限循环调用工具
- **`<think>` 标签清理** — 兼容 DeepSeek 等带思考过程的模型
- **Tool result 截断** — Session 历史中工具结果限制 500 字符，防止 context 爆炸
- **Runtime context 注入** — 每次对话自动注入当前时间、频道信息

### Provider（provider.py）

薄封装层，将 OpenAI SDK 的响应标准化为 `LLMResponse` 数据类：

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str

@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]
```

兼容任何 OpenAI 格式的 API（OpenAI / DeepSeek / OpenRouter / vLLM / Ollama）。

### Cron Service（cron.py）

支持三种调度模式：

| 模式 | 示例 | 说明 |
|------|------|------|
| `at` | `2026-03-10T10:30:00` | 一次性定时，执行后自动删除 |
| `every` | `every_seconds=300` | 固定间隔循环 |
| `cron` | `0 9 * * *` | 标准 cron 表达式（依赖 croniter） |

任务持久化为 JSON，重启后自动恢复。触发时通过回调调用 `Agent.process()`，形成完整闭环。

### Heartbeat（heartbeat.py）

定期读取 `workspace/HEARTBEAT.md`，将内容交给 LLM 判断是否需要执行。LLM 通过专用 tool call 返回 `skip`（无事可做）或 `run`（有待办任务），避免无意义的 Agent 唤醒。

### Sub-agent（subagent.py）

后台 `asyncio.Task` 运行独立的 Agent Loop，拥有受限工具集（**不含 cron 和 spawn**，防止递归）。完成后通过 `on_complete` 回调通知主进程。

### Session（session.py）

JSONL 格式持久化：
- 每个会话一个 `.jsonl` 文件
- 第一行为 metadata（key, created_at, updated_at）
- 后续每行一条消息
- 加载时自动对齐到 user turn（丢弃开头孤立的 tool 结果）

---

## 测试

### 单元测试（无需 API Key）

```bash
python tests/test_functional.py
```

覆盖 12 项：Config、Provider 数据类、ToolRegistry、文件工具（读/写/编辑/列目录）、Shell（含安全拦截）、Session 持久化、Skills 发现、Context 组装、Cron 全生命周期、CronTool 接口、Heartbeat、HTML 清洗。

### 端到端测试（需要 API Key）

```bash
python tests/test_e2e.py
```

覆盖 4 项：LLM 直接对话 → Tool Call 生成 → 完整 Agent Loop → Agent 调用工具创建文件并验证。


---

## License

MIT

---

*Built as a learning exercise — understanding AI agents by building one from scratch.*

---

## 致谢

- [OpenClaw](https://github.com/OpenClaw) — ToyClaw 的灵感来源与构建过程中的得力助手，没有它就没有这个项目。
- [nanobot](https://github.com/HKUDS/nanobot) — 优秀的轻量级 AI Agent 框架，ToyClaw 的架构设计参考了它的核心思路。
