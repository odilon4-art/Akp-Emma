@echo off
cd /d "%~dp0"
if exist dist\OrdonnanceMobile.exe (start "" dist\OrdonnanceMobile.exe --demo) else ("%LOCALAPPDATA%\om-build\hopital\venv\Scripts\python.exe" main.py --demo)
