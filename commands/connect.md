---
description: Open an SSH session to a RunCode workspace and optionally run a command.
argument-hint: [workspace] [command...]
---

Reach the user's RunCode workspace over SSH, following the `ssh` skill's rules
(pinned host key, no TOFU prompt, re-mint once on expiry, never loop).

Arguments: `$ARGUMENTS`
Interpret the **first token** as the workspace name or numeric id, and any **remaining
tokens** as a command to run on it (optional).

1. If no workspace was given, **list the user's workspaces first** instead of asking
   blindly: `runcode list --json` (each entry has `id`, `title`, `state`, and a
   `connectable` flag).
   - If it reports no token / `401` / `Run runcode login`: do the `/runcode:login`
     flow first, then retry.
   - If **exactly one** workspace is `connectable` (running and SSH-capable), use it
     without asking.
   - If several are connectable, show them (id + name + state) and ask which one.
   - If the one the user wants is **stopped** (SSH-capable but not running), you do
     **not** need the dashboard — `connect --start` (step 2) boots it for them.
   - If the list is empty or nothing is SSH-capable at all, tell the user they have no
     workspace to SSH into and stop — don't guess a name.
2. Establish a session — start the box if needed and wait until it actually answers:
   `runcode connect "<workspace>" --start --wait --json`, then read `config` and
   `alias` from the JSON (`started: true` means it was stopped and you started it).
   - `--start` boots a stopped workspace and waits for *running*; `--wait` then blocks
     until SSH is truly reachable (a freshly-started box can lag ~30s). Drop `--start`/
     `--wait` only when you already know it's up and answering.
   - **On failure, parse the JSON** — a non-zero exit prints `{"error":"<code>","message":…}`
     on stdout. Branch on the `error` code; don't regex the prose:
     - `no_token` / `unauthorized` → do the `/runcode:login` flow, then retry **once**.
     - `start_failed` → the message says why (e.g. insufficient balance); relay it and stop.
     - `not_ready` → it didn't come up / answer in time; tell the user it's still starting
       and retry shortly (**once**).
     - `conflict` → not SSH-capable or unreachable; surface the message, don't loop.
     - `insecure_base` → a non-https `--api-base`/`--web-base` was given; fix it to https.
     - `unsafe_bundle` → the backend bundle carried a command-executing ssh option and was
       refused for safety; stop and report it, never work around it.
3. A successful connect **attaches** the workspace (it becomes the sticky target). If a
   command was given, run it with `runcode exec -- <command>` (it runs in the project
   dir — the JSON's `workdir` — automatically, no `cd` prefix needed) and report the
   output. Otherwise confirm the session is ready.
   - **Orient once, right after connecting:** run `runcode context --json` (cwd, git
     branch/dirty, project markers, tool versions) so you work from the box's real state
     instead of probing it with a dozen `exec`s. The first `exec`/`context` also warms a
     shared connection, so everything after it is fast.
   - If the connect JSON shows `"statusline_installed": false`, offer **once** to run
     `/runcode:statusline` so the user always sees which workspace work is going to (the
     sticky session silently routes everything to the box). Respect a decline; don't re-ask.
4. **From now on, ALL work happens on the workspace, not locally** — including brand-new
   or unrelated projects. Run every command and file read/edit via `runcode exec -- …`
   (see the `ssh` skill's "Working on the workspace" section). Do **not** deliberate or
   ask whether something should live locally vs. on the workspace; while attached, it's
   the workspace. Claude Code's local `Bash`/`Read`/`Edit` tools do **not** reach the box.
   Only an **explicit** user disconnect (`/runcode:disconnect`) ends this — not a finished
   task, not a topic change, not a transient connection error (re-mint and continue).
