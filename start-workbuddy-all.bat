@echo off
setlocal EnableExtensions
rem ===========================================================
rem  WorkBuddy launcher - starts the 2api proxy when it is not
rem  already listening, then opens the executor workbench window.
rem
rem  Usage: double-click, or  start-workbuddy-all.bat [port]
rem         port defaults to 8788.
rem
rem  Optional environment variables:
rem    WORKBUDDY_GATEWAY_DIR   gateway folder that holds desktop\app
rem    WORKBUDDY_NODE          node.exe used to open the window
rem    WORKBUDDY_PYTHON        python.exe used to run wb_proxy.py
rem    WORKBUDDY_PROBE_PYTHON  console python.exe for the health probe
rem    WORKBUDDY_WORKBENCH_EMBED_URL / _EMBED_TITLE / _EMBED_ORIGINS
rem                            optional panel embedded in the window
rem    WORKBUDDY_LAUNCH_SKIP_WINDOW=1
rem                            start the proxy only, open no window
rem
rem  ASCII-only on purpose: .bat files are read using the console
rem  code page, so non-ASCII text breaks the parser. No %VAR% is
rem  used inside an if( ... ) block, because a path containing ( or )
rem  breaks the cmd.exe parser; plain if/errorlevel/goto instead.
rem ===========================================================

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8788"
set "HERE=%~dp0"
set "SCRIPT=%HERE%wb_proxy.py"

if not exist "%SCRIPT%" goto no_script

call :find_python
if not defined PYW goto no_python
call :find_probe_python
if not defined PROBEPY goto no_probe_python

echo [launcher] checking port %PORT% ...
"%PROBEPY%" "%SCRIPT%" --port %PORT% --probe-only
if errorlevel 3 goto port_free
if errorlevel 2 goto probe_failed
if errorlevel 1 goto probe_failed
goto proxy_running

:port_free

echo [launcher] starting wb_proxy.py on port %PORT% ...
start "" /D "%HERE%" "%PYW%" "%SCRIPT%" --port %PORT%

set /a TRIES=0
:wait_proxy
powershell -NoProfile -Command "Start-Sleep -Milliseconds 800"
"%PROBEPY%" "%SCRIPT%" --port %PORT% --probe-only
if not errorlevel 3 goto probe_ready_or_failed
set /a TRIES+=1
if %TRIES% lss 15 goto wait_proxy
echo [launcher] warning: the proxy did not bind port %PORT% within 15s.
goto open_window

:probe_ready_or_failed
if not errorlevel 1 goto proxy_running
echo [ERROR] port %PORT% is occupied by a non-WorkBuddy service or a wedged instance.
goto probe_failed

:proxy_running
echo [launcher] WorkBuddy proxy is healthy on port %PORT%.

:open_window
if /i "%WORKBUDDY_LAUNCH_SKIP_WINDOW%"=="1" goto done
call :find_node
if not defined NODE goto no_node
if not defined GW call :find_gateway
if not defined GW goto no_gateway
echo [launcher] opening the workbench window from %GW%
pushd "%GW%"
"%NODE%" scripts\open-workbench.mjs
if errorlevel 1 goto window_failed
popd
goto done

:done
echo [launcher] done.
exit /b 0

rem ===========================================================
rem  Python lookup: explicit override, bundled runtime, venv,
rem  then PATH. pythonw is preferred so no console window stays
rem  open for the lifetime of the service.
rem ===========================================================
:find_python
set "PYW="
if defined WORKBUDDY_PYTHON goto fp_env
if exist "%HERE%python\pythonw.exe" goto fp_bundled
if exist "%HERE%.venv\Scripts\pythonw.exe" goto fp_venv
where pythonw >nul 2>nul
if not errorlevel 1 goto fp_path_pyw
where python >nul 2>nul
if not errorlevel 1 goto fp_path_python
where py >nul 2>nul
if not errorlevel 1 goto fp_path_py
goto :eof

:fp_env
set "PYW=%WORKBUDDY_PYTHON%"
goto :eof

:fp_bundled
set "PYW=%HERE%python\pythonw.exe"
goto :eof

:fp_venv
set "PYW=%HERE%.venv\Scripts\pythonw.exe"
goto :eof

:fp_path_pyw
set "PYW=pythonw"
goto :eof

:fp_path_python
set "PYW=python"
goto :eof

:fp_path_py
set "PYW=py"
goto :eof

rem ===========================================================
rem  Console Python for --probe-only. The launcher prefers
rem  pythonw for the long-running service, but pythonw has no
rem  usable stdout/stderr; the probe needs a console interpreter.
rem ===========================================================
:find_probe_python
set "PROBEPY="
if defined WORKBUDDY_PROBE_PYTHON goto fpp_env
if defined WORKBUDDY_PYTHON goto fpp_wb_env
if exist "%HERE%python\python.exe" goto fpp_bundled
if exist "%HERE%.venv\Scripts\python.exe" goto fpp_venv
where python >nul 2>nul
if not errorlevel 1 goto fpp_path
where py >nul 2>nul
if not errorlevel 1 goto fpp_py
goto :eof

:fpp_env
set "PROBEPY=%WORKBUDDY_PROBE_PYTHON%"
goto :eof

:fpp_wb_env
set "PROBEPY=%WORKBUDDY_PYTHON%"
goto :eof

:fpp_bundled
set "PROBEPY=%HERE%python\python.exe"
goto :eof

:fpp_venv
set "PROBEPY=%HERE%.venv\Scripts\python.exe"
goto :eof

:fpp_path
set "PROBEPY=python"
goto :eof

:fpp_py
set "PROBEPY=py"
goto :eof

rem ===========================================================
rem  Node lookup: explicit override, then PATH.
rem ===========================================================
:find_node
set "NODE=%WORKBUDDY_NODE%"
if defined NODE goto :eof
where node >nul 2>nul
if errorlevel 1 goto :eof
for /f "delims=" %%N in ('where node') do call :take_node "%%~N"
goto :eof

:take_node
if defined NODE goto :eof
set "NODE=%~1"
goto :eof

rem ===========================================================
rem  Gateway lookup: newest installed workbench that ships
rem  desktop\app\WorkBuddy.Workbench.exe under %LOCALAPPDATA%\Programs.
rem  The result is written to a temp file and read back, so no
rem  pipeline parentheses reach the cmd.exe parser.
rem ===========================================================
:find_gateway
set "GW=%WORKBUDDY_GATEWAY_DIR%"
if defined GW goto gw_verify
if not defined LOCALAPPDATA goto :eof
set "GWTMP=%TEMP%\wb-gateway-%RANDOM%.txt"
powershell -NoProfile -Command "$root = Join-Path $env:LOCALAPPDATA 'Programs'; $hit = Get-ChildItem $root -Directory -Filter 'WorkBuddy*' -ErrorAction SilentlyContinue | ForEach-Object { Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue } | ForEach-Object { Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue } | Where-Object { Test-Path (Join-Path $_.FullName 'desktop\app\WorkBuddy.Workbench.exe') } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($hit) { $hit.FullName } | Out-File -Encoding ascii '%GWTMP%'"
if not exist "%GWTMP%" goto :eof
set /p GW=<"%GWTMP%"
del "%GWTMP%" >nul 2>nul
if not defined GW goto :eof

:gw_verify
if exist "%GW%\desktop\app\WorkBuddy.Workbench.exe" goto :eof
if exist "%GW%\gateway\desktop\app\WorkBuddy.Workbench.exe" set "GW=%GW%\gateway"
goto :eof

rem ===========================================================
rem  Error paths
rem ===========================================================
:no_script
echo [ERROR] wb_proxy.py not found next to this script.
echo         expected: %SCRIPT%
pause
exit /b 1

:no_python
echo [ERROR] No usable Python found for wb_proxy.py.
echo.
echo Options:
echo   1. Use the packaged zip, which already contains python\
echo   2. Install Python 3.9+ from https://www.python.org/downloads/
echo      (tick "Add python.exe to PATH" during setup)
echo   3. Set WORKBUDDY_PYTHON to a full path, e.g.
echo        set "WORKBUDDY_PYTHON=C:\Python312\python.exe"
pause
exit /b 1

:no_probe_python
echo [ERROR] No console Python found for the health probe.
echo         The launcher needs python.exe (not pythonw.exe) to tell
echo         a healthy proxy from a wedged or foreign listener.
echo.
echo Options:
echo   1. Use the packaged zip, which already contains python\
echo   2. Set WORKBUDDY_PROBE_PYTHON to a console python.exe
pause
exit /b 1

:probe_failed
echo [ERROR] Port %PORT% is already in use, but it is not a healthy
echo         WorkBuddy proxy (foreign service or a wedged instance).
echo         Run restart-wb-proxy.bat to stop the old instance, or
echo         choose another port: start-workbuddy-all.bat 8789
pause
exit /b 1

:no_node
echo [ERROR] No usable Node.js found for the workbench window.
echo         Install Node 18+ or set WORKBUDDY_NODE to node.exe.
pause
exit /b 1

:no_gateway
echo [ERROR] No installed WorkBuddy workbench gateway found.
echo         Set WORKBUDDY_GATEWAY_DIR to the folder that holds
echo         desktop\app\WorkBuddy.Workbench.exe.
pause
exit /b 1

:window_failed
popd
echo [ERROR] scripts\open-workbench.mjs failed in %GW%.
pause
exit /b 1
