"""Command-line interface for Sonic Courier."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .audio import read_wav, write_wav
from .live import LiveAudioError, play, record_transmission
from .protocol import ProtocolError, build_frame, demodulate, modulate


def _safe_output(base: Path, filename: str) -> Path:
    if base.exists() and base.is_dir() or not base.suffix:
        base.mkdir(parents=True, exist_ok=True)
        return base / Path(filename).name
    base.parent.mkdir(parents=True, exist_ok=True)
    return base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sonic-courier",
        description="Transfer any file using relative 16-tone near-ultrasonic FSK.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    encode = commands.add_parser("encode", help="encode a file as a PCM WAV transmission")
    encode.add_argument("input", type=Path, help="file to transmit")
    encode.add_argument("-o", "--output", type=Path, help="output WAV path")
    encode.add_argument("--play", action="store_true", help="play the transmission after encoding")

    send = commands.add_parser("send", help="encode and play a file through the default speaker")
    send.add_argument("input", type=Path, help="file to transmit")
    send.add_argument("--save", type=Path, help="also save the generated WAV")

    decode = commands.add_parser("decode", help="decode a transmission WAV")
    decode.add_argument("input", type=Path, help="16-bit PCM WAV to decode")
    decode.add_argument("-o", "--output", type=Path, default=Path("received"))

    listen = commands.add_parser("listen", help="receive one transmission from the microphone")
    listen.add_argument("-o", "--output", type=Path, default=Path("received"))
    listen.add_argument("--timeout", type=float, default=30.0)
    return parser


def _encode(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"input file does not exist: {path}")
    data = path.read_bytes()
    return modulate(build_frame(data, path.name)), len(data)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"encode", "send"}:
            samples, size = _encode(args.input)
            destination = args.output if args.command == "encode" else args.save
            if args.command == "encode" and destination is None:
                destination = args.input.with_suffix(args.input.suffix + ".wav")
            if destination:
                write_wav(destination, samples)
                print(f"Encoded {size} bytes to {destination} ({len(samples) / 44_100:.2f} seconds)")
            if args.command == "send" or args.play:
                print("Playing transmission...")
                play(samples)
            return 0

        if args.command == "decode":
            samples, rate = read_wav(args.input)
        else:
            samples, rate = record_transmission(timeout=args.timeout), 44_100
        decoded = demodulate(samples, rate)
        destination = _safe_output(args.output, decoded.filename)
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
