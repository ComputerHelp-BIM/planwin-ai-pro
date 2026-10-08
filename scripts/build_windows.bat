@echo off
REM Local Windows build: creates dist\PlanWinAIPro\PlanWinAIPro.exe, a portable single exe and the installer.
REM Requires Python 3.12 (64-bit). Inno Setup 6 is optional (for the setup.exe).
setlocal
cd /d %~dp0\..
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :err
python -m pytest -q || goto :err
pyinstaller packaging\planwin_ai.spec --noconfirm --clean || goto :err
dist\PlanWinAIPro\PlanWinAIPro.exe --selftest || goto :err
set PLANWIN_ONEFILE=1
pyinstaller packaging\planwin_ai.spec --noconfirm || goto :err
set PLANWIN_ONEFILE=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\installer.iss || goto :err
) else (
  echo Inno Setup not found - skipping installer. Get it from https://jrsoftware.org/isdl.php
)
echo.
echo Done. See the dist folder.
exit /b 0
:err
echo Build failed.
exit /b 1
