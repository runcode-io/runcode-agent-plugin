---
description: Orient on a RunCode workspace — one-shot digest of cwd, git, project, and tools.
argument-hint: "[workspace]"
---

Gather a quick, structured picture of the user's RunCode workspace **in one round trip**,
so you don't probe the box with a dozen separate `exec` calls. Follows the `ssh` skill's
rules (it re-mints a session if needed).

Arguments: `$ARGUMENTS` — an optional workspace name or numeric id. With **no argument** it
targets the **attached** workspace (the sticky `exec` target); pass a name/id to inspect a
specific one.

1. Run `runcode context --json` (append the workspace if one was given). It returns:
   - `cwd`, `os`, `arch`, `kernel`, `distro` — where you are and what you're on,
   - `git` — `branch`, `dirty` (yes/no), `remote`, `head` (omitted if the dir isn't a repo),
   - `markers` — project files present (`package.json`, `pyproject.toml`, `go.mod`, …),
   - `tools` — a name→version map (`python3`, `node`, `go`, `docker`, …),
   - `workspace_id`, `workdir`, `disk`.
   - If it reports no token / `401`: do the `/runcode:login` flow first, then retry.
   - `not_found` / `conflict`: nothing attached and no name given, or the box can't be
     reached — surface the message and stop.
2. **Use it to orient.** Summarize what the project is and what's installed, then work from
   the real state instead of guessing — e.g. run the right test command for the detected
   stack, branch off the current git branch, note uncommitted changes before editing.
3. This is most useful **right after `connect`** (the sticky session routes all work to the
   box, so know the box first) and again whenever you've lost track of where things stand.
