@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM This launcher is ASCII-only so it can run under every cmd.exe code page.
chcp 65001 >nul 2>&1
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ============================================
echo    Schedule Helper - Automatic Capture
echo ============================================
echo    Build: 2026.07.15-browser-12
echo    Copyright (c) 2026 Jiapeng Lee
echo    GitHub: https://github.com/awymp3/swu-schedule-export
echo    Email: wadrqhh@gmail.com
echo.

REM China-friendly sources: TUNA first, then Aliyun, then official PyPI.
set "PIP_TUNA=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_ALIYUN=https://mirrors.aliyun.com/pypi/simple/"
set "PYTHON_VERSION=3.12.10"
set "PYTHON_MIRROR=https://mirrors.aliyun.com/python-release/windows"
set "PYTHON_OFFICIAL=https://www.python.org/ftp/python/%PYTHON_VERSION%"
set "PIP_WHEEL_NAME=pip-25.3-py3-none-any.whl"
set "PIP_WHEEL_TUNA=https://pypi.tuna.tsinghua.edu.cn/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/%PIP_WHEEL_NAME%"
set "PIP_WHEEL_ALIYUN=https://mirrors.aliyun.com/pypi/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/%PIP_WHEEL_NAME%"
set "PY="
set "PIP_SCOPE=--user"
set "NEED_X64=0"
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "NEED_X64=1"
if /I "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "NEED_X64=1"

REM OpenCV has no reliable Windows ARM64 wheel. Windows on ARM can run x64
REM applications, so use an x64 project runtime and avoid source builds.
if "%NEED_X64%"=="1" goto :install_local_runtime

REM Prefer a usable Python 3 over the Microsoft Store python.exe alias.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_ready

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :python_ready

REM No system Python: reuse or download a project-local runtime in .runtime.
:install_local_runtime
if "%NEED_X64%"=="1" echo Windows on ARM detected. Using an x64 local Python runtime for OpenCV compatibility...
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
%PY% -c "import ddddocr" >nul 2>&1 || set "NEED=%NEED% ddddocr"
%PY% -c "import selenium" >nul 2>&1 || set "NEED=%NEED% selenium"
%PY% -c "import websocket" >nul 2>&1 || set "NEED=%NEED% websocket-client"
%PY% -c "import PIL" >nul 2>&1 || set "NEED=%NEED% pillow"

if not "%NEED%"=="" (
  echo Installing required packages from the Tsinghua mirror:%NEED%
  %PY% -m pip install %PIP_SCOPE% --upgrade --disable-pip-version-check --prefer-binary --progress-bar on --timeout 30 --retries 3 --index-url "%PIP_TUNA%" %NEED%
  if errorlevel 1 (
    echo Tsinghua mirror failed. Retrying with the Aliyun mirror...
    %PY% -m pip install %PIP_SCOPE% --upgrade --disable-pip-version-check --prefer-binary --progress-bar on --timeout 30 --retries 3 --index-url "%PIP_ALIYUN%" %NEED%
  )
  if errorlevel 1 (
    echo China mirrors failed. Retrying with the official PyPI source...
    %PY% -m pip install %PIP_SCOPE% --upgrade --disable-pip-version-check --prefer-binary --progress-bar on --timeout 30 --retries 3 %NEED%
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
echo [ERROR] A local Python runtime could not be installed.
echo Check your network, then run this file again.
echo.
pause
exit /b 1

:install_local_python
REM Keep Python packages under a short per-user path. ddddocr installs onnxruntime,
REM whose generated module paths exceed the legacy 260-character limit in deep ZIP folders.
set "RUNTIME_BASE=%LOCALAPPDATA%\SWUScheduleExport"
if "%LOCALAPPDATA%"=="" set "RUNTIME_BASE=%TEMP%\SWUScheduleExport"
set "RUNTIME_DIR=%RUNTIME_BASE%\runtime"
set "PYTHON_DIR=%RUNTIME_DIR%\python"
set "LOCAL_PY=%PYTHON_DIR%\python.exe"
call :local_runtime_is_usable
if not errorlevel 1 exit /b 0
set "LOCAL_PY="

mkdir "%RUNTIME_DIR%" >nul 2>&1
echo Local runtime path: %RUNTIME_DIR%
echo Python 3 was not found. Downloading a local runtime from the Aliyun mirror...

set "PYTHON_ARCH=amd64"
if "%NEED_X64%"=="0" if /I "%PROCESSOR_ARCHITECTURE%"=="x86" set "PYTHON_ARCH=win32"
set "PYTHON_ARCHIVE_NAME=python-%PYTHON_VERSION%-embed-%PYTHON_ARCH%.zip"
set "PYTHON_ARCHIVE=%RUNTIME_DIR%\%PYTHON_ARCHIVE_NAME%"

call :download_file "%PYTHON_MIRROR%/%PYTHON_ARCHIVE_NAME%" "%PYTHON_ARCHIVE%"
if errorlevel 1 (
  echo Aliyun mirror failed. Retrying with the official Python source...
  call :download_file "%PYTHON_OFFICIAL%/%PYTHON_ARCHIVE_NAME%" "%PYTHON_ARCHIVE%"
)
if errorlevel 1 exit /b 1

echo Extracting the project-local Python runtime...
rmdir /s /q "%PYTHON_DIR%" >nul 2>&1
mkdir "%PYTHON_DIR%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Expand-Archive -LiteralPath $env:PYTHON_ARCHIVE -DestinationPath $env:PYTHON_DIR -Force; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [ERROR] The Python archive could not be extracted.
  echo Archive kept for diagnosis: %PYTHON_ARCHIVE%
  exit /b 1
)
del /q "%PYTHON_ARCHIVE%" >nul 2>&1
set "LOCAL_PY=%PYTHON_DIR%\python.exe"
if not exist "%LOCAL_PY%" (
  echo [ERROR] Python archive extracted, but python.exe was not found under:
  echo         %PYTHON_DIR%
  exit /b 1
)
"%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Extracted python.exe could not start: %LOCAL_PY%
  exit /b 1
)
call :bootstrap_local_pip
if errorlevel 1 exit /b 1
exit /b 0

:local_runtime_is_usable
if not exist "%LOCAL_PY%" exit /b 1
"%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
if "%NEED_X64%"=="1" (
  "%LOCAL_PY%" -c "import platform; raise SystemExit(0 if platform.machine().lower() in ('amd64', 'x86_64') else 1)" >nul 2>&1
  if errorlevel 1 exit /b 1
)
"%LOCAL_PY%" -m pip --version >nul 2>&1
if not errorlevel 1 exit /b 0
call :bootstrap_local_pip
exit /b %ERRORLEVEL%

:bootstrap_local_pip
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $pth = Get-ChildItem -LiteralPath $env:PYTHON_DIR -Filter 'python*._pth' | Select-Object -First 1; if (-not $pth) { exit 1 }; $lines = Get-Content -LiteralPath $pth.FullName | ForEach-Object { if ($_ -match '^\s*#\s*import site\s*$') { 'import site' } else { $_ } }; Set-Content -LiteralPath $pth.FullName -Value $lines -Encoding ascii; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [ERROR] Could not enable site-packages for the local Python runtime.
  exit /b 1
)
set "PIP_WHEEL=%RUNTIME_DIR%\%PIP_WHEEL_NAME%"
echo Installing pip for the project-local Python runtime...
echo Fetching the pip package directly from the Tsinghua mirror...
call :download_file "%PIP_WHEEL_TUNA%" "%PIP_WHEEL%"
if errorlevel 1 (
  echo Tsinghua mirror failed. Retrying with the Aliyun mirror...
  call :download_file "%PIP_WHEEL_ALIYUN%" "%PIP_WHEEL%"
)
if errorlevel 1 (
  echo [ERROR] Could not download the pip package from a China mirror.
  exit /b 1
)

echo Installing pip from the downloaded package...
mkdir "%PYTHON_DIR%\Lib\site-packages" >nul 2>&1
"%LOCAL_PY%" -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "%PIP_WHEEL%" "%PYTHON_DIR%\Lib\site-packages"
if errorlevel 1 (
  echo [ERROR] The downloaded pip package could not be installed.
  exit /b 1
)
del /q "%PIP_WHEEL%" >nul 2>&1
"%LOCAL_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] pip was installed but cannot be started.
  exit /b 1
)
exit /b 0

:download_file
set "DOWNLOAD_URL=%~1"
set "DOWNLOAD_FILE=%~2"
echo Downloading: %DOWNLOAD_URL%
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='Continue'; try { Invoke-WebRequest -UseBasicParsing -Uri $env:DOWNLOAD_URL -OutFile $env:DOWNLOAD_FILE -TimeoutSec 60 -ErrorAction Stop; $size = (Get-Item -LiteralPath $env:DOWNLOAD_FILE -ErrorAction Stop).Length; if ($size -lt 1) { throw 'The downloaded file is empty.' }; Write-Host ('[OK] Downloaded ' + $size + ' bytes.'); exit 0 } catch { Write-Host ('[DETAIL] Request failed: ' + $env:DOWNLOAD_URL); Write-Host ('[DETAIL] ' + $_.Exception.Message); exit 1 }"
exit /b %ERRORLEVEL%
