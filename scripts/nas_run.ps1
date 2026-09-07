#Requires -Version 5.1
# Run a bash snippet as root on the NAS via the persistent agent loop,
# OR one-shot sudo bash -lc if the loop is not up.
#
# Usage:
#   .\nas_run.ps1 "whoami; hostname"
#   .\nas_run.ps1 -File O:\bip110minner\scripts\_something.sh
param(
  [Parameter(Position = 0)]
  [string]$Command = "",

  [string]$File = "",

  [int]$TimeoutSec = 120
)

$ErrorActionPreference = "Stop"
$SshHost = "bip110-nas"
$RemoteDir = "/tmp/bip110-agent"

function Fail([string]$m) { Write-Host ("FATAL: " + $m) -ForegroundColor Red; exit 1 }

if ($File) {
  if (-not (Test-Path -LiteralPath $File)) { Fail ("missing " + $File) }
  $body = [System.IO.File]::ReadAllText($File)
} elseif ($Command) {
  $body = $Command
} else {
  Fail "pass a command string or -File path"
}

# Ensure remote dir exists (chmod may fail if root-owned; ignore)
& ssh.exe -o BatchMode=yes $SshHost ("mkdir -p " + $RemoteDir + "; chmod 777 " + $RemoteDir + " 2>/dev/null || true; test -d " + $RemoteDir)
if ($LASTEXITCODE -ne 0) { Fail "ssh mkdir failed" }

$status = (& ssh.exe -o BatchMode=yes $SshHost ("cat " + $RemoteDir + "/status 2>/dev/null || true") | Out-String).Trim()
$useLoop = $false
if ($status -match "^(ready|idle|running)") {
  $useLoop = $true
}

$localCmd = Join-Path $env:TEMP "bip110-nas-cmd.sh"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($localCmd, ($body.TrimEnd() + "`n"), $utf8)

if ($useLoop) {
  Write-Host ("NAS agent loop detected: " + $status)
  & scp.exe -o BatchMode=yes -- $localCmd ($SshHost + ":" + $RemoteDir + "/cmd.sh")
  if ($LASTEXITCODE -ne 0) { Fail "scp cmd.sh failed" }

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    Start-Sleep -Milliseconds 300
    $st = & ssh.exe -o BatchMode=yes $SshHost ("cat " + $RemoteDir + "/status 2>/dev/null || true")
    if ($st -match "^idle") { break }
  } while ((Get-Date) -lt $deadline)

  & ssh.exe -o BatchMode=yes $SshHost ("cat " + $RemoteDir + "/out.txt; echo EXIT:; cat " + $RemoteDir + "/exit")
  $ecLine = & ssh.exe -o BatchMode=yes $SshHost ("cat " + $RemoteDir + "/exit 2>/dev/null || echo 1")
  exit [int]$ecLine
} else {
  Write-Host "NAS agent loop not running - one-shot sudo bash -lc"
  # Upload snippet and execute with bash (avoids PowerShell quoting of body)
  $remoteTmp = "/tmp/bip110-oneshot-$$.sh"
  # Use fixed name with timestamp
  $remoteTmp = "/tmp/bip110-oneshot.sh"
  & scp.exe -o BatchMode=yes -- $localCmd ($SshHost + ":" + $remoteTmp)
  if ($LASTEXITCODE -ne 0) { Fail "scp oneshot failed" }
  & ssh.exe -o BatchMode=yes $SshHost ("sudo -n bash " + $remoteTmp + "; ec=`$?; rm -f " + $remoteTmp + "; exit `$ec")
  exit $LASTEXITCODE
}
