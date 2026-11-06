@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "_review_root=%~dp0"
set "_review_tcl=%~dp0tools\create_m2_review.tcl"
set "_vivado=%VIVADO_BAT%"
if defined _vivado goto found_vivado
for /f "delims=" %%I in ('where vivado.bat 2^>nul') do if not defined _vivado set "_vivado=%%I"
:found_vivado
if not defined _vivado (
  echo Vivado was not found. Set VIVADO_BAT to vivado.bat or add its bin directory to PATH.
  pause
  exit /b 2
)
if not exist "%_vivado%" (
  echo VIVADO_BAT does not name an existing file.
  pause
  exit /b 2
)
if not exist "%_review_tcl%" (
  echo The M2 review Tcl is missing. Extract the entire source package first.
  pause
  exit /b 2
)
set "_review_logs=%_review_root%build\m2_review.cache\launch"
if not exist "%_review_logs%" mkdir "%_review_logs%"
set "_review_tag=%RANDOM%_%RANDOM%"
echo Opening M2 source review. No synthesis, implementation or hardware operation is requested.
call "%_vivado%" -mode gui -source "%_review_tcl%" -log "%_review_logs%\vivado_%_review_tag%.log" -journal "%_review_logs%\vivado_%_review_tag%.jou"
set "_review_rc=%ERRORLEVEL%"
if not "%_review_rc%"=="0" pause
exit /b %_review_rc%
