@echo off
rem Weebo 2.0 launcher: double-click to start Weebo and open it in your browser.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m weebo %*
) else (
    python -m weebo %*
)
if errorlevel 1 pause
