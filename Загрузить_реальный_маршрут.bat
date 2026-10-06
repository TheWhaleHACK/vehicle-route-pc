@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 generate_real_data.py
) else (
  python generate_real_data.py
)
if errorlevel 1 (
  echo.
  echo Не удалось получить данные. Проверьте Python 3 и подключение к интернету.
  pause
  exit /b 1
)
echo.
echo Теперь откройте index.html.
pause
