@echo off
REM 2) Installe l'application pour l'utilisateur courant (sans droits administrateur)
cd /d "%~dp0"
if not exist "dist\OrdonnanceMobile.exe" (
  echo dist\OrdonnanceMobile.exe est introuvable. Lancez d'abord build.bat
  pause
  exit /b 1
)
set "OM_DEST=%LOCALAPPDATA%\OrdonnanceMobile"
mkdir "%OM_DEST%" 2>nul
copy /y "dist\OrdonnanceMobile.exe" "%OM_DEST%\OrdonnanceMobile.exe" >nul
copy /y "icon.ico" "%OM_DEST%\icon.ico" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1"
> "%OM_DEST%\Desinstaller.bat" echo @echo off
>> "%OM_DEST%\Desinstaller.bat" echo del "%%USERPROFILE%%\Desktop\Ordonnance Mobile.lnk" "%%USERPROFILE%%\Desktop\Ordonnance Mobile (DEMO).lnk" 2^>nul
>> "%OM_DEST%\Desinstaller.bat" echo del "%%APPDATA%%\Microsoft\Windows\Start Menu\Programs\Ordonnance Mobile.lnk" "%%APPDATA%%\Microsoft\Windows\Start Menu\Programs\Ordonnance Mobile (DEMO).lnk" 2^>nul
>> "%OM_DEST%\Desinstaller.bat" echo del "%%LOCALAPPDATA%%\OrdonnanceMobile\OrdonnanceMobile.exe" "%%LOCALAPPDATA%%\OrdonnanceMobile\icon.ico"
>> "%OM_DEST%\Desinstaller.bat" echo echo Vos donnees restent dans %%USERPROFILE%%\.ordonnance_mobile
>> "%OM_DEST%\Desinstaller.bat" echo pause
echo.
echo Installe dans "%OM_DEST%"
echo Raccourcis crees sur le Bureau : "Ordonnance Mobile" et "Ordonnance Mobile (DEMO)".
echo Pour desinstaller : "%OM_DEST%\Desinstaller.bat"
pause
