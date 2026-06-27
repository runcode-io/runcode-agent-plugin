---
description: Preflight check that this machine can reach RunCode (python, OpenSSH, token, base).
---

Run a quick environment check so the user knows their machine is ready to use RunCode over
SSH — especially on a fresh setup or a new operating system (Windows / macOS / Linux).

1. Run `runcode doctor` (add `--json` to parse it — it prints
   `{"ok":bool,"checks":[{"name","status","detail"}]}` and exits non-zero when unhealthy).
   It checks: Python 3.8+, the OpenSSH client (`ssh` + `ssh-keygen`), whether an API token
   is configured, that the API base is `https`, and — best-effort — that the token actually
   authorizes against the backend.
2. Report the result. For any `✗` (fail):
   - `ssh` / `ssh-keygen` not found → install the OpenSSH client (on Windows: *Settings →
     Apps → Optional features → OpenSSH Client*).
   - `api-base` insecure → the configured base (or `RUNCODE_API_BASE`) is non-`https`; point
     it at an `https://` URL.
   A `⚠` on `token` / `auth` usually just means "not logged in yet" — run the
   `/runcode:login` flow, then re-run `runcode doctor`.
