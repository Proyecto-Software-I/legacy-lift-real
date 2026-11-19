@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\Docs\uni\proyecto\legacy-lift-real\deploy\stop-exam.ps1"
if "%1"=="" (
  echo.
  echo Stack APAGADO. Esta ventana se cerrara en 6 segundos...
  timeout /t 6 >nul
)
