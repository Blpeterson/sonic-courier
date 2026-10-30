"""Audio synthesis, spectrum analysis, and PCM WAV helpers."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

SAMPLE_RATE = 44_100
SYMBOL_DURATION = 0.040
PREAMBLE_DURATION = 0.100
AMPLITUDE = 0.72


@dataclass(frozen=True)
class ToneProfile:
    """Nominal frequencies for one relative 16-tone FSK channel."""

    name: str
    base_frequency: float
    step_frequency: float

    @property
    def first_data_frequency(self) -> float:
        return self.base_frequency + self.step_frequency

    @property
    def last_data_frequency(self) -> float:
        return self.base_frequency + 16 * self.step_frequency

    @property
    def high_preamble_frequency(self) -> float:
        return self.base_frequency + 17 * self.step_frequency


COMPATIBLE_PROFILE = ToneProfile("compatible", 12_000.0, 200.0)
ULTRASONIC_PROFILE = ToneProfile("ultrasonic", 17_000.0, 250.0)
TONE_PROFILES = {
    COMPATIBLE_PROFILE.name: COMPATIBLE_PROFILE,
    ULTRASONIC_PROFILE.name: ULTRASONIC_PROFILE,
}
DEFAULT_TONE_PROFILE = COMPATIBLE_PROFILE

# Backwards-compatible aliases for callers that imported the original constants.
BASE_FREQUENCY = DEFAULT_TONE_PROFILE.base_frequency
STEP_FREQUENCY = DEFAULT_TONE_PROFILE.step_frequency
HIGH_PREAMBLE_FREQUENCY = DEFAULT_TONE_PROFILE.high_preamble_frequency


def resolve_tone_profile(profile: str | ToneProfile | None = None) -> ToneProfile:
    """Resolve a profile name or object, defaulting to the compatible channel."""
    if profile is None:
        return DEFAULT_TONE_PROFILE
    if isinstance(profile, ToneProfile):
        return profile
    try:
        return TONE_PROFILES[profile]
    except KeyError as exc:
        choices = ", ".join(sorted(TONE_PROFILES))
        raise ValueError(f"unknown tone profile {profile!r}; choose one of: {choices}") from exc


def match_tone_profile(frequency: float, tolerance: float = 600.0) -> ToneProfile | None:
    """Return the supported profile whose LOW preamble is nearest to a frequency."""
    if tolerance < 0:
        raise ValueError("tolerance must not be negative")
    profile = min(
        TONE_PROFILES.values(),
        key=lambda candidate: abs(candidate.base_frequency - frequency),
    )
    if abs(profile.base_frequency - frequency) <= tolerance:
        return profile
    return None


class AudioFormatError(ValueError):
    """Raised when an input WAV cannot be decoded."""


def generate_sine_wave(
    frequency: float,
    duration: float,
    sample_rate: int = SAMPLE_RATE,
    *,
    amplitude: float = AMPLITUDE,
    fade_duration: float = 0.003,
) -> NDArray[np.int16]:
    """Generate click-reduced mono signed 16-bit PCM for one tone."""
    sample_count = round(sample_rate * duration)
    if sample_count <= 0:
        raise ValueError("duration must produce at least one sample")
    if not 0 < frequency < sample_rate / 2:
        raise ValueError("frequency must be between 0 and the Nyquist frequency")
    if not 0 < amplitude <= 1:
        raise ValueError("amplitude must be in the range (0, 1]")

    time = np.arange(sample_count, dtype=np.float64) / sample_rate
    tone = np.sin(2.0 * np.pi * frequency * time)
    fade_samples = min(round(sample_rate * fade_duration), sample_count // 2)
    if fade_samples:
        # A raised-cosine ramp suppresses clicks without broad spectral splatter.
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade_samples))
        tone[:fade_samples] *= ramp
        tone[-fade_samples:] *= ramp[::-1]
    return np.rint(tone * amplitude * np.iinfo(np.int16).max).astype(np.int16)


def find_dominant_frequency(
    audio_chunk: NDArray[np.generic], sample_rate: int = SAMPLE_RATE
) -> float:
    """Return an FFT peak with parabolic interpolation for sub-bin accuracy."""
    samples = np.asarray(audio_chunk, dtype=np.float64)
    if samples.ndim != 1 or samples.size < 3:
        raise ValueError("audio_chunk must be a one-dimensional array with at least 3 samples")
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    peak = int(np.argmax(spectrum))
    offset = 0.0
    if 0 < peak < spectrum.size - 1:
        left, center, right = np.log(spectrum[peak - 1 : peak + 2] + 1e-12)
        denominator = left - 2.0 * center + right
        if denominator:
            offset = float(0.5 * (left - right) / denominator)
    return (peak + offset) * sample_rate / samples.size


def write_wav(path: str | Path, samples: NDArray[np.int16], sample_rate: int = SAMPLE_RATE) -> None:
    """Write a mono 16-bit PCM WAV file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def read_wav(path: str | Path) -> tuple[NDArray[np.int16], int]:
    """Read a mono 16-bit PCM WAV, downmixing multi-channel input."""
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioFormatError(f"invalid WAV file: {exc}") from exc
    if width != 2:
        raise AudioFormatError("only 16-bit PCM WAV files are supported")
    if channels < 1:
        raise AudioFormatError("WAV must have at least one channel")
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = np.mean(samples.reshape(-1, channels).astype(np.float64), axis=1).astype(np.int16)
    return samples.copy(), rate
