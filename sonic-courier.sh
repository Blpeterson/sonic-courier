#!/bin/sh

set -u

usage() {
    cat <<'EOF'
Usage:
  sonic-courier.sh -e INPUT_FILE [--profile compatible|ultrasonic]
  sonic-courier.sh INPUT_FILE -e [--profile compatible|ultrasonic]
  sonic-courier.sh -d -o OUTPUT_FILE [--timeout SECONDS]

-e encodes, asks for confirmation, then plays through the default speaker.
-d listens on the default microphone and writes the received file.
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

while [ "$#" -gt 0 ]; do
    case $1 in
        -h|--help)
            usage
            exit 0
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

[ -n "$mode" ] || fail 'choose -e to send or -d to receive'
case $profile in
    compatible|ultrasonic) ;;
    *) fail '--profile must be compatible or ultrasonic' ;;
esac

if [ "$mode" = encode ]; then
    [ -n "$input_file" ] || fail '-e requires an input file'
    [ -z "$output_file" ] || fail '-o is only used with -d'
    [ "$timeout_set" = false ] || fail '--timeout is only used with -d'
else
    [ -z "$input_file" ] || fail '-d does not take an input file'
    [ -n "$output_file" ] || fail '-d requires -o OUTPUT_FILE'
    [ "$profile_set" = false ] || fail '--profile is only used with -e; receiving detects it'
fi

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
venv_python=$script_dir/.venv/bin/python
venv_launcher=$script_dir/.venv/bin/sonic-courier

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
    "$venv_python" -m pip install --upgrade pip \
        && (cd "$script_dir" && "$venv_python" -m pip install --editable '.[live]') \
        && "$venv_python" -c 'import numpy, pyaudio, sonic_courier'
}

bootstrap_environment() {
    printf '%s\n' "[setup] Preparing Sonic Courier's local Python environment..."
    if ! find_python; then
        install_system_dependencies || return 1
        find_python || {
            printf '%s\n' 'error: Python 3.10 or newer is required' >&2
            return 127
        }
    fi
    install_system_dependencies || return 1
    "$python_command" -m venv --clear "$script_dir/.venv" || return 1
    install_project || return 1
    printf '%s\n' '[setup] Sonic Courier is ready.'
}

ensure_local_runner() {
    if [ -x "$venv_python" ] \
        && "$venv_python" -c 'import numpy, pyaudio' >/dev/null 2>&1; then
        if [ -x "$venv_launcher" ] \
            && "$venv_launcher" send --help 2>/dev/null | grep -q -- '--confirm' \
            && "$venv_launcher" listen --help 2>/dev/null | grep -q -- '--output-file'; then
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

if [ "$mode" = encode ]; then
    run_courier send "$input_file" --profile "$profile" --confirm
else
    run_courier listen --output-file "$output_file" --timeout "$timeout"
fi
