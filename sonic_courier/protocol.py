"""Binary framing and relative 16-tone FSK modulation/demodulation."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .audio import (
    DEFAULT_TONE_PROFILE,
    PREAMBLE_DURATION,
    SAMPLE_RATE,
    SYMBOL_DURATION,
    ToneProfile,
    find_dominant_frequency,
    generate_sine_wave,
    match_tone_profile,
    resolve_tone_profile,
)

MAGIC = b"SC01"
HEADER = struct.Struct(">4sQIH")  # magic, payload length, CRC-32, UTF-8 filename length
MAX_FILENAME_BYTES = 255
MAX_PAYLOAD_BYTES = 1 << 34


class ProtocolError(ValueError):
    """Raised when a transmission is incomplete, invalid, or corrupt."""


@dataclass(frozen=True)
class DecodedFile:
    filename: str
    data: bytes
    measured_low: float
    measured_high: float


def build_frame(data: bytes, filename: str) -> bytes:
    """Build a self-describing frame with length and CRC-32 integrity data."""
    name = Path(filename).name.encode("utf-8")
    if not name:
        name = b"received.bin"
    if len(name) > MAX_FILENAME_BYTES:
        raise ValueError(f"UTF-8 filename is longer than {MAX_FILENAME_BYTES} bytes")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload is too large")
    checksum = zlib.crc32(data) & 0xFFFFFFFF
    return HEADER.pack(MAGIC, len(data), checksum, len(name)) + name + data


def parse_frame(frame: bytes) -> tuple[str, bytes]:
    """Validate and unpack a binary frame."""
    if len(frame) < HEADER.size:
        raise ProtocolError("transmission ended before the frame header")
    magic, payload_length, checksum, name_length = HEADER.unpack_from(frame)
    if magic != MAGIC:
        raise ProtocolError("frame magic mismatch; symbol timing or calibration may be wrong")
    if payload_length > MAX_PAYLOAD_BYTES:
        raise ProtocolError("declared payload is unreasonably large")
    expected = HEADER.size + name_length + payload_length
    if len(frame) < expected:
        raise ProtocolError(f"incomplete frame: expected {expected} bytes, received {len(frame)}")
    name_bytes = frame[HEADER.size : HEADER.size + name_length]
    try:
        filename = name_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid UTF-8 filename in frame") from exc
    payload = frame[HEADER.size + name_length : expected]
    actual_checksum = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_checksum != checksum:
        raise ProtocolError(
            f"CRC-32 mismatch: expected {checksum:08x}, calculated {actual_checksum:08x}"
        )
    return Path(filename).name or "received.bin", payload


def bytes_to_nibbles(data: bytes) -> list[int]:
    return [nibble for value in data for nibble in (value >> 4, value & 0x0F)]


def nibbles_to_bytes(nibbles: list[int]) -> bytes:
    return bytes(
        (nibbles[index] << 4) | nibbles[index + 1]
        for index in range(0, len(nibbles) - 1, 2)
    )


def modulate(
    frame: bytes,
    sample_rate: int = SAMPLE_RATE,
    *,
    profile: str | ToneProfile = DEFAULT_TONE_PROFILE,
) -> NDArray[np.int16]:
    """Modulate a frame with a LOW/HIGH calibration preamble and 16 FSK tones."""
    tone_profile = resolve_tone_profile(profile)
    parts = [
        generate_sine_wave(tone_profile.base_frequency, PREAMBLE_DURATION, sample_rate),
        generate_sine_wave(tone_profile.high_preamble_frequency, PREAMBLE_DURATION, sample_rate),
    ]
    parts.extend(
        generate_sine_wave(
            tone_profile.base_frequency + (nibble + 1) * tone_profile.step_frequency,
            SYMBOL_DURATION,
            sample_rate,
        )
        for nibble in bytes_to_nibbles(frame)
    )
    return np.concatenate(parts)


def _find_transmission_start(
    samples: NDArray[np.int16], sample_rate: int
) -> tuple[int, ToneProfile]:
    """Find a supported LOW preamble, ignoring leading silence and noise."""
    window_size = max(128, round(sample_rate * 0.020))
    hop = max(1, window_size // 2)
    upper = min(samples.size - window_size + 1, round(sample_rate * 30.0))
    for offset in range(0, max(0, upper), hop):
        chunk = samples[offset : offset + window_size]
        if np.sqrt(np.mean(chunk.astype(np.float64) ** 2)) < 150.0:
            continue
        peak = find_dominant_frequency(chunk, sample_rate)
        profile = match_tone_profile(peak)
        if profile is not None:
            # Refine onset using a short-time RMS threshold around this candidate.
            search_start = max(0, offset - window_size)
            absolute = np.abs(samples[search_start : offset + window_size].astype(np.int32))
            threshold = max(100, int(np.max(absolute) * 0.03))
            hits = np.flatnonzero(absolute >= threshold)
            start = search_start + int(hits[0]) if hits.size else offset
            return start, profile
    raise ProtocolError("no supported calibration preamble found (expected 12 kHz or 17 kHz)")


def _calibration_frequency(
    samples: NDArray[np.int16], start: int, duration: float, sample_rate: int
) -> float:
    margin = round(sample_rate * 0.020)
    tone_samples = round(sample_rate * duration)
    chunk = samples[start + margin : start + tone_samples - margin]
    if chunk.size < 128:
        raise ProtocolError("transmission ended during calibration preamble")
    return find_dominant_frequency(chunk, sample_rate)


def demodulate(samples: NDArray[np.int16], sample_rate: int = SAMPLE_RATE) -> DecodedFile:
    """Recover and validate one framed file from mono PCM samples."""
    mono = np.asarray(samples, dtype=np.int16)
    if mono.ndim != 1:
        raise ValueError("samples must be mono")
    start, profile = _find_transmission_start(mono, sample_rate)
    nominal_preamble_samples = sample_rate * PREAMBLE_DURATION
    nominal_symbol_samples = sample_rate * SYMBOL_DURATION
    f_low = _calibration_frequency(mono, start, PREAMBLE_DURATION, sample_rate)
    if abs(f_low - profile.base_frequency) > 600.0:
        raise ProtocolError(f"invalid LOW calibration frequency: {f_low:.1f} Hz")
    # Sample-clock mismatch shifts both pitch and tone duration by reciprocal
    # factors. Deriving timing from LOW keeps long frames aligned under drift.
    timing_scale = profile.base_frequency / f_low
    preamble_samples = nominal_preamble_samples * timing_scale
    symbol_samples = nominal_symbol_samples * timing_scale
    f_high = _calibration_frequency(
        mono, start + round(preamble_samples), PREAMBLE_DURATION * timing_scale, sample_rate
    )
    expected_high = profile.high_preamble_frequency / timing_scale
    if f_high >= sample_rate / 2 or abs(f_high - expected_high) > 600.0:
        raise ProtocolError(f"invalid HIGH calibration frequency: {f_high:.1f} Hz")
    step = (f_high - f_low) / 17.0
    expected_step = profile.step_frequency / timing_scale
    if not expected_step * 0.72 <= step <= expected_step * 1.28:
        raise ProtocolError(f"invalid measured channel spacing: {step:.1f} Hz")

    payload_start = start + 2 * preamble_samples
    remaining = max(0.0, mono.size - payload_start)
    # round() tolerates the handful of fade-in samples skipped by onset
    # detection while still rejecting meaningfully truncated symbols.
    available_symbols = max(0, round(remaining / symbol_samples))
    nibbles: list[int] = []
    expected_symbols: int | None = None
    trim = round(symbol_samples * 0.10)
    for index in range(available_symbols):
        offset = round(payload_start + index * symbol_samples)
        end = round(payload_start + (index + 1) * symbol_samples)
        chunk = mono[offset + trim : min(end - trim, mono.size)]
        if chunk.size < symbol_samples * 0.60:
            break
        peak = find_dominant_frequency(chunk, sample_rate)
        nibble = round((peak - f_low) / step) - 1
        if not 0 <= nibble <= 15:
            raise ProtocolError(f"invalid symbol {index}: peak {peak:.1f} Hz maps to {nibble}")
        nibbles.append(int(nibble))

        byte_count = len(nibbles) // 2
        if expected_symbols is None and byte_count >= HEADER.size:
            partial = nibbles_to_bytes(nibbles)
            magic, length, _checksum, name_length = HEADER.unpack_from(partial)
            if magic != MAGIC:
                raise ProtocolError(
                    "frame magic mismatch; symbol timing or calibration may be wrong"
                )
            if length > MAX_PAYLOAD_BYTES or name_length > MAX_FILENAME_BYTES:
                raise ProtocolError("invalid frame length fields")
            expected_symbols = 2 * (HEADER.size + name_length + length)
        if expected_symbols is not None and len(nibbles) >= expected_symbols:
            break

    return DecodedFile(*parse_frame(nibbles_to_bytes(nibbles)), f_low, f_high)
