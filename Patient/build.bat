@echo off
REM Construit dist\OrdonnanceMobilePatient.exe  (Windows, Python 3.10+)
REM L'environnement Python et les fichiers temporaires sont crees HORS de OneDrive
REM (dans %LOCALAPPDATA%\om-build) : OneDrive abime les .venv et bloque PyInstaller.
REM Le dossier du projet peut donc rester sur le Bureau.
cd /d "%~dp0"
set "P=%CD%"
set "B=%LOCALAPPDATA%\om-build\patient"
if not exist "%B%\venv\Scripts\python.exe" (
  python -m venv "%B%\venv" || goto :err
)
call "%B%\venv\Scripts\activate.bat"
python -m pip install -r "%P%\requirements.txt" || goto :err
python -m PyInstaller --noconfirm --clean --onefile --windowed --name OrdonnanceMobilePatient --icon "%P%\icon.ico" --add-data "%P%\app;app" --hidden-import backend --distpath "%B%\dist" --workpath "%B%\work" --specpath "%B%" "%P%\main.py" || goto :err
if not exist "%P%\dist" mkdir "%P%\dist"
copy /y "%B%\dist\OrdonnanceMobilePatient.exe" "%P%\dist\OrdonnanceMobilePatient.exe" >nul || goto :err
echo.
echo OK : dist\OrdonnanceMobilePatient.exe
echo Etape suivante : .\Installer.bat
goto :eof
:err
echo ECHEC de la construction.
exit /b 1
