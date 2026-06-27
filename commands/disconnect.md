---
description: Disconnect from the attached RunCode workspace; work reverts to the local machine.
---

Stop the sticky RunCode workspace session, following the `ssh` skill's rules.

1. Run `runcode disconnect`. This detaches the workspace (so `runcode exec`
   refuses afterwards) and drops its cached session material. Add `--keep` to just
   detach while leaving the cached session in place.
2. Tell the user they're disconnected and that subsequent work now runs on the **local**
   machine again (Claude Code's normal `Bash`/`Read`/`Edit` tools).
3. If nothing was attached, `disconnect` says so — just report that.
