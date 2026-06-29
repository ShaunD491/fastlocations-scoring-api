@echo off
REM ============================================================
REM  FastLocations Scoring API - GitHub setup (Windows)
REM  Usage:
REM    Double-click, OR run from Command Prompt.
REM    To wire a repo + push in one go:
REM      deploy_setup.bat https://github.com/<you>/fastlocations-scoring-api.git
REM ============================================================
setlocal
cd /d "%~dp0"
echo === FastLocations API - GitHub setup ===
echo Folder: %cd%
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git is not installed. Install it from https://git-scm.com/download/win
  echo         then run this script again.
  pause & exit /b 1
)

if not exist app.py (
  echo [ERROR] app.py not found here. Run this from the Projects folder.
  pause & exit /b 1
)

REM local commit identity (only affects this repo; edit if you like)
git config user.email "shaundonnelly45@gmail.com"
git config user.name  "Shaun Donnelly"

if not exist ".git" (
  echo Initializing git repo...
  git init
)
echo Staging files...
git add .
git commit -m "FastLocations scoring API" 2>nul
git branch -M main

REM --- If a repo URL was passed, add remote and push ---
if not "%~1"=="" (
  echo Connecting remote %~1 ...
  git remote remove origin 2>nul
  git remote add origin %~1
  echo Pushing to GitHub...
  git push -u origin main
  echo.
  echo [DONE] Code is on GitHub. Next: deploy on Render (see DEPLOY.md).
  pause & exit /b 0
)

REM --- Try GitHub CLI for one-shot create + push ---
where gh >nul 2>nul
if not errorlevel 1 (
  echo GitHub CLI detected - creating repo and pushing...
  gh repo create fastlocations-scoring-api --public --source=. --remote=origin --push
  echo.
  echo [DONE] Repo created and pushed. Next: deploy on Render (see DEPLOY.md).
  pause & exit /b 0
)

echo.
echo Local commit is ready. To finish pushing to GitHub:
echo   1) Create an EMPTY repo at https://github.com/new
echo      (name it: fastlocations-scoring-api  - do NOT add a README)
echo   2) Re-run this script with the repo URL, e.g.:
echo      deploy_setup.bat https://github.com/YOURNAME/fastlocations-scoring-api.git
echo.
pause
endlocal
