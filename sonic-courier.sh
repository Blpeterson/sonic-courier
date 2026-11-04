#!/bin/sh

set -u

usage() {
    cat <<'EOF'
Usage:
  sonic-courier.sh --setup
  sonic-courier.sh --doctor
  sonic-courier.sh --probe [SECONDS]
  sonic-courier.sh -e INPUT_FILE [--profile compatible|ultrasonic]
  sonic-courier.sh INPUT_FILE -e [--profile compatible|ultrasonic]
  sonic-courier.sh -d -o OUTPUT_FILE [--timeout SECONDS]

-e encodes, asks for permission to play, then waits/retries for a receiver handshake.
--profile fixes the file's tone band; the receiver handshake is still required.
-d listens on the default microphone and writes the received file.
Press q during audio or Python setup tasks to cancel; use Ctrl+C for system packages.
Run without arguments for the mobile-friendly menu.
EOF
}

fail() {
    printf 'error: %s\n' "$1" >&2
    usage >&2
    exit 2
}

mode=
input_file=
output_file=
profile=compatible
profile_set=false
timeout=60
timeout_set=false

exit_if_quit() {
    case $1 in
        q|Q) printf '%s\n' 'Cancelled.'; exit 0 ;;
    esac
}

interactive_menu() {
    printf '\n%s\n' 'Sonic Courier'
    printf '%s\n' 'Press q at a prompt, during audio, or during Python setup to cancel.'
    printf '%s\n' 'Use Ctrl+C during system package installation (sudo may need a password).'
    printf '%s\n' '1) Receive a file (recommended defaults)'
    printf '%s\n' '2) Send a file (wait for receiver handshake)'
    printf '%s\n' '3) Check audio devices'
    printf '%s\n' '4) Test the microphone'
    printf '%s\n' '5) Install or repair Sonic Courier'
    printf '%s\n' '6) Show help'
    printf '%s' 'Choose [1]: '
    IFS= read -r choice || fail 'no menu selection was received'
    choice=${choice:-1}
    case $choice in
        1)
            printf '%s' 'Save received file as [received.bin]: '
            IFS= read -r output_file || fail 'no output filename was received'
            exit_if_quit "$output_file"
            output_file=${output_file:-received.bin}
            printf '%s' 'Listening timeout in seconds [120]: '
            IFS= read -r timeout || fail 'no timeout was received'
            exit_if_quit "$timeout"
            timeout=${timeout:-120}
            mode=decode
            ;;
        2)
            printf '%s' 'File to send: '
            IFS= read -r input_file || fail 'no input filename was received'
            exit_if_quit "$input_file"
            [ -n "$input_file" ] || fail 'a file to send is required'
            printf '%s\n' 'File tone band: 1) Automatic (recommended)  2) Compatible  3) Ultrasonic'
            printf '%s\n' 'Every mode waits/retries for the receiver before sending the file.'
            printf '%s' 'Choose [1]: '
            IFS= read -r frequency_choice || fail 'no frequency mode was received'
            exit_if_quit "$frequency_choice"
            case ${frequency_choice:-1} in
                1) ;;
                2) profile=compatible; profile_set=true ;;
                3) profile=ultrasonic; profile_set=true ;;
                *) fail 'frequency mode must be 1, 2, or 3' ;;
            esac
            mode=encode
            ;;
        3) mode=doctor ;;
        4)
            printf '%s' 'Microphone test length in seconds [5]: '
            IFS= read -r timeout || fail 'no test length was received'
            exit_if_quit "$timeout"
            timeout=${timeout:-5}
            mode=probe
            ;;
        5) mode=setup ;;
        6) usage; exit 0 ;;
        q|Q) exit 0 ;;
        *) fail 'menu choice must be 1 through 6' ;;
    esac
}

[ "$#" -gt 0 ] || interactive_menu

while [ "$#" -gt 0 ]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        --setup)
            [ -z "$mode" ] || fail '--setup cannot be combined with another mode'
            mode=setup
            shift
            ;;
        --doctor)
            [ -z "$mode" ] || fail '--doctor cannot be combined with another mode'
            mode=doctor
            shift
            ;;
        --probe)
            [ -z "$mode" ] || fail '--probe cannot be combined with another mode'
            mode=probe
            shift
            if [ "$#" -gt 0 ]; then
                case $1 in
                    ''|*[!0-9.]*) ;;
                    *) timeout=$1; shift ;;
                esac
            fi
            ;;
        -e|--encode)
            [ "$mode" != decode ] || fail 'choose exactly one of -e or -d'
            mode=encode
            shift
            ;;
        -d|--decode)
            [ "$mode" != encode ] || fail 'choose exactly one of -e or -d'
            mode=decode
            shift
            ;;
        -o|--output)
            [ "$#" -ge 2 ] || fail '-o requires an output filename'
            output_file=$2
            shift 2
            ;;
        --profile)
            [ "$#" -ge 2 ] || fail '--profile requires compatible or ultrasonic'
            profile=$2
            profile_set=true
            shift 2
            ;;
        --timeout)
            [ "$#" -ge 2 ] || fail '--timeout requires a number of seconds'
            timeout=$2
            timeout_set=true
            shift 2
            ;;
        --)
            shift
            [ "$#" -eq 1 ] || fail '-- must be followed by exactly one input file'
            [ -z "$input_file" ] || fail 'only one input file may be supplied'
            input_file=$1
            shift
            ;;
        -*)
            fail "unknown option $1"
            ;;
        *)
            [ -z "$input_file" ] || fail 'only one input file may be supplied'
            input_file=$1
            shift
            ;;
    esac
done

[ -n "$mode" ] || fail 'choose -e, -d, --setup, --doctor, or --probe'
case $profile in
    compatible|ultrasonic) ;;
    *) fail '--profile must be compatible or ultrasonic' ;;
esac

if [ "$mode" = encode ]; then
    [ -n "$input_file" ] || fail '-e requires an input file'
    [ -z "$output_file" ] || fail '-o is only used with -d'
    [ "$timeout_set" = false ] || fail '--timeout is only used with -d'
elif [ "$mode" = decode ]; then
    [ -z "$input_file" ] || fail '-d does not take an input file'
    [ -n "$output_file" ] || fail '-d requires -o OUTPUT_FILE'
    [ "$profile_set" = false ] || fail '--profile is only used with -e; receiving detects it'
else
    [ -z "$input_file" ] || fail "$mode mode does not take a file"
    [ -z "$output_file" ] || fail "-o cannot be used with $mode mode"
    [ "$profile_set" = false ] || fail "--profile cannot be used with $mode mode"
    [ "$timeout_set" = false ] || fail "--timeout cannot be used with $mode mode"
fi

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
venv_python=$script_dir/.venv/bin/python
venv_launcher=$script_dir/.venv/bin/sonic-courier
cancellation_script=$script_dir/sonic_courier/cancellation.py

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        printf '%s\n' 'error: sudo or root access is required for Linux audio packages' >&2
        return 1
    fi
}

install_system_dependencies() {
    if command -v apt-get >/dev/null 2>&1; then
        if command -v dpkg >/dev/null 2>&1 \
            && dpkg -s python3-venv portaudio19-dev >/dev/null 2>&1; then
            return 0
        fi
        printf '%s\n' '[setup] Installing Python venv and PortAudio packages...'
        run_as_root apt-get update \
            && run_as_root apt-get install -y python3-venv python3-pip portaudio19-dev
    elif command -v dnf >/dev/null 2>&1; then
        if command -v rpm >/dev/null 2>&1 \
            && rpm -q python3-devel portaudio-devel gcc >/dev/null 2>&1; then
            return 0
        fi
        printf '%s\n' '[setup] Installing Python and PortAudio build packages...'
        run_as_root dnf install -y python3 python3-devel portaudio-devel gcc
    elif command -v pacman >/dev/null 2>&1; then
        printf '%s\n' '[setup] Ensuring Python and PortAudio packages are installed...'
        run_as_root pacman -S --needed --noconfirm python python-pip portaudio base-devel
    else
        return 0
    fi
}

find_python() {
    if command -v python3 >/dev/null 2>&1; then
        python_command=$(command -v python3)
    elif command -v python >/dev/null 2>&1; then
        python_command=$(command -v python)
    else
        return 1
    fi
    "$python_command" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
}

install_project() {
    printf '%s\n' '[setup] Installing Sonic Courier and live-audio support...'
    "$venv_python" "$cancellation_script" "$venv_python" -m pip install --upgrade pip \
        || return $?
    (cd "$script_dir" \
        && "$venv_python" "$cancellation_script" "$venv_python" \
            -m pip install --editable '.[live]') || return $?
    "$venv_python" -c 'import numpy, pyaudio, sonic_courier'
}

bootstrap_environment() {
    printf '%s\n' "[setup] Preparing Sonic Courier's local Python environment..."
    if ! find_python; then
        install_system_dependencies || return $?
        find_python || {
            printf '%s\n' 'error: Python 3.10 or newer is required' >&2
            return 127
        }
    fi
    install_system_dependencies || return $?
    "$python_command" "$cancellation_script" "$python_command" \
        -m venv --clear "$script_dir/.venv" || return $?
    install_project || return $?
    printf '%s\n' '[setup] Sonic Courier is ready.'
}

ensure_local_runner() {
    if [ -x "$venv_python" ] \
        && "$venv_python" -c 'import numpy, pyaudio' >/dev/null 2>&1; then
        if ! "$venv_python" -c \
            'import inspect; from sonic_courier.live import negotiate_sender; assert "profile" in inspect.signature(negotiate_sender).parameters' \
            >/dev/null 2>&1; then
            install_project
            return $?
        fi
        if [ -x "$venv_launcher" ] \
            && "$venv_launcher" send --help 2>/dev/null | grep -q -- '--confirm' \
            && "$venv_launcher" send --help 2>/dev/null | grep -q -- '--auto' \
            && "$venv_launcher" listen --help 2>/dev/null | grep -q -- '--output-file' \
            && "$venv_launcher" listen --help 2>/dev/null | grep -q -- '--auto'; then
            return 0
        fi
        install_project
        return $?
    fi
    bootstrap_environment
}

run_courier() {
    if [ -n "${SONIC_COURIER_EXECUTABLE:-}" ]; then
        "$SONIC_COURIER_EXECUTABLE" "$@"
    else
        ensure_local_runner || return $?
        "$venv_launcher" "$@"
    fi
}

if [ "$mode" = setup ]; then
    bootstrap_environment
elif [ "$mode" = doctor ]; then
    run_courier doctor
elif [ "$mode" = probe ]; then
    run_courier probe --seconds "$timeout"
elif [ "$mode" = encode ]; then
    if [ "$profile_set" = true ]; then
        run_courier send "$input_file" --auto --profile "$profile" --confirm
    else
        run_courier send "$input_file" --auto --confirm
    fi
else
    run_courier listen --auto --output-file "$output_file" --timeout "$timeout"
fi
