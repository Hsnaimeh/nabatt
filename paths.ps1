<#
  Where Nabatt keeps its program files and where it keeps your data.

  Installed:  program in %LOCALAPPDATA%\Programs\Nabatt
              data    in %LOCALAPPDATA%\Nabatt   (config.json + logs)

  Portable:   drop a file named portable.txt beside the scripts and everything
              -- config and logs included -- stays in the app folder instead.

  Dot-source this: . (Join-Path $PSScriptRoot 'paths.ps1')
  It defines $NabattApp, $NabattData, $NabattLogs and $NabattConfig.
#>

$NabattApp = Split-Path -Parent $PSCommandPath

if (Test-Path (Join-Path $NabattApp 'portable.txt')) {
  $NabattData = $NabattApp
} else {
  $NabattData = Join-Path $env:LOCALAPPDATA 'Nabatt'
}

if (-not (Test-Path $NabattData)) {
  New-Item -ItemType Directory -Force -Path $NabattData | Out-Null
}

$NabattLogs = Join-Path $NabattData 'logs'
if (-not (Test-Path $NabattLogs)) {
  New-Item -ItemType Directory -Force -Path $NabattLogs | Out-Null
}

# Your settings live with your data. The copy shipped beside the scripts is only
# the default, used to seed yours the first time.
$NabattConfig = Join-Path $NabattData 'config.json'
if (-not (Test-Path $NabattConfig)) {
  $seed = Join-Path $NabattApp 'config.json'
  if (Test-Path $seed) { Copy-Item $seed $NabattConfig }
  else { $NabattConfig = $seed }
}
