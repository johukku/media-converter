@echo off
chcp 65001 >nul
title Media Converter - Uninstall

REM ------------------------------------------------------------------
REM  The real work is done by uninstall.ps1 next to this file.
REM
REM  1. Leave this folder first. Windows cannot delete a folder that is
REM     the current directory of a running program (this cmd window).
REM
REM  2. Everything after powershell stays on the same line, and ends
REM     with "exit" rather than "exit /b". This .bat file and its folder
REM     are deleted while PowerShell runs, so reading one more line fails
REM     with "The batch file cannot be found", and "exit /b" fails with
REM     "The system cannot find the path specified". Plain "exit" just
REM     ends cmd. (Started from an open command prompt, that window
REM     closes as well.)
REM
REM  3. "if errorlevel" is evaluated when the line runs, so the exit code
REM     of PowerShell is passed on without delayed expansion.
REM ------------------------------------------------------------------

set "TOOL_DIR=%~dp0"
if not exist "%TOOL_DIR%uninstall.ps1" goto :missing
cd /d "%TEMP%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%TOOL_DIR%uninstall.ps1" -ToolDir "%TOOL_DIR%." & if errorlevel 2 (exit 2) else if errorlevel 1 (exit 1) else exit 0


:missing
echo.
echo ==================================================
echo    uninstall.ps1 was not found.
echo ==================================================
echo.
echo   It has to be in the same folder as this file.
echo   You can also delete this folder by hand.
echo.
pause
exit /b 1
