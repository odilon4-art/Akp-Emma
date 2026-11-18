@echo off
cd /d "%~dp0"
if exist dist\OrdonnanceMobile.exe (start "" dist\OrdonnanceMobile.exe) else ("%LOCALAPPDATA%\om-build\hopital\venv\Scripts\python.exe" main.py)
