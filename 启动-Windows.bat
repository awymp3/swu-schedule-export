@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM This launcher is ASCII-only so it can run under every cmd.exe code page.
chcp 65001 >nul 2>&1
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ============================================
echo    Schedule Helper - Automatic Capture
echo ============================================
echo.

set "PY="
set "PIP_SCOPE=--user"

REM Prefer a usable Python 3 over the Microsoft Store python.exe alias.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_ready

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :python_ready

REM No system Python: reuse or download a project-local runtime in .runtime.
call :install_local_python
if errorlevel 1 goto :python_download_failed
set "PY="%LOCAL_PY%""
set "PIP_SCOPE="

:python_ready
echo Using Python: %PY%
%PY% -m ensurepip --upgrade >nul 2>&1
%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] pip is unavailable for the selected Python installation.
  echo.
  pause
  exit /b 1
)

echo Checking dependencies...
set "NEED="
%PY% -c "import undetected_chromedriver" >nul 2>&1 || set "NEED=%NEED% undetected-chromedriver"
%PY% -c "import ddddocr" >nul 2>&1 || set "NEED=%NEED% ddddocr"
%PY% -c "import selenium" >nul 2>&1 || set "NEED=%NEED% selenium"
%PY% -c "import PIL" >nul 2>&1 || set "NEED=%NEED% pillow"

if not "%NEED%"=="" (
  echo Installing required packages:%NEED%
  %PY% -m pip install %PIP_SCOPE% --disable-pip-version-check %NEED% -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo Mirror install failed. Retrying with the official PyPI source...
    %PY% -m pip install %PIP_SCOPE% --disable-pip-version-check %NEED%
  )
  if errorlevel 1 (
    echo [ERROR] Dependencies could not be installed. Check your network, then retry.
    echo.
    pause
    exit /b 1
  )
)

echo.
%PY% "%ROOT%capture_auto.py"
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" echo [ERROR] The program stopped with exit code %EXITCODE%.
pause
exit /b %EXITCODE%

:python_download_failed
echo [ERROR] A local Python runtime could not be downloaded.
echo Check your network, then run this file again.
echo.
pause
exit /b 1

:install_local_python
set "RUNTIME_DIR=%ROOT%.runtime"
set "PYTHON_DIR=%RUNTIME_DIR%\python"
set "LOCAL_PY="
for /f "delims=" %%F in ('dir /b /s "%PYTHON_DIR%\python.exe" 2^>nul') do if not defined LOCAL_PY set "LOCAL_PY=%%F"
if defined LOCAL_PY (
  "%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
  if not errorlevel 1 exit /b 0
  set "LOCAL_PY="
)

mkdir "%RUNTIME_DIR%" >nul 2>&1
set "UV_DIR=%RUNTIME_DIR%\uv"
set "UV_EXE=%UV_DIR%\uv.exe"
if not exist "%UV_EXE%" (
  echo Python 3 was not found. Downloading a local runtime...
  set "UV_INSTALLER=%RUNTIME_DIR%\install-uv.ps1"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile '%UV_INSTALLER%'"
  if errorlevel 1 exit /b 1
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_INSTALL_DIR='%UV_DIR%'; $env:UV_NO_MODIFY_PATH='1'; & '%UV_INSTALLER%'"
  if errorlevel 1 exit /b 1
)
if not exist "%UV_EXE%" (
  set "UV_EXE_FOUND="
  for /f "delims=" %%F in ('dir /b /s "%UV_DIR%\uv.exe" 2^>nul') do if not defined UV_EXE_FOUND set "UV_EXE_FOUND=%%F"
  if defined UV_EXE_FOUND set "UV_EXE=%UV_EXE_FOUND%"
)
if not exist "%UV_EXE%" exit /b 1

"%UV_EXE%" python install --install-dir "%PYTHON_DIR%" --no-bin 3.12
if errorlevel 1 exit /b 1
for /f "delims=" %%F in ('dir /b /s "%PYTHON_DIR%\python.exe" 2^>nul') do if not defined LOCAL_PY set "LOCAL_PY=%%F"
if not defined LOCAL_PY exit /b 1
"%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
