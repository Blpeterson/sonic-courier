"""Command-line interface for Sonic Courier."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from . import __version__
from .audio import (
    DEFAULT_TONE_PROFILE,
    TONE_PROFILES,
    ToneProfile,
    read_wav,
    resolve_tone_profile,
    write_wav,
)
from .live import (
    LiveAudioError,
    inspect_devices,
    negotiate_receiver,
    negotiate_sender,
    play,
    record_transmission,
)
from .protocol import ProtocolError, build_frame, demodulate, modulate


def _safe_output(base: Path, filename: str, *, exact: bool = False) -> Path:
    if exact:
        base.parent.mkdir(parents=True, exist_ok=True)
        return base
    if base.exists() and base.is_dir() or not base.suffix:
        base.mkdir(parents=True, exist_ok=True)
        return base / Path(filename).name
    base.parent.mkdir(parents=True, exist_ok=True)
    return base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sonic-courier",
        description="Transfer any file using relative 16-tone high-frequency FSK.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="verify the installation and live audio devices")

    encode = commands.add_parser("encode", help="encode a file as a PCM WAV transmission")
    encode.add_argument("input", type=Path, help="file to transmit")
    encode.add_argument("-o", "--output", type=Path, help="output WAV path")
    encode.add_argument("--play", action="store_true", help="play the transmission after encoding")
    encode.add_argument(
        "--profile",
        choices=TONE_PROFILES,
        default=DEFAULT_TONE_PROFILE.name,
        help="tone range to use (default: %(default)s)",
    )

    send = commands.add_parser("send", help="encode and play a file through the default speaker")
    send.add_argument("input", type=Path, help="file to transmit")
    send.add_argument("--save", type=Path, help="also save the generated WAV")
    send_transport = send.add_mutually_exclusive_group()
    send_transport.add_argument(
        "--profile",
        choices=TONE_PROFILES,
        help="use a fixed tone range instead of negotiating automatically",
    )
    send_transport.add_argument(
        "--auto",
        action="store_true",
        help="negotiate a noise-aware frequency range with the receiver",
    )
    send.add_argument(
        "--confirm",
        action="store_true",
        help="encode first, then ask before playing sound",
    )
    send.add_argument("--handshake-timeout", type=float, default=30.0)

    decode = commands.add_parser("decode", help="decode a transmission WAV")
    decode.add_argument("input", type=Path, help="16-bit PCM WAV to decode")
    decode.add_argument("-o", "--output", type=Path, default=Path("received"))

    listen = commands.add_parser("listen", help="receive one transmission from the microphone")
    listen.add_argument("-o", "--output", type=Path, default=Path("received"))
    listen.add_argument(
        "--output-file",
        type=Path,
        help="write to this exact filename instead of using the transmitted filename",
    )
    listen.add_argument(
        "--auto",
        action="store_true",
        help="negotiate a noise-aware frequency range with the sender",
    )
    listen.add_argument("--timeout", type=float, default=30.0)
    return parser


def _encode(path: Path, profile: str | ToneProfile = DEFAULT_TONE_PROFILE):
    if not path.is_file():
        raise FileNotFoundError(f"input file does not exist: {path}")
    data = path.read_bytes()
    return modulate(build_frame(data, path.name), profile=profile), len(data)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            print(f"Sonic Courier: {__version__} [OK]")
            print(f"Python: {sys.version.split()[0]} [OK]")
            print(f"NumPy: {np.__version__} [OK]")
            print(
                f"Default tones: {DEFAULT_TONE_PROFILE.first_data_frequency / 1000:g}-"
                f"{DEFAULT_TONE_PROFILE.last_data_frequency / 1000:g} kHz "
                f"({DEFAULT_TONE_PROFILE.name}) [OK]"
            )
            try:
                devices = inspect_devices()
            except LiveAudioError as exc:
                print(f"Live audio: NOT READY ({exc})")
                print("Install it with: python -m pip install '.[live]'")
                return 1
            print(f"PyAudio/PortAudio: {devices.total} devices [OK]")
            print(f"Input devices: {devices.inputs}")
            print(f"Output devices: {devices.outputs}")
            print(f"Default input: {devices.default_input or 'NONE'}")
            print(f"Default output: {devices.default_output or 'NONE'}")
            if not devices.inputs or not devices.outputs:
                print("Live audio: NOT READY (an input and output device are both required)")
                return 1
            print("Live audio: READY")
            return 0

        if args.command in {"encode", "send"}:
            profile = resolve_tone_profile(args.profile)
            samples, size = _encode(args.input, profile)
            destination = args.output if args.command == "encode" else args.save
            if args.command == "encode" and destination is None:
                destination = args.input.with_suffix(args.input.suffix + ".wav")
            if destination:
                write_wav(destination, samples)
                print(
                    f"Encoded {size} bytes to {destination} "
                    f"({len(samples) / 44_100:.2f} seconds, {profile.name} profile)"
                )
            elif args.command == "send":
                print(
                    f"Encoded {size} bytes "
                    f"({len(samples) / 44_100:.2f} seconds, {profile.name} profile)"
                )
            if args.command == "send" and args.confirm:
                try:
                    confirmed = input("Play this transmission now? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    confirmed = ""
                    print()
                if confirmed not in {"y", "yes"}:
                    print("Playback cancelled; no sound was played.")
                    return 0
            if args.command == "send" and args.auto:
                print("Starting the adaptive acoustic handshake...")
                profile = negotiate_sender(timeout=args.handshake_timeout)
                samples, size = _encode(args.input, profile)
                print(
                    f"Re-encoded {size} bytes for the negotiated band "
                    f"({len(samples) / 44_100:.2f} seconds)."
                )
                time.sleep(1.0)
            if args.command == "send" or args.play:
                print("Playing transmission...")
                play(samples)
            return 0

        if args.command == "decode":
            samples, rate = read_wav(args.input)
            output = args.output
            exact_output = False
            negotiated_profile = None
        else:
            negotiated_profile = (
                negotiate_receiver(timeout=args.timeout) if args.auto else None
            )
            samples = record_transmission(
                timeout=args.timeout,
                profile=negotiated_profile,
            )
            rate = 44_100
            exact_output = args.output_file is not None
            output = args.output_file if exact_output else args.output
        decoded = demodulate(samples, rate, profile=negotiated_profile)
        destination = _safe_output(output, decoded.filename, exact=exact_output)
        destination.write_bytes(decoded.data)
        print(
            f"Recovered {len(decoded.data)} bytes to {destination} "
            f"(calibration {decoded.measured_low:.1f}/{decoded.measured_high:.1f} Hz)"
        )
        return 0
    except (FileNotFoundError, OSError, ValueError, ProtocolError, LiveAudioError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
