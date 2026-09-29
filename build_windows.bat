@echo off
setlocal
cd /d "%~dp0"

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo ERROR: Inno Setup 6 was not found.
  exit /b 1
)
if not exist "runtime\pythonw.exe" (
  echo ERROR: runtime\pythonw.exe is missing.
  echo Put the official CPython Windows embeddable package contents into runtime\
  echo before building the installer.
  exit /b 2
)
if not exist "runtime\python.exe" (
  echo ERROR: runtime\python.exe is missing.
  exit /b 2
)

if exist installer_output rmdir /s /q installer_output
"%ISCC%" installer.iss
if errorlevel 1 exit /b %errorlevel%

echo.
echo SUCCESS: installer_output\PartsManager-Setup-*.exe
dir /b installer_output\PartsManager-Setup-*.exe
