# Create a shortcut for DB Browser (desktop by default) that opens our database file.
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI without a BOM,
# so non-ASCII text here would break the parser.
param([string]$Target = "Desktop")

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "tools\DBBrowser\DB Browser for SQLite.exe"
$db = Join-Path $root "data\market.db"

if (-not (Test-Path $exe)) {
    Write-Host "DB Browser not found: $exe"
    exit 1
}

if ($Target -eq "Desktop") {
    $folder = [Environment]::GetFolderPath("Desktop")
} else {
    $folder = $Target
}

$link = Join-Path $folder "DB Browser for SQLite.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $exe
$shortcut.Arguments = '"' + $db + '"'
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = "Browse the A-share alert system SQLite database"
try {
    $shortcut.Save()
} catch {
    Write-Host "FAILED to save shortcut: $($_.Exception.Message)"
    Write-Host "Run this from a normal (non-restricted) window, or create the shortcut manually."
    exit 1
}

Write-Host "Shortcut created: $link"
Write-Host "Target : $exe"
Write-Host "Opens  : $db"
