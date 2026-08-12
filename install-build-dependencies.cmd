@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   Vela Windows build dependency setup
echo ============================================================
echo.

where winget >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows Package Manager ^(winget^) is required.
  echo Install "App Installer" from the Microsoft Store, then run this file again.
  pause
  exit /b 1
)

call :ensure python "Python.Python.3.12" "Python 3.12"
if errorlevel 1 goto :failed
call :ensure node "OpenJS.NodeJS.LTS" "Node.js LTS"
if errorlevel 1 goto :failed
call :ensure go "GoLang.Go" "Go"
if errorlevel 1 goto :failed

set "PATH=%PATH%;%ProgramFiles%\nodejs;%ProgramFiles%\Go\bin;%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%AppData%\npm;%UserProfile%\go\bin"

python --version >nul 2>nul
if errorlevel 1 (
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "VELA_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
  ) else (
    echo [ERROR] Python was installed but is not available yet.
    echo Close this window and run this file once more.
    goto :failed
  )
) else (
  set "VELA_PYTHON=python"
)

go version >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles%\Go\bin\go.exe" (
    set "VELA_GO=%ProgramFiles%\Go\bin\go.exe"
  ) else (
    echo [ERROR] Go was installed but is not available yet.
    echo Close this window and run this file once more.
    goto :failed
  )
) else (
  set "VELA_GO=go"
)

echo.
echo [1/4] Installing Python build and runtime packages...
"%VELA_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%VELA_PYTHON%" -m pip install -r requirements-runtime.txt -r requirements-desktop.txt
if errorlevel 1 goto :failed

echo.
echo [2/4] Installing Wails v2.12.0...
"%VELA_GO%" install github.com/wailsapp/wails/v2/cmd/wails@v2.12.0
if errorlevel 1 goto :failed

echo.
echo [3/4] Downloading Go modules...
pushd antra-wails
"%VELA_GO%" mod download
if errorlevel 1 (popd & goto :failed)
popd

echo.
echo [4/4] Installing frontend packages...
pushd antra-wails\frontend
call npm.cmd ci
if errorlevel 1 (popd & goto :failed)
popd

echo.
echo ============================================================
echo   Setup complete
echo ============================================================
echo Build Vela by running: build-app.cmd
pause
exit /b 0

:ensure
if /i "%~1"=="go" (
  go version >nul 2>nul
) else (
  %~1 --version >nul 2>nul
)
if not errorlevel 1 (
  echo [OK] %~3 is already installed.
  exit /b 0
)
echo Installing %~3...
winget install --id %~2 --exact --accept-package-agreements --accept-source-agreements
exit /b %errorlevel%

:failed
echo.
echo [ERROR] Dependency setup did not complete.
echo Review the message above, then run this file again.
pause
exit /b 1
