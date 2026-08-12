@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PATH=%PATH%;%ProgramFiles%\nodejs;%ProgramFiles%\Go\bin;%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%AppData%\npm;%UserProfile%\go\bin"

python --version >nul 2>nul
if errorlevel 1 (
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "VELA_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
  ) else (
    echo [ERROR] Python is missing. Run install-build-dependencies.cmd first.
    pause
    exit /b 1
  )
) else (
  set "VELA_PYTHON=python"
)

where wails >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Wails is missing. Run install-build-dependencies.cmd first.
  pause
  exit /b 1
)

echo Building Vela...
"%VELA_PYTHON%" build_desktop.py
if errorlevel 1 (
  echo.
  echo [ERROR] Vela could not be built.
  pause
  exit /b 1
)

echo.
echo Vela.exe is ready in antra-wails\build\bin\
pause
