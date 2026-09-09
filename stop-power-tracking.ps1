# Stops the loggers, the dashboard service and the tray icon.
# The dashboard app window (Chrome) is left alone -- close it yourself.
$targets = @('power-logger.ps1','app-logger.ps1','dashboard.py','tray.py')
$procs = Get-CimInstance Win32_Process `
         -Filter "Name='powershell.exe' OR Name='python.exe' OR Name='pythonw.exe'"
$found = $false
foreach ($p in $procs) {
  foreach ($t in $targets) {
    if ($p.CommandLine -like "*$t*") {
      Write-Host ("stopping {0}  (pid {1})" -f $t, $p.ProcessId)
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      $found = $true
      break
    }
  }
}
if (-not $found) { Write-Host 'nothing was running' }
Write-Host 'stopped'
