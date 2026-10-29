# Sonic Courier 📡🎵

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

## Install

Python 3.10 or newer is required.

```bash
python -m pip install .
```

For live speaker and microphone support, install the PortAudio-backed extra:

```bash
python -m pip install ".[live]"
```

PyAudio may require PortAudio development packages on Linux (`portaudio19-dev`)
or Homebrew (`brew install portaudio`) before installation.

## Use

First verify the codec without acoustic hardware:

```bash
sonic-courier encode photo.jpg -o transmission.wav
sonic-courier decode transmission.wav -o received/
```

To transmit through speakers and receive from a microphone, start the listener
first and then send from a second computer or terminal:

```bash
# Receiver
sonic-courier listen -o received/

# Sender
sonic-courier send photo.jpg --save transmission.wav
```

The receiver refuses to write corrupt output when the frame is truncated or its
CRC-32 does not match.

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
