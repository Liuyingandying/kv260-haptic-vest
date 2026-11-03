@echo off
setlocal
if defined PYTHON_EXE goto explicit
where python >nul 2>&1
if not errorlevel 1 goto pythonpath
where py >nul 2>&1
if not errorlevel 1 goto pylauncher
if exist "D:\03_Runtimes\Conda\python.exe" goto legacy
echo Python 3 missing. Set PYTHON_EXE or put python on PATH.
exit /b 2
:explicit
"%PYTHON_EXE%" -B -X utf8 "%~dp0board_20261103\scripts\open_board.py"
exit /b %errorlevel%
:pythonpath
python -B -X utf8 "%~dp0board_20261103\scripts\open_board.py"
exit /b %errorlevel%
:pylauncher
py -3 -B -X utf8 "%~dp0board_20261103\scripts\open_board.py"
exit /b %errorlevel%
:legacy
"D:\03_Runtimes\Conda\python.exe" -B -X utf8 "%~dp0board_20261103\scripts\open_board.py"
exit /b %errorlevel%
