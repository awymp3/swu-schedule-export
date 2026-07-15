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

REM China-friendly sources: TUNA first, then Aliyun, then official PyPI.
set "PIP_TUNA=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_ALIYUN=https://mirrors.aliyun.com/pypi/simple/"
set "PYTHON_VERSION=3.12.10"
set "PYTHON_MIRROR=https://mirrors.aliyun.com/python-release/windows"
set "PYTHON_OFFICIAL=https://www.python.org/ftp/python/%PYTHON_VERSION%"

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
  echo Installing required packages from the Tsinghua mirror:%NEED%
  %PY% -m pip install %PIP_SCOPE% --disable-pip-version-check --prefer-binary --timeout 30 --retries 3 --index-url "%PIP_TUNA%" %NEED%
  if errorlevel 1 (
    echo Tsinghua mirror failed. Retrying with the Aliyun mirror...
    %PY% -m pip install %PIP_SCOPE% --disable-pip-version-check --prefer-binary --timeout 30 --retries 3 --index-url "%PIP_ALIYUN%" %NEED%
  )
  if errorlevel 1 (
    echo China mirrors failed. Retrying with the official PyPI source...
    %PY% -m pip install %PIP_SCOPE% --disable-pip-version-check --prefer-binary --timeout 30 --retries 3 %NEED%
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
set "RUNTIME_DIR=%ROOT%.runtime"
set "PYTHON_DIR=%RUNTIME_DIR%\python"
set "LOCAL_PY=%PYTHON_DIR%\python.exe"
if exist "%LOCAL_PY%" (
  "%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
  if not errorlevel 1 exit /b 0
)
set "LOCAL_PY="

mkdir "%RUNTIME_DIR%" >nul 2>&1
echo Python 3 was not found. Downloading a local runtime from the Aliyun mirror...

set "PYTHON_ARCH=amd64"
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PYTHON_ARCH=arm64"
if /I "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "PYTHON_ARCH=arm64"
if /I "%PROCESSOR_ARCHITECTURE%"=="x86" set "PYTHON_ARCH=x86"
set "PYTHON_INSTALLER_NAME=python-%PYTHON_VERSION%-%PYTHON_ARCH%.exe"
if /I "%PYTHON_ARCH%"=="x86" set "PYTHON_INSTALLER_NAME=python-%PYTHON_VERSION%.exe"
set "PYTHON_INSTALLER=%RUNTIME_DIR%\%PYTHON_INSTALLER_NAME%"

call :download_file "%PYTHON_MIRROR%/%PYTHON_INSTALLER_NAME%" "%PYTHON_INSTALLER%"
if errorlevel 1 (
  echo Aliyun mirror failed. Retrying with the official Python source...
  call :download_file "%PYTHON_OFFICIAL%/%PYTHON_INSTALLER_NAME%" "%PYTHON_INSTALLER%"
)
if errorlevel 1 exit /b 1

echo Installing the project-local Python runtime...
REM The Python bootstrapper can spawn a child installer. start /wait prevents
REM the next line from searching for python.exe before installation is done.
start "" /wait "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%PYTHON_DIR%" PrependPath=0 Include_pip=1 Include_test=0 Include_doc=0 Include_tcltk=0 Include_launcher=0 Shortcuts=0
set "INSTALL_EXIT=%ERRORLEVEL%"
if "%INSTALL_EXIT%"=="0" goto :python_installed
if "%INSTALL_EXIT%"=="3010" goto :python_installed
echo [ERROR] Python installer exited with code %INSTALL_EXIT%.
echo Installer kept for diagnosis: %PYTHON_INSTALLER%
exit /b 1

:python_installed

if not exist "%LOCAL_PY%" (
  for /f "delims=" %%F in ('dir /b /s "%PYTHON_DIR%\python.exe" 2^>nul') do if not defined LOCAL_PY set "LOCAL_PY=%%F"
)
if not defined LOCAL_PY (
  echo [ERROR] Python installer completed, but python.exe was not found under:
  echo         %PYTHON_DIR%
  echo Installer kept for diagnosis: %PYTHON_INSTALLER%
  exit /b 1
)
"%LOCAL_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Installed python.exe could not start: %LOCAL_PY%
  exit /b 1
)
del /q "%PYTHON_INSTALLER%" >nul 2>&1
exit /b 0

:download_file
set "DOWNLOAD_URL=%~1"
set "DOWNLOAD_FILE=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri $env:DOWNLOAD_URL -OutFile $env:DOWNLOAD_FILE -ErrorAction Stop; exit 0 } catch { exit 1 }"
exit /b %ERRORLEVEL%
