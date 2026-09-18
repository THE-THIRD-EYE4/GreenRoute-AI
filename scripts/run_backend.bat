@echo off
REM Run from anywhere: sets up the backend venv (once) and starts uvicorn.
cd /d "%~dp0..\backend"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
echo Installing/updating backend dependencies...
pip install -e ".[dev]" -q

echo.
echo Starting backend on http://localhost:8000
echo First boot solves 55 Pareto fronts in the background -- the app
echo works immediately; GET /health shows warm-up progress.
echo Press Ctrl+C to stop.
echo.
uvicorn dispatch.api:app --reload --port 8000
