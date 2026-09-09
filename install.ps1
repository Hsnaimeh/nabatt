<#
  Installs Nabatt for the current user. No admin rights needed.

    program  ->  %LOCALAPPDATA%\Programs\Nabatt     (the app itself)
    data     ->  %LOCALAPPDATA%\Nabatt              (config.json + logs)

  Data is kept separate from the program, so an upgrade, a reinstall or an
  uninstall never puts your history at risk.

  Running this from anywhere copies the app into place, brings across any logs
  and settings found next to it, and registers everything with Windows:

    * Start Menu and Desktop shortcuts, with the app icon
    * an entry in Settings > Apps > Installed apps
    * starts at sign-in, listed in Task Manager > Startup
    * a tray icon next to the clock

  Re-running is safe -- it upgrades in place and keeps your data.
#>
param(
  [switch]$NoStart,
  [string]$To = (Join-Path $env:LOCALAPPDATA 'Programs\Nabatt')
)

$ErrorActionPreference = 'Stop'

$App       = 'Nabatt'
$Tagline   = 'Power & cost monitor'
$Publisher = 'Hisham Snaimeh'
$Version   = '1.0.0'

$Src     = Split-Path -Parent $PSCommandPath
$Target  = $To
$DataDir = Join-Path $env:LOCALAPPDATA $App
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'

Write-Host ''
Write-Host "  Installing $App" -ForegroundColor Yellow
Write-Host "    program : $Target"
Write-Host "    data    : $DataDir"
Write-Host ''

# --- prerequisites -------------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
  Write-Warning 'Python was not found on PATH. Nabatt needs it for the dashboard.'
  Write-Warning 'Install Python 3, tick "Add python.exe to PATH", then run this again.'
  exit 1
}

# --- stop anything already running, from either location -----------------
foreach ($stopper in @((Join-Path $Target 'stop-power-tracking.ps1'),
                       (Join-Path $Src 'stop-power-tracking.ps1'))) {
  if (Test-Path $stopper) { & $stopper | Out-Null; break }
}
Start-Sleep -Seconds 2

# --- copy the program into place -----------------------------------------
if ($Src -ne $Target) {
  New-Item -ItemType Directory -Force -Path $Target | Out-Null

  # Program files only. Logs, the browser profile and the generated launchers
  # are deliberately excluded -- they are data, or they get rebuilt below.
  $files = @('paths.ps1', 'paths.py', 'power-logger.ps1', 'app-logger.ps1',
             'dashboard.py', 'tray.py', 'open-app.ps1', 'make-icon.py',
             'start-power-tracking.ps1', 'stop-power-tracking.ps1',
             'install.ps1', 'uninstall.ps1', 'config.json', 'README.md',
             'demo_data.py', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md')
  $n = 0
  foreach ($f in $files) {
    $from = Join-Path $Src $f
    if (Test-Path $from) { Copy-Item $from (Join-Path $Target $f) -Force; $n++ }
  }
  $webSrc = Join-Path $Src 'web'
  if (Test-Path $webSrc) {
    $webDst = Join-Path $Target 'web'
    New-Item -ItemType Directory -Force -Path $webDst | Out-Null
    Copy-Item (Join-Path $webSrc '*') $webDst -Recurse -Force
  }
  Write-Host "  copied $n files + web"
}

# --- bring the data across ------------------------------------------------
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$dataLogs = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $dataLogs | Out-Null

# Settings: an existing config in the data folder always wins, so a reinstall
# never overwrites a tariff you have edited.
$dataCfg = Join-Path $DataDir 'config.json'
if (-not (Test-Path $dataCfg)) {
  $srcCfg = Join-Path $Src 'config.json'
  if (Test-Path $srcCfg) { Copy-Item $srcCfg $dataCfg; Write-Host '  settings brought across' }
}

# History: copy anything not already there. Never overwrite -- those files are
# being appended to.
$srcLogs = Join-Path $Src 'logs'
if ((Test-Path $srcLogs) -and ($srcLogs -ne $dataLogs)) {
  $moved = 0
  foreach ($f in Get-ChildItem $srcLogs -File -ErrorAction SilentlyContinue) {
    $dest = Join-Path $dataLogs $f.Name
    if (-not (Test-Path $dest)) { Copy-Item $f.FullName $dest; $moved++ }
  }
  if ($moved) { Write-Host "  $moved log file(s) brought across" }
}

$Ico = Join-Path $Target 'web\Nabatt.ico'
if (-not (Test-Path $Ico)) {
  Write-Host '  building icon...'
  & $py (Join-Path $Target 'make-icon.py') | Out-Null
}

& $py -c "import pystray, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host '  installing tray dependencies (pystray, pillow)...'
  & $py -m pip install --quiet --disable-pip-version-check pystray pillow
}

# --- launchers -----------------------------------------------------------
# .vbs shims run through wscript.exe: no console ever flashes, and the services
# they start are not children of a shell that is about to close.
$launcher = Join-Path $Target 'Nabatt.vbs'
@"
' Nabatt. Brings the background services up if they are not already running,
' then opens the dashboard window.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""$Target\start-power-tracking.ps1"" -OpenWindow", 0, False
"@ | Set-Content -Path $launcher -Encoding ASCII

$bootLauncher = Join-Path $Target 'Nabatt-boot.vbs'
@"
' Started at sign-in: services and tray icon only, no window.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""$Target\start-power-tracking.ps1""", 0, False
"@ | Set-Content -Path $bootLauncher -Encoding ASCII

@"
' Removes Nabatt.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""$Target\uninstall.ps1""", 1, False
"@ | Set-Content -Path (Join-Path $Target 'Nabatt-uninstall.vbs') -Encoding ASCII

# --- shortcuts -----------------------------------------------------------
function New-Shortcut([string]$path, [string]$arguments) {
  $dir = Split-Path -Parent $path
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $ws  = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut($path)
  $lnk.TargetPath       = $wscript
  $lnk.Arguments        = $arguments
  $lnk.WorkingDirectory = $Target
  $lnk.Description      = "$App - $Tagline"
  if (Test-Path $Ico) { $lnk.IconLocation = "$Ico,0" }
  $lnk.Save()
  Write-Host "  shortcut  $path"
}

$startDir = [Environment]::GetFolderPath('Programs')
New-Shortcut (Join-Path $startDir "$App.lnk")                                 "`"$launcher`""
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) "$App.lnk") "`"$launcher`""

# --- start at sign-in ----------------------------------------------------
# The Run key rather than the Startup folder: this is what Windows shows in
# Task Manager > Startup, so it can be switched off there like any other app.
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Set-ItemProperty -Path $runKey -Name $App -Value "`"$wscript`" `"$bootLauncher`""
Write-Host '  autostart registered (Task Manager > Startup)'

# --- Settings > Apps > Installed apps ------------------------------------
$size = [math]::Round((Get-ChildItem $Target -Recurse -File -ErrorAction SilentlyContinue |
                       Measure-Object Length -Sum).Sum / 1KB)
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$App"
if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
$props = @{
  DisplayName     = $App
  DisplayVersion  = $Version
  Publisher       = $Publisher
  DisplayIcon     = "$Ico,0"
  InstallLocation = $Target
  UninstallString = "`"$wscript`" `"$Target\Nabatt-uninstall.vbs`""
  Comments        = $Tagline
  NoModify        = 1
  NoRepair        = 1
  EstimatedSize   = $size
}
foreach ($k in $props.Keys) { Set-ItemProperty -Path $key -Name $k -Value $props[$k] }
Write-Host '  registered in Settings > Apps > Installed apps'

# --- clear out anything left by the older layout -------------------------
foreach ($stale in @(
  (Join-Path ([Environment]::GetFolderPath('Startup')) 'PowerTracker.vbs'),
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Power Tracker.lnk'),
  (Join-Path $startDir 'Power Tracker.lnk'))) {
  if (Test-Path $stale) { Remove-Item $stale -Force -ErrorAction SilentlyContinue }
}

# --- start it ------------------------------------------------------------
if (-not $NoStart) {
  Write-Host ''
  Write-Host '  starting...'
  Start-Process $wscript -ArgumentList "`"$bootLauncher`""
  Start-Sleep -Seconds 7
}

Write-Host ''
Write-Host "  $App is installed." -ForegroundColor Green
Write-Host '  Open it from the Start Menu, the Desktop, or the tray icon by the clock.'
Write-Host '  To pin it: open it once, right-click its taskbar button, Pin to taskbar.'
Write-Host ''
exit 0
