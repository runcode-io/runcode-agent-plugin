# RunCode CDE plugin — SSH for coding agents

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Platforms: Windows · macOS · Linux](https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-lightgrey.svg)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

Give a coding agent running **locally** (Claude Code / Codex / an editor) low-friction,
secure SSH access to your **RunCode workspace**, so it can run commands, sync files,
and forward ports on the remote box.

The agent never manages keys. The plugin generates an **ephemeral** keypair on your
machine, asks the RunCode backend for a short-lived connection bundle (sending only the
*public* key), and writes an **isolated** ssh config with a **pinned** host key. There is
no first-connect `yes/no` prompt, and your `~/.ssh` is never touched — except by the
opt-in `config-ssh` command, which writes a clearly delimited, backed-up, removable block
so your **own** editor and git can reach the box too (see [Your own tools](#your-own-tools-vs-code-remote--jetbrains--git)).

Today the plugin registers an **ephemeral key** that the RunCode gateway honors for a few
minutes. It will be byte-for-byte identical when the backend later moves to short-lived SSH
certificates — only the server side changes, never this plugin.

## Layout

```
cde-plugin/
├── .claude-plugin/
│   ├── plugin.json              # plugin manifest (name: "runcode")
│   └── marketplace.json         # lets the dir double as a one-plugin marketplace
├── bin/runcode              # the engine — Python 3 stdlib, no deps (POSIX entry)
├── bin/runcode.cmd          # Windows entry point (launches the engine via Python)
├── commands/                    # explicit slash commands (all under /runcode:…)
│   ├── connect.md               #   /runcode:connect [workspace] [command…]
│   ├── stop.md                  #   /runcode:stop [workspace] (power the box down)
│   ├── disconnect.md            #   /runcode:disconnect
│   ├── list.md                  #   /runcode:list
│   ├── login.md                 #   /runcode:login
│   ├── doctor.md                #   /runcode:doctor (preflight a new machine/OS)
│   └── statusline.md            #   /runcode:statusline (wire the workspace cue)
├── skills/ssh/SKILL.md          # the /runcode:ssh skill (auto-activates from chat)
└── README.md
```

### Slash commands

Plugin commands are always namespaced under the plugin name, so typing `/runcode` in the
slash menu surfaces the whole family:

| Command | Does |
|---|---|
| `/runcode:login` | one-time browser authorize (saves the API token) |
| `/runcode:list` | list your workspaces (running ones are SSH-connectable) |
| `/runcode:connect [workspace] [command…]` | open a session (attaches the workspace; `--start` boots a stopped box, `--wait` waits for SSH); optionally run a command |
| `/runcode:context [workspace]` | one-shot workspace digest (cwd, git, project, tool versions) so the agent orients without probing the box piecemeal |
| `/runcode:forward <port>` | expose a workspace port locally (e.g. open a dev server in your browser) |
| `/runcode:config-ssh` | write `~/.ssh/config` host entries so VS Code Remote / JetBrains / git / scp reach your workspaces as `runcode.<name>` |
| `/runcode:stop [workspace]` | stop a workspace on the control plane (VM powers down, compute billing pauses; storage persists). Defaults to the attached box and detaches it. Only on your explicit request |
| `/runcode:disconnect` | detach from the workspace; work reverts to the local machine |
| `/runcode:statusline` | wire the always-on "working-on: ws#" cue into your `settings.json` |
| `/runcode:doctor` | preflight this machine — python, OpenSSH client, token, https base, reachability |
| `/runcode:ssh` | the umbrella skill (also auto-activates when you ask in plain language) |

You rarely need to type these — just ask Claude Code in plain language ("run the tests on my
workspace") and the `ssh` skill activates on its own.

## Install

Add the RunCode marketplace and install the plugin from inside Claude Code (one time):

```
/plugin marketplace add runcode-io/claude-plugin
/plugin install runcode@runcode
```

(Equivalently from a shell: `claude plugin marketplace add runcode-io/claude-plugin` then
`claude plugin install runcode@runcode`.)

The skill then loads as **`/runcode:ssh`**. Claude Code adds the plugin's `bin/` to the
**agent's** Bash-tool `PATH` while the plugin is enabled, so the agent invokes `runcode`
by that bare name on every platform (on Windows it resolves to `runcode.cmd`). Run
**`/runcode:login`** once to authorize this machine (**`/runcode:doctor`** checks your setup).

Want to run `runcode` yourself in your **own** terminal (the standalone CLI below)? That
PATH injection is for the agent only, so do it once: **`runcode install-path`** (POSIX
symlinks the engine into `~/.local/bin`; Windows prints the directory to add to `PATH`).

> **Hacking on the plugin itself?** Point Claude Code at a local checkout instead of the
> marketplace — changes reload without a reinstall:
> ```bash
> claude --plugin-dir /path/to/cde-plugin   # after edits: /reload-plugins
> ```

### Platforms

Runs on **Windows, macOS, and Linux** with the same engine (Python 3.8+ stdlib, no
third-party packages). The two prerequisites are common to all three:

- **Python 3.8+** on `PATH` (on Windows the `py` launcher or `python`).
- The **OpenSSH client** (`ssh` + `ssh-keygen`) on `PATH`. macOS and most Linux ship it;
  Windows 10/11 include it as an optional feature (*Settings → Apps → Optional features →
  OpenSSH Client*) — the engine resolves `ssh.exe`/`ssh-keygen.exe` via `PATH`.

On Windows, `runcode` resolves to `bin/runcode.cmd`, which launches the engine with
Python. Per-user state follows each platform's convention (an explicit `XDG_*` override
always wins):

| | config (token) | cache (session keys/config) |
|---|---|---|
| Linux | `$XDG_CONFIG_HOME` or `~/.config/runcode/` | `$XDG_CACHE_HOME` or `~/.cache/runcode/` |
| macOS | same as Linux | same as Linux |
| Windows | `%APPDATA%\runcode\` | `%LOCALAPPDATA%\runcode\` |

On Windows the private key + token are protected by the user-profile ACLs rather than POSIX
`0600` mode bits (`AppData` is not readable by other standard users); the `chmod` calls are
harmless no-ops there.

## Authentication

Log in once — opens your browser, you click **Authorize**, the token is saved:

```bash
runcode login            # browser flow → ~/.config/runcode/token (0600)
runcode login --paste    # headless/remote box: paste a token instead
runcode login --no-browser   # print the URL to open elsewhere
runcode logout           # remove the saved token
```

The consent page is served on the RunCode web host (default `https://runcode.io`, override
with `RUNCODE_WEB_BASE` / `--web-base`) where you already have a session; it mints your
account's API token and hands it to a one-shot `127.0.0.1` listener the CLI opened — the
token is only ever delivered to your own machine.

Prefer not to use the browser flow? Provide the token directly:

```bash
export RUNCODE_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   …or write it to ~/.config/runcode/token (chmod 600)
```

The bundle API defaults to `https://app.runcode.io`; override with `RUNCODE_API_BASE` or
`--api-base` (handy for a dev control plane behind a tunnel).

## Usage (standalone, without the agent)

```bash
runcode doctor                          # preflight: python, ssh, token, https base, reachability
runcode login                           # one-time: authorize via browser, save token
runcode list                            # list your workspaces (running = SSH-connectable)
runcode connect my-workspace            # mint a session + ATTACH the workspace
runcode connect my-ws --start --wait    # boot it if stopped, then wait until SSH answers
runcode connect 1234 --json             # by numeric id, machine-readable
runcode exec -- uname -a                # run on the ATTACHED workspace (in its project dir)
runcode exec --home -- uname -a         # …or from $HOME instead of the project dir
runcode context --json                  # orient: cwd, git, project, tool versions (one round trip)
runcode write src/app.py < app.py       # write a file on the box (base64 — any content is safe)
runcode get logs/app.log --out app.log  # download a workspace file (omit --out for stdout)
runcode put ./local.py src/app.py       # upload a local file to the box
runcode forward 5173                     # expose the box's :5173 at http://localhost:5173
runcode config-ssh                       # write ~/.ssh/config so YOUR ssh/git/VS Code reach the box
runcode config-ssh --remove              # remove those entries again
runcode current                         # show the attached workspace
runcode stop                            # stop the ATTACHED box (pauses compute billing) + detach
runcode stop my-ws --json               # stop a specific box, machine-readable
runcode disconnect                      # detach + drop the session (box keeps running)
runcode run my-workspace -- uname -a    # run on a specific workspace (no attach needed)
runcode shell my-workspace              # interactive shell
runcode status                          # list cached sessions + expiry
runcode statusline                      # one-line segment for a CLI status line
runcode install-statusline              # wire that cue into Claude Code settings.json
runcode install-path                    # put `runcode` on YOUR shell PATH (agent already has it)
runcode clean --all                     # wipe cached keys/configs
runcode logout                          # forget the saved token
```

`connect`/`run`/`shell`/`exec` reuse a cached session while it's still valid and silently
re-mint when it's close to expiry (or with `--force`).

**`connect --start --wait`** makes connecting "just work": `--start` boots a stopped
workspace (waiting until it reports *running*), then `--wait` blocks until SSH genuinely
answers (a just-started box can lag ~30s). `--timeout N` caps the combined wait (default
180s). Progress during that wait is written to **stderr** ("starting workspace #…",
"waiting for SSH…"), so a multi-minute wait never looks hung while **stdout stays pure
JSON**. With `--json`, failures are machine-readable: a non-zero exit prints
`{"error":"<code>","message":…}` (`no_token`, `unauthorized`, `start_failed`, `stop_failed`,
`not_ready`, `conflict`, `unavailable`, `not_found`, `insecure_base`, `unsafe_bundle`) so an
agent branches on the code, not the prose. The success JSON also includes `workdir` (the
project dir `exec`/`run` default into) and `statusline_installed` (so the agent can offer
to wire the workspace cue when it's missing).

**`stop` vs `disconnect`** are different verbs: `disconnect` is *local only* — it drops the
sticky attachment and cached session but leaves the workspace **running** (and still
billing compute). `stop` powers the **VM down** on the control plane (compute billing
pauses; storage persists), and because a stopped box can't serve `exec`, stopping the
attached box also detaches it. Run `stop` only when the user explicitly asks to shut the
box down; `connect --start` boots it again later.

### Sticky session: work happens on the workspace

`connect` **attaches** the workspace; `exec -- <cmd>` then runs on it without repeating
the name, and `disconnect` detaches. This is how the agent keeps *all* work on the
workspace until you're done — see the "Working on the workspace" section in
`skills/ssh/SKILL.md`. Note this is a **convention**: a coding agent's own shell/file
tools run locally, so the plugin can't force-reroute them — `exec` is what routes work to
the box, and the skill instructs the agent to use it for everything while attached.

### Your own tools (VS Code Remote / JetBrains / git)

The commands above route the **agent** to the box. To point your **own** editor, terminal,
or git at it, run:

```bash
runcode config-ssh           # write ~/.ssh/config host entries for every workspace
runcode config-ssh --remove  # take them back out
```

This adds a clearly delimited, backed-up managed block to `~/.ssh/config` — one
`Host runcode.<name>` per workspace — so everything that speaks ssh just works:

```bash
ssh runcode.my-workspace                         # a shell on the box
git clone runcode.my-workspace:/path/to/repo     # git/scp/rsync over the same path
code --remote ssh-remote+runcode.my-workspace /home/ubuntu/workspace   # VS Code Remote-SSH
# JetBrains Gateway: New connection → SSH → host = runcode.my-workspace
```

It needs **no** gateway address and stays valid even for a **stopped** box: each host
proxies through `runcode proxy <id>`, which mints/refreshes the short-lived key on
every connect (so the static config never goes stale) and pins the gateway host key itself.
Point your tool at a stopped workspace and the connection just fails cleanly — start it
first with `runcode connect <name> --start`. Re-run `config-ssh` after you create or
rename workspaces to refresh the list.

### Status line

`runcode statusline` prints a **loud, highlighted** one-line cue —
`⬡ working-on:royal-tree-08189931.ws.runcode.io · ~27m` on a filled color block (green,
yellow under 5 min, red under 2 min) — so it's unmistakable that work is going to a
remote workspace and not this machine. It tracks **only the currently _attached_
workspace** — the one `connect` last pointed at, exactly what `runcode current`
reports. It shows **only in the Claude Code session/project that attached** the
workspace — not in every Claude Code window on the machine. (The cue is scoped to the
directory you ran `connect` from: Claude Code runs the status-line command for every
session and tells it which one is asking, so other projects' status lines stay dark.)
It prints **nothing** when nothing is attached, and goes dark the instant you
`disconnect` — including `disconnect --keep` (which detaches but keeps the cached
session). It never lights up for a stale or unrelated cached session. It reads only the
local cache (no network), and never errors. The shown URL is the workspace's
**token-less** web host — never the `?tkn=` IDE URL, which would leak a live credential.
Add `--plain` for non-ANSI status lines (`working-on:<host> ~27m`).

Claude Code does **not** let a plugin contribute a `statusLine` (a plugin's `settings.json`
can only set `agent`/`subagentStatusLine`), and there's no composition API — there's a
single status-line slot. So the plugin owns the cue's *logic and styling* in this command,
and **you wire it into your status line** — easiest with one command:

```bash
runcode install-statusline          # writes the statusLine block into settings.json
runcode install-statusline --remove # undo
```

It edits the user's Claude Code `settings.json` (honoring `CLAUDE_CONFIG_DIR`), pointing
the `statusLine` at this script by **absolute path** — because `${CLAUDE_PLUGIN_ROOT}` is
*not* expanded in `settings.json`. It's idempotent, backs the old file up to
`settings.json.bak`, and won't clobber a different `statusLine` without `--force`.
`/runcode:statusline` runs it for you.

Prefer to wire it by hand?

- **No status line yet?** Point Claude Code straight at it (absolute path, since
  `runcode` may not be on the status-line process's `PATH`):
  ```jsonc
  // ~/.claude/settings.json
  { "statusLine": { "type": "command", "command": "/abs/path/to/cde-plugin/bin/runcode statusline --plain" } }
  ```
- **Already have a status-line script?** Call the command and prepend its output — one
  thin call, no RunCode logic copied into your script:
  ```python
  import shutil, subprocess
  exe = shutil.which("runcode")
  cue = subprocess.run([exe, "statusline"], capture_output=True, text=True).stdout.strip() if exe else ""
  status_line = f"{cue} {status_line}" if cue else status_line
  ```

A workspace **name** is resolved against your personal + shared workspaces; for **team**
workspaces pass the numeric **id** (shown in the RunCode dashboard).

## How it works

```
agent ──"${CLAUDE_PLUGIN_ROOT}/bin/runcode" connect <ws>──► generates ephemeral key
   │                                                            POST /api/rc/workspace/<id>/ssh-session
   │                                                              (Authorization: Token …, sends pubkey only)
   ▼                                          ◄── { host, port, user, known_hosts, ssh_config, expires_at }
ssh -F <isolated config> runcode-<ws> ───────► RunCode SSH gateway ───────► your workspace
```

The plugin builds the `ssh_config` **locally** from the bundle's `host`/`port`/`user` (each
validated) — it never feeds the server's `ssh_config` string to `ssh`, so the only directives
`ssh` ever parses are ones the plugin itself wrote. The generated block sets the alias/host/
port/user with `IdentitiesOnly yes` and `StrictHostKeyChecking yes`, plus the **local-only**
`IdentityFile`, `UserKnownHostsFile`, `GlobalKnownHostsFile /dev/null`, and `BatchMode yes` so
a non-interactive agent never hangs on a prompt. The bundle's `known_hosts` is normalized to
well-formed host-key lines before it's pinned.

On POSIX it also enables **connection multiplexing** (`ControlMaster auto` + a per-workspace
`ControlPath` socket inside the `0700` cache dir + `ControlPersist`): the first
`connect`/`exec` opens one authenticated connection through the gateway and every later
command reuses it as a near-instant channel-open instead of re-handshaking — so an agent
running dozens of commands feels like a local shell. The master is torn down on
`disconnect`/`stop`/`clean`. (Windows OpenSSH has no `ControlMaster`, so it's omitted there;
each command just opens its own connection.)

Higher-level helpers ride this same isolated session: `context` (a one-round-trip workspace
digest), `write`/`get`/`put` (move file bytes base64-encoded — any content survives with no
quoting hazard), and `forward` (add a port-forward to the master and return immediately).

**`config-ssh`** lets your *own* tools ride the same gateway. Because the bundle's
`host`/`port`/`user` (= `ws.title`) and the gateway host key are all **stable** per
workspace, the only thing that expires is the key — so each `~/.ssh/config` host is static
except for a `ProxyCommand runcode proxy <id>`. On connect, `proxy` re-mints (reusing a
still-valid session so an editor's many connections don't each mint), re-keys the gateway
host key into a shared `gateway_known_hosts` under a synthetic `HostName`, then relays
stdin/stdout to the gateway as a raw byte pipe — the same shape as Coder's
`ProxyCommand coder ssh --stdio %h`. This is the *one* place the plugin writes outside its
own cache; it never touches anything but its delimited block (we still **refuse** a
server-supplied `ProxyCommand` in the bundle — that guard is about untrusted input, whereas
this command is locally authored).

## Security

- **Private key never leaves this machine** — only the public key is sent.
- **Pinned host key** from the bundle ⇒ no TOFU prompt, no MITM window.
- **Isolated** `ssh -F` config ⇒ `~/.ssh` untouched; the user's personal keys are never
  offered to the gateway (`IdentitiesOnly yes`).
- **Short TTL** (default ~30 min, server-capped) and workspace-scoped.
- Session material lives in `~/.cache/runcode/ws-<id>/` (dir `700`, files `600`;
  user-profile ACLs on Windows); `runcode clean` removes it.
- **https-only control plane** — the engine refuses a non-`https` `--api-base`/`--web-base`
  (loopback `http` excepted for local dev), so the API token never rides over cleartext and
  the bundle that steers `ssh` can't be MITM'd into a downgrade. Refusal is `insecure_base`.
- **Locally-built ssh config (no injectable surface)** — the plugin constructs the `ssh_config`
  itself from the bundle's validated `host`/`port`/`user`; the server's `ssh_config` string is
  never parsed by `ssh`, and the bundle's `known_hosts` is normalized to host-key lines only.
  So a compromised/MITM'd control plane cannot inject a `ProxyCommand`/`Include`, enable agent
  forwarding, or downgrade host-key checking — it's an allowlist by construction, not a denylist
  of "bad" keywords. As a loud tripwire, a bundle whose `ssh_config` still ships a command-
  executing directive (`ProxyCommand`, `LocalCommand`, `KnownHostsCommand`, `Include`,
  `Match exec …`) is refused (`unsafe_bundle`) before `ssh` ever runs. Defense in depth atop
  the pinned host key + isolated config.
- **Sanitized remote paths** — the bundle's `workdir` is validated to a plain absolute path
  before it's interpolated into the remote `cd … && …` (and the `context` probe), so a
  compromised/MITM'd control plane can't inject a *remote* shell command through it; an
  unsafe value falls back to the default. Paths handed to `write`/`get`/`put` are
  single-quoted for the remote shell, so spaces and metacharacters are safe too. The same
  defense-in-depth stance as the hostile-bundle guard, applied to the remote side.
- **Terminal-safe output** — server- and remote-derived strings echoed to you (a teammate's
  workspace `custom_title` in `list`, the box's own `context` digest) are stripped of control
  bytes first, so a hostile title or compromised workspace can't smuggle ANSI/OSC escape
  sequences (cursor/title/clipboard spoofing) into your terminal.
- **Opt-in, surgical `~/.ssh/config` writes** — `config-ssh` is the only command that
  touches `~/.ssh`, and only ever its own `# >>> runcode managed block` … `<<<` span:
  it backs the file up to `config.bak`, writes atomically at `0600`, and removes cleanly. A
  workspace title is written into `Host`/`User` lines only if it matches a strict
  `[A-Za-z0-9._-]+` charset, and a user-set display name in the comment is collapsed to one
  printable line — so a compromised/MITM'd *list* response can't inject an ssh directive
  (verified against real `ssh -G`). The `proxy` ProxyCommand is locally authored (our own
  script by absolute path), not server-supplied.
- SSH access ≡ web-IDE access (you land as `ubuntu`, with sudo). No new privilege.

## Status / caveat

The backend SSH endpoint is **fail-closed** until the operator enables it on the server.
Until then `connect` returns **503 "SSH access is not configured"** — that's expected, not
a plugin bug.

## Troubleshooting

Run **`runcode doctor`** first — it checks Python, the OpenSSH client, your token, the
base URL, and reachability in one shot (great on a fresh machine or a new OS).

| Symptom | Cause / fix |
|---|---|
| `503 … not enabled on the server yet` | Backend gateway write channel not configured/deployed yet. |
| `401 unauthorized` | No/invalid token — run `runcode login` (or set `RUNCODE_TOKEN`). |
| `login timed out … no callback` | Browser never reached the `127.0.0.1` listener (e.g. logging in on a remote box) — use `runcode login --paste`. |
| `404 Workspace not found` | Token's user isn't authorized for that workspace, or wrong name/id. |
| `409 … must be running` / not SSH-capable | Pass `connect --start` to boot a stopped box; if it persists, it's one SSH can't reach. |
| `not_ready` after `--start`/`--wait` | Box didn't come up / answer in time — retry, or raise `--timeout`. |
| `ssh-keygen not found` / `ssh not found` | Install the OpenSSH client (on Windows: *Optional features → OpenSSH Client*). |
| `insecure_base` / refusing an insecure … | `--api-base`/`--web-base` (or `RUNCODE_API_BASE`/`RUNCODE_WEB_BASE`) is `http://` to a non-loopback host. Use `https://`. |
| `unsafe_bundle` / disallowed ssh option | The backend bundle contained a command-executing ssh directive — refused on purpose. Report it; it should never happen from a healthy control plane. |
| a host-key `yes/no` prompt appears | Should never happen (key is pinned); report it. |

## Development

The engine is a single Python 3.8+ stdlib script — **no build step, no dependencies, no
virtualenv**. To hack on it, point Claude Code at a local checkout (changes reload with
`/reload-plugins`, no reinstall):

```bash
claude --plugin-dir /path/to/cde-plugin
```

Run the test suite (a self-contained script — no pytest, no network; it exercises the
engine against monkeypatched seams):

```bash
python3 tests/test_engine.py        # prints "[N] …: OK" per case, ends "ALL PASS"
```

A note on the threat model the code defends against: the SSH connection bundle from the
backend is treated as **attacker-controllable** (a compromised or MITM'd control plane).
The plugin's defenses — an ssh config built locally by allowlist, `known_hosts` normalized
to host-key lines, and terminal-escape sanitization of any server- or remote-derived string
before it's printed — exist for exactly that case. If you touch those paths, keep them
allowlist-by-construction and add a test alongside.

## License

[MIT](LICENSE) © RunCode
