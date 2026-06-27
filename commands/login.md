---
description: Authorize this machine for RunCode workspace SSH (one-time browser login).
---

Authorize this machine to use the user's RunCode workspaces over SSH. **You** run the
login command — never tell the user to open a separate terminal; the user's only action
is one click.

1. Tell the user: a browser tab will open and they must click **Authorize**.
2. Run `runcode login` as a **background shell**. It prints the authorize URL right away
   (output is line-buffered), so read the background output **once** and surface that URL to
   the user as a clickable fallback in case the browser doesn't pop up — no need to poll in a
   loop. The command then blocks (~180s) until the click, after which it saves the token to
   `~/.config/runcode/token` and exits.
3. On success, tell the user they're logged in; they can now use `/runcode:connect`.

The single **Authorize** click is a deliberate security consent (the token grants full API
access) — it can't be skipped or automated.

Remote/headless machine (no local browser, so the `127.0.0.1` callback isn't reachable):
run `runcode login --paste` and ask the user to paste a token from the RunCode
dashboard's CLI-token page instead.

`runcode logout` forgets the saved token.
