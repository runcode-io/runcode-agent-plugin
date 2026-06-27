@echo off
rem Windows entry point for runcode. The engine is the extensionless,
rem shebang'd Python script next to this file; Windows can't run that directly,
rem so this shim launches it with the Python interpreter. cmd.exe finds this
rem .cmd via PATHEXT when "runcode" is typed and bin/ is on PATH.
setlocal
where py >NUL 2>NUL
if %ERRORLEVEL%==0 (
  py -3 "%~dp0runcode" %*
) else (
  python "%~dp0runcode" %*
)
exit /b %ERRORLEVEL%
