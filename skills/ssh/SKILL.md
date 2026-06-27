---
name: ssh
description: Connect to and work inside a user's RunCode cloud workspace over SSH. Use when the user asks to list, open, connect to, run commands on, edit files in, port-forward from, or configure SSH access for a RunCode workspace / CDE.
---

# RunCode Workspace SSH

Use this skill to operate on a RunCode cloud development environment from the
local machine. The helper CLI is `runcode`; it mints a short-lived,
workspace-scoped SSH session, writes an isolated SSH config with a pinned host
key, and avoids using the user's personal SSH keys.

## Find The CLI

First try:

```bash
runcode doctor
```

If `runcode` is not on `PATH`, use the plugin's helper directly:

- Claude Code plugin sessions usually expose `runcode` on the agent shell
  `PATH`; if not, use `${CLAUDE_PLUGIN_ROOT}/bin/runcode`.
- Codex plugin sessions may not expose plugin `bin/` on `PATH`. If this skill
  is loaded from a local checkout or plugin cache, use the sibling
  `bin/runcode` by absolute path. For repeated use, run
  `<plugin-root>/bin/runcode install-path` once so future shells can invoke
  `runcode` directly.

Do not guess an API token. A token comes from `runcode login`, `RUNCODE_TOKEN`,
or the saved token file under the user's config directory.

## Login

If a command reports `no_token` or `unauthorized`, run the login command
yourself and have the user complete the browser consent:

```bash
runcode login
```

Tell the user that a browser tab will open and they need to click Authorize.
Surface the printed authorization URL as a fallback if the browser does not
open. After login succeeds, retry the original RunCode command once.

For a remote/headless machine where the loopback browser callback will not work,
run:

```bash
runcode login --paste
```

and ask the user to paste a token from the RunCode dashboard's CLI-token page.

## Choose A Workflow

There are two valid ways to use RunCode with Codex:

1. Use this thread through the RunCode helper:
   connect once, then run all remote work through `runcode exec`, `runcode get`,
   `runcode write`, `runcode put`, and `runcode forward`.
2. Configure SSH aliases for Codex App remote projects:
   run `runcode config-ssh`, then the user can add the generated
   `runcode.<workspace>` host in Codex App Settings > Connections > SSH. In that
   mode Codex runs directly on the remote filesystem and shell after the SSH
   connection is selected.

For a single current task, use workflow 1. When the user wants Codex itself,
VS Code Remote-SSH, JetBrains Gateway, plain `ssh`, `git`, `scp`, or `rsync` to
connect to RunCode workspaces as normal SSH hosts, use workflow 2.

## List And Connect

If the user did not name a workspace, list available workspaces first:

```bash
runcode list --json
```

Use the numeric `id` when available; it works for personal, shared, and team
workspaces. If exactly one workspace is connectable, use it. If several are
available, show `id`, title, state, and ask which one to use. If a workspace is
stopped but SSH-capable, `connect --start` can boot it.

Connect and wait until SSH actually answers:

```bash
runcode connect "<workspace id or name>" --start --wait --json
```

Parse stdout as JSON. Stderr progress such as "starting workspace" and
"waiting for SSH" is normal. The JSON includes:

- `alias` and `config`: low-level SSH details.
- `workdir`: project directory on the workspace; do not hard-code it.
- `started`: whether the command started a stopped workspace.
- `web_url`: token-free workspace web host when available.

Right after connecting, orient once:

```bash
runcode context --json
```

Use that digest for cwd, git state, project markers, and available tools instead
of probing the workspace with many separate commands.

## Work On The Attached Workspace

A successful `connect` attaches one sticky workspace. Until the user explicitly
disconnects, route the task's remote work through the RunCode helper:

```bash
runcode exec -- <command>
runcode get <remote-path>
runcode write <remote-path>
runcode put <local-path> <remote-path>
runcode forward <port>
```

`runcode exec -- <command>` runs in the workspace `workdir` by default and
re-mints/retries once on an SSH transport drop. Add `--home` only when the
command should run from the remote user's home directory.

Read files with `runcode get <path>` or `runcode exec -- cat <path>`. Write files
directly with `runcode write <path>`:

```bash
runcode write src/app.py <<'EOF'
...file contents...
EOF
```

Use `put` only when a real local file already exists and needs to be uploaded.
Do not create local temporary source files just to copy them up.

When you start a dev server on the workspace, offer to forward its port:

```bash
runcode forward 5173
```

On Windows, `forward` may print a manual `ssh -L` command because persistent SSH
masters are POSIX-only; relay that command to the user instead of retrying.

## Configure SSH Hosts

When the user wants Codex App, their editor, or their own terminal to connect to
RunCode workspaces as SSH hosts, run:

```bash
runcode config-ssh
```

This writes a reversible managed block in `~/.ssh/config` with aliases such as
`runcode.<workspace-title>`. Each alias uses a locally authored `ProxyCommand`
back to this helper, which refreshes the short-lived key and pins the gateway
host key on each connection.

After this, tell the user:

- Codex App: add or enable host `runcode.<workspace-title>` in Settings >
  Connections > SSH, then open the remote project folder.
- Shell: `ssh runcode.<workspace-title>`.
- VS Code: Remote-SSH host `runcode.<workspace-title>`.
- JetBrains Gateway: new SSH connection with host `runcode.<workspace-title>`.
- Git/scp/rsync: use `runcode.<workspace-title>:<path>`.

Remove the managed block with:

```bash
runcode config-ssh --remove
```

## Disconnect Or Stop

Disconnect only when the user explicitly asks to detach:

```bash
runcode disconnect
```

This clears the sticky attachment. It does not power off the workspace.

Stop a workspace only when the user explicitly asks to stop, shut down, power
off, or pause compute billing:

```bash
runcode stop [workspace id or name] --json
```

With no argument, `stop` targets the attached workspace. Stopping powers down the
VM on the control plane, compute billing pauses, and storage persists.

## Claude-Only Status Line

`runcode install-statusline` writes to Claude Code's `~/.claude/settings.json`.
Run it only in Claude Code sessions. Do not run it for Codex; use Codex App's
own status and remote-connection UI instead.

## Failure Handling

With `--json`, failures exit non-zero and print
`{"error":"<code>","message":"..."}` on stdout. Branch on `error`:

- `no_token` or `unauthorized`: run `runcode login`, then retry once.
- `start_failed`: relay the message and stop.
- `stop_failed`: relay the message and stop.
- `not_ready`: the workspace did not answer in time; retry once with a longer
  timeout only if useful.
- `conflict`: workspace is not SSH-capable or cannot be reached; surface it.
- `unavailable`: backend/gateway feature is not enabled or temporarily refused.
- `not_found`: wrong workspace or no access.
- `insecure_base`: configured API/web base is non-HTTPS except loopback.
- `unsafe_bundle`: backend bundle contained unsafe SSH config; stop and report.

Never loop indefinitely. Never work around host-key prompts; they should not
appear because the helper pins the host key.

## Security Notes

The private key stays on the local machine under the RunCode cache directory.
Only the public key is sent to the backend. Session material is short-lived and
workspace-scoped. `runcode clean` removes cached keys/configs.
