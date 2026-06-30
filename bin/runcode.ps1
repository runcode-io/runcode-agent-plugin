# runcode.ps1 — plugin launcher for the RunCode CLI (Windows).
#
# PowerShell mirror of the POSIX `bin/runcode` launcher. The CLI is a static Go
# binary published at github.com/runcode-io/runcode-cli; the plugin does NOT
# vendor it. On first use this fetches the matching binary, verifies its sha256
# against the release checksums.txt, caches it, and execs it. Later calls run
# straight from cache.
#
# Resolution order:
#   1. $env:RUNCODE_BIN  — explicit path to a runcode.exe; execed as-is.
#   2. cached binary     — %LOCALAPPDATA%\runcode\bin\runcode-<ver>-windows-<arch>.exe
#   3. verified download — from the pinned release, then cached.
#
# Override knobs (parity with the POSIX launcher):
#   RUNCODE_BIN, RUNCODE_CACHE, RUNCODE_PIN_VERSION, RUNCODE_DOWNLOAD_BASE
#
# Never evaluates downloaded bytes; only ever runs a binary whose sha256 matched
# the release manifest.
$ErrorActionPreference = 'Stop'
$repo = 'runcode-io/runcode-cli'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Die($msg) { [Console]::Error.WriteLine("runcode: $msg"); exit 1 }

# ── 1. explicit binary override ──────────────────────────────────────────────
if ($env:RUNCODE_BIN) {
  if (-not (Test-Path -LiteralPath $env:RUNCODE_BIN -PathType Leaf)) {
    Die "RUNCODE_BIN=$($env:RUNCODE_BIN) is not a file"
  }
  & $env:RUNCODE_BIN @args
  exit $LASTEXITCODE
}

# ── pinned version ───────────────────────────────────────────────────────────
if ($env:RUNCODE_PIN_VERSION) {
  $version = $env:RUNCODE_PIN_VERSION
} elseif (Test-Path -LiteralPath (Join-Path $here 'RUNCODE_VERSION')) {
  $version = (Get-Content -Raw (Join-Path $here 'RUNCODE_VERSION')).Trim()
} else {
  Die "no pinned version (missing $here\RUNCODE_VERSION and RUNCODE_PIN_VERSION)"
}
if (-not $version) { Die 'pinned version is empty' }

# ── arch (must match the release asset names) ────────────────────────────────
switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { $arch = 'amd64' }
  'ARM64' { $arch = 'arm64' }
  default { Die "unsupported architecture: $($env:PROCESSOR_ARCHITECTURE) (only amd64 and arm64 are released)" }
}
$os = 'windows'

# ── 2. cached binary ─────────────────────────────────────────────────────────
$cacheRoot = if ($env:RUNCODE_CACHE) { $env:RUNCODE_CACHE } else { Join-Path $env:LOCALAPPDATA 'runcode' }
$binDir = Join-Path $cacheRoot 'bin'
$bin = Join-Path $binDir "runcode-$version-$os-$arch.exe"
if (Test-Path -LiteralPath $bin -PathType Leaf) {
  & $bin @args
  exit $LASTEXITCODE
}

# ── 3. verified download ─────────────────────────────────────────────────────
$asset = "runcode_${os}_${arch}.zip"
$base = if ($env:RUNCODE_DOWNLOAD_BASE) { $env:RUNCODE_DOWNLOAD_BASE } else { "https://github.com/$repo/releases/download/$version" }

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$stage = Join-Path $binDir (".dl." + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  [Console]::Error.WriteLine("runcode: fetching $version ($os/$arch) ...")
  $zip = Join-Path $stage $asset
  $sums = Join-Path $stage 'checksums.txt'
  Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $zip
  Invoke-WebRequest -UseBasicParsing -Uri "$base/checksums.txt" -OutFile $sums

  $expected = $null
  foreach ($line in Get-Content -LiteralPath $sums) {
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2 -and $parts[1].Trim() -eq $asset) { $expected = $parts[0].Trim(); break }
  }
  if (-not $expected) { Die "no checksum for $asset in checksums.txt" }

  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
  if ($actual -ine $expected) {
    Die "checksum mismatch for $asset (expected $expected, got $actual); refusing to run"
  }

  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
  $extracted = Join-Path $stage 'runcode.exe'
  if (-not (Test-Path -LiteralPath $extracted -PathType Leaf)) { Die "runcode.exe not found in $asset" }
  # Atomic publish into the cache (same volume → Move is atomic). Only a
  # checksum-verified binary ever reaches $bin.
  Move-Item -LiteralPath $extracted -Destination $bin -Force
} finally {
  Remove-Item -Recurse -Force -LiteralPath $stage -ErrorAction SilentlyContinue
}

& $bin @args
exit $LASTEXITCODE
