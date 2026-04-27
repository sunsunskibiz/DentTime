@echo off
setlocal

REM Run this file from the DentTime project root.
REM Example:
REM   D:
REM   cd "D:\University Work CU\Year 1, Term 2\SE for ML Systems\Final term project-B-api-monitoring\DentTime"
REM   scripts\run_data_diff_demo.bat

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_data_diff_demo.ps1" -Total 80

endlocal
