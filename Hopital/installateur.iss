; OPTIONNEL : vrai installateur Setup.exe avec Inno Setup (gratuit : jrsoftware.org/isinfo.php)
; Apres build.bat : ouvrez ce fichier dans Inno Setup puis Build > Compile -> Output\Setup_OrdonnanceMobile.exe
[Setup]
AppName=Ordonnance Mobile
AppVersion=11
DefaultDirName={autopf}\Ordonnance Mobile
DefaultGroupName=Ordonnance Mobile
PrivilegesRequired=lowest
OutputBaseFilename=Setup_OrdonnanceMobile
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\OrdonnanceMobile.exe
Compression=lzma2
[Files]
Source: "dist\OrdonnanceMobile.exe"; DestDir: "{app}"
Source: "icon.ico"; DestDir: "{app}"
[Icons]
Name: "{autodesktop}\Ordonnance Mobile"; Filename: "{app}\OrdonnanceMobile.exe"
Name: "{autodesktop}\Ordonnance Mobile (DEMO)"; Filename: "{app}\OrdonnanceMobile.exe"; Parameters: "--demo"
Name: "{group}\Ordonnance Mobile"; Filename: "{app}\OrdonnanceMobile.exe"
Name: "{group}\Ordonnance Mobile (DEMO)"; Filename: "{app}\OrdonnanceMobile.exe"; Parameters: "--demo"
[Run]
Filename: "{app}\OrdonnanceMobile.exe"; Description: "Lancer Ordonnance Mobile"; Flags: nowait postinstall skipifsilent
