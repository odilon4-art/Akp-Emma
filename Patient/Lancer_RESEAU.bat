@echo off
cd /d "%~dp0"
if exist dist\OrdonnanceMobilePatient.exe (start "" dist\OrdonnanceMobilePatient.exe --reseau) else ("%LOCALAPPDATA%\om-build\patient\venv\Scripts\python.exe" main.py --reseau)
