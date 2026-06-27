---
description: Expose a RunCode workspace port locally (e.g. open a dev server in your browser).
argument-hint: "<port> [--cancel]"
---

Forward a port from the user's **attached** RunCode workspace to their local machine, so a
dev server / app running on the box opens in their browser. Follows the `ssh` skill's rules.

Arguments: `$ARGUMENTS` — the workspace **port** to expose (e.g. `5173`), plus optional
`--local <port>` (listen on a different local port) / `--to <host>` (reach a host other than
`localhost` on the box) / `--cancel` (remove a forward instead of adding it).

1. There must be an **attached** workspace (`runcode connect …` first). Then run
   `runcode forward <port>`. It adds the forward to the session's multiplexed connection
   and **returns immediately** — there's no blocking tunnel process to manage. It prints the
   local URL (`http://localhost:<port>`).
2. **Tell the user the local URL** and what it maps to on the workspace. Remove it later with
   `runcode forward <port> --cancel`.
3. **Offer this proactively** when you start a long-running server on the box (a dev server,
   an API, a notebook) — the user usually wants to see it. The workspace's token-less web URL
   (from `connect --json` → `web_url`) is the other way to reach published apps.
   - `unavailable` on **Windows**: port forwarding via the session master isn't available
     there; relay the `ssh -L …` command the tool prints for the user to run in a terminal.
   - `conflict`: the forward couldn't be set up (port in use locally, box unreachable) —
     surface the message; don't loop.
