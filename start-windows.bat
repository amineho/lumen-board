@echo off
cd /d "%~dp0"
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8080"
where py >nul 2>nul
if %errorlevel%==0 (py tracker.py) else (python tracker.py)
pause
