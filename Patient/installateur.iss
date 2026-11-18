; OPTIONNEL : vrai installateur Setup.exe avec Inno Setup (gratuit : jrsoftware.org/isinfo.php)
; Apres build.bat : ouvrez ce fichier dans Inno Setup puis Build > Compile -> Output\Setup_OrdonnanceMobilePatient.exe
[Setup]
AppName=Ordonnance Mobile Patient
AppVersion=11
DefaultDirName={autopf}\Ordonnance Mobile Patient
DefaultGroupName=Ordonnance Mobile Patient
PrivilegesRequired=lowest
OutputBaseFilename=Setup_OrdonnanceMobilePatient
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\OrdonnanceMobilePatient.exe
Compression=lzma2
[Files]
Source: "dist\OrdonnanceMobilePatient.exe"; DestDir: "{app}"
Source: "icon.ico"; DestDir: "{app}"
[Icons]
Name: "{autodesktop}\Ordonnance Mobile Patient"; Filename: "{app}\OrdonnanceMobilePatient.exe"
Name: "{autodesktop}\Ordonnance Mobile Patient (DEMO)"; Filename: "{app}\OrdonnanceMobilePatient.exe"; Parameters: "--demo"
Name: "{group}\Ordonnance Mobile Patient"; Filename: "{app}\OrdonnanceMobilePatient.exe"
Name: "{group}\Ordonnance Mobile Patient (DEMO)"; Filename: "{app}\OrdonnanceMobilePatient.exe"; Parameters: "--demo"
[Run]
Filename: "{app}\OrdonnanceMobilePatient.exe"; Description: "Lancer Ordonnance Mobile Patient"; Flags: nowait postinstall skipifsilent
