param(
    [ValidateSet("enable", "disable", "status")]
    [string]$Action = "status"
)

$TaskName = "LegacyLift-Exam-KeepAlive"
$StartupCmd = "C:\Users\S3B4S5\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\LegacyLift-AutoStart.cmd"
$StartScript = "F:\Docs\uni\proyecto\legacy-lift-real\deploy\start-exam.ps1"

if ($Action -eq "enable") {
    Write-Host "=== Activando KeepAlive cada 5 minutos para el examen ===" -ForegroundColor Cyan
    schtasks.exe /Create /TN $TaskName /SC MINUTE /MO 5 /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`"" /F | Out-Null
    $startupContent = "@echo off`r`nstart `"`" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`"`r`n"
    Set-Content -Path $StartupCmd -Value $startupContent -Encoding ASCII
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript
    Write-Host "KeepAlive ACTIVADO (se ejecutara cada 5 minutos y al iniciar sesion en Windows)." -ForegroundColor Green
}
elseif ($Action -eq "disable") {
    Write-Host "=== Desactivando KeepAlive automatico ===" -ForegroundColor Yellow
    schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
    if (Test-Path $StartupCmd) {
        Remove-Item -Path $StartupCmd -Force -ErrorAction SilentlyContinue
    }
    Write-Host "KeepAlive DESACTIVADO (la tarea programada cada 5 min y el auto-inicio de Windows fueron eliminados)." -ForegroundColor Green
}
else {
    $exists = schtasks.exe /Query /TN $TaskName 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Estado KeepAlive: ACTIVO (ejecutandose cada 5 minutos)" -ForegroundColor Green
    } else {
        Write-Host "Estado KeepAlive: DESACTIVADO" -ForegroundColor Yellow
    }
}
