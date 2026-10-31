"""Optional PyAudio speaker and microphone integration."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
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
from .negotiation import (
    CONTROL_CANDIDATES,
    CONTROL_DURATION,
    CONTROL_PLAN,
    SOUNDING_DURATION,
    NegotiationError,
    analyze_sounding_sweep,
    choose_mutual_profile,
    decode_control_message,
    encode_control_message,
    generate_sounding_sweep,
    rank_candidate_profiles,
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


@contextmanager
def _quiet_native_audio_errors():
    """Hide Linux backend probing noise while preserving Python exceptions."""
    if not sys.platform.startswith("linux") or os.environ.get("SONIC_COURIER_AUDIO_DEBUG"):
        yield
        return
    try:
        sys.stderr.flush()
        saved_stderr = os.dup(2)
        null_stderr = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    try:
        os.dup2(null_stderr, 2)
        yield
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)
        os.close(null_stderr)


def _audio_instance(pyaudio):
    with _quiet_native_audio_errors():
        return pyaudio.PyAudio()


def inspect_devices() -> AudioDeviceSummary:
    """Inspect PortAudio without playing or recording any sound."""
    pyaudio = _pyaudio()
    audio = _audio_instance(pyaudio)
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
    audio = _audio_instance(pyaudio)
    stream = None
    try:
        with _quiet_native_audio_errors():
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                output=True,
            )
        stream.write(np.asarray(samples, dtype="<i2").tobytes())
    except Exception as exc:
        raise LiveAudioError(f"audio playback failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


def record_audio(duration: float, sample_rate: int = SAMPLE_RATE) -> NDArray[np.int16]:
    """Record a fixed-duration mono clip from the default microphone."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    pyaudio = _pyaudio()
    audio = _audio_instance(pyaudio)
    stream = None
    chunk_size = 1024
    remaining = round(duration * sample_rate)
    captured: list[NDArray[np.int16]] = []
    try:
        with _quiet_native_audio_errors():
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                frames_per_buffer=chunk_size,
            )
        while remaining > 0:
            count = min(chunk_size, remaining)
            raw = stream.read(count, exception_on_overflow=False)
            captured.append(np.frombuffer(raw, dtype="<i2").copy())
            remaining -= count
    except Exception as exc:
        raise LiveAudioError(f"microphone capture failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
    return np.concatenate(captured)


def negotiate_sender(
    *,
    timeout: float = 30.0,
    status: Callable[[str], None] = print,
) -> ToneProfile:
    """Sound the channel, measure the reverse path, and announce the final plan."""
    deadline = time.monotonic() + timeout
    sweep = generate_sounding_sweep()
    # The receiver may finish a partially elapsed capture window before it can
    # answer, so retain generous headroom ahead of the response waveform.
    response_duration = CONTROL_DURATION + 0.15 + SOUNDING_DURATION + 1.80
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        status(f"Handshake attempt {attempt}: sounding the channel...")
        play(sweep)
        response = record_audio(response_duration)
        try:
            message = decode_control_message(response)
            if message.kind != CONTROL_CANDIDATES:
                raise NegotiationError("expected receiver frequency candidates")
            reverse_result = analyze_sounding_sweep(response)
            profile = choose_mutual_profile(message.profiles, reverse_result)
        except NegotiationError as exc:
            status(f"No complete receiver response yet ({exc}); retrying...")
            continue
        status(
            f"Negotiated {profile.first_data_frequency / 1000:g}-"
            f"{profile.last_data_frequency / 1000:g} kHz; confirming..."
        )
        play(encode_control_message(CONTROL_PLAN, (profile,)))
        return profile
    raise LiveAudioError(f"no receiver handshake completed within {timeout:g} seconds")


def negotiate_receiver(
    *,
    timeout: float = 60.0,
    status: Callable[[str], None] = print,
) -> ToneProfile:
    """Wait for channel sounding, return candidates, and receive the final plan."""
    deadline = time.monotonic() + timeout
    capture_duration = SOUNDING_DURATION + 0.85
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        status(f"Listening for sender handshake (window {attempt})...")
        recording = record_audio(capture_duration)
        try:
            forward_result = analyze_sounding_sweep(recording)
            candidates = rank_candidate_profiles(forward_result)
        except NegotiationError as exc:
            rms = float(np.sqrt(np.mean(recording.astype(np.float64) ** 2)))
            if rms < 2.0:
                status(
                    "No sender chirp detected; microphone RMS is effectively zero. "
                    "This Linux session may not have microphone access."
                )
            else:
                status(f"No sender chirp detected ({exc}); microphone RMS {rms:.0f}.")
            continue
        candidate_text = ", ".join(
            f"{profile.base_frequency / 1000:g}-"
            f"{profile.high_preamble_frequency / 1000:g} kHz"
            for profile in candidates
        )
        status(f"Sender found; proposing bands {candidate_text}...")
        response = np.concatenate(
            (
                encode_control_message(CONTROL_CANDIDATES, candidates),
                np.zeros(round(SAMPLE_RATE * 0.15), dtype=np.int16),
                generate_sounding_sweep(),
            )
        )
        play(response)
        final_recording = record_audio(CONTROL_DURATION + 1.55)
        try:
            final_message = decode_control_message(final_recording)
        except NegotiationError as exc:
            status(f"Final frequency confirmation was not received ({exc}); restarting...")
            continue
        if final_message.kind != CONTROL_PLAN:
            status("Received an unexpected handshake message; restarting...")
            continue
        profile = final_message.profiles[0]
        if all(
            abs(profile.base_frequency - candidate.base_frequency) > 1.0
            or abs(profile.step_frequency - candidate.step_frequency) > 1.0
            for candidate in candidates
        ):
            status("Sender selected an unrecognized frequency plan; restarting...")
            continue
        status(
            f"Handshake complete at {profile.first_data_frequency / 1000:g}-"
            f"{profile.last_data_frequency / 1000:g} kHz."
        )
        return profile
    raise LiveAudioError(f"no sender handshake detected within {timeout:g} seconds")


def record_transmission(
    sample_rate: int = SAMPLE_RATE,
    *,
    timeout: float = 30.0,
    silence_duration: float = 0.75,
    profile: ToneProfile | None = None,
    status: Callable[[str], None] = print,
) -> NDArray[np.int16]:
    """Listen until a LOW preamble arrives, then record until sustained silence."""
    pyaudio = _pyaudio()
    chunk_size = round(sample_rate * 0.020)
    audio = _audio_instance(pyaudio)
    stream = None
    captured: list[NDArray[np.int16]] = []
    rolling: list[NDArray[np.int16]] = []
    started = False
    active_profile: ToneProfile | None = None
    silent_chunks = 0
    max_wait_chunks = round(timeout * sample_rate / chunk_size)
    required_silence = round(silence_duration * sample_rate / chunk_size)
    available_profiles = (profile,) if profile is not None else tuple(TONE_PROFILES.values())
    profile_labels = " or ".join(
        f"{candidate.base_frequency / 1000:g} kHz ({candidate.name})"
        for candidate in available_profiles
    )
    status(f"Listening for a calibration tone at {profile_labels}...")
    try:
        with _quiet_native_audio_errors():
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
                detected_profile = (
                    profile
                    if profile is not None and abs(profile.base_frequency - peak) <= 600.0
                    else match_tone_profile(peak, tolerance=600.0)
                    if profile is None
                    else None
                )
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
