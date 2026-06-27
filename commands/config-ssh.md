---
description: Write ~/.ssh/config entries so the user's own VS Code Remote / JetBrains / git / ssh reach their RunCode workspaces.
argument-hint: "[--remove]"
---

Set up the user's **own** local tools (not just this agent) to reach their RunCode
workspaces over SSH, by writing a managed block to `~/.ssh/config`. Follows the `ssh`
skill's rules.

Arguments: `$ARGUMENTS` — pass `--remove` to take the entries back out; otherwise it
writes/refreshes them.

1. Run `runcode config-ssh`. It writes a clearly delimited, backed-up
   (`~/.ssh/config.bak`), removable block — one `Host runcode.<name>` per SSH-capable
   workspace — and prints the host aliases it added.
   - If it reports no token / `401`: do the `/runcode:login` flow first, then retry.
   - If it reports `not_found` ("no SSH-capable workspaces"): the user has no workspace
     SSH can reach; tell them and stop.
2. **Tell the user how to use it**, tailored to what they're doing:
   - Shell: `ssh runcode.<name>`
   - VS Code: Remote-SSH → connect to host `runcode.<name>` (or
     `code --remote ssh-remote+runcode.<name> /home/ubuntu/workspace`)
   - JetBrains: Gateway → New SSH connection → host `runcode.<name>`
   - git/scp/rsync: use `runcode.<name>:<path>` as the remote
3. **It works even for a stopped box** — each host re-mints the short-lived key on connect
   via a `proxy` ProxyCommand, so the config never goes stale. But a *stopped* workspace
   still can't accept connections: if the user points a tool at one and it fails, tell them
   to start it first with `runcode connect <name> --start` (or `/runcode:connect <name>`),
   then reconnect. Re-run `config-ssh` after they create or rename workspaces.
4. This is the **only** command that writes to `~/.ssh` — it's opt-in and reversible.
   `runcode config-ssh --remove` strips the block, leaving the rest of their config
   untouched. Offer it when the user asks to use their *own* editor/IDE/git against the
   workspace; for routing **this agent's** work to the box, use `/runcode:connect` + `exec`
   instead (the sticky session), not config-ssh.
