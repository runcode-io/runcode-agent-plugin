---
description: Add the RunCode workspace indicator to your Claude Code status line (one-time).
---

Wire an always-on status-line cue that names the **attached** RunCode workspace and the
time left on the session, so it's always visible when work is being routed to the remote
box (and goes dark the moment you disconnect). Follow the `ssh` skill's rules.

1. Run `runcode install-statusline`. It writes a `statusLine` block into the user's
   Claude Code `settings.json` (honoring `CLAUDE_CONFIG_DIR`), pointing at this plugin's
   `runcode statusline` by absolute path. It is idempotent, backs up any existing
   `settings.json` to `settings.json.bak`, and will **not** overwrite a different,
   non-RunCode `statusLine` unless you pass `--force`.
   - If it reports a different statusLine is already set: tell the user, and only re-run
     with `--force` if they confirm they want to replace it (their old one is in the
     `.bak` backup).
2. Tell the user it's installed and that the cue appears once they `/runcode:connect` to a
   workspace. The status line refreshes on each turn; no restart needed.
3. To remove it later: `runcode install-statusline --remove` (only removes the RunCode
   one; leaves other settings untouched).
