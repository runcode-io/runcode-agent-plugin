@echo off
rem Windows entry point for runcode. The CLI is a static Go binary published at
rem github.com/runcode-io/runcode-cli; this shim hands off to runcode.ps1, which
rem fetches + sha256-verifies + caches + execs it (the PowerShell mirror of the
rem POSIX bin/runcode launcher). cmd.exe finds this .cmd via PATHEXT when
rem "runcode" is typed and bin/ is on PATH.
setlocal
set "RC_PS=powershell"
where pwsh >NUL 2>NUL && set "RC_PS=pwsh"
"%RC_PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0runcode.ps1" %*
exit /b %ERRORLEVEL%
