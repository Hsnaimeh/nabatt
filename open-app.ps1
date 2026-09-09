# Opens the dashboard as a desktop window: Chrome (or Edge) in --app mode, so
# there is no tab strip, no address bar and no browser menu -- just the page,
# with its own taskbar button and its own icon.
#
# A dedicated user-data-dir keeps it out of your normal browsing profile, so
# closing your last Chrome window never closes this, and vice versa.
. (Join-Path (Split-Path -Parent $PSCommandPath) 'paths.ps1')
$Root = $NabattApp
$cfg  = Get-Content $NabattConfig -Raw | ConvertFrom-Json
$url  = 'http://localhost:{0}/' -f $cfg.port

# Wait briefly for the server, so a shortcut clicked at logon does not race it.
for ($i = 0; $i -lt 20; $i++) {
  try {
    Invoke-WebRequest -Uri ('http://127.0.0.1:{0}/api/health' -f $cfg.port) -TimeoutSec 3 -UseBasicParsing | Out-Null
    break
  } catch { Start-Sleep -Milliseconds 500 }
}

$profileDir = Join-Path $NabattData '.appprofile'
$browsers = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$exe = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $exe) {
  # No Chromium browser: fall back to whatever handles http.
  Start-Process $url
  return
}

Start-Process $exe -ArgumentList @(
  "--app=$url",
  "--user-data-dir=$profileDir",
  '--window-size=1240,900',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-features=Translate,MediaRouter'
)
