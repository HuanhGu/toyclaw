"""Functional tests for toyclaw components (no API key needed)."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ── 1. Config ──
from toyclaw.config import Config, load_config


def test_config():
    # Default config
    cfg = Config()
    assert cfg.model == "gpt-4o"
    assert cfg.max_iterations == 30
    assert cfg.heartbeat_interval == 1800

    # Load from a temp JSON file
    tmpdir = tempfile.mkdtemp(prefix="oc_cfg_")
    try:
        p = Path(tmpdir) / "config.json"
        p.write_text(json.dumps({"api_key": "sk-test", "model": "gpt-3.5-turbo"}))
        cfg = load_config(p)
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-3.5-turbo"
        # Defaults still applied for missing keys
        assert cfg.max_iterations == 30
        print("  config: Config + load_config OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 2. Provider data classes ──
from toyclaw.provider import LLMResponse, ToolCallRequest


def test_provider_dataclasses():
    tc = ToolCallRequest(id="t1", name="read_file", arguments={"path": "x"})
    assert tc.id == "t1" and tc.name == "read_file"
    assert tc.arguments == {"path": "x"}

    resp = LLMResponse(content="hi", tool_calls=[], finish_reason="stop")
    assert resp.content == "hi"
    assert not resp.has_tool_calls

    resp2 = LLMResponse(tool_calls=[tc])
    assert resp2.has_tool_calls
    print("  provider: dataclasses OK")


# ── 3. Tool registry ──
from toyclaw.tools.base import ToolRegistry


def test_tool_registry():
    reg = ToolRegistry()
    assert reg.get_definitions() == []
    assert reg.names == []

    # Execute on missing tool
    result = loop.run_until_complete(reg.execute("nope", {}))
    assert "not found" in result.lower()
    print("  tools.base: ToolRegistry OK")


# ── 4. Builtin tools ──
from toyclaw.tools.builtin import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    ShellTool,
    WriteFileTool,
)


def test_builtin_tools():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_test_"))
    ws = tmpdir  # workspace

    try:
        # ReadFile — missing file
        rt = ReadFileTool(workspace=ws)
        result = loop.run_until_complete(rt.execute(path=str(tmpdir / "nope.txt")))
        assert "not found" in result.lower()

        # WriteFile
        wt = WriteFileTool(workspace=ws)
        wf = tmpdir / "out.txt"
        result = loop.run_until_complete(wt.execute(path=str(wf), content="wrote this"))
        assert wf.exists()
        assert wf.read_text() == "wrote this"
        print("  builtin: WriteFileTool OK")

        # ReadFile — existing file
        result = loop.run_until_complete(rt.execute(path=str(wf)))
        assert "wrote this" in result
        print("  builtin: ReadFileTool OK")

        # EditFile
        et = EditFileTool(workspace=ws)
        result = loop.run_until_complete(et.execute(path=str(wf), old_text="wrote", new_text="edited"))
        assert "edited this" in wf.read_text()
        print("  builtin: EditFileTool OK")

        # EditFile — old_text not found
        result = loop.run_until_complete(et.execute(path=str(wf), old_text="NOPE", new_text="x"))
        assert "not found" in result.lower()
        print("  builtin: EditFileTool error case OK")

        # ListDir
        ld = ListDirTool(workspace=ws)
        result = loop.run_until_complete(ld.execute(path=str(tmpdir)))
        assert "out.txt" in result
        print("  builtin: ListDirTool OK")

        # ShellTool
        st = ShellTool(workspace=ws)
        result = loop.run_until_complete(st.execute(command="echo test123"))
        assert "test123" in result
        print("  builtin: ShellTool OK")

        # ShellTool — deny pattern
        result = loop.run_until_complete(st.execute(command="rm -rf /"))
        assert "blocked" in result.lower()
        print("  builtin: ShellTool safety guard OK")

    finally:
        shutil.rmtree(tmpdir)


# ── 5. Tool registry integration ──
def test_tool_registry_with_tools():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_reg_"))
    try:
        reg = ToolRegistry()
        reg.register(ReadFileTool(workspace=tmpdir))
        reg.register(WriteFileTool(workspace=tmpdir))
        reg.register(ListDirTool(workspace=tmpdir))

        assert len(reg.names) == 3
        assert "read_file" in reg.names

        # Schema structure
        schemas = reg.get_definitions()
        assert len(schemas) == 3
        assert all(s["type"] == "function" for s in schemas)
        assert schemas[0]["function"]["name"] in ("read_file", "write_file", "list_dir")
        print("  registry: register + schema OK")

        # Execute via registry
        loop.run_until_complete(reg.execute("write_file", {"path": str(tmpdir / "hi.txt"), "content": "hi"}))
        result = loop.run_until_complete(reg.execute("read_file", {"path": str(tmpdir / "hi.txt")}))
        assert "hi" in result
        print("  registry: execute integration OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 6. Session ──
from toyclaw.session import Session, SessionManager


def test_session():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_sess_"))
    try:
        mgr = SessionManager(workspace=tmpdir)

        # New session
        sess = mgr.get_or_create("test_conv")
        assert isinstance(sess, Session)
        assert sess.key == "test_conv"
        assert len(sess.messages) == 0

        # Add messages
        sess.messages.append({"role": "user", "content": "hi"})
        sess.messages.append({"role": "assistant", "content": "hello"})
        sess.messages.append({"role": "user", "content": "q2"})
        mgr.save(sess)

        # get_history trims to user turn
        hist = sess.get_history()
        assert hist[0]["role"] == "user"
        assert len(hist) == 3
        print("  session: message append + get_history OK")

        # Persistence — reload from disk
        mgr2 = SessionManager(workspace=tmpdir)
        sess2 = mgr2.get_or_create("test_conv")
        assert len(sess2.messages) == 3
        assert sess2.messages[0]["content"] == "hi"
        print("  session: persistence OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 7. Skills ──
from toyclaw.skills import SkillsLoader


def test_skills():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_skills_"))
    try:
        # No skills dir
        loader = SkillsLoader(tmpdir)
        assert loader.list_skills() == []

        # Create a fake skill
        sk = tmpdir / "skills" / "demo"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("# Demo Skill\nDoes demo things.\n\nMore detail here.")
        found = loader.list_skills()
        assert len(found) == 1
        assert found[0]["name"] == "demo"
        assert "Does demo things" in found[0]["description"]

        # build_summary
        summary = loader.build_summary()
        assert "demo" in summary
        assert "Does demo things" in summary
        print("  skills: SkillsLoader OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 8. Context ──
from toyclaw.context import ContextBuilder


def test_context():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_ctx_"))
    try:
        # Bootstrap file
        (tmpdir / "AGENTS.md").write_text("# Test Agent Instructions")

        builder = ContextBuilder(tmpdir)
        messages = builder.build_messages(
            history=[],
            user_message="hello",
            skills_summary="- **test_skill**: does stuff",
        )

        # Should have system + user
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

        system = messages[0]["content"]
        assert "ToyClaw" in system
        assert "Test Agent Instructions" in system
        assert "test_skill" in system
        print("  context: ContextBuilder OK")

        # add_assistant_message / add_tool_result
        ContextBuilder.add_assistant_message(messages, "thinking...")
        assert messages[-1]["role"] == "assistant"

        ContextBuilder.add_tool_result(messages, "tc1", "read_file", "file content")
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "tc1"
        print("  context: message helpers OK")

        # strip_runtime_tag
        raw = messages[1]["content"]
        assert "[Runtime Context]" in raw
        stripped = ContextBuilder.strip_runtime_tag(raw)
        assert stripped == "hello"
        print("  context: strip_runtime_tag OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 9. Cron ──
from toyclaw.cron import CronJob, CronSchedule, CronService


def test_cron():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_cron_"))
    try:
        store = tmpdir / "cron.json"
        svc = CronService(store_path=store)

        # Add a recurring job
        sched = CronSchedule(kind="every", every_ms=30_000)
        job = svc.add_job(name="test-job", schedule=sched, message="say hi")
        assert isinstance(job, CronJob)
        assert job.id
        assert job.name == "test-job"

        # List
        jobs = svc.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == job.id
        print("  cron: add_job + list_jobs OK")

        # Persistence
        assert store.exists()
        data = json.loads(store.read_text())
        assert len(data) == 1
        print("  cron: JSON persistence OK")

        # Remove
        ok = svc.remove_job(job.id)
        assert ok
        assert len(svc.list_jobs()) == 0

        # Remove non-existent
        ok = svc.remove_job("nope")
        assert not ok
        print("  cron: remove_job OK")

        # One-shot schedule
        import time
        future_ms = int(time.time() * 1000) + 60_000
        sched2 = CronSchedule(kind="at", at_ms=future_ms)
        job2 = svc.add_job(name="once", schedule=sched2, message="one time", delete_after_run=True)
        assert job2.delete_after_run
        svc.remove_job(job2.id)

        # Cron expression
        sched3 = CronSchedule(kind="cron", expr="0 9 * * *")
        job3 = svc.add_job(name="daily", schedule=sched3, message="good morning")
        assert job3.next_run_ms is not None  # croniter computed it
        svc.remove_job(job3.id)
        print("  cron: all schedule types OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 10. CronTool ──
from toyclaw.tools.builtin import CronTool


def test_cron_tool():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_ct_"))
    try:
        svc = CronService(store_path=tmpdir / "cron.json")
        ct = CronTool(service=svc)
        ct.set_context("cli", "test")

        # Add via tool
        result = loop.run_until_complete(ct.execute(action="add", message="remind me", every_seconds=60))
        assert "created" in result.lower()
        print("  cron_tool: add OK")

        # List via tool
        result = loop.run_until_complete(ct.execute(action="list"))
        assert "remind me" in result
        print("  cron_tool: list OK")

        # Remove via tool
        job_id = svc.list_jobs()[0].id
        result = loop.run_until_complete(ct.execute(action="remove", job_id=job_id))
        assert "removed" in result.lower()
        print("  cron_tool: remove OK")

        # Error: missing message
        result = loop.run_until_complete(ct.execute(action="add"))
        assert "error" in result.lower()
        print("  cron_tool: error handling OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 11. Heartbeat ──
from toyclaw.heartbeat import HeartbeatService


def test_heartbeat():
    tmpdir = Path(tempfile.mkdtemp(prefix="oc_hb_"))
    try:
        # HeartbeatService requires a provider; use a mock
        class FakeProvider:
            pass

        hb = HeartbeatService(
            workspace=tmpdir,
            provider=FakeProvider(),
            interval_s=1800,
            enabled=False,
        )
        assert hb.interval_s == 1800
        assert not hb.enabled
        assert hb._file == tmpdir / "HEARTBEAT.md"
        print("  heartbeat: init OK")

        # With HEARTBEAT.md
        (tmpdir / "HEARTBEAT.md").write_text("- Check email\n- Review tasks")
        assert hb._file.exists()
        print("  heartbeat: HEARTBEAT.md detected OK")
    finally:
        shutil.rmtree(tmpdir)


# ── 12. WebFetchTool (strip HTML) ──
from toyclaw.tools.builtin import WebFetchTool


def test_web_fetch_strip():
    wf = WebFetchTool()
    html = "<html><body><h1>Title</h1><p>Hello <b>world</b></p></body></html>"
    text = wf._strip(html)
    assert "Title" in text
    assert "Hello" in text
    assert "world" in text
    assert "<" not in text  # all tags stripped
    print("  web_fetch: HTML strip OK")


# ── Run all ──
if __name__ == "__main__":
    tests = [
        test_config,
        test_provider_dataclasses,
        test_tool_registry,
        test_builtin_tools,
        test_tool_registry_with_tools,
        test_session,
        test_skills,
        test_context,
        test_cron,
        test_cron_tool,
        test_heartbeat,
        test_web_fetch_strip,
    ]
    passed = 0
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed!")
