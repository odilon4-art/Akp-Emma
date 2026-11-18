# Cree les raccourcis. Les chemins viennent de la variable d'environnement OM_DEST (aucun souci d'apostrophe).
$dest = $env:OM_DEST
$w = New-Object -ComObject WScript.Shell
$dossiers = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($d in $dossiers) {
    $s = $w.CreateShortcut((Join-Path $d 'Ordonnance Mobile.lnk'))
    $s.TargetPath = (Join-Path $dest 'OrdonnanceMobile.exe')
    $s.IconLocation = (Join-Path $dest 'icon.ico')
    $s.WorkingDirectory = $dest
    $s.Save()
    $s = $w.CreateShortcut((Join-Path $d 'Ordonnance Mobile (DEMO).lnk'))
    $s.TargetPath = (Join-Path $dest 'OrdonnanceMobile.exe')
    $s.Arguments = '--demo'
    $s.IconLocation = (Join-Path $dest 'icon.ico')
    $s.WorkingDirectory = $dest
    $s.Save()
}
