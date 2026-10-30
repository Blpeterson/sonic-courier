"""Optional PyAudio speaker and microphone integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .audio import (
    SAMPLE_RATE,
    TONE_PROFILES,
    ToneProfile,
    find_dominant_frequency,
    match_tone_profile,
)


class LiveAudioError(RuntimeError):
    """Raised when live audio support is unavailable or fails."""


@dataclass(frozen=True)
class AudioDeviceSummary:
    """A compact inventory used by the setup diagnostics command."""

    total: int
    inputs: int
    outputs: int
    default_input: str | None
    default_output: str | None


def _pyaudio():
    try:
        import pyaudio  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LiveAudioError(
            "PyAudio is required for live sound. Install with: pip install 'sonic-courier[live]'"
        ) from exc
    return pyaudio


def inspect_devices() -> AudioDeviceSummary:
    """Inspect PortAudio without playing or recording any sound."""
    pyaudio = _pyaudio()
    audio = pyaudio.PyAudio()
    try:
        total = audio.get_device_count()
        devices = [audio.get_device_info_by_index(index) for index in range(total)]
        inputs = sum(int(device.get("maxInputChannels", 0)) > 0 for device in devices)
        outputs = sum(int(device.get("maxOutputChannels", 0)) > 0 for device in devices)
        try:
            default_input = str(audio.get_default_input_device_info()["name"])
        except OSError:
            default_input = None
        try:
            default_output = str(audio.get_default_output_device_info()["name"])
        except OSError:
            default_output = None
        return AudioDeviceSummary(total, inputs, outputs, default_input, default_output)
    except Exception as exc:
        raise LiveAudioError(f"audio device inspection failed: {exc}") from exc
    finally:
        audio.terminate()


def play(samples: NDArray[np.int16], sample_rate: int = SAMPLE_RATE) -> None:
    """Play signed 16-bit mono PCM through the default output device."""
    pyaudio = _pyaudio()
    audio = pyaudio.PyAudio()
    stream = None
    try:
        stream = audio.open(format=pyaudio.paInt16, channels=1, rate=sample_rate, output=True)
        stream.write(np.asarray(samples, dtype="<i2").tobytes())
    except Exception as exc:
        raise LiveAudioError(f"audio playback failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


def record_transmission(
    sample_rate: int = SAMPLE_RATE,
    *,
    timeout: float = 30.0,
    silence_duration: float = 0.75,
    status: Callable[[str], None] = print,
) -> NDArray[np.int16]:
    """Listen until a LOW preamble arrives, then record until sustained silence."""
    pyaudio = _pyaudio()
    chunk_size = round(sample_rate * 0.020)
    audio = pyaudio.PyAudio()
    stream = None
    captured: list[NDArray[np.int16]] = []
    rolling: list[NDArray[np.int16]] = []
    started = False
    active_profile: ToneProfile | None = None
    silent_chunks = 0
    max_wait_chunks = round(timeout * sample_rate / chunk_size)
    required_silence = round(silence_duration * sample_rate / chunk_size)
    profile_labels = " or ".join(
        f"{profile.base_frequency / 1000:g} kHz ({profile.name})"
        for profile in TONE_PROFILES.values()
    )
    status(f"Listening for a calibration tone at {profile_labels}...")
    try:
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )
        waited_chunks = 0
        while True:
            raw = stream.read(chunk_size, exception_on_overflow=False)
            chunk = np.frombuffer(raw, dtype="<i2").copy()
            if not started:
                waited_chunks += 1
                if waited_chunks > max_wait_chunks:
                    break
                rolling.append(chunk)
                rolling = rolling[-5:]
                peak = find_dominant_frequency(chunk, sample_rate)
                rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
                detected_profile = match_tone_profile(peak, tolerance=600.0)
                if rms > 150 and detected_profile is not None:
                    started = True
                    active_profile = detected_profile
                    captured.extend(rolling)
                    status(f"Preamble detected ({active_profile.name}); receiving...")
                continue
            captured.append(chunk)
            peak = find_dominant_frequency(chunk, sample_rate)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            if active_profile is None:  # Defensive: started and profile are set together above.
                raise LiveAudioError("internal error: transmission profile was not detected")
            margin = max(500.0, active_profile.step_frequency * 2.0)
            in_band = (
                active_profile.base_frequency - margin
                <= peak
                <= active_profile.high_preamble_frequency + margin
                and rms > 150
            )
            silent_chunks = 0 if in_band else silent_chunks + 1
            if silent_chunks >= required_silence:
                break
        if not started:
            raise LiveAudioError(f"no transmission detected within {timeout:g} seconds")
    except LiveAudioError:
        raise
    except Exception as exc:
        raise LiveAudioError(f"microphone capture failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
    return np.concatenate(captured)
