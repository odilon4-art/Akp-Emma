@echo off
cd /d "%~dp0"
if exist dist\OrdonnanceMobilePharmacie.exe (start "" dist\OrdonnanceMobilePharmacie.exe) else ("%LOCALAPPDATA%\om-build\pharmacie\venv\Scripts\python.exe" main.py)
