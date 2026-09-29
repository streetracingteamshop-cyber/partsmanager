@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo Embedded Python Runtime is missing.
  echo Please reinstall Parts Manager.
  pause
  exit /b 2
)
"runtime\python.exe" "%~dp0parts_manager.py"
