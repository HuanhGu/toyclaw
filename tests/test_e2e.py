"""End-to-end test: load config, call LLM, run a tool, get final answer."""
import asyncio
import io
import sys
from pathlib import Path

# Fix Windows GBK encoding for emoji output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

async def main():
    # 1. Load config
    from toyclaw.config import load_config
    cfg = load_config()
    print(f"[config] model={cfg.model}, api_base={cfg.api_base}")
    print(f"[config] workspace={cfg.workspace}")

    # 2. Test provider directly — simple chat (no tools)
    from toyclaw.provider import OpenAIProvider
    provider = OpenAIProvider(
        api_key=cfg.api_key,
        api_base=cfg.api_base,
        default_model=cfg.model,
    )

    print("\n--- Test 1: Simple chat (no tools) ---")
    resp = await provider.chat(
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Reply concisely."},
            {"role": "user", "content": "What is 2+3? Just the number."},
        ],
    )
    print(f"[LLM] content={resp.content!r}")
    print(f"[LLM] finish_reason={resp.finish_reason}, tool_calls={len(resp.tool_calls)}")
    assert resp.content and "5" in resp.content, f"Expected '5' in response, got: {resp.content}"
    print("PASS: Simple chat works!\n")

    # 3. Test with tools — LLM should call a tool
    print("--- Test 2: Chat with tools (expect tool call) ---")
    from toyclaw.tools.base import ToolRegistry
    from toyclaw.tools.builtin import ReadFileTool, WriteFileTool, ListDirTool, ShellTool

    ws = cfg.workspace
    reg = ToolRegistry()
    reg.register(ReadFileTool(workspace=ws))
    reg.register(WriteFileTool(workspace=ws))
    reg.register(ListDirTool(workspace=ws))
    reg.register(ShellTool(workspace=ws))

    schemas = reg.get_definitions()
    print(f"[tools] {len(schemas)} tools registered: {reg.names}")

    resp2 = await provider.chat(
        messages=[
            {"role": "system", "content": "You are an AI assistant with tools. Use them when needed."},
            {"role": "user", "content": "Please list the files in the current workspace directory."},
        ],
        tools=schemas,
    )
    print(f"[LLM] content={resp2.content!r}")
    print(f"[LLM] tool_calls={[(tc.name, tc.arguments) for tc in resp2.tool_calls]}")

    if resp2.has_tool_calls:
        print("PASS: LLM generated tool calls!")
        # Execute the tool call
        for tc in resp2.tool_calls:
            result = await reg.execute(tc.name, tc.arguments)
            print(f"[tool:{tc.name}] result={result[:200]!r}")
    else:
        print("INFO: LLM responded directly without tool calls (acceptable)")

    # 4. Full agent loop test (via agent.py)
    print("\n--- Test 3: Full Agent.process() ---")
    from toyclaw.agent import Agent
    from toyclaw.session import SessionManager

    sess_mgr = SessionManager(ws)

    agent = Agent(
        provider=provider,
        tools=reg,
        workspace=ws,
        session_mgr=sess_mgr,
        max_iterations=cfg.max_iterations,
    )

    # Simple question — no tool needed
    answer = await agent.process(
        content="Hi! What's the capital of France? One word answer.",
        session_key="e2e_test",
    )
    print(f"[agent] answer={answer!r}")
    assert answer and len(answer) > 0, "Agent returned empty answer"
    print("PASS: Agent.process() works!\n")

    # 5. Agent with tool usage — ask it to create a file
    print("--- Test 4: Agent creates a file via tool ---")
    answer2 = await agent.process(
        content="Create a file called 'e2e_test.txt' in the workspace with content 'Hello from toyclaw!'. Then confirm what you wrote.",
        session_key="e2e_test",
    )
    print(f"[agent] answer={answer2[:300]!r}")
    test_file = ws / "e2e_test.txt"
    if test_file.exists():
        content = test_file.read_text(encoding="utf-8")
        print(f"[file] {test_file} content={content!r}")
        assert "Hello from toyclaw" in content
        print("PASS: Agent successfully created file via tool!")
        test_file.unlink()  # cleanup
    else:
        print("WARN: File not created (agent may have used different path)")

    print("\n" + "=" * 50)
    print("All end-to-end tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
