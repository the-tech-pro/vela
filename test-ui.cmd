@echo off
setlocal
cd /d "%~dp0antra-wails\frontend"

if not exist node_modules (
  echo Installing frontend dependencies...
  call npm.cmd ci
  if errorlevel 1 goto :error
)

echo Starting the Vela UI preview...
echo Close this window when you are finished testing.
call npm.cmd run demo
exit /b %errorlevel%

:error
echo.
echo The UI preview could not start.
pause
exit /b 1
