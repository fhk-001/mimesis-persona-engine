# Add the installed Python to the *current user* PATH so that `python`, `py`
# and `pip` work in any new terminal window.
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes files without a BOM
# using the system ANSI code page, so non-ASCII text here would get mangled.
#
# Safe to run repeatedly (it will not duplicate entries).
# Undo: [Environment]::SetEnvironmentVariable("Path", "<old value>", "User")

param([switch]$DryRun)

$ErrorActionPreference = "Stop"

function Get-UserPath {
    $value = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $value) { return "" }
    return $value
}

$old = Get-UserPath
$pyRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"

Write-Host ""
Write-Host "Python install folder: $pyRoot"

if (-not (Test-Path $pyRoot)) {
    Write-Host "NOT FOUND. Please install Python 3.9+ first." -ForegroundColor Red
    exit 1
}

$dirs = @()
$versionDirs = @(
    Get-ChildItem -Path $pyRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "Python3*" } |
        Sort-Object Name -Descending
)
foreach ($versionDir in $versionDirs) {
    $dirs += ($versionDir.FullName.TrimEnd("\") + "\")
    $scripts = Join-Path $versionDir.FullName "Scripts"
    if (Test-Path $scripts) { $dirs += ($scripts.TrimEnd("\") + "\") }
}
$launcher = Join-Path $pyRoot "Launcher"
if (Test-Path $launcher) { $dirs += ($launcher.TrimEnd("\") + "\") }

if ($dirs.Count -eq 0) {
    Write-Host "No python.exe folder found under that path." -ForegroundColor Red
    exit 1
}

$existing = @()
if ($old.Trim() -ne "") {
    $existing = @($old -split ";" | Where-Object { $_.Trim() -ne "" })
}

$seen = @{}
foreach ($item in $existing) { $seen[$item.TrimEnd("\").ToLower()] = $true }

$final = @($existing)
$added = @()
foreach ($dir in $dirs) {
    $key = $dir.TrimEnd("\").ToLower()
    if (-not $seen.ContainsKey($key)) {
        $final += $dir
        $seen[$key] = $true
        $added += $dir
    }
}
$newPath = ($final -join ";")

Write-Host ""
Write-Host "--- before ---"
Write-Host $old
Write-Host ""
if ($added.Count -eq 0) {
    Write-Host "--- nothing to add, PATH already contains Python ---"
} else {
    Write-Host "--- adding ---"
    foreach ($dir in $added) { Write-Host "  $dir" }
}
Write-Host ""
Write-Host "--- after ---"
Write-Host $newPath
Write-Host ""

if ($DryRun) {
    Write-Host "(dry run: nothing was written)" -ForegroundColor Yellow
    exit 0
}

try {
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Saved to your user environment variables." -ForegroundColor Green
} catch {
    Write-Host "FAILED to save: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "If this keeps failing, re-run the Python installer and choose Modify,"
    Write-Host "then tick 'Add Python to environment variables'."
    exit 1
}

# Tell Windows (Explorer and anything opened from it) that the environment changed.
try {
    $signature = @"
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
"@
    Add-Type -MemberDefinition $signature -Name "EnvBroadcast" -Namespace "PersonaBot" | Out-Null
    $result = [UIntPtr]::Zero
    [void][PersonaBot.EnvBroadcast]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result)
    Write-Host "Broadcast sent: newly opened windows will pick it up." -ForegroundColor Green
} catch {
    Write-Host "(broadcast skipped, no problem)"
}

Write-Host ""
Write-Host "DONE. Close this window, open a NEW terminal, then run:" -ForegroundColor Green
Write-Host "    python --version"
Write-Host "    cd `"$PSScriptRoot\..`""
Write-Host "    python run.py selftest"
Write-Host ""
