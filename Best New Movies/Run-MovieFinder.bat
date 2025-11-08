@echo off
cd /d "%~dp0"

rem Quiet pip’s version check for this run
set PIP_DISABLE_PIP_VERSION_CHECK=1

python -V >NUL 2>&1
IF ERRORLEVEL 1 (
  echo Python is required but not found. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)

IF NOT EXIST .venv (
  python -m venv ".venv"
)

rem *** Upgrade pip the correct way ***
call ".venv\Scripts\python.exe" -m pip install --upgrade pip

rem Install/refresh deps (quiet)
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q

rem Launch with auto-open enabled (your normal interactive run)
call ".venv\Scripts\python.exe" "movie_finder.py"

echo.
echo Done! Open new_streaming_movies_by_genre.html or CSV from this folder.
pause
