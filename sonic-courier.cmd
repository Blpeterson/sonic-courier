@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "MODE="
set "INPUT_FILE="
set "OUTPUT_FILE="
set "PROFILE=compatible"
set "PROFILE_SET="
set "TIMEOUT=60"
set "TIMEOUT_SET="

:parse
if "%~1"=="" goto validate
set "ARG=%~1"
if /I "%ARG%"=="-h" goto usage_success
if /I "%ARG%"=="--help" goto usage_success
if /I "%ARG%"=="-e" goto set_encode
if /I "%ARG%"=="--encode" goto set_encode
if /I "%ARG%"=="-d" goto set_decode
if /I "%ARG%"=="--decode" goto set_decode
if /I "%ARG%"=="-o" goto set_output
if /I "%ARG%"=="--output" goto set_output
if /I "%ARG%"=="--profile" goto set_profile
if /I "%ARG%"=="--timeout" goto set_timeout
if "%ARG:~0,1%"=="-" goto unknown_option
if defined INPUT_FILE goto too_many_files
set "INPUT_FILE=%~1"
shift
goto parse

:set_encode
if defined MODE if /I not "%MODE%"=="encode" goto conflicting_modes
set "MODE=encode"
shift
goto parse

:set_decode
if defined MODE if /I not "%MODE%"=="decode" goto conflicting_modes
set "MODE=decode"
shift
goto parse

:set_output
if "%~2"=="" goto output_value_missing
set "OUTPUT_FILE=%~2"
shift
shift
goto parse

:set_profile
if "%~2"=="" goto profile_value_missing
set "PROFILE=%~2"
set "PROFILE_SET=1"
shift
shift
goto parse

:set_timeout
if "%~2"=="" goto timeout_value_missing
set "TIMEOUT=%~2"
set "TIMEOUT_SET=1"
shift
shift
goto parse

:validate
if not defined MODE goto mode_missing
if /I "%PROFILE%"=="compatible" goto profile_compatible
if /I "%PROFILE%"=="ultrasonic" goto profile_ultrasonic
echo error: --profile must be compatible or ultrasonic 1>&2
goto usage_error

:profile_compatible
set "PROFILE=compatible"
goto validate_mode

:profile_ultrasonic
set "PROFILE=ultrasonic"

:validate_mode
if /I "%MODE%"=="encode" goto validate_encode
goto validate_decode

:validate_encode
if not defined INPUT_FILE goto encode_file_missing
if defined OUTPUT_FILE goto encode_output_invalid
if defined TIMEOUT_SET goto encode_timeout_invalid
goto find_runner

:validate_decode
if defined INPUT_FILE goto decode_file_invalid
if not defined OUTPUT_FILE goto decode_output_missing
if defined PROFILE_SET goto decode_profile_invalid
goto find_runner

:find_runner
set "RUNNER="
set "RUNNER_IS_PYTHON="
if defined SONIC_COURIER_EXECUTABLE goto use_override
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "VENV_LAUNCHER=%SCRIPT_DIR%.venv\Scripts\sonic-courier.exe"
if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" goto bootstrap_environment
"%SCRIPT_DIR%.venv\Scripts\python.exe" -c "import numpy, pyaudio" >nul 2>nul
if errorlevel 1 goto bootstrap_environment
if not exist "%VENV_LAUNCHER%" goto install_project
"%VENV_LAUNCHER%" send --help 2>nul | findstr /C:"--confirm" >nul
if errorlevel 1 goto install_project
"%VENV_LAUNCHER%" send --help 2>nul | findstr /C:"--auto" >nul
if errorlevel 1 goto install_project
"%VENV_LAUNCHER%" listen --help 2>nul | findstr /C:"--output-file" >nul
if errorlevel 1 goto install_project
"%VENV_LAUNCHER%" listen --help 2>nul | findstr /C:"--auto" >nul
if errorlevel 1 goto install_project
goto use_local_launcher

:use_override
set "RUNNER=%SONIC_COURIER_EXECUTABLE%"
goto execute

:use_local_launcher
set "RUNNER=%VENV_LAUNCHER%"
goto execute

:bootstrap_environment
echo [setup] Preparing Sonic Courier's local Python environment...
if defined SONIC_COURIER_PYTHON goto try_python_override
where py.exe >nul 2>nul
if not errorlevel 1 goto try_py_launcher
goto try_python3

:try_python_override
"%SONIC_COURIER_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto create_with_python_override
echo error: SONIC_COURIER_PYTHON must point to Python 3.10 or newer 1>&2
exit /b 127

:try_py_launcher
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto create_with_py_launcher

:try_python3
where python3.exe >nul 2>nul
if errorlevel 1 goto try_python
python3.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto create_with_python3

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto report_python_missing
python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto create_with_python
goto report_python_missing

:create_with_python_override
"%SONIC_COURIER_PYTHON%" -m venv --clear "%VENV_DIR%"
if errorlevel 1 goto venv_failed
goto install_project

:create_with_py_launcher
py -3 -m venv --clear "%VENV_DIR%"
if errorlevel 1 goto venv_failed
goto install_project

:create_with_python3
python3.exe -m venv --clear "%VENV_DIR%"
if errorlevel 1 goto venv_failed
goto install_project

:create_with_python
python.exe -m venv --clear "%VENV_DIR%"
if errorlevel 1 goto venv_failed

:install_project
echo [setup] Installing Sonic Courier and live-audio support...
pushd "%SCRIPT_DIR%" >nul
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto install_failed_popd
"%VENV_PYTHON%" -m pip install --editable ".[live]"
if errorlevel 1 goto install_failed_popd
popd
if not exist "%VENV_LAUNCHER%" goto install_failed
"%VENV_PYTHON%" -c "import numpy, pyaudio, sonic_courier, sonic_courier.negotiation" >nul 2>nul
if errorlevel 1 goto install_failed
echo [setup] Sonic Courier is ready.
goto use_local_launcher

:install_failed_popd
popd

:install_failed
echo error: automatic Sonic Courier installation failed 1>&2
exit /b 1

:venv_failed
echo error: could not create the project .venv 1>&2
exit /b 1

:report_python_missing
echo error: Python 3.10 or newer is required. Install it, then run this script again. 1>&2
exit /b 127

:execute
if /I "%MODE%"=="encode" goto execute_encode
goto execute_decode

:execute_encode
if defined PROFILE_SET goto execute_encode_fixed
if defined RUNNER_IS_PYTHON (
    "%RUNNER%" -m sonic_courier send "%INPUT_FILE%" --auto --confirm
) else (
    "%RUNNER%" send "%INPUT_FILE%" --auto --confirm
)
exit /b %ERRORLEVEL%

:execute_encode_fixed
if defined RUNNER_IS_PYTHON (
    "%RUNNER%" -m sonic_courier send "%INPUT_FILE%" --profile "%PROFILE%" --confirm
) else (
    "%RUNNER%" send "%INPUT_FILE%" --profile "%PROFILE%" --confirm
)
exit /b %ERRORLEVEL%

:execute_decode
if defined RUNNER_IS_PYTHON (
    "%RUNNER%" -m sonic_courier listen --auto --output-file "%OUTPUT_FILE%" --timeout "%TIMEOUT%"
) else (
    "%RUNNER%" listen --auto --output-file "%OUTPUT_FILE%" --timeout "%TIMEOUT%"
)
exit /b %ERRORLEVEL%

:unknown_option
echo error: unknown option %ARG% 1>&2
goto usage_error

:too_many_files
echo error: only one input file may be supplied 1>&2
goto usage_error

:conflicting_modes
echo error: choose exactly one of -e or -d 1>&2
goto usage_error

:output_value_missing
echo error: -o requires an output filename 1>&2
goto usage_error

:profile_value_missing
echo error: --profile requires compatible or ultrasonic 1>&2
goto usage_error

:timeout_value_missing
echo error: --timeout requires a number of seconds 1>&2
goto usage_error

:mode_missing
echo error: choose -e to send or -d to receive 1>&2
goto usage_error

:encode_file_missing
echo error: -e requires an input file 1>&2
goto usage_error

:encode_output_invalid
echo error: -o is only used with -d 1>&2
goto usage_error

:encode_timeout_invalid
echo error: --timeout is only used with -d 1>&2
goto usage_error

:decode_file_invalid
echo error: -d does not take an input file 1>&2
goto usage_error

:decode_output_missing
echo error: -d requires -o OUTPUT_FILE 1>&2
goto usage_error

:decode_profile_invalid
echo error: --profile is only used with -e; receiving detects the profile automatically 1>&2
goto usage_error

:usage_error
call :usage 1>&2
exit /b 2

:usage_success
call :usage
exit /b 0

:usage
echo Usage:
echo   %SCRIPT_NAME% -e INPUT_FILE [--profile compatible^|ultrasonic]
echo   %SCRIPT_NAME% INPUT_FILE -e [--profile compatible^|ultrasonic]
echo   %SCRIPT_NAME% -d -o OUTPUT_FILE [--timeout SECONDS]
echo.
echo -e encodes, asks for confirmation, then plays through the default speaker.
echo -d listens on the default microphone and writes the received file.
exit /b 0
