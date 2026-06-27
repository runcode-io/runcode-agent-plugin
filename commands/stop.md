---
description: Stop a RunCode workspace (VM powers down, compute billing pauses; storage persists).
---

Stop a RunCode workspace on the control plane, following the `ssh` skill's rules. This is
heavier than `disconnect`: it powers the **VM down** (compute billing pauses, storage
persists), not just the local session.

**Only run this when the user explicitly asks to stop/shut down/power off the box.** Never
stop a workspace to "tidy up" after a task — finishing work is not a reason to stop.

1. Pick the target:
   - No argument → the **attached** workspace (the one `connect` last pointed at).
   - `$ARGUMENTS` (a workspace name or numeric id) → that specific workspace.
2. Run `runcode stop [workspace] --json` and branch on the `error` code (don't
   pattern-match the prose):
   - success → it prints `{"stopped":true,"workspace_id":N,"detached":bool}`. If it was the
     attached box, `detached` is `true`: the sticky pointer is cleared and work reverts to
     the **local** machine. Add `--keep` to detach yet keep the cached session material.
   - `stop_failed` → the backend refused (not authorized, banned). Relay the message and
     stop.
   - `forbidden`/403 → the token's user can't manage that workspace. Surface it.
   - `not_found` → no workspace was given and none is attached, or the name/id is wrong.
   - `insecure_base` → a non-`https` API base was configured; fix the URL.
3. Tell the user the box is stopped (compute billing paused; storage persists) and that
   `connect --start` will boot it again later.
