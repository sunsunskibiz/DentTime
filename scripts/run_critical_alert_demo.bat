@echo off
setlocal

REM Run this file from the DentTime project root.
REM Example:
REM   D:
REM   cd "D:\University Work CU\Year 1, Term 2\SE for ML Systems\Final term project-B-api-monitoring\DentTime"
REM   scripts\run_critical_alert_demo.bat

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_critical_alert_demo.ps1" -F1CriticalTotal 130 -UnderEstCriticalTotal 40

endlocal
