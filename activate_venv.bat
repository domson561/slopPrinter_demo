@echo off
rem Otwiera nowe okno konsoli w folderze projektu z aktywnym venv.
cd /d "%~dp0"
start "slop_printer" cmd /k .venv\Scripts\activate.bat
