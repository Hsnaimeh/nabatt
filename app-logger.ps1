<#
  Per-application power attribution.

  Runs as its own process, on a slower cadence than power-logger.ps1, because the
  '\GPU Engine(*)' counter query costs ~2s per call and we do not want that
  jitter inside the main energy loop.

  Sources (both real, neither modeled):
    * \GPU Engine(pid_N_..._engtype_X)\Utilization Percentage  -> per-PID GPU busy %
    * Win32_PerfFormattedData_PerfProc_Process                 -> per-PID CPU %

  Attribution model (deliberately conservative):
    * GPU watts ABOVE the idle floor are split across PIDs by GPU busy share.
    * CPU watts ABOVE cpu_idle_w are split across PIDs by CPU share.
    * Idle floors, rest-of-system and PSU loss stay unattributed and are
      reported as "Baseline" so the app numbers never inflate to fill the wall.

  Writes one CSV per day: logs/apps-YYYY-MM-DD.csv
#>
$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path -Parent $PSCommandPath) 'paths.ps1')
$Root = $NabattApp
$cfg  = Get-Content $NabattConfig -Raw | ConvertFrom-Json

function Cfg([string]$key, [double]$fallback) {
  $v = $cfg.PSObject.Properties[$key]
  if ($null -ne $v -and $null -ne $v.Value) { return [double]$v.Value }
  return $fallback
}
$interval = Cfg 'app_sample_seconds' 30
$gpuIdleW = Cfg 'gpu_idle_w'         40
$cores     = [double](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$header    = 'ts,app,pids,gpu_pct,cpu_pct,gpu_w,cpu_w,watts,wh'

function Get-AppLogPath([datetime]$t) {
  Join-Path $NabattLogs ('apps-' + $t.ToString('yyyy-MM-dd') + '.csv')
}

function Write-Row([string]$path, [string[]]$lines) {
  if (-not (Test-Path $path)) { Set-Content -Path $path -Value $header -Encoding utf8 }
  if ($lines.Count) { Add-Content -Path $path -Value $lines -Encoding utf8 }
}

# Latest wattage figures come from the main logger's CSV so both agree exactly.
function Get-LatestPower([datetime]$now) {
  $p = Join-Path $NabattLogs ('power-' + $now.ToString('yyyy-MM') + '.csv')
  if (-not (Test-Path $p)) { return $null }
  try {
    $last = Get-Content -Path $p -Tail 1 -ErrorAction Stop
    $f = $last -split ','
    if ($f.Count -lt 10) { return $null }
    $ts = [datetime]::ParseExact($f[0], 'yyyy-MM-dd HH:mm:ss', $null)
    if (((Get-Date) - $ts).TotalSeconds -gt 120) { return $null }   # stale: logger down
    return [pscustomobject]@{ GpuW = [double]$f[3]; CpuW = [double]$f[6] }
  } catch { return $null }
}

Write-Host "app-logger started; interval ${interval}s; $cores logical cores"

$lastTick = $null
while ($true) {
  try {
    $now = Get-Date
    $pw  = Get-LatestPower $now

    if ($null -eq $pw) {
      # Main logger is not producing samples; record nothing rather than guess.
      $lastTick = $null
      Start-Sleep -Seconds $interval
      continue
    }

    # ---- per-PID GPU busy % (summed over 3D / Compute / Copy / Video engines) ----
    $gpuByPid = @{}
    try {
      foreach ($s in (Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction Stop).CounterSamples) {
        if ($s.CookedValue -gt 0 -and $s.InstanceName -match '^pid_(\d+)_') {
          $pid_ = [int]$Matches[1]
          $gpuByPid[$pid_] = [double]$gpuByPid[$pid_] + [double]$s.CookedValue
        }
      }
    } catch { }

    # ---- per-PID CPU % (normalised to whole-machine percent) ----
    $cpuByPid = @{}; $nameByPid = @{}
    try {
      foreach ($p in (Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction Stop)) {
        $n = $p.Name
        if ($n -eq '_Total' -or $n -eq 'Idle' -or $p.IDProcess -le 0) { continue }
        $nameByPid[[int]$p.IDProcess] = ($n -replace '#\d+$', '')
        $v = [double]$p.PercentProcessorTime / $cores
        if ($v -gt 0) { $cpuByPid[[int]$p.IDProcess] = $v }
      }
    } catch { }

    foreach ($k in @($gpuByPid.Keys)) {
      if (-not $nameByPid.ContainsKey($k)) {
        try { $nameByPid[$k] = (Get-Process -Id $k -ErrorAction Stop).ProcessName } catch { $nameByPid[$k] = 'pid ' + $k }
      }
    }

    # ---- roll PIDs up into applications ----
    $apps = @{}
    function Add-App([string]$app, [int]$thePid, [double]$g, [double]$c) {
      if (-not $script:apps.ContainsKey($app)) {
        $script:apps[$app] = [pscustomobject]@{ Gpu = 0.0; Cpu = 0.0; Pids = New-Object 'System.Collections.Generic.HashSet[int]' }
      }
      $a = $script:apps[$app]
      $a.Gpu += $g; $a.Cpu += $c; [void]$a.Pids.Add($thePid)
    }
    foreach ($k in $gpuByPid.Keys) { Add-App $nameByPid[$k] $k ([double]$gpuByPid[$k]) 0.0 }
    foreach ($k in $cpuByPid.Keys) { Add-App $nameByPid[$k] $k 0.0 ([double]$cpuByPid[$k]) }

    $gpuTot = ($apps.Values | Measure-Object -Property Gpu -Sum).Sum
    $cpuTot = ($apps.Values | Measure-Object -Property Cpu -Sum).Sum
    if (-not $gpuTot) { $gpuTot = 0.0 }
    if (-not $cpuTot) { $cpuTot = 0.0 }

    # only the dynamic headroom is attributable
    $gpuPool = [math]::Max(0.0, $pw.GpuW - $gpuIdleW)
    $cpuPool = [math]::Max(0.0, $pw.CpuW - [double]$cfg.cpu_idle_w)

    if ($null -eq $lastTick) { $dtUsed = $interval }
    else {
      $d = ($now - $lastTick).TotalSeconds
      $dtUsed = if ($d -gt $interval * 5) { $interval } else { $d }
    }
    $lastTick = $now
    $hours = $dtUsed / 3600.0

    $lines = New-Object System.Collections.Generic.List[string]
    $attributedW = 0.0
    foreach ($e in $apps.GetEnumerator()) {
      $g = [double]$e.Value.Gpu; $c = [double]$e.Value.Cpu
      $gw = if ($gpuTot -gt 0) { $gpuPool * ($g / $gpuTot) } else { 0.0 }
      $cw = if ($cpuTot -gt 0) { $cpuPool * ($c / $cpuTot) } else { 0.0 }
      $w  = ($gw + $cw) / [double]$cfg.psu_efficiency
      if ($w -lt 0.05) { continue }          # skip noise
      $attributedW += $w
      $lines.Add(('{0},{1},{2},{3:F2},{4:F2},{5:F2},{6:F2},{7:F2},{8:F5}' -f `
        $now.ToString('yyyy-MM-dd HH:mm:ss'), $e.Key, $e.Value.Pids.Count,
        $g, $c, $gw, $cw, $w, ($w * $hours)))
    }

    # everything the apps did not account for, logged explicitly so the day always sums to 100%
    $baseW = (($gpuIdleW + [double]$cfg.cpu_idle_w + [double]$cfg.rest_of_system_w + [double]$cfg.monitors_w) / [double]$cfg.psu_efficiency)
    $lines.Add(('{0},{1},{2},{3:F2},{4:F2},{5:F2},{6:F2},{7:F2},{8:F5}' -f `
      $now.ToString('yyyy-MM-dd HH:mm:ss'), '__baseline__', 0, 0, 0,
      ($gpuIdleW / [double]$cfg.psu_efficiency), ([double]$cfg.cpu_idle_w / [double]$cfg.psu_efficiency),
      $baseW, ($baseW * $hours)))

    Write-Row (Get-AppLogPath $now) $lines.ToArray()
  }
  catch {
    try { Add-Content -Path (Join-Path $NabattData 'app-logger-errors.log') `
            -Value ("{0}  {1}" -f (Get-Date), $_.Exception.Message) -Encoding utf8 } catch { }
  }
  Start-Sleep -Seconds $interval
}
