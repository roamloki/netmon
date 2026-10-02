@echo off
setlocal EnableExtensions
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

netstat -ano | findstr "127.0.0.1:8787" | findstr "LISTENING" >nul
if not errorlevel 1 goto open

call :find_python
if not defined PY (
  echo [ERROR] Python not found.
  echo Install Python 3 and check "Add python.exe to PATH".
  echo Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist "%DIR%\data" mkdir "%DIR%\data"
set "LAUNCH=%PY%"
if /I "%PY:~-10%"=="python.exe" (
  if exist "%PY:~0,-10%pythonw.exe" set "LAUNCH=%PY:~0,-10%pythonw.exe"
)
echo [%date% %time%] launching "%LAUNCH%" "%DIR%\monitor.py" >> "%DIR%\data\run.log"

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%LAUNCH%' -ArgumentList ('\"' + '%DIR%\monitor.py' + '\"') -WorkingDirectory '%DIR%' -WindowStyle Hidden"

set "OK="
for /L %%I in (1,1,10) do (
  timeout /t 1 /nobreak >nul
  netstat -ano | findstr "127.0.0.1:8787" | findstr "LISTENING" >nul
  if not errorlevel 1 (
    set "OK=1"
    goto open
  )
)

echo [ERROR] Monitor failed to listen on 127.0.0.1:8787, check data\run.log
echo ----- data\run.log -----
if exist "%DIR%\data\run.log" type "%DIR%\data\run.log"
pause
exit /b 1

:open
start "" http://127.0.0.1:8787
exit /b 0

:find_python
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe" & goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe" & goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" & goto :eof
if exist "C:\Windows\py.exe" set "PY=C:\Windows\py.exe" & goto :eof
where py >nul 2>&1 && (set "PY=py" & goto :eof)
for /f "delims=" %%I in ('where python 2^>nul') do (
  if %%~zI GTR 0 (
    echo %%I | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
      set "PY=%%I"
      goto :eof
    )
  )
)
goto :eof
