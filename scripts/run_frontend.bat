@echo off
REM Run from anywhere: installs frontend deps (once) and starts Next.js dev server.
cd /d "%~dp0..\frontend"

if not exist node_modules (
    echo Installing frontend dependencies...
    npm install
)

echo.
echo Starting frontend on http://localhost:3000
echo Start the backend first (scripts\run_backend.bat) -- the app polls
echo /health and shows a banner if the backend isn't reachable yet.
echo Press Ctrl+C to stop.
echo.
npm run dev
