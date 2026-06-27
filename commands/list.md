---
description: List your RunCode workspaces (running ones can be reached over SSH).
---

List the user's RunCode workspaces so they can see what's available and pick one to
connect to, following the `ssh` skill's rules.

1. Run `runcode list` for a human-readable table, or `runcode list --json` to
   parse it (each entry has `id`, `title`, `custom_title`, `state`, and a `connectable`
   flag). Add `--all` to include workspaces that can't be reached over SSH.
   - If it reports no token / `401` / `Run runcode login`: do the `/runcode:login`
     flow first (you run it, the user clicks **Authorize**), then retry **once**.
2. Present the workspaces to the user. A **running** workspace is SSH-connectable now
   (`/runcode:connect <id>`); a **stopped** SSH-capable one can be booted directly with
   `/runcode:connect <id> --start` (no dashboard needed); some workspaces can't be reached
   over SSH at all (omitted unless `--all`).
3. By default only SSH-connectable workspaces are shown. If the user explicitly wants to
   see everything, re-run with `runcode list --all`.
