# Cree les raccourcis. Chemins lus dans OM_DEST (aucun souci d'apostrophe dans le nom d'utilisateur).
$dest = $env:OM_DEST
$exe  = Join-Path $dest 'OrdonnanceMobilePharmacie.exe'
$ico  = Join-Path $dest 'icon.ico'
$w = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $s = $w.CreateShortcut((Join-Path $d 'Ordonnance Mobile Pharmacie.lnk'))
    $s.TargetPath = $exe; $s.IconLocation = $ico; $s.WorkingDirectory = $dest; $s.Save()
    $s = $w.CreateShortcut((Join-Path $d 'Ordonnance Mobile Pharmacie (DEMO).lnk'))
    $s.TargetPath = $exe; $s.Arguments = '--demo'; $s.IconLocation = $ico; $s.WorkingDirectory = $dest; $s.Save()
}
