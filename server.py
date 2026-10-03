"""MCP server exposing the Tern terminal's scripting CLI (`tern ls|new|split|run|send|capture|wait|...`).

Read-only tools take any block reference Tern accepts: an id, a title, a program name or SESSION/BLOCK.
Tools that type into, close or kill something take exact ids from `tern_list` only, and refuse the pane
this server runs under (its agent's own terminal): $TERN_PANE when set, else any pane whose shell is
an ancestor of this process.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Annotated, Literal

import psutil
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

log = logging.getLogger("tern-mcp")

TERN = shutil.which("tern")  # honours PATHEXT, so it finds the console launcher tern.com
mcp = MCPServer("tern")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
LAYOUT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)

BlockRef = Annotated[str, Field(min_length=1, description="Block id, title, program name, or SESSION/BLOCK")]
BlockId = Annotated[str, Field(pattern=r"^\d+$", description="Exact block id from tern_list")]
Window = Annotated[
    str | None,
    Field(description="Window key ($TERN_WINDOW_KEY inside one of its panes); default: $TERN_WINDOW_KEY, else the first window"),
]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Command = Annotated[
    list[Annotated[str, Field(min_length=1)]] | None,
    Field(min_length=1, description='Program and its arguments, e.g. ["cmd.exe", "/c", "dir"]; omit for the default shell'),
]

EMPTY_CAPTURE_NOTE = (
    "(The block shows no text. Programs drawing through the Tern Surface Protocol, such as omp, "
    "do not draw on the text grid; start omp with PI_TUI_NATIVE=0 to make it capturable.)"
)


def _with_flags(args: tuple[str, ...], flags: list[str]) -> list[str]:
    # Flags must precede a launch's `--`, or they become arguments of the launched program.
    cut = args.index("--") if "--" in args else len(args)
    return [*args[:cut], *flags, *args[cut:]]


def _window_flags(window: str | None) -> list[str]:
    key = window or os.environ.get("TERN_WINDOW_KEY") or None
    return ["--window", key] if key else []


def _exec(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    started = time.monotonic()
    try:
        r = subprocess.run(
            [TERN, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log.warning("tern %s: no answer within %ss", args[0], timeout)
        raise ToolError(f"tern {args[0]} did not answer within {timeout}s") from None
    # The verb and its target only: typed text and launched commands may hold secrets.
    log.info("tern %s -> %d in %.0fms", " ".join(args[:2]), r.returncode, (time.monotonic() - started) * 1000)
    return r


def _tern(*args: str, window: str | None = None, json_out: bool = False, timeout: float = 30) -> str:
    flags = _window_flags(window) + (["--json"] if json_out else [])
    r = _exec(_with_flags(args, flags), timeout)
    if r.returncode != 0:
        raise ToolError(r.stderr.strip() or f"tern {args[0]} exited {r.returncode}")
    return r.stdout


def _json(*args: str, window: str | None = None) -> dict:
    return json.loads(_tern(*args, window=window, json_out=True))


def _ancestor_pids() -> set[int]:
    try:
        return {p.pid for p in psutil.Process().parents()}
    except psutil.Error as exc:
        raise ToolError("cannot verify process ancestry; refusing mutation") from exc


def _is_own_pane(block: str, window: str | None) -> bool:
    if os.environ.get("TERN_PANE") == block:
        return True
    child = _json("process", block, window=window).get("child") or {}
    return child.get("pid") in _ancestor_pids()


def _guard(block: str, window: str | None) -> None:
    if _is_own_pane(block, window):
        raise ToolError(f"refusing block {block}: it is the terminal this agent runs in")


def _require_window(window: str | None) -> None:
    if not any(c["kind"] == "window" for c in _json("inspect", window=window)["clients"]):
        raise ToolError("tern send requires a connected Tern window; daemon-only input is not delivered")


def _launch(cwd: str | None, command: list[str] | None, keep_open: bool) -> list[str]:
    args: list[str] = []
    if cwd:
        args += ["--cwd", cwd]
    if keep_open:
        args.append("--keep-open")
    if command:
        args += ["--", *command]
    return args


@mcp.tool(annotations=READ)
def tern_list(window: Window = None) -> dict:
    """Sessions, tabs and blocks (ids, titles, programs, cwd, exited, focused) of the Tern window."""
    return _json("ls", window=window)


@mcp.tool(annotations=READ)
def tern_capture(
    block: BlockRef,
    scrollback: bool = False,
    max_lines: Annotated[int, Field(ge=1, le=10000)] = 200,
    window: Window = None,
) -> str:
    """The text `block` shows (scrollback=True: its scrollback too), keeping the last max_lines lines."""
    text = _tern("capture", block, *(["--scrollback"] if scrollback else []), window=window)
    if not text.strip():
        return EMPTY_CAPTURE_NOTE
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return f"[{len(lines) - max_lines} earlier lines omitted]\n" + "\n".join(lines[-max_lines:]) + "\n"


@mcp.tool(annotations=READ)
def tern_process(block: BlockRef, window: Window = None) -> dict:
    """The child and foreground processes (pid, name) of `block`."""
    return _json("process", block, window=window)


@mcp.tool(annotations=CREATE)
def tern_new_session(
    name: Name, cwd: str | None = None, command: Command = None, keep_open: bool = False, window: Window = None
) -> dict:
    """Create a session with one tab. A block whose program succeeds closes unless keep_open.
    Returns session/tab/block ids."""
    return _json("new", "session", name, *_launch(cwd, command, keep_open), window=window)


@mcp.tool(annotations=CREATE)
def tern_new_tab(
    session: str | None = None,
    cwd: str | None = None,
    command: Command = None,
    keep_open: bool = False,
    window: Window = None,
) -> dict:
    """Open a tab (default: in the session shown). Returns session/tab/block ids."""
    return _json("new", "tab", *([session] if session else []), *_launch(cwd, command, keep_open), window=window)


@mcp.tool(annotations=CREATE)
def tern_split(
    block: BlockRef,
    direction: Literal["right", "down"],
    cwd: str | None = None,
    command: Command = None,
    keep_open: bool = False,
    window: Window = None,
) -> dict:
    """Split a new block beside `block`. Returns session/tab/block ids."""
    return _json("split", block, direction, *_launch(cwd, command, keep_open), window=window)


@mcp.tool(annotations=DESTRUCTIVE)
def tern_run(block: BlockId, command: Annotated[str, Field(min_length=1)], window: Window = None) -> dict:
    """Type `command` and Enter into `block`'s shell. Does not wait: follow with tern_wait, then tern_capture."""
    _guard(block, window)
    return _json("run", block, command, window=window)


@mcp.tool(annotations=DESTRUCTIVE)
def tern_send_text(
    block: BlockId, text: Annotated[str, Field(min_length=1)], paste: bool = False, window: Window = None
) -> str:
    """Type `text` into `block` without Enter (paste=True sends it as a bracketed paste)."""
    _guard(block, window)
    _require_window(window)
    _tern("send", block, "paste" if paste else "text", text, window=window)
    return "sent"


@mcp.tool(annotations=DESTRUCTIVE)
def tern_send_keys(
    block: BlockId, keys: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)], window: Window = None
) -> str:
    """Send key combos to `block`, e.g. ["ctrl+c"], ["enter"], ["up", "enter"]."""
    _guard(block, window)
    _require_window(window)
    _tern("send", block, "keys", *keys, window=window)
    return "sent"


@mcp.tool(annotations=READ)
def tern_wait(
    block: BlockRef,
    until: Literal["prompt", "exit", "idle"] = "prompt",
    idle_ms: Annotated[int, Field(ge=50, le=60000)] = 500,
    timeout_s: Annotated[float, Field(gt=0, le=600)] = 30,
    window: Window = None,
) -> dict:
    """Wait until `block` shows its shell prompt, its program exits, or its output is idle for idle_ms.
    Returns {"status", "timed_out"}; status is Tern CLI's exit code, not necessarily the program's code."""
    cond = f"idle={idle_ms}" if until == "idle" else until
    args = _with_flags(("wait", block, "--until", cond, "--timeout", str(timeout_s)), _window_flags(window))
    r = _exec(args, timeout_s + 10)
    # Timeout code 124 may also include a daemon timeout diagnostic on stderr.
    if r.returncode not in (0, 124) and r.stderr.strip():
        raise ToolError(r.stderr.strip())
    return {"status": r.returncode, "timed_out": r.returncode == 124}


@mcp.tool(annotations=LAYOUT)
def tern_focus(block: BlockRef, window: Window = None) -> dict:
    """Show `block` in every window."""
    return _json("focus", block, window=window)


@mcp.tool(annotations=LAYOUT)
def tern_rename(target: Annotated[str, Field(min_length=1)], name: Name, window: Window = None) -> dict:
    """Rename a session, or a block's tab."""
    return _json("rename", target, name, window=window)


@mcp.tool(annotations=DESTRUCTIVE)
def tern_close(block: BlockId, window: Window = None) -> dict:
    """Close `block` and end its program."""
    _guard(block, window)
    return _json("close", block, window=window)


@mcp.tool(annotations=DESTRUCTIVE)
def tern_kill_session(session: Annotated[str, Field(min_length=1)], window: Window = None) -> dict:
    """Kill a session (exact id or unique name from tern_list) and every program in it."""
    sessions = _json("ls", window=window)["sessions"]
    found = [s for s in sessions if str(s["id"]) == session] or [s for s in sessions if s["name"] == session]
    if not found:
        raise ToolError(f"no session is called `{session}`")
    if len(found) > 1:
        raise ToolError(f"{len(found)} sessions are called `{session}`; pass its id from tern_list")
    target = found[0]
    for tab in target["tabs"]:
        for b in tab["blocks"]:
            if _is_own_pane(str(b["id"]), window):
                raise ToolError(f"refusing session {target['name']}: it holds the terminal this agent runs in")
    return _json("kill", "session", str(target["id"]), window=window)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if TERN is None:
        sys.exit("tern-mcp: `tern` is not on PATH; install Tern or add its directory to PATH")
    log.info("using %s", TERN)
    mcp.run()


if __name__ == "__main__":
    main()
