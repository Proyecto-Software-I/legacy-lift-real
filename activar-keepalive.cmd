@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\Docs\uni\proyecto\legacy-lift-real\deploy\keepalive-exam.ps1" -Action enable
if "%1"=="" (
  echo.
  echo KeepAlive ACTIVADO. Esta ventana se cerrara en 6 segundos...
  timeout /t 6 >nul
)
