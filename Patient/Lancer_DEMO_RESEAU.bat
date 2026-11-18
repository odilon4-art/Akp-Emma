@echo off
REM Test du telephone en DEMO : le logiciel Patient en mode reseau avec les donnees fictives (port 8770)
cd /d "%~dp0"
if exist dist\OrdonnanceMobilePatient.exe (start "" dist\OrdonnanceMobilePatient.exe --demo --reseau) else (python main.py --demo --reseau)
