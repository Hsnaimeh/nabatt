<#
  Removes Nabatt: stops it, unregisters it from Windows, deletes the shortcuts
  and the program folder.

  Your data is kept. The logs and settings in %LOCALAPPDATA%\Nabatt stay put,
  so reinstalling later picks the history straight back up. Pass -RemoveData to
  delete those too.
#>
param([switch]$Quiet, [switch]$RemoveData)

$App     = 'Nabatt'
$Root    = Split-Path -Parent $PSCommandPath
$DataDir = Join-Path $env:LOCALAPPDATA $App

Write-Host ''
Write-Host "  Removing $App" -ForegroundColor Yellow

# stop the background services
$stopper = Join-Path $Root 'stop-power-tracking.ps1'
if (Test-Path $stopper) { & $stopper }

# close the app window if it is open. Asking for a browser that is not running
# raises a non-terminating error, which would leave a non-zero exit code behind
# and make Settings report a failed uninstall.
foreach ($name in @('chrome', 'msedge')) {
  try {
    foreach ($p in @(Get-Process -Name $name -ErrorAction Stop)) {
      if ($p.MainWindowTitle -eq $App) { $p.CloseMainWindow() | Out-Null }
    }
  } catch { }
}

# autostart
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $runKey -Name $App -ErrorAction SilentlyContinue) {
  Remove-ItemProperty -Path $runKey -Name $App -Force -ErrorAction SilentlyContinue
  Write-Host '  autostart removed'
}

# Settings > Apps entry
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$App"
if (Test-Path $key) {
  Remove-Item $key -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host '  unregistered from Installed apps'
}

# shortcuts, and anything the older layout left behind
foreach ($t in @(
  (Join-Path ([Environment]::GetFolderPath('Programs')) "$App.lnk"),
  (Join-Path ([Environment]::GetFolderPath('Desktop'))  "$App.lnk"),
  (Join-Path ([Environment]::GetFolderPath('Startup'))  'PowerTracker.vbs'))) {
  if (Test-Path $t) { Remove-Item $t -Force -ErrorAction SilentlyContinue }
}
Write-Host '  shortcuts removed'

# the browser profile the app window used
$prof = Join-Path $DataDir '.appprofile'
if (Test-Path $prof) { Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue }

if ($RemoveData) {
  if (Test-Path $DataDir) {
    Remove-Item $DataDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  data deleted   $DataDir" -ForegroundColor Red
  }
} else {
  Write-Host "  data kept      $DataDir"
}

Write-Host ''
Write-Host "  $App has been removed." -ForegroundColor Green
if (-not $RemoveData) {
  Write-Host '  Your logs and settings are still there; reinstalling picks them up again.'
}
Write-Host ''

if (-not $Quiet) {
  Write-Host '  Press any key to close...'
  try { $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown') } catch { Start-Sleep -Seconds 4 }
}

# The program folder cannot delete itself while this script is running from
# inside it, so hand that last step to a detached cmd that waits for us to exit.
if ($Root -like (Join-Path $env:LOCALAPPDATA 'Programs\*')) {
  Start-Process cmd.exe -WindowStyle Hidden -ArgumentList @(
    '/c', 'ping', '127.0.0.1', '-n', '4', '>nul', '&', 'rmdir', '/s', '/q', "`"$Root`"")
}

# Report success explicitly: Settings treats a non-zero exit as a failed
# uninstall, and a suppressed error above would otherwise leak into it.
exit 0
