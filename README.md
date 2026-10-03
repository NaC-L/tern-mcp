# Tern MCP

Local stdio MCP server wrapping the Tern terminal CLI. Requires Tern on PATH, Python 3.10+ and uv.
Dependencies are pinned in pyproject.toml and uv.lock. No HTTP endpoint or remote authentication is provided.

## Install

For an AI coding assistant, use [AI_INSTALL.md](AI_INSTALL.md). It covers prerequisites,
configuration merging, a read-only MCP smoke check, and safe update/uninstall steps.

```sh
git clone https://github.com/NaC-L/tern-mcp.git
cd tern-mcp
uv sync --frozen --no-dev
uv run --frozen --no-dev server.py
```

The last command starts a stdio server and waits for an MCP client; an idle terminal is expected.

## Register with an MCP client

Replace both `/absolute/path/to/tern-mcp` entries with your clone's actual absolute path.
On Windows, forward slashes work, e.g. `C:/tools/tern-mcp`.
OMP's user configuration is `~/.omp/agent/mcp.json` (merge this entry into its existing mcpServers):

```json
{
  "mcpServers": {
    "tern": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--frozen", "--no-dev", "--project", "/absolute/path/to/tern-mcp", "/absolute/path/to/tern-mcp/server.py"]
    }
  }
}
```

Open a fresh OMP session after changing configuration. A missing Tern binary fails startup with a message
on stderr. This installation's CLI does not expose a usable `--version`; compatibility is exercised
through integration tests, not an invented version gate. Re-run tests after updating Tern.

## Tools

All 14 tools accept optional `window`: Tern's window key (`TERN_WINDOW_KEY` inside its panes).
An omitted/empty key uses the server's inherited TERN_WINDOW_KEY, otherwise Tern's first window.
TERN_DAEMON_SOCKET can point the server at another daemon.

| Tool | Inputs and behavior |
|---|---|
| tern_list | Session/tab/block ids and state |
| tern_capture | block reference; scrollback=false; max_lines=200 (1..10000); retains the tail and marks omitted lines |
| tern_process | block reference; child and foreground process details |
| tern_new_session | name; optional cwd, command argv, keep_open=false |
| tern_new_tab | optional session, cwd, command argv, keep_open=false |
| tern_split | block reference; direction right/down; optional cwd, command argv, keep_open=false |
| tern_run | exact block id; shell command string; types command and Enter, does not wait |
| tern_send_text | exact block id; text; paste=false; does not add Enter |
| tern_send_keys | exact block id; nonempty key-combo list, e.g. ["ctrl+c"] |
| tern_wait | block reference; until=prompt/exit/idle; idle_ms=500 (50..60000); timeout_s=30 (>0..600) |
| tern_focus | block reference; shows block in windows |
| tern_rename | session/block reference and new name |
| tern_close | exact block id; closes block and ends its program |
| tern_kill_session | exact session id or unique exact name; kills all programs in session |

Creation `command` is argv, e.g. `["cmd.exe", "/c", "echo hello"]`; `tern_run` takes a shell command string.
Omit creation command to open the default shell. A successful program closes its block unless keep_open=true.
Use keep_open=true if you intend to read output after exit.

`tern_wait` returns `{ "status": 0, "timed_out": false }`. Status is the **Tern CLI exit code**,
not a promise to preserve the launched program's exit code (Windows shell wrappers can map failures to 1).
124 is Tern's timeout indicator, including a daemon request timeout. Other CLI failures with stderr become
MCP tool errors. Acknowledgement/idle does not guarantee that newly sent input has repainted yet.

## Safety and limits

- run/send/close require numeric block ids from tern_list: title/program-name ambiguity and @focused are refused.
- These tools refuse TERN_PANE and panes whose child shell is an ancestor of the server process.
  Session kill checks all blocks too. An ancestry inspection failure refuses mutation.
- These are accidental-self-targeting protections, **not a sandbox or an authorization boundary**.
  Arbitrary shell commands can still affect the agent, filesystem, other panes and services. Use a trusted
  local client, OS permissions and client approval rules. MCP read-only/destructive annotations are hints,
  not server-enforced consent. Creation commands also execute programs.
- send_text/send_keys require a connected Tern window. A daemon alone acknowledges input without delivering it;
  the server now reports that situation instead of claiming success. The check cannot prevent a window
  disconnecting immediately afterwards.
- OMP uses Tern Surface Protocol native rendering, which bypasses the terminal text grid. `tern capture`
  cannot read that native surface. Start OMP with **PI_TUI_NATIVE=0** to use its capturable ANSI renderer.
  Empty captures return an explanatory note; an empty screen does not prove native rendering is active.
- Capture limits returned **lines**, not bytes or Tern's internal scrollback allocation. A single enormous
  line can still consume substantial memory; do not expose this server to untrusted clients/output producers.
- Each CLI call has a timeout. Logs go to stderr with verb/target, CLI status and elapsed time; typed payloads
  and launch arguments are not logged. MCP data is stdout only. No log rotation/background service is installed.

## Verification

```
uv run --frozen pytest -q
```

Tests use a private Tern daemon socket and temporary TERN_CONFIG_DIR, not the user's daemon/configuration.
The input/layout test opens a disposable real Tern window because headless `tern serve` has an in-process
session model unavailable to the public CLI, and daemon-only send does not deliver text. It terminates only
its own window and daemon, and kills only sessions on the private socket during cleanup. Other tests need
no window. Windows runtime is verified; Linux/macOS fixture paths have not been exercised here.

Coverage: shell input and output, no-Enter text, tab focus/rename/close, command flags after --,
nonzero exit reporting, output tail truncation, timeout, empty capture, unknown block, name rejection and
protected-pane/session rejection. Missing Tern skips integration tests; a skipped suite is not runtime proof.
