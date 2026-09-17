# Create a desktop shortcut that runs the daily job - the Windows twin of the
# macOS "每日任务.command" that the shortcut step writes to the Desktop.
#
# ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI,
# so Chinese text here would break the parser. The shortcut *name* is therefore
# built from code points at runtime, and it points at the ASCII-named dispatcher
# (windows\win-run.bat) instead of the Chinese-named launcher.
param([string]$Target = "Desktop")

if ($Target -eq "Desktop") {
    $folder = [Environment]::GetFolderPath("Desktop")
} else {
    $folder = $Target
}
if (-not (Test-Path $folder)) {
    Write-Host "Target folder not found: $folder"
    exit 1
}

$name = -join ([char]0x6BCF, [char]0x65E5, [char]0x4EFB, [char]0x52A1)   # 每日任务
$root = Split-Path -Parent $PSScriptRoot                     # project root = tools\..
# Point at the numbered launcher, not the dispatcher: that one pauses at the end,
# so double-clicking the shortcut shows the result instead of flashing a window.
$bat = Join-Path $root ("windows\3-" + $name + ".bat")
if (-not (Test-Path $bat)) {
    Write-Host "Launcher not found: $bat"
    exit 1
}

$link = Join-Path $folder ($name + ".lnk")
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $bat
$shortcut.Arguments = ""
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,137"
$shortcut.Description = "Run the daily job: fetch bars, compute state, emit alerts"
try {
    $shortcut.Save()
} catch {
    Write-Host "FAILED to save shortcut: $($_.Exception.Message)"
    exit 1
}

Write-Host "Shortcut created: $link"
Write-Host "Runs  : $bat"
