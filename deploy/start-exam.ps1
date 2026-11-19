$ErrorActionPreference = "Continue"
$RepoRoot = "F:\Docs\uni\proyecto\legacy-lift-real"
$CaddyExe = "F:\ServidorCasa\Caddy\caddy.exe"
$CaddyConfig = "$RepoRoot\deploy\Caddyfile.example"
$Dynv6Script = "C:\ClassForge\update-dynv6.ps1"
$LogFile = "$RepoRoot\deploy\autostart.log"
$WslVbs = "$RepoRoot\deploy\wsl-keepalive.vbs"

function Write-Step($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

Write-Step "=== Verificando/Iniciando LegacyLift Stack ==="

# 1. Actualizar DNS AAAA (classforge-ai.dynv6.net) con la IPv6 global /128 actual
if (Test-Path $Dynv6Script) {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Dynv6Script | Out-Null
        Write-Step "1. DNS AAAA (classforge-ai.dynv6.net) actualizado."
    } catch {
        Write-Step "1. Aviso al actualizar DNS AAAA: $($_.Exception.Message)"
    }
}

# 2. Lanzar holder de WSL2 desde el Programador de Tareas de Windows (svchost.exe) para que sea inmune al cierre de consolas
$wslHolder = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'sleep infinity' }
if (-not $wslHolder) {
    Write-Step "2a. Iniciando holder persistente de WSL2 Ubuntu-24.04 via Task Scheduler..."
    schtasks.exe /Create /TN "LegacyLift-WSL-Service" /SC ONCE /ST 00:00 /TR "wscript.exe //B //NoLogo `"$WslVbs`"" /F 2>$null | Out-Null
    schtasks.exe /Run /TN "LegacyLift-WSL-Service" 2>$null | Out-Null
    Start-Sleep -Seconds 2
}

# 3. Asegurar que WSL2 Ubuntu-24.04, Docker, migration-worker y legacylift-api estén activos
Write-Step "2b. Verificando servicios en WSL2 Ubuntu-24.04 (Docker + migration-worker + FastAPI)..."
wsl.exe -d Ubuntu-24.04 -u root -- bash -c "systemctl stop caddy 2>/dev/null || true; systemctl disable caddy 2>/dev/null || true; systemctl start docker && systemctl start legacylift-api && cd /mnt/f/Docs/uni/proyecto/legacy-lift-real && docker compose -f compose.migration-worker.yml up -d" | Out-Null

# 4. Lanzar Caddy desde el Programador de Tareas de Windows (svchost.exe) para que NUNCA muera al cerrar la ventana del script
$caddyProc = Get-Process -Name "caddy" -ErrorAction SilentlyContinue
if (-not $caddyProc) {
    Write-Step "3. Iniciando Caddy reverse proxy TLS sobre IPv6 via Task Scheduler..."
    schtasks.exe /Create /TN "LegacyLift-Caddy-Service" /SC ONCE /ST 00:00 /TR "`"$CaddyExe`" run --config `"$CaddyConfig`"" /F 2>$null | Out-Null
    schtasks.exe /Run /TN "LegacyLift-Caddy-Service" 2>$null | Out-Null
    Start-Sleep -Seconds 3
} else {
    Write-Step "3. Caddy ya está en ejecución (PID $($caddyProc.Id -join ','))."
}

# 5. Comprobación final local y pública por IPv6
$localHealth = (curl.exe -s --connect-timeout 5 http://127.0.0.1:8000/api/health)
$publicHealth = (curl.exe -6 -s --connect-timeout 8 https://classforge-ai.dynv6.net/api/health)
Write-Step "4. Health Local (127.0.0.1:8000): $localHealth"
Write-Step "5. Health Público IPv6 (https://classforge-ai.dynv6.net/api/health): $publicHealth"
Write-Step "=== LegacyLift listo ==="
