@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_NAME=%~nx0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "VENV_LAUNCHER=%SCRIPT_DIR%.venv\Scripts\sonic-courier.exe"
set "CANCELLATION_SCRIPT=%SCRIPT_DIR%sonic_courier\cancellation.py"
set "MODE="
set "INPUT_FILE="
set "OUTPUT_FILE="
set "PROFILE=compatible"
set "PROFILE_SET="
set "TIMEOUT=60"
set "TIMEOUT_SET="

if "%~1"=="" goto interactive_menu

:parse
if "%~1"=="" goto validate
set "ARG=%~1"
if /I "%ARG%"=="-h" goto usage_success
if /I "%ARG%"=="--help" goto usage_success
if /I "%ARG%"=="--setup" goto set_setup
if /I "%ARG%"=="--doctor" goto set_doctor
if /I "%ARG%"=="--probe" goto set_probe
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

:set_setup
set "MODE=setup"
shift
goto parse

:set_doctor
set "MODE=doctor"
shift
goto parse

:set_probe
set "MODE=probe"
shift
if "%~1"=="" goto parse
set "TIMEOUT=%~1"
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
if /I "%MODE%"=="doctor" goto validate_diagnostic
if /I "%MODE%"=="probe" goto validate_diagnostic
if /I "%MODE%"=="setup" goto bootstrap_environment
goto validate_decode

:validate_diagnostic
if defined INPUT_FILE goto diagnostic_file_invalid
if defined OUTPUT_FILE goto diagnostic_file_invalid
if defined PROFILE_SET goto diagnostic_file_invalid
goto find_runner

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
"%VENV_PYTHON%" -c "import inspect; from sonic_courier.live import negotiate_sender; assert 'profile' in inspect.signature(negotiate_sender).parameters" >nul 2>nul
if errorlevel 1 goto install_project
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
"%SONIC_COURIER_PYTHON%" "%CANCELLATION_SCRIPT%" "%SONIC_COURIER_PYTHON%" -m venv --clear "%VENV_DIR%"
if "%ERRORLEVEL%"=="130" exit /b 130
if errorlevel 1 goto venv_failed
goto install_project

:create_with_py_launcher
py -3 "%CANCELLATION_SCRIPT%" py -3 -m venv --clear "%VENV_DIR%"
if "%ERRORLEVEL%"=="130" exit /b 130
if errorlevel 1 goto venv_failed
goto install_project

:create_with_python3
python3.exe "%CANCELLATION_SCRIPT%" python3.exe -m venv --clear "%VENV_DIR%"
if "%ERRORLEVEL%"=="130" exit /b 130
if errorlevel 1 goto venv_failed
goto install_project

:create_with_python
python.exe "%CANCELLATION_SCRIPT%" python.exe -m venv --clear "%VENV_DIR%"
if "%ERRORLEVEL%"=="130" exit /b 130
if errorlevel 1 goto venv_failed

:install_project
echo [setup] Installing Sonic Courier and live-audio support...
pushd "%SCRIPT_DIR%" >nul
"%VENV_PYTHON%" "%CANCELLATION_SCRIPT%" "%VENV_PYTHON%" -m pip install --upgrade pip
if "%ERRORLEVEL%"=="130" goto install_cancelled_popd
if errorlevel 1 goto install_failed_popd
"%VENV_PYTHON%" "%CANCELLATION_SCRIPT%" "%VENV_PYTHON%" -m pip install --editable ".[live]"
if "%ERRORLEVEL%"=="130" goto install_cancelled_popd
if errorlevel 1 goto install_failed_popd
popd
if not exist "%VENV_LAUNCHER%" goto install_failed
"%VENV_PYTHON%" -c "import numpy, pyaudio, sonic_courier, sonic_courier.negotiation" >nul 2>nul
if errorlevel 1 goto install_failed
echo [setup] Sonic Courier is ready.
goto use_local_launcher

:install_failed_popd
popd
goto install_failed

:install_cancelled_popd
popd
exit /b 130

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
if /I "%MODE%"=="doctor" goto execute_doctor
if /I "%MODE%"=="probe" goto execute_probe
if /I "%MODE%"=="setup" goto execute_setup
goto execute_decode

:execute_doctor
if defined RUNNER_IS_PYTHON (
    "%RUNNER%" -m sonic_courier doctor
) else (
    "%RUNNER%" doctor
)
exit /b %ERRORLEVEL%

:execute_probe
if defined RUNNER_IS_PYTHON (
    "%RUNNER%" -m sonic_courier probe --seconds "%TIMEOUT%"
) else (
    "%RUNNER%" probe --seconds "%TIMEOUT%"
)
exit /b %ERRORLEVEL%

:execute_setup
echo [setup] Sonic Courier is ready.
exit /b 0

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
    "%RUNNER%" -m sonic_courier send "%INPUT_FILE%" --auto --profile "%PROFILE%" --confirm
) else (
    "%RUNNER%" send "%INPUT_FILE%" --auto --profile "%PROFILE%" --confirm
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

:diagnostic_file_invalid
echo error: diagnostic and setup modes cannot be combined with file options 1>&2
goto usage_error

:interactive_menu
echo.
echo Sonic Courier
echo Press q at a prompt, during audio, or during Python setup to cancel.
echo 1^) Receive a file ^(recommended defaults^)
echo 2^) Send a file ^(wait for receiver handshake^)
echo 3^) Check audio devices
echo 4^) Test the microphone
echo 5^) Install or repair Sonic Courier
echo 6^) Show help
set "MENU_CHOICE="
set /p "MENU_CHOICE=Choose [1]: "
if not defined MENU_CHOICE set "MENU_CHOICE=1"
if "%MENU_CHOICE%"=="1" goto interactive_receive
if "%MENU_CHOICE%"=="2" goto interactive_send
if "%MENU_CHOICE%"=="3" (set "MODE=doctor" & goto validate)
if "%MENU_CHOICE%"=="4" goto interactive_probe
if "%MENU_CHOICE%"=="5" (set "MODE=setup" & goto validate)
if "%MENU_CHOICE%"=="6" goto usage_success
if /I "%MENU_CHOICE%"=="q" exit /b 0
echo error: menu choice must be 1 through 6 1>&2
exit /b 2

:interactive_receive
set /p "OUTPUT_FILE=Save received file as [received.bin]: "
if /I "%OUTPUT_FILE%"=="q" exit /b 0
if not defined OUTPUT_FILE set "OUTPUT_FILE=received.bin"
set "TIMEOUT="
set /p "TIMEOUT=Listening timeout in seconds [120]: "
if /I "%TIMEOUT%"=="q" exit /b 0
if not defined TIMEOUT set "TIMEOUT=120"
set "MODE=decode"
goto validate

:interactive_send
set /p "INPUT_FILE=File to send: "
if /I "%INPUT_FILE%"=="q" exit /b 0
if not defined INPUT_FILE goto encode_file_missing
echo File tone band: 1^) Automatic ^(recommended^)  2^) Compatible  3^) Ultrasonic
echo Every mode waits/retries for the receiver before sending the file.
set "FREQUENCY_CHOICE="
set /p "FREQUENCY_CHOICE=Choose [1]: "
if /I "%FREQUENCY_CHOICE%"=="q" exit /b 0
if not defined FREQUENCY_CHOICE set "FREQUENCY_CHOICE=1"
if "%FREQUENCY_CHOICE%"=="2" (set "PROFILE=compatible" & set "PROFILE_SET=1")
if "%FREQUENCY_CHOICE%"=="3" (set "PROFILE=ultrasonic" & set "PROFILE_SET=1")
if not "%FREQUENCY_CHOICE%"=="1" if not "%FREQUENCY_CHOICE%"=="2" if not "%FREQUENCY_CHOICE%"=="3" goto profile_value_missing
set "MODE=encode"
goto validate

:interactive_probe
set "TIMEOUT="
set /p "TIMEOUT=Microphone test length in seconds [5]: "
if /I "%TIMEOUT%"=="q" exit /b 0
if not defined TIMEOUT set "TIMEOUT=5"
set "MODE=probe"
goto validate

:usage_error
call :usage 1>&2
exit /b 2

:usage_success
call :usage
exit /b 0

:usage
echo Usage:
echo   %SCRIPT_NAME%                         ^(interactive menu^)
echo   %SCRIPT_NAME% --setup^|--doctor^|--probe [SECONDS]
echo   %SCRIPT_NAME% -e INPUT_FILE [--profile compatible^|ultrasonic]
echo   %SCRIPT_NAME% INPUT_FILE -e [--profile compatible^|ultrasonic]
echo   %SCRIPT_NAME% -d -o OUTPUT_FILE [--timeout SECONDS]
echo.
echo -e encodes, asks for permission to play, then waits/retries for a receiver handshake.
echo --profile fixes the file's tone band; the receiver handshake is still required.
echo -d listens on the default microphone and writes the received file.
echo Press q during audio or Python setup tasks to cancel; Ctrl+C also cancels.
exit /b 0
