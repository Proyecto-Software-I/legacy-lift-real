$ErrorActionPreference = "Continue"
Write-Host "=== Apagando todo el stack de LegacyLift ===" -ForegroundColor Yellow

# 1. Desactivar KeepAlive y Startup para que no vuelva a levantarse solo
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\Docs\uni\proyecto\legacy-lift-real\deploy\keepalive-exam.ps1" -Action disable
schtasks.exe /Delete /TN "LegacyLift-Caddy-Service" /F 2>$null | Out-Null
schtasks.exe /Delete /TN "LegacyLift-WSL-Service" /F 2>$null | Out-Null

# 2. Detener Caddy en Windows
$caddy = Get-Process -Name "caddy" -ErrorAction SilentlyContinue
if ($caddy) {
    Stop-Process -Name "caddy" -Force -ErrorAction SilentlyContinue
    Write-Host "1. Caddy detenido." -ForegroundColor Green
} else {
    Write-Host "1. Caddy ya estaba detenido." -ForegroundColor Gray
}

# 3. Detener FastAPI y el contenedor migration-worker en WSL2 Ubuntu-24.04
Write-Host "2. Deteniendo FastAPI (legacylift-api) y worker (legacylift-worker) en WSL2..."
wsl.exe -d Ubuntu-24.04 -u root -- bash -c "systemctl stop legacylift-worker 2>/dev/null || true; systemctl stop legacylift-api 2>/dev/null || true; cd /mnt/f/Docs/uni/proyecto/legacy-lift-real && docker compose -f compose.migration-worker.yml down 2>/dev/null || true"

# 4. Terminar procesos holder de WSL2 y apagar la distro
Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'sleep infinity' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
wsl.exe --terminate Ubuntu-24.04 2>$null

Write-Host "=== Todo el stack de LegacyLift fue apagado correctamente ===" -ForegroundColor Green
