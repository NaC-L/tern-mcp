"""Exercise the public MCP boundary against a separate real Tern daemon, never user windows."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def daemon(tmp_path):
    tern = shutil.which("tern")
    if not tern:
        pytest.skip("Tern is required for integration tests")
    token = uuid.uuid4().hex
    socket = rf"\\.\pipe\tern-mcp-test-{token}" if os.name == "nt" else f"/tmp/tern-mcp-test-{token}.sock"
    env = {**os.environ, "TERN_DAEMON_SOCKET": socket, "TERN_CONFIG_DIR": str(tmp_path),
           "TERN_WINDOW_KEY": "", "TERN_PANE": ""}
    proc = subprocess.Popen([tern, "daemon", "--socket", socket], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if proc.poll() is not None:
                pytest.fail("isolated Tern daemon exited")
            r = subprocess.run([tern, "ls", "--json"], env=env, capture_output=True, timeout=5)
            if r.returncode == 0:
                break
            time.sleep(0.1)
        else:
            pytest.fail("isolated Tern daemon did not answer")
        yield env
    finally:
        # Kill only sessions on our private socket before stopping its daemon.
        r = subprocess.run([tern, "ls", "--json"], env=env, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for session in json.loads(r.stdout)["sessions"]:
                subprocess.run([tern, "kill", "session", str(session["id"])], env=env, capture_output=True, timeout=5)
        proc.terminate()
        proc.wait(timeout=10)
        if os.name != "nt":
            Path(socket).unlink(missing_ok=True)


@pytest.fixture
def window(daemon):
    """The send commands route through a window, so use a disposable real window."""
    tern = shutil.which("tern")
    executable = str(Path(tern).with_suffix(".exe")) if os.name == "nt" else tern
    proc = subprocess.Popen([executable, "--dir", str(ROOT)], env=daemon,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            r = subprocess.run([tern, "inspect", "--json"], env=daemon, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                state = json.loads(r.stdout)
                if state["panes"] and any(c["kind"] == "window" for c in state["clients"]):
                    break
            time.sleep(0.1)
        else:
            pytest.fail("isolated Tern window did not connect")
        for pane in state["panes"]:
            for condition in ("prompt", "idle=500"):
                ready = subprocess.run([tern, "wait", str(pane["pane"]), "--until", condition, "--timeout", "20"],
                                       env=daemon, capture_output=True, timeout=25)
                assert ready.returncode == 0, ready.stderr
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def run(env, scenario):
    async def go():
        params = StdioServerParameters(command=shutil.which("uv"), args=["run", "--frozen", "--project", str(ROOT), str(ROOT / "server.py")], env=env)
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as client:
                await client.initialize()
                return await scenario(client)
    return asyncio.run(go())


async def call(client, tool, **args):
    result = await client.call_tool(tool, args)
    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert not result.is_error, text
    if tool in ("tern_capture", "tern_send_text", "tern_send_keys"):
        return text
    return result.structured_content if result.structured_content is not None else json.loads(text)

async def capture_until(client, block, marker, occurrences):
    # Input acknowledgement precedes shell repaint; idle alone can see the old prompt.
    deadline = time.monotonic() + 10
    while True:
        text = await call(client, "tern_capture", block=block)
        if text.count(marker) == occurrences:
            return text
        assert time.monotonic() < deadline, text
        await asyncio.sleep(0.1)


def shell_command(script):
    if os.name == "nt":
        return ["powershell.exe", "-NoProfile", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]
    return ["sh", "-c", script]


def test_shell_input_layout_and_cleanup(daemon, window):
    async def scenario(c):
        made = await call(c, "tern_new_session", name="test", keep_open=True)
        b = str(made["block"])
        await call(c, "tern_wait", block=b, until="prompt", timeout_s=20)
        await call(c, "tern_run", block=b, command="echo mcp-roundtrip")
        await call(c, "tern_wait", block=b, until="idle", idle_ms=300)
        await capture_until(c, b, "mcp-roundtrip", 2)
        tab = await call(c, "tern_new_tab", session="test", keep_open=True)
        nb = str(tab["block"])
        await call(c, "tern_wait", block=nb, until="prompt", timeout_s=20)
        await call(c, "tern_send_text", block=nb, text="echo typed-marker")
        await call(c, "tern_wait", block=nb, until="idle", idle_ms=500)
        before = await capture_until(c, nb, "typed-marker", 1)
        assert before.count("typed-marker") == 1
        await call(c, "tern_send_keys", block=nb, keys=["enter"])
        await call(c, "tern_wait", block=nb, until="idle", idle_ms=300)
        after = await capture_until(c, nb, "typed-marker", 2)
        assert after.count("typed-marker") == 2
        await call(c, "tern_rename", target=nb, name="renamed-tab")
        await call(c, "tern_rename", target=str(made["session"]), name="renamed-session")
        await call(c, "tern_focus", block=nb)
        state = await call(c, "tern_list")
        s = next(s for s in state["sessions"] if s["id"] == made["session"])
        assert s["shown"] and s["name"] == "renamed-session"
        assert next(t for t in s["tabs"] if t["id"] == tab["tab"])["name"] == "renamed-tab"
        assert any(block["focused"] for t in s["tabs"] for block in t["blocks"] if block["id"] == tab["block"])
        await call(c, "tern_close", block=nb)
        state = await call(c, "tern_list")
        assert all(block["id"] != tab["block"] for s in state["sessions"] for t in s["tabs"] for block in t["blocks"])
        await call(c, "tern_kill_session", session=str(made["session"]))
        assert all(s["id"] != made["session"] for s in (await call(c, "tern_list"))["sessions"])
    run(daemon, scenario)


def test_launch_flags_capture_limits_and_exit_status(daemon):
    async def scenario(c):
        first = await call(c, "tern_new_session", name="commands", keep_open=True)
        script = "1..20 | ForEach-Object { Write-Output ('line-' + $_) }; Write-Output '--json'; exit 7" if os.name == "nt" else "seq 1 20; echo --json; exit 7"
        split = await call(c, "tern_split", block=str(first["block"]), direction="down", command=shell_command(script), keep_open=True)
        b = str(split["block"])
        waited = await call(c, "tern_wait", block=b, until="exit", timeout_s=20)
        assert not waited["timed_out"] and waited["status"] != 0
        text = await call(c, "tern_capture", block=b, scrollback=True, max_lines=3)
        assert "earlier lines omitted" in text and "--json" in text
        assert len(text.splitlines()) == 4
        assert "line-1\n" not in text
    run(daemon, scenario)


def test_timeout_empty_capture_and_missing_block(daemon):
    async def scenario(c):
        script = "Start-Sleep 30" if os.name == "nt" else "sleep 30"
        made = await call(c, "tern_new_session", name="sleeping", keep_open=True, command=shell_command(script))
        b = str(made["block"])
        assert await call(c, "tern_wait", block=b, until="exit", timeout_s=0.2) == {"timed_out": True, "status": 124}
        assert "Tern Surface Protocol" in await call(c, "tern_capture", block=b)
        result = await c.call_tool("tern_capture", {"block": "no-such-block"})
        assert result.is_error and "no block" in result.content[0].text
    run(daemon, scenario)


def test_mutation_rejects_names_and_protected_pane(daemon):
    async def create(c):
        return await call(c, "tern_new_session", name="protected", keep_open=True)
    made = run(daemon, create)
    env = {**daemon, "TERN_PANE": str(made["block"])}
    async def scenario(c):
        for tool, args in [
            ("tern_run", {"block": "@focused", "command": "echo forbidden"}),
            ("tern_send_text", {"block": "powershell.exe", "text": "forbidden"}),
            ("tern_close", {"block": str(made["block"])}),
            ("tern_run", {"block": str(made["block"]), "command": "echo forbidden"}),
            ("tern_kill_session", {"session": "protected"}),
        ]:
            assert (await c.call_tool(tool, args)).is_error
        state = await call(c, "tern_list")
        assert state["sessions"][0]["id"] == made["session"]
        assert "forbidden" not in await call(c, "tern_capture", block=str(made["block"]))
    run(env, scenario)


def test_send_requires_window(daemon):
    async def scenario(c):
        made = await call(c, "tern_new_session", name="no-window", keep_open=True)
        block = str(made["block"])
        for tool, args in [
            ("tern_send_text", {"text": "should-not-be-sent"}),
            ("tern_send_keys", {"keys": ["enter"]}),
        ]:
            result = await c.call_tool(tool, {"block": block, **args})
            assert result.is_error and "requires a connected Tern window" in result.content[0].text
    run(daemon, scenario)
