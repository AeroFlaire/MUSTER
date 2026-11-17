# Starts the MUSTER app (Gatekeeper + the pages in edge/app) on this laptop.
#   .\start_app.ps1                 real cards (people register by scanning their own CAC)
#   .\start_app.ps1 -Synthetic -Reset   Arjo's fake test cards, fresh database
#   .\start_app.ps1 -Https          also reachable from a phone on the same network
# The two secret keys are made once and kept in .app_keys (ignored by git, never commit it).
# If you delete .app_keys, the old database can no longer be opened: run with -Reset.
param([switch]$Https, [switch]$Synthetic, [switch]$Reset)
Set-Location $PSScriptRoot
$keys = Join-Path $PSScriptRoot ".app_keys"
if (-not (Test-Path $keys)) {
    $edipi = python -c "import secrets; print(secrets.token_hex(32))"
    $db = python -c "import secrets; print(secrets.token_hex(32))"
    Set-Content -Path $keys -Value @("MUSTER_EDIPI_KEY=$edipi", "MUSTER_DB_KEY=$db")
    Write-Host "Made new keys in .app_keys (keep this file private, do not commit it)."
    $Reset = $true
}
foreach ($line in Get-Content $keys) {
    $k, $v = $line.Split("=", 2)
    Set-Item -Path "Env:$k" -Value $v
}
$env:PYTHONPATH = "..\..\vault;."
$argsList = @("live_app.py", "--app")
if ($Reset) { $argsList += "--reset" }
if ($Synthetic) { $argsList += "--synthetic" }
if ($Https) { $argsList += "--https" }
python @argsList
