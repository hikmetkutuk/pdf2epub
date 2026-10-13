@echo off
chcp 65001 >nul
rem PDF dosyasini bu dosyanin uzerine surukleyip birakin.
if "%~1"=="" (
  echo PDF dosyasini bu dosyanin uzerine surukleyip birakin.
  pause
  exit /b
)
for %%F in (%*) do (
  echo.
  echo === %%~nxF ===
  python "%~dp0pdf2epub.py" "%%~F" --title "%%~nF"
)
echo.
pause
