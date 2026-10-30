"""Optional PyAudio speaker and microphone integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .audio import BASE_FREQUENCY, SAMPLE_RATE, find_dominant_frequency


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
    silent_chunks = 0
    max_wait_chunks = round(timeout * sample_rate / chunk_size)
    required_silence = round(silence_duration * sample_rate / chunk_size)
    status("Listening for the 17 kHz calibration tone...")
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
                if rms > 150 and BASE_FREQUENCY - 500 <= peak <= BASE_FREQUENCY + 500:
                    started = True
                    captured.extend(rolling)
                    status("Preamble detected; receiving...")
                continue
            captured.append(chunk)
            peak = find_dominant_frequency(chunk, sample_rate)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            in_band = 16_500 <= peak <= 21_700 and rms > 150
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
