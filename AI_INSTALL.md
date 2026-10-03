# Install Tern MCP with an AI assistant

Use this guide when the user asks you to install this repository. Read README.md's safety limits first.
This is a local stdio MCP server, not a hosted service. It can execute arbitrary shell commands with
user permissions; configure it only for trusted local clients. Do not disable the client's approval rules.

## 1. Discover prerequisites and target

- Identify the OS, intended MCP client, existing client configuration, and a user-owned clone directory.
  Prefer the client's documented configuration location. For OMP use `~/.omp/agent/mcp.json`.
  Ask which client only if the user's environment does not identify one.
- Resolve `git`, `uv`, and `tern` on PATH. On Windows Tern's console launcher is `tern.com`.
  Do not use `tern --version`: the tested Tern CLI does not implement it reliably.
- Tern must already be installed and usable. Do not download Tern from an unverified third-party source,
  create accounts, change its sign-in state, or buy a subscription. If missing, report that prerequisite.
- Python 3.10+ is required. uv can select an installed compatible interpreter. If prerequisite installation
  or an interpreter download needs approval under the user's policy, obtain it before proceeding.
- Do not copy any developer-specific paths or credentials. Do not modify existing Tern panes for testing.

## 2. Clone and install locked dependencies

Clone into a new user-owned directory:

```sh
git clone https://github.com/NaC-L/tern-mcp.git
```

If the directory already exists, inspect its origin and local changes. Do not overwrite it, reset changes,
change remotes, or silently pull a different repository. Reuse a matching clean checkout only when appropriate.
From the checkout directory run:

```sh
uv sync --frozen --no-dev
```

Use the checked-in lockfile; do not regenerate it or upgrade packages during installation.
Record the checkout's full commit id (`git rev-parse HEAD`) for the installation report.
Only Windows has been runtime-verified; do not describe Linux/macOS as verified.

## 3. Register without overwriting existing configuration

Resolve the clone directory and uv executable to absolute paths. Preserve all other MCP servers and
client settings. Back up an existing config file before editing it. If an existing `tern` entry differs,
explain the difference and obtain approval before replacing that entry. Do not print config credentials.

For OMP merge this **single entry** into `mcpServers`:

```json
{
  "tern": {
    "type": "stdio",
    "command": "/absolute/path/to/uv",
    "args": [
      "run", "--frozen", "--no-dev", "--project",
      "/absolute/path/to/tern-mcp",
      "/absolute/path/to/tern-mcp/server.py"
    ]
  }
}
```

Replace all placeholder paths. Windows JSON paths can use forward slashes, e.g. `C:/tools/tern-mcp`.
Other stdio MCP clients use their own schema: translate only the command/args, not OMP-specific fields.
Validate the resulting config using the client's schema/parser. Never overwrite the whole config with
this fragment. Do not persist TERN_PANE or TERN_WINDOW_KEY from the installer's current pane into config;
those values would become stale. Explicit window targeting is per tool call when needed.

## 4. Verify the real MCP boundary, read-only

Open a fresh client session (do not restart or kill existing sessions without permission), initialize
this stdio server, and list its tools. Expect these 14 tools:

```
tern_list tern_capture tern_process tern_new_session tern_new_tab tern_split tern_run
tern_send_text tern_send_keys tern_wait tern_focus tern_rename tern_close tern_kill_session
```

Call `tern_list` once and confirm a successful MCP result. Empty sessions are valid; a transport or tool
error is not success. Do not call run/send/close/kill or create a pane merely to verify installation.
If your client cannot load refreshed configuration, a Python MCP client launched with
`uv run --frozen --no-dev --project <clone> python` can verify the stdio boundary using
`mcp.ClientSession`, `mcp.StdioServerParameters`, and `mcp.client.stdio.stdio_client`.
Clearly distinguish direct-protocol verification from verification in the intended client.

Troubleshooting:

- Missing Tern: fix the MCP process's PATH, not just the installer's shell PATH. The server reports missing
  Tern on stderr. Do not fake an executable or suppress the error.
- Idle manual launch: normal for a stdio server waiting for input. Do not treat it as a failed HTTP server.
- Empty native OMP capture: Tern Surface Protocol bypasses the text grid. For a newly launched OMP process,
  the user can choose `PI_TUI_NATIVE=0` to use the ANSI renderer. Do not change their global environment.
- send requires a connected Tern window. A daemon-only installation cannot deliver those inputs.
- Self-pane mutations are intentionally refused. Do not remove that protection to make a check pass.

The development suite (`uv run --frozen pytest -q`) creates isolated daemon sockets/configuration and a
**disposable real window**. Run it only when development verification is requested; missing Tern skips
integration tests and is not proof of working installation.

## 5. Report, update, uninstall

Report the clone path, commit id, config file changed, verification actually performed, and any unresolved
prerequisites. Never claim successful installation based only on cloned files or dependency resolution.

For a user-requested update, first inspect local changes and the configured origin, then fast-forward only
(`git pull --ff-only`) and repeat locked sync plus read-only smoke. Do not reset/rebase user work.

For a user-requested uninstall, remove only the matching `tern` MCP entry, preserve all other settings,
and request confirmation before deleting the checkout. Leave Tern, uv, accounts, panes and other services alone.
