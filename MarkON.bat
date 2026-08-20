@echo off
rem MarkON Studio GUI - double-click to run.
rem Starts disconnected; pick a COM port in the app and press Connect.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m host.gui.app
if errorlevel 1 pause
