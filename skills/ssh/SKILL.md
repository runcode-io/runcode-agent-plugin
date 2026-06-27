---
name: ssh
description: Connect to and run commands on the user's RunCode cloud workspace over SSH. Use when the user asks to open, connect to, work on, run something on, deploy to, or port-forward from their RunCode workspace (a remote cloud dev environment / CDE). Mints a short-lived, workspace-scoped SSH session through the RunCode gateway.
---

# RunCode workspace over SSH

Operate on a user's **RunCode workspace** (a remote cloud dev environment) over
SSH from the local machine — run commands, edit via `rsync`/`scp`, forward ports.

## Prerequisites
- A RunCode API token, obtained **once** via `runcode login` (see below), or supplied
  in `RUNCODE_TOKEN` / `~/.config/runcode/token` (chmod 600). If it's missing, the tool
  says `Run \`runcode login\`` — **run that yourself** (see First-time login); do not
  guess a token and do not tell the user to open a separate terminal.
- The target workspace must be **SSH-capable**. It does **not** need to be running first:
  `connect --start` boots a stopped box for you and waits until it's up (see the workflow).
  If a workspace isn't SSH-capable at all, the backend returns a clear `conflict`/409 —
  surface that and don't retry blindly.
- The local box needs **Python 3.8+** and the **OpenSSH client** (`ssh`/`ssh-keygen`) on
  `PATH` (Windows/macOS/Linux all supported). If the user hits environment trouble — a fresh
  machine, a new OS, "ssh not found", "can't reach the server" — run `runcode doctor`
  (`--json` for `{"ok",checks[]}`, non-zero exit when unhealthy); it pinpoints what's missing
  in one shot. Report the `✗` lines and fix them before retrying.

## First-time login — YOU run this, the user just clicks
When no token is saved, run the login command **yourself** (it's the agent's job, not the
user's):
```bash
runcode login
```
1. First tell the user: *"A browser tab will open to authorize RunCode access — please click
   **Authorize**."*
2. Then run the command (allow ~180s for the Bash call — it blocks until the click). It opens
   the user's default browser to the consent page and, on **Authorize**, saves the token to
   `~/.config/runcode/token` (0600) and exits `✓`.
3. The command also prints the authorize URL — **surface that URL to the user** as a clickable
   fallback in case the browser doesn't pop up on its own. Run `login` as a **background
   shell**: it prints the URL right away (output is line-buffered), so read the background
   output **once** to grab the URL — no poll loop — then show it and wait for the shell to
   finish (it blocks ~180s until the click).

The single **Authorize** click is the *only* thing the user does — it's a deliberate security
consent (the token grants full API access), so it can't be skipped or automated. You drive
everything else. After it succeeds, retry the original `connect`/`run`.

- **Remote/headless** machine (Claude Code is not running on the user's desktop, so the
  `127.0.0.1` callback isn't reachable from their browser): run `runcode login --paste`
  and ask the user to paste a token from the dashboard's CLI-token page, or `--no-browser`
  to print the URL to open elsewhere.
- `runcode logout` forgets the saved token.

## The tool
The command is **`runcode`** — invoke it by that bare name. Claude Code puts the
plugin's `bin/` on the Bash tool's `PATH` while the plugin is enabled, so `runcode`
resolves in every Bash call, identically on Windows/macOS/Linux (on Windows it resolves
to `runcode.cmd`). In the rare case a shell reports `runcode: command not found`, fall
back to the absolute path `${CLAUDE_PLUGIN_ROOT}/bin/runcode`. Python 3 stdlib, no
dependencies. It generates an ephemeral key, fetches a connection bundle, and writes an
**isolated** ssh config with a **pinned host key** — `ssh -F <config>` ignores `~/.ssh`
entirely, so it never offers the user's personal keys to the gateway and never edits
their config.

## Workflow

0. **Don't know which workspace?** List them — never guess a name:
   ```bash
   runcode list --json
   ```
   Each entry has `id`, `title`, `custom_title`, `state`, and a `connectable` flag
   (running and SSH-capable). If exactly one is `connectable`, use it; otherwise show
   the user the connectable workspaces and ask which. A **stopped** SSH-capable box is
   fine — `connect --start` (next) boots it; workspaces SSH can't reach are hidden unless
   you pass `--all`. The default (no `--json`) prints a readable table.

1. **Establish a session** — start the box if it's stopped and wait until it actually
   answers SSH (reuses a cached session if one is still valid):
   ```bash
   runcode connect "<workspace name or id>" --start --wait --json
   ```
   Read `config` and `alias` from the JSON; `started: true` means it was stopped and you
   started it. The JSON also carries **`workdir`** (the project dir on the box — use it
   instead of hard-coding a path) and **`statusline_installed`** (see the status-line
   bullet below — if it's `false`, offer to wire the cue once). `<workspace>` may be the
   workspace **name** (resolved for the user's personal/shared workspaces) or its
   **numeric id** (always works — use the id for team workspaces).
   - During `--start`/`--wait` the command prints progress to **stderr** ("starting
     workspace #…", "waiting for SSH…") while stdout stays pure JSON — those stderr lines
     are normal reassurance, not errors; parse only stdout.
   - `--start` boots a stopped workspace and waits for *running*; `--wait` then blocks
     until SSH is genuinely reachable (a just-started box can lag ~30s behind "running").
     Skip them only when you already know the box is up and answering.
   - **On failure the command exits non-zero and prints `{"error":"<code>","message":…}`
     on stdout** — branch on the stable `error` code, don't pattern-match the message:
     `no_token`/`unauthorized` → log in then retry once; `start_failed` → relay the reason
     (e.g. insufficient balance) and stop; `not_ready` → still booting, retry shortly once;
     `conflict` → not SSH-capable/unreachable, surface and stop.

   A successful `connect` **attaches** the workspace: it becomes the sticky target for
   `exec` below, so you don't repeat its name. See **Working on the workspace**.

1a. **Orient on the box — one round trip, right after connecting.** Don't probe the
   workspace with a dozen separate `exec`s; get a structured digest at once:
   ```bash
   runcode context --json
   ```
   It returns `cwd`, `os`/`arch`/`distro`, a `git` block (`branch`, `dirty`, `remote`,
   `head`), `markers` (project files present), and `tools` (name→version). Read it and
   work from the real state — run the test command that fits the detected stack, branch
   off the current git branch, notice uncommitted changes before editing.

2. **Run a command on the attached workspace** — your shell for the whole session.
   `exec` lands in the project dir (the `workdir` from connect) by default, so you no
   longer need a `cd …` prefix:
   ```bash
   runcode exec -- pytest -q
   ```
   The **first** `exec`/`connect` opens a shared (multiplexed) connection and every later
   `exec` reuses it, so commands after the first are near-instant rather than re-handshaking
   through the gateway. `exec` also **re-mints and retries once on its own** if the session
   was dropped mid-run — you don't have to handle that. (`run "<workspace>" -- <command>`
   does the same for a specific workspace; add `--home` to run from `$HOME` instead of the
   project dir; the low-level form is `ssh -F <config> <alias> -- <command>`.)

3. **Interactive shell** — only when the user explicitly wants a terminal:
   ```bash
   runcode shell "<workspace>"
   ```

4. **Forward a port** (e.g. a dev server running on the workspace) — adds the forward to
   the session and returns immediately, no blocking tunnel to babysit:
   ```bash
   runcode forward 5173            # http://localhost:5173 -> box :5173
   runcode forward 5173 --cancel   # remove it
   ```
   **Offer this when you start a server on the box** — the user usually wants to see it.
   (Windows has no session master, so `forward` there prints an `ssh -L` command to run
   in a terminal instead.)

5. **Set up the user's OWN editor / IDE / git** (distinct from routing *your* work to the
   box) — when the user wants to open the workspace in **their** VS Code Remote, JetBrains
   Gateway, or use plain `ssh`/`git`/`scp`/`rsync` themselves:
   ```bash
   runcode config-ssh            # write ~/.ssh/config host entries
   runcode config-ssh --remove   # take them back out
   ```
   This adds a delimited, backed-up, removable block to `~/.ssh/config` so each workspace is
   reachable as `runcode.<name>` (`ssh runcode.<name>`, `code --remote ssh-remote+runcode.<name>`,
   JetBrains host `runcode.<name>`, `git clone runcode.<name>:…`). It's the **only** command
   that writes to `~/.ssh` — opt-in and reversible. **Offer it specifically when the user
   asks to use their own editor/IDE/git** against the box; for keeping *your* work on the
   workspace, use the sticky session (`exec`) below, not config-ssh. A stopped box still
   needs `connect <name> --start` before a tool can connect.

`git`, `rsync`, `scp`, and Remote-SSH-style tools work either via `ssh -F <config> <alias>`
(the session this skill mints) or, for the user's own tools, via `config-ssh` (step 5).

## Working on the workspace (sticky session)

Once connected, the workspace is your **working environment**: every command, file
read, and file edit happens **on the workspace**, not locally, until the user
**explicitly** disconnects. This is a discipline you must keep — **Claude Code's own
`Bash`, `Read`, `Edit`, `Write`, and `Grep` tools all act on the LOCAL machine and do
NOT reach the workspace.** Route everything through the session instead:

**Attachment is sticky and exclusive — it is NOT scoped to any repo or task.** Once a
workspace is attached, *all* work goes there: new projects, scratch files, tasks
unrelated to whatever is already on the box, a fresh git repo, anything. Do **not**
deliberate "local vs. workspace," do **not** ask the user where the work should live, and
do **not** treat "this is unrelated to what's on the box" as a reason to work locally —
the user attached a workspace, so the workspace is where work happens. (Starting an
aquaculture marketing site while attached to a Django box? It still goes on the
workspace.) If you genuinely cannot proceed on the workspace, say so explicitly and stop;
never silently do the work locally instead.

**Working directory:** the project lives at the session's **`workdir`** (read it from the
connect JSON; the default is **`/home/ubuntu/workspace`**) — the repo (if any) is cloned
there and the web IDE opens it. `exec`/`run` **`cd` into it automatically**, so a bare
`exec -- <cmd>` already runs in the project dir; you only need an explicit path or `--home`
to step outside it. SSH lands as `ubuntu`.

- **Orient first:** `runcode context --json` (cwd, git, project markers, tool
  versions) — one round trip instead of probing with many `exec`s.
- **Commands / builds / tests / git / installs:** `runcode exec -- <command>`.
  Use this in place of the local `Bash` tool for all task work. It targets the attached
  workspace automatically (no name needed), runs in the project dir, reuses one multiplexed
  connection (fast after the first), and re-mints + retries once on its own if the session
  drops.
  ```bash
  runcode exec -- <command>            # runs in the project dir
  runcode exec --home -- <command>     # …or from $HOME instead
  ```
- **Read a workspace file:** `runcode get <path>` (to stdout, or `--out <local>` to
  save it). Quick peeks can still use `runcode exec -- cat <path>`. Do **not** use the
  local `Read` tool — it reads your machine.
- **Create / edit a workspace file — directly on the box, no local copy.** `runcode
  write <remote-path>` streams content straight from stdin into the remote file
  (base64 over the session): **nothing is written to your local disk**, and any
  content is safe (no heredoc/quoting hazard, no `$VAR`/backtick expansion on the
  remote shell, no clobber from a stray redirect). Generate the content in your
  context and pipe/heredoc it in — do **not** author it with the local `Write` tool
  first:
  ```bash
  runcode write src/app.py <<'EOF'        # create or replace, content inline
  ...file contents...
  EOF
  printf '%s' "$NEW" | runcode write src/app.py   # or pipe generated content in
  ```
  **To edit an existing file, don't stage it locally:** `runcode get <path>` (to
  stdout) reads the current content into your context, change it there, then `runcode
  write <path>` the new version back — still zero local files. For a one-liner in
  place, `runcode exec -- sed -i 's/.../.../' <file>`. Paths are relative to the
  project dir (or absolute).
  - `runcode put ./local.py src/app.py` and `rsync -az -e "ssh -F <config>" ./
    <alias>:/home/ubuntu/workspace/` (absolute project path — `rsync` drives its own
    `ssh`, so the `exec` cwd default doesn't apply) are for when you **already have a
    real local file/tree** to copy up. They are **not** a path to author workspace
    content: never create a local file just to `put` it — `write` it directly.
- **Check what's attached:** `runcode current`. There is exactly **one** attached
  workspace at a time (the sticky pointer). An always-on **status-line cue** reflects this
  same pointer — it names the attached workspace + time left and goes dark the moment you
  disconnect. Because attachment silently routes *all* work to the remote box, this cue is
  the user's main signal that they're on the workspace. `connect --json` reports
  **`statusline_installed`**: when it's `false` (and `doctor` shows a `⚠ status-line`),
  offer **once** to run `runcode install-statusline` (writes a `statusLine` into their
  Claude Code `settings.json`, pointing at this script; `--remove` undoes it).
  `/runcode:statusline` does this for them. Don't nag — offer the first time, then respect
  a decline.
- **Disconnect — only on the user's explicit request** (they say "disconnect" or run
  `/runcode:disconnect`): `runcode disconnect`. After that, `exec` refuses, the
  status-line cue goes dark, and work returns to the local machine. Do **not** disconnect
  on your own initiative — not when a task finishes, not when you switch topics.
- **Stop the workspace — also only on the user's explicit request** (they say "stop",
  "shut down", "power off", or want to stop being billed): `runcode stop [workspace]
  --json`. This is heavier than `disconnect` — it powers the **VM down** on the control
  plane (compute billing pauses, storage persists), not just the local session. With no
  argument it stops the **attached** box and detaches it (a stopped box can't serve
  `exec`); pass a name/id to stop a specific one. On success it prints
  `{"stopped":true,"workspace_id":N,"detached":bool}`; on refusal it returns
  `stop_failed` — relay the reason and stop. `connect --start` boots the box again later.
  **Never stop a workspace on your own initiative** — finishing a task is *not* a reason to
  stop the box; only the user decides to power it down.

**Only an explicit user disconnect ends the session — nothing else does.** A connection
error, a timed-out command, an expired session, or a topic change does **not** revert you
to local. On a transient SSH/connection error or expiry, re-mint **once** (`exec`
re-mints automatically, or `connect "<workspace>" --force`) and continue **on the
workspace**. Never quietly fall back to running the work locally because the box seemed
unreachable or the task seemed unrelated — if you truly can't reach the workspace, report
that and stop, don't substitute local work.

## Failure handling (do not loop)
With `--json`, a failed command exits non-zero and prints `{"error":"<code>","message":…}`
on stdout. Branch on the **`error` code** (stable) rather than the message text:
- **`no_token` / `unauthorized`** (also shown as `Run runcode login`): run
  `runcode login` yourself (First-time login above), have the user click **Authorize**,
  then retry the original command **once**. Never tell the user to use a separate terminal.
- **`start_failed`** (only with `--start`): the backend refused to start the box — the
  message says why (insufficient balance, not authorized, banned). Relay it and stop.
- **`stop_failed`** (only with `stop`): the backend refused to stop the box (not authorized,
  banned). Relay the message and stop; not retryable as-is.
- **`not_ready`** (only with `--start`/`--wait`): the box didn't reach running or didn't
  answer SSH within the timeout. Tell the user it's still starting; retry shortly **once**
  (optionally a longer `--timeout`). Do **not** fall back to local work.
- **`conflict`/409**: the workspace isn't SSH-capable or can't be reached — surface the
  message; not retryable.
- **`unavailable`/503**: the server side of the feature isn't enabled / the gateway
  refused — report it; not retryable.
- **`not_found`/404**: the token's user isn't authorized for that workspace, or the
  name/id is wrong.
- **`insecure_base`**: a non-`https` `--api-base`/`--web-base` (or `RUNCODE_API_BASE`/
  `RUNCODE_WEB_BASE`) was given for a non-loopback host. The engine refuses cleartext;
  fix the URL to `https://`. Not retryable as-is.
- **`unsafe_bundle`**: the backend's connection bundle contained an ssh option that could
  run a local command — refused on purpose (a safety net against a compromised/MITM'd
  control plane). Stop and report it; never work around it. Should never occur normally.
- **No host-key (TOFU) prompt** should ever appear — the key is pinned. If one does,
  stop and report it; something is wrong.
- **Expiry**: sessions are short-lived (default ~30 min). `exec` re-mints automatically;
  otherwise re-mint **once** with `connect "<workspace>" --force`, then retry. Don't loop.

## Security note
SSH lands you as `ubuntu` (with sudo) — the **same** access level as the web IDE
terminal. The credential is short-lived and scoped to this one workspace. The private
key lives only on this machine (`~/.cache/runcode/`, mode 600); `runcode clean`
removes it.
