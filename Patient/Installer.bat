@echo off
REM 2) Installe l'application pour l'utilisateur courant (sans droits administrateur)
cd /d "%~dp0"
if not exist "dist\OrdonnanceMobilePatient.exe" (
  echo dist\OrdonnanceMobilePatient.exe est introuvable. Lancez d'abord build.bat
  pause
  exit /b 1
)
set "OM_DEST=%LOCALAPPDATA%\OrdonnanceMobilePatient"
mkdir "%OM_DEST%" 2>nul
copy /y "dist\OrdonnanceMobilePatient.exe" "%OM_DEST%\OrdonnanceMobilePatient.exe" >nul
copy /y "icon.ico" "%OM_DEST%\icon.ico" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1"
> "%OM_DEST%\Desinstaller.bat" echo @echo off
>> "%OM_DEST%\Desinstaller.bat" echo del "%%USERPROFILE%%\Desktop\Ordonnance Mobile Patient.lnk" "%%USERPROFILE%%\Desktop\Ordonnance Mobile Patient (DEMO).lnk" 2^>nul
>> "%OM_DEST%\Desinstaller.bat" echo del "%%APPDATA%%\Microsoft\Windows\Start Menu\Programs\Ordonnance Mobile Patient.lnk" "%%APPDATA%%\Microsoft\Windows\Start Menu\Programs\Ordonnance Mobile Patient (DEMO).lnk" 2^>nul
>> "%OM_DEST%\Desinstaller.bat" echo del "%%LOCALAPPDATA%%\OrdonnanceMobilePatient\OrdonnanceMobilePatient.exe" "%%LOCALAPPDATA%%\OrdonnanceMobilePatient\icon.ico"
>> "%OM_DEST%\Desinstaller.bat" echo echo Vos donnees restent dans %%USERPROFILE%%\.ordonnance_mobile
>> "%OM_DEST%\Desinstaller.bat" echo pause
echo.
echo Installe dans "%OM_DEST%"
echo Raccourcis crees sur le Bureau : "Ordonnance Mobile Patient" et "Ordonnance Mobile Patient (DEMO)".
echo Pour desinstaller : "%OM_DEST%\Desinstaller.bat"
pause
