@echo off
rem MarkON Studio GUI - double-click to run.
rem PORT: change here if the board moves. BAUD must match the value
rem saved on the board flash (currently 921600).
set PORT=COM23
set BAUD=921600

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m host.gui.app --port %PORT% --baud %BAUD%
if errorlevel 1 pause
