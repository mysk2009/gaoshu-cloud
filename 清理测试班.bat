@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

if not exist ".cloud_dsn" (
  echo [ERROR] .cloud_dsn not found.
  echo Create it and paste your Neon connection string as one line.
  pause
  exit /b 1
)

set "DSN="
for /f "usebackq delims=" %%L in (".cloud_dsn") do set "DSN=%%L"

set "PY=D:\_scratch\pgvenv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo  Cloud class purge - DRY RUN first
echo  Patterns: DEMO%% AUD%% PG26%% DSH18%% LV26%%
echo ============================================
echo.
"%PY%" tools\cloud_purge.py --dsn "%DSN%" --match "DEMO%%,AUD%%,PG26%%,DSH18%%,LV26%%"
echo.
echo --------------------------------------------
echo  Nothing was deleted. Above is the plan only.
echo  Type YES and press Enter to REALLY delete.
echo  Press Enter alone to abort.
echo --------------------------------------------
set "CONFIRM="
set /p CONFIRM=Confirm: 
if /i not "%CONFIRM%"=="YES" (
  echo Aborted. Nothing changed.
  pause
  exit /b 0
)

echo.
echo Deleting...
"%PY%" tools\cloud_purge.py --dsn "%DSN%" --match "DEMO%%,AUD%%,PG26%%,DSH18%%,LV26%%" --yes
echo.
echo Done.
pause
