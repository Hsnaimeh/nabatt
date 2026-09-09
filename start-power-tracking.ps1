# Starts everything, all hidden. Safe to run twice: it never starts a
# second copy of anything that is already running.
#
#   power-logger.ps1  whole-machine watts -> logs\power-YYYY-MM.csv
#   app-logger.ps1    per-application share -> logs\apps-YYYY-MM-DD.csv
#   dashboard.py      the web service on config.json:port
#   tray.py           notification-area icon (optional; skipped if pystray missing)
param([switch]$NoTray, [switch]$OpenWindow)

. (Join-Path (Split-Path -Parent $PSCommandPath) 'paths.ps1')
$Root = $NabattApp

# Every process from this shell upwards, so a shell that merely *mentions* a
# script name (this one included) is never mistaken for the service itself.
$Self = @{}
$walk = $PID
for ($i = 0; $i -lt 12 -and $walk; $i++) {
  $Self[[int]$walk] = $true
  $walk = (Get-CimInstance Win32_Process -Filter "ProcessId=$walk" -ErrorAction SilentlyContinue).ParentProcessId
}

function Test-Running([string]$script) {
  # A service is only "running" if some process was actually launched *with the
  # script as its argument*. Matching the bare name anywhere in a command line
  # is not enough -- any shell that merely mentions the file (a grep, an editor,
  # this script itself) would look like the service and block the real start.
  $full = [regex]::Escape((Join-Path $Root $script))
  if ($script -like '*.py') {
    $filter = "Name='python.exe' OR Name='pythonw.exe'"
    $pattern = '"?' + $full + '"?\s*$'          # python.exe <script>
  } else {
    $filter = "Name='powershell.exe'"
    $pattern = '-File\s+"?' + $full + '"?\s*$'  # powershell -File <script>
  }
  foreach ($p in (Get-CimInstance Win32_Process -Filter $filter -ErrorAction SilentlyContinue)) {
    if ($Self.ContainsKey([int]$p.ProcessId)) { continue }
    if ($p.CommandLine -and $p.CommandLine -match $pattern) { return $true }
  }
  return $false
}

# The dashboard is a listening service, so ask it directly -- that is the only
# check that cannot be fooled by a stale or look-alike process.
function Test-Dashboard([int]$port) {
  try {
    Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $port) `
                      -TimeoutSec 3 -UseBasicParsing | Out-Null
    return $true
  } catch { return $false }
}

# Python needs a moment to import and bind the port, so never judge it on a
# single immediate probe -- that is what made a healthy service look dead.
function Wait-Dashboard([int]$port, [double]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-Dashboard $port) { return $true }
    Start-Sleep -Milliseconds 400
  }
  return $false
}

function Start-Ps([string]$script, [string]$label) {
  if (Test-Running $script) { Write-Host "$label already running"; return }
  Start-Process powershell -WindowStyle Hidden -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Root $script))
  Write-Host "$label started"
}

# Prefer pythonw.exe for the background python bits: no console window ever flashes.
$py  = (Get-Command python  -ErrorAction SilentlyContinue).Source
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw -and $py) { $pyw = Join-Path (Split-Path $py) 'pythonw.exe' }
if (-not $pyw -or -not (Test-Path $pyw)) { $pyw = $py }

Start-Ps 'power-logger.ps1' 'logger'
Start-Ps 'app-logger.ps1'   'app logger'

$cfg = Get-Content $NabattConfig -Raw | ConvertFrom-Json

function Get-DashboardProcs {
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -EA SilentlyContinue |
    Where-Object { $_.CommandLine -like ('*' + (Join-Path $Root 'dashboard.py') + '*') }
}

if (-not $pyw) {
  Write-Warning 'python not found on PATH - dashboard not started'
} elseif (Test-Dashboard $cfg.port) {
  Write-Host 'dashboard already running'
} else {
  $existing = @(Get-DashboardProcs)
  # A process that exists but is not answering yet is probably still starting:
  # give it time before assuming it is broken and killing it.
  if ($existing.Count -and (Wait-Dashboard $cfg.port 8)) {
    Write-Host 'dashboard already running'
  } else {
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force -EA SilentlyContinue }
    Start-Process $pyw -ArgumentList (Join-Path $Root 'dashboard.py') `
                  -WorkingDirectory $Root -WindowStyle Hidden
    if (Wait-Dashboard $cfg.port 15) { Write-Host 'dashboard started' }
    else { Write-Warning 'dashboard did not come up - run: python dashboard.py' }
  }
}

if (-not $NoTray) {
  if (Test-Running 'tray.py') {
    Write-Host 'tray already running'
  } elseif ($pyw) {
    & $py -c "import pystray, PIL" 2>$null
    if ($LASTEXITCODE -eq 0) {
      Start-Process $pyw -ArgumentList (Join-Path $Root 'tray.py') `
                    -WorkingDirectory $Root -WindowStyle Hidden
      Write-Host 'tray started'
    } else {
      Write-Host 'tray skipped (run: pip install pystray pillow)'
    }
  }
}

$ip  = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1).IPAddress
Write-Host ''
Write-Host ('  on this PC : http://localhost:{0}' -f $cfg.port)
if ($ip) { Write-Host ('  on phone   : http://{0}:{1}' -f $ip, $cfg.port) }

if ($OpenWindow) { & (Join-Path $Root 'open-app.ps1') }
