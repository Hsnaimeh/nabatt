<#
  Samples real GPU power (nvidia-smi) + modeled CPU/system power, appends to a
  monthly CSV.  Runs unelevated.  Designed to never die: every sample is guarded.
#>
$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $PSCommandPath) 'paths.ps1')
$Root = $NabattApp
$cfg  = Get-Content $NabattConfig -Raw | ConvertFrom-Json

$interval = [double]$cfg.sample_seconds
$maxGap   = $interval * [double]$cfg.max_gap_multiplier
$header   = 'ts,epoch,dt_s,gpu_w,gpu_util,cpu_util,cpu_w,rest_w,wall_w,wh,src'

function Get-LogPath([datetime]$t) {
  Join-Path $NabattLogs ('power-' + $t.ToString('yyyy-MM') + '.csv')
}

function Write-Row([string]$path, [string]$line) {
  if (-not (Test-Path $path)) { Set-Content -Path $path -Value $header -Encoding utf8 }
  Add-Content -Path $path -Value $line -Encoding utf8
}

$lastTick = $null
Write-Host "power-logger started; interval ${interval}s; logs -> $NabattLogs"

while ($true) {
  try {
    $now = Get-Date

    # ---- GPU: real sensor reading (whole board power) ----
    $gpuW = 0.0; $gpuUtil = 0.0
    try {
      $raw = & nvidia-smi --query-gpu=power.draw,utilization.gpu --format=csv,noheader,nounits 2>$null
      if ($raw) {
        $first = @($raw)[0]
        $p = $first -split ','
        $gpuW    = [double]($p[0].Trim())
        $gpuUtil = [double]($p[1].Trim())
      }
    } catch { }

    # ---- CPU: utilisation is real, wattage is modeled ----
    $cpuUtil = 0.0
    try {
      $cpuUtil = [double](Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor `
                   -Filter "Name='_Total'" -ErrorAction Stop).PercentProcessorTime
    } catch { }
    if ($cpuUtil -lt 0) { $cpuUtil = 0 }
    if ($cpuUtil -gt 100) { $cpuUtil = 100 }

    $span  = [double]$cfg.cpu_max_w - [double]$cfg.cpu_idle_w
    $cpuW  = [double]$cfg.cpu_idle_w + $span * [math]::Pow(($cpuUtil / 100.0), [double]$cfg.cpu_curve_exp)

    $restW = [double]$cfg.rest_of_system_w + [double]$cfg.monitors_w
    $wallW = ($gpuW + $cpuW + $restW) / [double]$cfg.psu_efficiency

    # ---- energy for this interval ----
    if ($null -eq $lastTick) {
      $dt = $interval
      $dtUsed = $interval
    } else {
      $dt = ($now - $lastTick).TotalSeconds
      if ($dt -gt $maxGap) { $dtUsed = $interval } else { $dtUsed = $dt }
    }
    $wh = $wallW * $dtUsed / 3600.0
    $lastTick = $now

    $line = '{0},{1},{2:F1},{3:F1},{4:F0},{5:F0},{6:F1},{7:F1},{8:F1},{9:F5},live' -f `
      $now.ToString('yyyy-MM-dd HH:mm:ss'),
      [int64]([DateTimeOffset]$now).ToUnixTimeSeconds(),
      $dt, $gpuW, $gpuUtil, $cpuUtil, $cpuW, $restW, $wallW, $wh

    Write-Row (Get-LogPath $now) $line
  }
  catch {
    # never let a bad sample kill the loop
    try { Add-Content -Path (Join-Path $NabattData 'logger-errors.log') `
            -Value ("{0}  {1}" -f (Get-Date), $_.Exception.Message) -Encoding utf8 } catch { }
  }

  Start-Sleep -Seconds $interval
}
