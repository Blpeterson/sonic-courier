# Sonic Courier 📡🎵

[![Tests](https://github.com/Blpeterson/sonic-courier/actions/workflows/tests.yml/badge.svg)](https://github.com/Blpeterson/sonic-courier/actions/workflows/tests.yml)

Send any file through the air as near-ultrasonic sound. Sonic Courier maps each
4-bit nibble to one of 16 tones between 17.25 and 21 kHz, using 17 and 21.25 kHz
reference tones to calibrate the receiver dynamically.

> **Experimental:** many laptop/phone speakers, microphones, audio codecs, and
> noise-cancellation systems attenuate frequencies above 17 kHz. Start with the
> WAV round trip, keep devices close, and never use high playback volume.

## What it includes

- Relative 16-tone FSK at about 100 bit/s (12.5 bytes/s)
- Dynamic calibration for speaker/microphone clock and pitch variation
- Framed transfers with filename, payload length, and CRC-32 integrity checking
- Deterministic WAV encoding/decoding plus optional live speaker/microphone I/O
- Click-reduced tones, leading-silence detection, and actionable decode errors

## Windows quick start (actual `.exe`)

These are complete PowerShell commands. They create an isolated environment and
install a real launcher at `.venv\Scripts\sonic-courier.exe`; you do **not** run
the `sonic_courier` source folder.

### 1. Install Python once

Check whether Python 3.10 or newer is installed:

```powershell
py --version
```

If that command is not found, install Python 3.12 and then open a new PowerShell
window:

```powershell
winget install --exact --id Python.Python.3.12
```

### 2. Download and install Sonic Courier

```powershell
git clone https://github.com/Blpeterson/sonic-courier.git
cd sonic-courier
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install ".[live]"
```

The last command installs Sonic Courier, NumPy, PyAudio, and the Windows
`sonic-courier.exe` command inside the virtual environment.

### 3. Prove the installation is ready

```powershell
.\.venv\Scripts\sonic-courier.exe --version
.\.venv\Scripts\sonic-courier.exe doctor
```

A correct setup ends with `Live audio: READY` and lists a default microphone and
speaker. You can see the exact executable Windows will run with:

```powershell
Get-Item .\.venv\Scripts\sonic-courier.exe
```

If `doctor` says PyAudio is missing, rerun the `pip install ".[live]"` command.
If it shows `Default input: NONE` or `Default output: NONE`, connect or enable a
microphone/speaker in Windows Sound settings and run `doctor` again.

### 4. Verify a file round trip without speakers

This creates a test file, turns it into audio, decodes it, and proves that the
original and recovered SHA-256 hashes match:

```powershell
Set-Content -NoNewline -Path .\hello.txt -Value "Hello from Sonic Courier!"
.\.venv\Scripts\sonic-courier.exe encode .\hello.txt -o .\hello.wav
.\.venv\Scripts\sonic-courier.exe decode .\hello.wav -o .\received
Get-FileHash .\hello.txt
Get-FileHash .\received\hello.txt
```

Both hashes must be identical. This verifies the codec and file-integrity path
without depending on the ultrasonic capabilities of your hardware.

## Linux quick start

Linux does not use `.exe` extensions. The installation creates an executable
launcher at `.venv/bin/sonic-courier`.

### 1. Install system prerequisites

Ubuntu, Debian, Linux Mint, and Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip portaudio19-dev
```

Fedora:

```bash
sudo dnf install -y git python3 python3-devel portaudio-devel gcc
```

Arch Linux and Manjaro:

```bash
sudo pacman -S --needed git python python-pip portaudio base-devel
```

### 2. Download and install Sonic Courier

```bash
git clone https://github.com/Blpeterson/sonic-courier.git
cd sonic-courier
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install ".[live]"
```

### 3. Prove the Linux installation is ready

```bash
./.venv/bin/sonic-courier --version
./.venv/bin/sonic-courier doctor
command -v "$PWD/.venv/bin/sonic-courier"
```

`doctor` should end with `Live audio: READY`. On a headless machine or inside a
container it can report no default devices even though WAV encoding and decoding
work correctly. On a desktop, check that PipeWire or PulseAudio sees the devices:

```bash
wpctl status 2>/dev/null || pactl info
```

If PyAudio fails to build, confirm that the PortAudio development package from
step 1 is installed, then rerun the `pip install ".[live]"` command.

### Fix `Illegal instruction` on an existing Linux installation

NumPy 2.4 raised its minimum x86 CPU requirement. Older physical CPUs and some
virtual machines exit immediately with `Illegal instruction` when that build is
imported. This includes Core 2-era systems such as the Lenovo ThinkPad X61.
Sonic Courier 1.0.1 pins NumPy below that incompatible boundary.

From inside the cloned `sonic-courier` directory, repair an existing virtual
environment with:

```bash
git pull
./.venv/bin/python -m pip install --upgrade --force-reinstall "numpy<2.4" ".[live]"
./.venv/bin/sonic-courier --version
./.venv/bin/sonic-courier doctor
```

The version command should print `sonic-courier 1.0.1`. To confirm the repaired
NumPy version without starting Sonic Courier:

```bash
./.venv/bin/python -c 'import numpy; print(numpy.__version__)'
```

It must print a version lower than `2.4`. If that Python command itself still
exits with `Illegal instruction`, the virtual environment did not replace the
old wheel; remove and recreate `.venv` using the installation steps above.

### 4. Verify a Linux file round trip

```bash
printf %s "Hello from Sonic Courier!" > hello.txt
./.venv/bin/sonic-courier encode ./hello.txt -o ./hello.wav
./.venv/bin/sonic-courier decode ./hello.wav -o ./received
sha256sum ./hello.txt ./received/hello.txt
```

The two SHA-256 hashes must be identical.

## Send a real file through the air

Use two computers for the clearest test. Install Sonic Courier on both, put them
close together, start at low speaker volume, and disable noise suppression or
"audio enhancements" if possible.

On the **receiving computer**, open a terminal in the cloned repository and run
the command for its operating system.

Windows PowerShell:

```powershell
.\.venv\Scripts\sonic-courier.exe listen -o .\received --timeout 60
```

Linux:

```bash
./.venv/bin/sonic-courier listen -o ./received --timeout 60
```

Leave that window listening. On the **sending computer**, run the matching
command.

Windows PowerShell:

```powershell
.\.venv\Scripts\sonic-courier.exe send .\photo.jpg --save .\photo-transmission.wav
```

Linux:

```bash
./.venv/bin/sonic-courier send ./photo.jpg --save ./photo-transmission.wav
```

The sender plays the transmission and also saves the exact WAV for debugging.
The receiver writes `received\photo.jpg` only after its length and CRC-32 pass.

Start with a tiny text file: throughput is about 12.5 bytes per second, and many
consumer speakers/microphones filter frequencies above 17 kHz. A successful WAV
test with a failed over-the-air test usually means the hardware or operating
system audio processing is filtering the signal—not that the installation is
broken.

## Command alternative

Every command also works through the virtual environment's Python if a launcher
is unavailable:

```bash
./.venv/bin/python -m sonic_courier doctor
./.venv/bin/python -m sonic_courier encode photo.jpg -o transmission.wav
./.venv/bin/python -m sonic_courier decode transmission.wav -o received
```

`python -m sonic_courier` asks Python to run the installed package; it is an
alternative entry point, not an instruction to execute a folder.

## Protocol

| Signal | Frequency | Duration |
|---|---:|---:|
| Calibration LOW | 17,000 Hz | 100 ms |
| Calibration HIGH | 21,250 Hz | 100 ms |
| Nibble `0x0`–`0xF` | 17,250–21,000 Hz in 250 Hz steps | 40 ms |

The measured step is `(HIGH - LOW) / 17`. A received data tone is mapped with
`round((peak - LOW) / step) - 1`. Every pair of symbols reconstructs one byte.
The byte stream contains `SC01`, an unsigned 64-bit payload size, CRC-32, UTF-8
filename length and filename, followed by the payload.

At 12.5 bytes/s, this is intended for small payloads and experimentation—not
bulk transfer. A 1 KiB payload takes roughly 82 seconds plus framing overhead.

## Test

```bash
python -m unittest discover -v
```

The suite covers every byte value, framing and CRC failures, frequency
estimation, WAV I/O, clean/noisy/drifted signal round trips, truncation, CLI
behavior, and output integrity.

## License

MIT
