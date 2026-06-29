@echo off
title JeansFinder
cd /d "%~dp0"
echo.
echo  ============================================
echo   JeansFinder
echo  ============================================
echo.
python --version >nul 2>&1 || (echo ERROR: Python not found. Install from python.org && pause && exit)

if not exist "data"             mkdir data
if not exist "static\images"   mkdir "static\images"
if not exist "reference_images" mkdir reference_images
if not exist "logs"             mkdir logs
if not exist "templates"        mkdir templates

:: copy index.html into templates/ if it's sitting at root
if exist "index.html" (
    if not exist "templates\index.html" (
        copy "index.html" "templates\index.html" >nul
    )
)

if not exist "templates\index.html" (
    echo ERROR: templates\index.html not found.
    echo Place index.html in the templates\ folder next to this script.
    pause & exit
)

if not exist "data\vinted_session.json" (
    echo First time setup: log into your throwaway Vinted account.
    echo.
    python scraper.py --login
    echo.
    if not exist "data\vinted_session.json" (
        echo Login failed or cancelled.
        pause & exit
    )
)

echo Starting scraper pipeline ^(background^)...
start "JeansFinder Scraper" /min cmd /c "python pipeline.py >> logs\pipeline.log 2>&1 & echo Pipeline stopped. Check logs\pipeline.log for errors. & pause"
timeout /t 3 /nobreak >nul

echo Starting phone UI...
echo.
echo  ============================================
echo   Open on your phone: check the IP below
echo   Both devices must be on the same WiFi.
echo   Windows Firewall: allow Python if prompted.
echo  ============================================
echo.
python app.py
