@echo off
REM One command to build the public demo: checks what's missing, exports a snapshot,
REM then serves docs/ so you can look at it before pushing.
setlocal
cd /d "%~dp0"

echo.
echo [1/3] Checking everything is ready...
python export_demo.py --check
if errorlevel 1 (
  echo.
  echo Fix the points above, then run this again.
  pause
  exit /b 1
)

echo.
echo [2/3] Running one real scrape and exporting the snapshot. This takes a few minutes.
python export_demo.py %*
if errorlevel 1 (
  echo Export failed. The message above says why.
  pause
  exit /b 1
)

echo.
echo [3/3] Serving the demo at http://localhost:8000 - press Ctrl+C when you're done.
start "" http://localhost:8000
python -m http.server 8000 --directory docs
endlocal
