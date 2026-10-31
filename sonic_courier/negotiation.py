"""Adaptive acoustic channel sounding and compact control messages."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .audio import SAMPLE_RATE, ToneProfile, generate_sine_wave

SOUNDING_MIN_FREQUENCY = 4_000.0
SOUNDING_MAX_FREQUENCY = 19_000.0
NEGOTIATED_STEP_FREQUENCY = 200.0
SOUNDING_TONE_DURATION = 0.020
SOUNDING_PREAMBLE_DURATION = 0.300
SOUNDING_GUARD_DURATION = 0.050
SOUNDING_FREQUENCIES = np.arange(
    SOUNDING_MIN_FREQUENCY,
    SOUNDING_MAX_FREQUENCY + NEGOTIATED_STEP_FREQUENCY,
    NEGOTIATED_STEP_FREQUENCY,
)

CONTROL_FREQUENCIES = (3_000.0, 4_000.0, 5_000.0, 6_000.0)
CONTROL_SYMBOL_DURATION = 0.030
CONTROL_PREAMBLE_DURATION = 0.300
CONTROL_GUARD_DURATION = 0.050
CONTROL_REPETITIONS = 2
CONTROL_MAGIC = b"SC"
CONTROL_CANDIDATES = 1
CONTROL_PLAN = 2
CONTROL_PACKET_SIZE = 8

SOUNDING_DURATION = (
    SOUNDING_PREAMBLE_DURATION
    + SOUNDING_GUARD_DURATION
    + len(SOUNDING_FREQUENCIES) * SOUNDING_TONE_DURATION
)
CONTROL_DURATION = (
    CONTROL_PREAMBLE_DURATION
    + CONTROL_GUARD_DURATION
    + CONTROL_REPETITIONS * CONTROL_PACKET_SIZE * 4 * CONTROL_SYMBOL_DURATION
)


class NegotiationError(ValueError):
    """Raised when an acoustic handshake signal cannot be validated."""


@dataclass(frozen=True)
class SoundingResult:
    frequencies: NDArray[np.float64]
    snr_db: NDArray[np.float64]
    amplitudes: NDArray[np.float64]


@dataclass(frozen=True)
class ControlMessage:
    kind: int
    profiles: tuple[ToneProfile, ...]


def _chirp(
    start_frequency: float,
    end_frequency: float,
    duration: float,
    sample_rate: int = SAMPLE_RATE,
    *,
    amplitude: float = 0.70,
) -> NDArray[np.int16]:
    sample_count = round(sample_rate * duration)
    time = np.arange(sample_count, dtype=np.float64) / sample_rate
    slope = (end_frequency - start_frequency) / duration
    phase = 2.0 * np.pi * (start_frequency * time + 0.5 * slope * time**2)
    wave = np.sin(phase)
    fade_samples = min(round(sample_rate * 0.010), sample_count // 2)
    if fade_samples:
        ramp = np.sin(np.linspace(0.0, np.pi / 2.0, fade_samples)) ** 2
        wave[:fade_samples] *= ramp
        wave[-fade_samples:] *= ramp[::-1]
    return np.rint(wave * amplitude * np.iinfo(np.int16).max).astype(np.int16)


def _sounding_preamble(sample_rate: int = SAMPLE_RATE) -> NDArray[np.int16]:
    return _chirp(2_200.0, 7_800.0, SOUNDING_PREAMBLE_DURATION, sample_rate)


def _control_preamble(sample_rate: int = SAMPLE_RATE) -> NDArray[np.int16]:
    return _chirp(7_200.0, 2_400.0, CONTROL_PREAMBLE_DURATION, sample_rate)


def generate_sounding_sweep(
    sample_rate: int = SAMPLE_RATE,
    *,
    amplitudes: NDArray[np.floating] | None = None,
) -> NDArray[np.int16]:
    """Generate an upward chirp followed by individually measurable tones."""
    if amplitudes is None:
        levels = np.full(len(SOUNDING_FREQUENCIES), 0.62, dtype=np.float64)
    else:
        levels = np.asarray(amplitudes, dtype=np.float64)
        if levels.shape != SOUNDING_FREQUENCIES.shape:
            raise ValueError("amplitudes must contain one value per sounding frequency")
        if np.any((levels < 0.0) | (levels > 1.0)):
            raise ValueError("sounding amplitudes must be between 0 and 1")
    guard = np.zeros(round(sample_rate * SOUNDING_GUARD_DURATION), dtype=np.int16)
    parts = [_sounding_preamble(sample_rate), guard]
    parts.extend(
        generate_sine_wave(
            float(frequency),
            SOUNDING_TONE_DURATION,
            sample_rate,
            amplitude=float(level),
            fade_duration=0.001,
        )
        for frequency, level in zip(SOUNDING_FREQUENCIES, levels, strict=True)
    )
    return np.concatenate(parts)


def _find_template(
    samples: NDArray[np.generic],
    template: NDArray[np.int16],
    *,
    minimum_correlation: float = 0.16,
) -> int:
    signal = np.asarray(samples, dtype=np.float64) / np.iinfo(np.int16).max
    reference = np.asarray(template, dtype=np.float64) / np.iinfo(np.int16).max
    if signal.ndim != 1 or signal.size < reference.size:
        raise NegotiationError("recording is too short for the handshake preamble")
    output_size = signal.size + reference.size - 1
    fft_size = 1 << (output_size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(signal, fft_size) * np.fft.rfft(reference[::-1], fft_size),
        fft_size,
    )[:output_size]
    numerator = convolution[reference.size - 1 : signal.size]
    cumulative = np.concatenate(([0.0], np.cumsum(signal**2)))
    window_energy = cumulative[reference.size :] - cumulative[: -reference.size]
    denominator = np.sqrt(window_energy * np.sum(reference**2)) + 1e-12
    correlation = numerator / denominator
    peak_index = int(np.argmax(correlation))
    peak = float(correlation[peak_index])
    if peak < minimum_correlation:
        raise NegotiationError(f"handshake preamble correlation is too weak ({peak:.2f})")
    return peak_index


def _tone_amplitude(chunk: NDArray[np.generic], frequency: float, sample_rate: int) -> float:
    values = np.asarray(chunk, dtype=np.float64)
    if values.size < 8:
        return 0.0
    window = np.hanning(values.size)
    phase = np.exp(-2j * np.pi * frequency * np.arange(values.size) / sample_rate)
    return float(2.0 * np.abs(np.dot(values * window, phase)) / np.sum(window))


def analyze_sounding_sweep(
    samples: NDArray[np.generic], sample_rate: int = SAMPLE_RATE
) -> SoundingResult:
    """Locate a sounding sweep and measure noise-relative energy in every bin."""
    mono = np.asarray(samples, dtype=np.int16)
    preamble = _sounding_preamble(sample_rate)
    preamble_start = _find_template(mono, preamble)
    guard_samples = round(sample_rate * SOUNDING_GUARD_DURATION)
    tone_samples = round(sample_rate * SOUNDING_TONE_DURATION)
    tones_start = preamble_start + preamble.size + guard_samples
    required_end = tones_start + tone_samples * len(SOUNDING_FREQUENCIES)
    if required_end > mono.size:
        raise NegotiationError("recording ended during the frequency sweep")

    noise_start = max(0, preamble_start - round(sample_rate * 0.60))
    noise = mono[noise_start:preamble_start]
    noise_windows = [
        noise[index : index + tone_samples]
        for index in range(0, max(0, noise.size - tone_samples + 1), tone_samples)
    ]
    amplitudes: list[float] = []
    snr_values: list[float] = []
    for index, frequency in enumerate(SOUNDING_FREQUENCIES):
        start = tones_start + index * tone_samples
        chunk = mono[start : start + tone_samples]
        amplitude = _tone_amplitude(chunk, float(frequency), sample_rate)
        adjacent = max(
            _tone_amplitude(chunk, float(frequency - 100.0), sample_rate),
            _tone_amplitude(chunk, float(frequency + 100.0), sample_rate),
        )
        historical = [
            _tone_amplitude(window, float(frequency), sample_rate)
            for window in noise_windows
        ]
        noise_floor = float(np.median(historical)) if historical else 1.0
        noise_floor = max(noise_floor, adjacent * 0.35, 1.0)
        amplitudes.append(amplitude)
        snr_values.append(20.0 * np.log10((amplitude + 1.0) / noise_floor))
    return SoundingResult(
        SOUNDING_FREQUENCIES.copy(),
        np.asarray(snr_values, dtype=np.float64),
        np.asarray(amplitudes, dtype=np.float64),
    )


def _profile_for_base(base_frequency: float) -> ToneProfile:
    return ToneProfile(
        f"auto-{round(base_frequency)}",
        float(base_frequency),
        NEGOTIATED_STEP_FREQUENCY,
    )


def profile_score(result: SoundingResult, profile: ToneProfile) -> float:
    """Score all 18 calibration/data frequencies used by a profile."""
    indices = np.rint(
        (profile.base_frequency - SOUNDING_MIN_FREQUENCY) / NEGOTIATED_STEP_FREQUENCY
        + np.arange(18)
    ).astype(int)
    if np.any(indices < 0) or np.any(indices >= result.snr_db.size):
        return float("-inf")
    window = result.snr_db[indices]
    return float(np.percentile(window, 15.0) + 0.20 * np.mean(window))


def rank_candidate_profiles(result: SoundingResult, limit: int = 3) -> tuple[ToneProfile, ...]:
    """Return diverse high-scoring contiguous bands, preferring higher bands slightly."""
    if limit < 1:
        raise ValueError("limit must be positive")
    window_count = len(SOUNDING_FREQUENCIES) - 17
    ranked: list[tuple[float, ToneProfile]] = []
    for index in range(window_count):
        profile = _profile_for_base(float(SOUNDING_FREQUENCIES[index]))
        high_frequency_bias = profile.base_frequency / 20_000.0
        ranked.append((profile_score(result, profile) + high_frequency_bias, profile))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[ToneProfile] = []
    for _score, profile in ranked:
        if all(abs(profile.base_frequency - other.base_frequency) >= 800.0 for other in selected):
            selected.append(profile)
        if len(selected) == limit:
            break
    if not selected:
        raise NegotiationError("no usable contiguous frequency band was measured")
    return tuple(selected)


def choose_mutual_profile(
    forward_candidates: tuple[ToneProfile, ...], reverse_result: SoundingResult
) -> ToneProfile:
    """Choose the best candidate that also performed well in the reverse direction."""
    if not forward_candidates:
        raise NegotiationError("the receiver did not propose a frequency band")
    ranked = [
        (profile_score(reverse_result, profile) - index * 1.5, profile)
        for index, profile in enumerate(forward_candidates)
    ]
    return max(ranked, key=lambda item: item[0])[1]


def _packet(kind: int, profiles: tuple[ToneProfile, ...]) -> bytes:
    if kind not in {CONTROL_CANDIDATES, CONTROL_PLAN}:
        raise ValueError("invalid control message kind")
    if not profiles or len(profiles) > 3:
        raise ValueError("control messages require one to three profiles")
    step_units = round(profiles[0].step_frequency / 50.0)
    bases = [round(profile.base_frequency / 100.0) for profile in profiles]
    if not 1 <= step_units <= 255 or any(not 1 <= base <= 255 for base in bases):
        raise ValueError("negotiated frequencies cannot be represented")
    bases.extend([0] * (3 - len(bases)))
    body = CONTROL_MAGIC + bytes((kind, step_units, *bases))
    return body + bytes((zlib.crc32(body) & 0xFF,))


def _parse_packet(packet: bytes) -> ControlMessage:
    if len(packet) != CONTROL_PACKET_SIZE:
        raise NegotiationError("invalid control packet length")
    body, checksum = packet[:-1], packet[-1]
    if body[:2] != CONTROL_MAGIC or zlib.crc32(body) & 0xFF != checksum:
        raise NegotiationError("control packet checksum mismatch")
    kind, step_units, *bases = body[2:]
    if kind not in {CONTROL_CANDIDATES, CONTROL_PLAN} or step_units == 0:
        raise NegotiationError("invalid control packet fields")
    profiles = tuple(
        ToneProfile(f"auto-{base * 100}", float(base * 100), float(step_units * 50))
        for base in bases
        if base
    )
    if not profiles or kind == CONTROL_PLAN and len(profiles) != 1:
        raise NegotiationError("invalid number of negotiated profiles")
    return ControlMessage(kind, profiles)


def encode_control_message(
    kind: int,
    profiles: tuple[ToneProfile, ...],
    sample_rate: int = SAMPLE_RATE,
) -> NDArray[np.int16]:
    """Encode a repeated, CRC-protected message using robust low-band 4-FSK."""
    packet = _packet(kind, profiles)
    symbols = [
        (byte >> shift) & 0x03
        for byte in packet
        for shift in (6, 4, 2, 0)
    ]
    guard = np.zeros(round(sample_rate * CONTROL_GUARD_DURATION), dtype=np.int16)
    body = np.concatenate(
        [
            generate_sine_wave(
                CONTROL_FREQUENCIES[symbol],
                CONTROL_SYMBOL_DURATION,
                sample_rate,
                amplitude=0.68,
                fade_duration=0.002,
            )
            for symbol in symbols
        ]
    )
    return np.concatenate([_control_preamble(sample_rate), guard, *([body] * CONTROL_REPETITIONS)])


def decode_control_message(
    samples: NDArray[np.generic], sample_rate: int = SAMPLE_RATE
) -> ControlMessage:
    """Locate and decode either repeated copy of a control packet."""
    mono = np.asarray(samples, dtype=np.int16)
    preamble = _control_preamble(sample_rate)
    preamble_start = _find_template(mono, preamble)
    symbol_samples = round(sample_rate * CONTROL_SYMBOL_DURATION)
    body_start = preamble_start + preamble.size + round(sample_rate * CONTROL_GUARD_DURATION)
    symbols_per_packet = CONTROL_PACKET_SIZE * 4
    errors: list[Exception] = []
    for repetition in range(CONTROL_REPETITIONS):
        start = body_start + repetition * symbols_per_packet * symbol_samples
        end = start + symbols_per_packet * symbol_samples
        if end > mono.size:
            errors.append(NegotiationError("recording ended during the control message"))
            continue
        symbols: list[int] = []
        for symbol_index in range(symbols_per_packet):
            offset = start + symbol_index * symbol_samples
            chunk = mono[offset : offset + symbol_samples]
            energies = [
                _tone_amplitude(chunk, frequency, sample_rate)
                for frequency in CONTROL_FREQUENCIES
            ]
            symbols.append(int(np.argmax(energies)))
        packet = bytes(
            sum(symbols[index + offset] << (6 - 2 * offset) for offset in range(4))
            for index in range(0, len(symbols), 4)
        )
        try:
            return _parse_packet(packet)
        except NegotiationError as exc:
            errors.append(exc)
    raise NegotiationError(str(errors[-1]) if errors else "no control packet was recorded")
