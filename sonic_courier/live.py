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
from .cancellation import CancelledError, check_cancelled
from .negotiation import (
    CONTROL_CANDIDATES,
    CONTROL_DURATION,
    CONTROL_FIXED_PLAN,
    CONTROL_PLAN,
    CONTROL_READY,
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
    device_details: tuple[str, ...] = ()


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


def _configured_device(kind: str) -> int | None:
    value = os.environ.get(f"SONIC_COURIER_{kind.upper()}_DEVICE")
    if value is None or not value.strip():
        return None
    try:
        index = int(value)
    except ValueError as exc:
        raise LiveAudioError(
            f"SONIC_COURIER_{kind.upper()}_DEVICE must be a numeric PortAudio device index"
        ) from exc
    if index < 0:
        raise LiveAudioError(f"the configured {kind} device index cannot be negative")
    return index


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
        details = tuple(
            f"{index}: {device.get('name', 'unnamed')} "
            f"(inputs={int(device.get('maxInputChannels', 0))}, "
            f"outputs={int(device.get('maxOutputChannels', 0))}, "
            f"rate={float(device.get('defaultSampleRate', 0)):g})"
            for index, device in enumerate(devices)
        )
        return AudioDeviceSummary(total, inputs, outputs, default_input, default_output, details)
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
                output_device_index=_configured_device("output"),
            )
        values = np.asarray(samples, dtype="<i2")
        # Short writes let Q stop playback before the whole file is queued.
        for offset in range(0, values.size, 1024):
            check_cancelled()
            stream.write(values[offset : offset + 1024].tobytes())
    except CancelledError:
        raise
    except Exception as exc:
        raise LiveAudioError(f"audio playback failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


def record_audio(
    duration: float,
    sample_rate: int = SAMPLE_RATE,
    *,
    detect: Callable[[NDArray[np.int16]], object] | None = None,
) -> NDArray[np.int16]:
    """Record mono PCM, optionally stopping when a detector accepts the rolling buffer."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    pyaudio = _pyaudio()
    audio = _audio_instance(pyaudio)
    stream = None
    chunk_size = 1024
    remaining = round(duration * sample_rate)
    captured: list[NDArray[np.int16]] = []
    next_detection = 0
    captured_samples = 0
    buffer_samples = round(sample_rate * 8.0)
    try:
        with _quiet_native_audio_errors():
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                input_device_index=_configured_device("input"),
                frames_per_buffer=chunk_size,
            )
        while remaining > 0:
            check_cancelled()
            count = min(chunk_size, remaining)
            raw = stream.read(count, exception_on_overflow=False)
            captured.append(np.frombuffer(raw, dtype="<i2").copy())
            remaining -= count
            if detect is not None:
                captured_samples += count
                while captured_samples > buffer_samples and len(captured) > 1:
                    captured_samples -= captured.pop(0).size
                next_detection -= count
                if next_detection <= 0:
                    next_detection = round(sample_rate * 0.20)
                    try:
                        detect(np.concatenate(captured))
                    except NegotiationError:
                        pass
                    else:
                        break
    except CancelledError:
        raise
    except Exception as exc:
        raise LiveAudioError(f"microphone capture failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
    return np.concatenate(captured)


HANDSHAKE_TURNAROUND = 0.50


def _play_handshake(samples: NDArray[np.int16]) -> None:
    # Leave time for the peer to switch from playback to microphone capture.
    play(np.concatenate((np.zeros(round(SAMPLE_RATE * HANDSHAKE_TURNAROUND), np.int16), samples)))


def _same_profile(left: ToneProfile, right: ToneProfile) -> bool:
    return (
        abs(left.base_frequency - right.base_frequency) < 1.0
        and abs(left.step_frequency - right.step_frequency) < 1.0
    )


def _ready_message(recording: NDArray[np.int16], profile: ToneProfile):
    message = decode_control_message(recording, complete=True)
    if message.kind != CONTROL_READY or not _same_profile(message.profiles[0], profile):
        raise NegotiationError("receiver did not confirm the selected frequency plan")
    return message


def negotiate_sender(
    *,
    timeout: float = 30.0,
    profile: ToneProfile | None = None,
    status: Callable[[str], None] = print,
) -> ToneProfile:
    """Sound the channel and wait for the receiver to acknowledge the final plan."""
    if timeout <= 0:
        raise ValueError("handshake timeout must be positive")
    deadline = time.monotonic() + timeout
    sweep = generate_sounding_sweep()
    # Allow capture startup, turnaround guards and processing on slower peers.
    response_duration = CONTROL_DURATION + 0.15 + SOUNDING_DURATION + 2.5
    requested_profile = profile
    if requested_profile is not None and not any(
        _same_profile(requested_profile, fixed) for fixed in TONE_PROFILES.values()
    ):
        raise ValueError("a fixed handshake requires a compatible or ultrasonic profile")

    def response_complete(recording):
        message = decode_control_message(recording, complete=True)
        if message.kind != CONTROL_CANDIDATES:
            raise NegotiationError("expected receiver frequency candidates")
        analyze_sounding_sweep(recording)
        return message

    attempt = 0
    while time.monotonic() < deadline:
        check_cancelled()
        attempt += 1
        status(f"Handshake attempt {attempt}: sounding the channel...")
        _play_handshake(sweep)
        response = record_audio(response_duration, detect=response_complete)
        try:
            message = decode_control_message(response)
            if message.kind != CONTROL_CANDIDATES:
                raise NegotiationError("expected receiver frequency candidates")
            reverse_result = analyze_sounding_sweep(response)
            profile = requested_profile or choose_mutual_profile(message.profiles, reverse_result)
        except NegotiationError as exc:
            status(f"No complete receiver response yet ({exc}); retrying...")
            continue
        status(
            f"Selected {profile.first_data_frequency / 1000:g}-"
            f"{profile.last_data_frequency / 1000:g} kHz; confirming..."
        )
        kind = CONTROL_FIXED_PLAN if requested_profile is not None else CONTROL_PLAN
        for confirmation_attempt in range(1, 4):
            if confirmation_attempt > 1 and time.monotonic() >= deadline:
                break
            status(f"Waiting for receiver readiness (confirmation {confirmation_attempt}/3)...")
            _play_handshake(encode_control_message(kind, (profile,)))
            ready = record_audio(
                CONTROL_DURATION + 2.0,
                detect=lambda clip, selected=profile: _ready_message(clip, selected),
            )
            try:
                _ready_message(ready, profile)
            except NegotiationError as exc:
                status(f"Receiver readiness was not confirmed ({exc}); retrying...")
                continue
            status("Receiver confirmed the frequency plan; ready to send the file.")
            return profile
    raise LiveAudioError(f"no receiver handshake completed within {timeout:g} seconds")


def negotiate_receiver(
    *,
    timeout: float = 60.0,
    status: Callable[[str], None] = print,
) -> ToneProfile:
    """Wait for channel sounding, return candidates, and receive the final plan."""
    deadline = time.monotonic() + timeout
    if timeout <= 0:
        raise ValueError("handshake timeout must be positive")
    attempt = 0
    while time.monotonic() < deadline:
        check_cancelled()
        attempt += 1
        status(f"Listening continuously for sender handshake (attempt {attempt})...")
        # A single open stream and sliding buffer retain sweeps that cross the
        # old fixed capture-window boundary.
        recording = record_audio(
            max(0.01, deadline - time.monotonic()), detect=analyze_sounding_sweep
        )
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
        _play_handshake(response)
        final_recording = record_audio(
            CONTROL_DURATION + 2.0,
            detect=lambda clip: decode_control_message(clip, complete=True),
        )
        try:
            final_message = decode_control_message(final_recording)
        except NegotiationError as exc:
            status(f"Final frequency confirmation was not received ({exc}); restarting...")
            continue
        if final_message.kind not in {CONTROL_PLAN, CONTROL_FIXED_PLAN}:
            status("Received an unexpected handshake message; restarting...")
            continue
        profile = final_message.profiles[0]
        allowed = candidates if final_message.kind == CONTROL_PLAN else tuple(TONE_PROFILES.values())
        if not any(_same_profile(profile, candidate) for candidate in allowed):
            status("Sender selected an unrecognized frequency plan; restarting...")
            continue
        if final_message.kind == CONTROL_FIXED_PLAN:
            status("Sender requested a fixed data band; the handshake does not validate that band.")
        _play_handshake(encode_control_message(CONTROL_READY, (profile,)))
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
    handshake: bool = False,
    status: Callable[[str], None] = print,
) -> NDArray[np.int16]:
    """Listen until a LOW preamble arrives, then record until sustained silence."""
    if handshake and profile is None:
        raise ValueError("a handshake transmission requires the selected profile")
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
    control_history: list[NDArray[np.int16]] = []
    control_history_limit = round((CONTROL_DURATION + 0.50) * sample_rate / chunk_size) + 1
    control_check_interval = max(1, round(0.20 * sample_rate / chunk_size))
    preamble_candidate: list[NDArray[np.int16]] = []
    preamble_candidate_limit = round(5.30 * sample_rate / chunk_size)
    low_chunks = 0
    high_chunks = 0
    minimum_low_chunks = max(1, round(0.060 * sample_rate / chunk_size))
    minimum_high_chunks = max(1, round(0.040 * sample_rate / chunk_size))
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
                input_device_index=_configured_device("input"),
                frames_per_buffer=chunk_size,
            )
        waited_chunks = 0
        while True:
            check_cancelled()
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
                if handshake and profile is not None:
                    # A lost READY makes the sender repeat its frequency plan.
                    # Keep listening on the same open input stream and repeat
                    # the acknowledgement until the file preamble arrives.
                    control_history.append(chunk)
                    control_history = control_history[-control_history_limit:]
                    if waited_chunks % control_check_interval == 0:
                        try:
                            message = decode_control_message(
                                np.concatenate(control_history), sample_rate, complete=True
                            )
                        except NegotiationError:
                            pass
                        else:
                            if (
                                message.kind in {CONTROL_PLAN, CONTROL_FIXED_PLAN}
                                and _same_profile(message.profiles[0], profile)
                            ):
                                status("Frequency plan repeated; confirming receiver readiness...")
                                _play_handshake(encode_control_message(CONTROL_READY, (profile,)))
                                control_history.clear()
                                preamble_candidate.clear()
                                rolling.clear()
                                low_chunks = high_chunks = 0
                                continue

                    # The 3–6 kHz control tones overlap low negotiated bands.
                    # A LOW tone alone must not start a file recording: wait
                    # for its HIGH calibration partner and retain both tones.
                    tolerance = min(150.0, profile.step_frequency * 0.50)
                    is_low = rms > 150 and abs(profile.base_frequency - peak) <= tolerance
                    is_high = (
                        rms > 150
                        and abs(profile.high_preamble_frequency - peak) <= tolerance
                    )
                    if is_low:
                        if high_chunks:
                            preamble_candidate.clear()
                            low_chunks = high_chunks = 0
                        if not preamble_candidate:
                            preamble_candidate.extend(rolling[-2:])
                        else:
                            preamble_candidate.append(chunk)
                        preamble_candidate = preamble_candidate[-preamble_candidate_limit:]
                        low_chunks += 1
                    elif is_high and low_chunks >= minimum_low_chunks:
                        preamble_candidate.append(chunk)
                        high_chunks += 1
                        if high_chunks >= minimum_high_chunks:
                            started = True
                            active_profile = profile
                            captured.extend(preamble_candidate)
                            status(f"Preamble detected ({profile.name}); receiving...")
                    else:
                        preamble_candidate.clear()
                        low_chunks = high_chunks = 0
                    continue
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
    except (LiveAudioError, CancelledError):
        raise
    except Exception as exc:
        raise LiveAudioError(f"microphone capture failed: {exc}") from exc
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
    return np.concatenate(captured)
