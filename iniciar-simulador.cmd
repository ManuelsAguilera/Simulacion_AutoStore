@echo off
cd /d "%~dp0"
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo No se pudo iniciar el simulador. Comprueba que Docker Desktop este abierto.
  pause
  exit /b 1
)
start "" "http://localhost:8100"
