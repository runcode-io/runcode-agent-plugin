#!/bin/sh
# Offline tests for the bin/runcode launcher.
#
# The plugin ships a tiny POSIX launcher as bin/runcode. On first use it fetches
# the matching Go binary from the runcode-cli GitHub release, verifies its
# sha256 against checksums.txt, caches it, and execs it. These tests prove the
# security-critical behaviour WITHOUT touching the network, by pointing the
# launcher at a local fixture "release" over file:// and at a temp cache dir.
#
# Run:  sh tests/test_launcher.sh
# Requires: sh, curl, tar, sha256sum|shasum (the launcher's own dependencies).

set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LAUNCHER="$HERE/../bin/runcode"

fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }

if ! command -v curl >/dev/null 2>&1; then
  echo "SKIP - curl not available; launcher cannot be tested" >&2
  exit 0
fi

# Map this host to the release asset name exactly as the launcher must.
host_os() { case "$(uname -s)" in Linux) echo linux ;; Darwin) echo darwin ;; *) echo unsupported ;; esac; }
host_arch() { case "$(uname -m)" in x86_64 | amd64) echo amd64 ;; aarch64 | arm64) echo arm64 ;; *) echo unsupported ;; esac; }
OS=$(host_os)
ARCH=$(host_arch)
ASSET="runcode_${OS}_${ARCH}.tar.gz"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# Build a fake "release" dir: a runcode payload that announces itself + echoes
# its args, exits 7 on `--exit-7`; tar.gz'd as the host asset; checksums.txt.
# mode=bad writes a deliberately wrong checksum.
make_release() {
  rel="$1"
  mode="${2:-good}"
  mkdir -p "$rel"
  cat >"$rel/runcode" <<'PAYLOAD'
#!/bin/sh
echo "FAKE_RUNCODE_RAN args=[$*]"
[ "${1:-}" = "--exit-7" ] && exit 7
exit 0
PAYLOAD
  chmod +x "$rel/runcode"
  (cd "$rel" && tar -czf "$ASSET" runcode)
  if [ "$mode" = "bad" ]; then
    sum="0000000000000000000000000000000000000000000000000000000000000000"
  else
    sum=$(sha256_of "$rel/$ASSET")
  fi
  printf '%s  %s\n' "$sum" "$ASSET" >"$rel/checksums.txt"
}

cached_count() { find "$1" -type f -name 'runcode-*' 2>/dev/null | wc -l | tr -d ' '; }

# ── Test 1: good release → downloads, verifies, execs, caches ────────────────
T=$(mktemp -d)
make_release "$T/rel" good
out=$(RUNCODE_PIN_VERSION=testver RUNCODE_CACHE="$T/cache" \
  RUNCODE_DOWNLOAD_BASE="file://$T/rel" "$LAUNCHER" list --json 2>/dev/null)
if echo "$out" | grep -q 'FAKE_RUNCODE_RAN args=\[list --json\]'; then
  pass "good release: downloads, verifies, execs with args passed through"
else
  fail "good release: expected payload to run with args; got: $out"
fi
if [ "$(cached_count "$T/cache")" -ge 1 ]; then
  pass "good release: binary is cached after first run"
else
  fail "good release: nothing cached"
fi

# ── Test 2: exit code passes through ────────────────────────────────────────
RUNCODE_PIN_VERSION=testver RUNCODE_CACHE="$T/cache" \
  RUNCODE_DOWNLOAD_BASE="file://$T/rel" "$LAUNCHER" --exit-7 >/dev/null 2>&1
[ "$?" -eq 7 ] && pass "exit code from real binary propagates (7)" || fail "exit code not propagated"

# ── Test 3: cache reuse → no network needed on second run ───────────────────
out=$(RUNCODE_PIN_VERSION=testver RUNCODE_CACHE="$T/cache" \
  RUNCODE_DOWNLOAD_BASE="file:///nonexistent-base" "$LAUNCHER" again 2>/dev/null)
if echo "$out" | grep -q 'FAKE_RUNCODE_RAN args=\[again\]'; then
  pass "cache reuse: second run execs cached binary without re-downloading"
else
  fail "cache reuse: expected cached run; got: $out"
fi

# ── Test 4: checksum mismatch → abort, non-zero, no exec, no poisoned cache ──
T2=$(mktemp -d)
make_release "$T2/rel" bad
out=$(RUNCODE_PIN_VERSION=testver RUNCODE_CACHE="$T2/cache" \
  RUNCODE_DOWNLOAD_BASE="file://$T2/rel" "$LAUNCHER" list 2>/dev/null)
rc=$?
if [ "$rc" -ne 0 ]; then pass "bad checksum: launcher exits non-zero"; else fail "bad checksum: launcher exited 0"; fi
if echo "$out" | grep -q FAKE_RUNCODE_RAN; then fail "bad checksum: payload was executed!"; else pass "bad checksum: payload NOT executed"; fi
if [ "$(cached_count "$T2/cache")" -eq 0 ]; then pass "bad checksum: no poisoned binary left in cache"; else fail "bad checksum: cache holds an unverified binary"; fi

# ── Test 5: RUNCODE_BIN override short-circuits download entirely ────────────
T3=$(mktemp -d)
cat >"$T3/mybin" <<'STUB'
#!/bin/sh
echo "OVERRIDE_RAN args=[$*]"
STUB
chmod +x "$T3/mybin"
out=$(RUNCODE_BIN="$T3/mybin" RUNCODE_CACHE="$T3/cache" \
  RUNCODE_DOWNLOAD_BASE="file:///nonexistent-base" "$LAUNCHER" whoami 2>/dev/null)
if echo "$out" | grep -q 'OVERRIDE_RAN args=\[whoami\]'; then
  pass "RUNCODE_BIN override: execs user binary, no download"
else
  fail "RUNCODE_BIN override: expected override to run; got: $out"
fi
if [ "$(cached_count "$T3/cache")" -eq 0 ]; then pass "RUNCODE_BIN override: nothing downloaded"; else fail "RUNCODE_BIN override: it downloaded anyway"; fi

rm -rf "$T" "$T2" "$T3"
echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "$fails FAILED"; exit 1; fi
