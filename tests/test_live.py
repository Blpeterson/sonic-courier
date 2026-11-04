import os
import unittest
from unittest.mock import patch

import numpy as np

from sonic_courier.audio import SAMPLE_RATE, ToneProfile, generate_sine_wave
from sonic_courier.cancellation import CancelledError
from sonic_courier.live import LiveAudioError, play, record_audio, record_transmission
from sonic_courier.negotiation import (
    CONTROL_DURATION,
    CONTROL_FIXED_PLAN,
    CONTROL_PLAN,
    CONTROL_READY,
    NegotiationError,
    analyze_sounding_sweep,
    decode_control_message,
    encode_control_message,
    generate_sounding_sweep,
)
from sonic_courier.protocol import build_frame, demodulate, modulate


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.stopped = False
        self.closed = False
        self.frames_read = 0

    def read(self, _chunk_size, *, exception_on_overflow):
        if exception_on_overflow:
            raise AssertionError("recording must tolerate input overflow")
        try:
            chunk = next(self._chunks)
            self.frames_read += len(chunk) // 2
            return chunk
        except StopIteration as exc:
            raise AssertionError("recording consumed every test chunk without stopping") from exc

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakeAudio:
    def __init__(self, stream):
        self.stream = stream
        self.open_arguments = None
        self.terminated = False
        self.open_count = 0

    def open(self, **kwargs):
        self.open_count += 1
        self.open_arguments = kwargs
        return self.stream

    def terminate(self):
        self.terminated = True


class _FakePyAudioModule:
    paInt16 = object()

    def __init__(self, audio):
        self._audio = audio

    def PyAudio(self):
        return self._audio


class LiveAudioTests(unittest.TestCase):
    @staticmethod
    def _stream_for_signal(signal, chunk_size=None):
        if chunk_size is None:
            chunk_size = round(SAMPLE_RATE * 0.020)
        padded = np.pad(signal, (0, (-signal.size) % chunk_size))
        return _FakeStream(
            padded[offset : offset + chunk_size].tobytes()
            for offset in range(0, padded.size, chunk_size)
        )

    def test_streaming_sweep_detection_crosses_old_capture_windows(self):
        sweep = generate_sounding_sweep()
        leading_frames = round(SAMPLE_RATE * 9.60)
        signal = np.concatenate(
            (
                np.zeros(leading_frames, dtype=np.int16),
                sweep,
                np.zeros(round(SAMPLE_RATE * 2.0), dtype=np.int16),
            )
        )
        stream = self._stream_for_signal(signal, chunk_size=1024)
        audio = _FakeAudio(stream)
        module = _FakePyAudioModule(audio)

        with patch("sonic_courier.live._pyaudio", return_value=module):
            recording = record_audio(14.0, detect=analyze_sounding_sweep)

        # The sweep spans the old 2.72-second capture boundaries and starts
        # after the entire sliding buffer has already filled once.
        self.assertEqual(audio.open_count, 1)
        self.assertGreaterEqual(stream.frames_read, leading_frames + sweep.size)
        self.assertLess(stream.frames_read, leading_frames + sweep.size + SAMPLE_RATE * 0.25)
        self.assertLessEqual(recording.size, SAMPLE_RATE * 8.0)
        self.assertGreater(float(np.min(analyze_sounding_sweep(recording).amplitudes)), 1_000.0)
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertTrue(audio.terminated)

    def test_complete_control_detection_waits_for_both_played_copies(self):
        profile = ToneProfile("auto-8400", 8_400.0, 200.0)
        control = encode_control_message(CONTROL_PLAN, (profile,))
        first_copy_end = round(SAMPLE_RATE * (0.30 + 0.05 + (CONTROL_DURATION - 0.35) / 2))
        self.assertEqual(decode_control_message(control[:first_copy_end]).kind, CONTROL_PLAN)
        with self.assertRaisesRegex(NegotiationError, "repeated control message"):
            decode_control_message(control[:first_copy_end], complete=True)

        leading_frames = round(SAMPLE_RATE * 0.137)
        signal = np.concatenate(
            (
                np.zeros(leading_frames, dtype=np.int16),
                control,
                np.zeros(round(SAMPLE_RATE * 0.5), dtype=np.int16),
            )
        )
        stream = self._stream_for_signal(signal, chunk_size=1024)
        audio = _FakeAudio(stream)
        module = _FakePyAudioModule(audio)
        with patch("sonic_courier.live._pyaudio", return_value=module):
            recording = record_audio(
                4.0, detect=lambda clip: decode_control_message(clip, complete=True)
            )

        self.assertEqual(audio.open_count, 1)
        self.assertGreaterEqual(stream.frames_read, leading_frames + control.size)
        self.assertLess(stream.frames_read, leading_frames + control.size + SAMPLE_RATE * 0.25)
        self.assertEqual(decode_control_message(recording).profiles, (profile,))

    def test_playback_cancellation_stops_short_writes_and_closes_device(self):
        stream = _FakeStream(())
        writes = []
        stream.write = writes.append
        audio = _FakeAudio(stream)
        module = _FakePyAudioModule(audio)
        with (
            patch("sonic_courier.live._pyaudio", return_value=module),
            patch("sonic_courier.live.check_cancelled", side_effect=(None, CancelledError())),
            self.assertRaises(CancelledError),
        ):
            play(np.zeros(8_000, dtype=np.int16))

        self.assertEqual(len(writes), 1)
        self.assertEqual(len(writes[0]), 1024 * 2)
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertTrue(audio.terminated)

    def test_recording_keeps_data_tones_for_both_profiles_until_real_silence(self):
        # Each data run is longer than the silence threshold. A hardcoded band
        # therefore makes this test stop in the middle of a valid transmission.
        profiles = (
            ("compatible", 12_000.0, 12_200.0),
            ("ultrasonic", 17_000.0, 17_250.0),
        )
        chunk_duration = 0.020
        data_chunk_count = 8
        silence_chunk_count = 3
        chunk_size = round(SAMPLE_RATE * chunk_duration)

        for name, preamble_frequency, data_frequency in profiles:
            with self.subTest(profile=name):
                preamble = generate_sine_wave(
                    preamble_frequency,
                    chunk_duration,
                    fade_duration=0,
                )
                data = generate_sine_wave(
                    data_frequency,
                    chunk_duration,
                    fade_duration=0,
                )
                silence = np.zeros(chunk_size, dtype=np.int16)
                source_chunks = (
                    [preamble.tobytes()]
                    + [data.tobytes()] * data_chunk_count
                    + [silence.tobytes()] * silence_chunk_count
                )
                stream = _FakeStream(source_chunks)
                audio = _FakeAudio(stream)
                module = _FakePyAudioModule(audio)
                statuses = []

                with patch("sonic_courier.live._pyaudio", return_value=module):
                    captured = record_transmission(
                        timeout=1.0,
                        silence_duration=silence_chunk_count * chunk_duration,
                        status=statuses.append,
                    )

                expected_chunks = 1 + data_chunk_count + silence_chunk_count
                self.assertEqual(captured.size, expected_chunks * chunk_size)
                np.testing.assert_array_equal(
                    captured[chunk_size : (1 + data_chunk_count) * chunk_size],
                    np.tile(data, data_chunk_count),
                )
                self.assertTrue(any("Preamble detected" in item for item in statuses))
                self.assertEqual(audio.open_arguments["rate"], SAMPLE_RATE)
                self.assertTrue(stream.stopped)
                self.assertTrue(stream.closed)
                self.assertTrue(audio.terminated)

    def test_recording_honors_configured_input_device(self):
        chunk_size = round(SAMPLE_RATE * 0.020)
        tone = generate_sine_wave(12_000.0, 0.020, fade_duration=0)
        silence = np.zeros(chunk_size, dtype=np.int16)
        stream = _FakeStream([tone.tobytes(), silence.tobytes()])
        audio = _FakeAudio(stream)
        module = _FakePyAudioModule(audio)

        with (
            patch("sonic_courier.live._pyaudio", return_value=module),
            patch.dict(os.environ, {"SONIC_COURIER_INPUT_DEVICE": "7"}),
        ):
            record_transmission(timeout=1.0, silence_duration=0.020, status=lambda _msg: None)

        self.assertEqual(audio.open_arguments["input_device_index"], 7)

    def test_handshake_reacknowledges_repeated_plan_before_low_band_payload(self):
        profile = ToneProfile("auto-4000", 4_000.0, 200.0)
        data = b"ready retry"
        payload = modulate(build_frame(data, "retry.bin"), profile=profile)
        for kind in (CONTROL_PLAN, CONTROL_FIXED_PLAN):
            with self.subTest(kind=kind):
                # Deliberately misalign the control preamble with input chunks.
                # Its 4 kHz symbols overlap the negotiated LOW data preamble.
                repeated_plan = encode_control_message(kind, (profile,))
                signal = np.concatenate(
                    (
                        np.zeros(round(SAMPLE_RATE * 0.037), dtype=np.int16),
                        repeated_plan,
                        np.zeros(round(SAMPLE_RATE * 0.40), dtype=np.int16),
                        payload,
                        np.zeros(round(SAMPLE_RATE * 0.10), dtype=np.int16),
                    )
                )
                stream = self._stream_for_signal(signal)
                module = _FakePyAudioModule(_FakeAudio(stream))
                statuses = []
                with (
                    patch("sonic_courier.live._pyaudio", return_value=module),
                    patch("sonic_courier.live._play_handshake") as acknowledge,
                ):
                    captured = record_transmission(
                        timeout=10.0,
                        silence_duration=0.060,
                        profile=profile,
                        handshake=True,
                        status=statuses.append,
                    )

                acknowledge.assert_called_once()
                ready = decode_control_message(acknowledge.call_args.args[0])
                self.assertEqual(ready.kind, CONTROL_READY)
                self.assertEqual(ready.profiles, (profile,))
                self.assertLess(
                    next(i for i, message in enumerate(statuses) if "plan repeated" in message),
                    next(i for i, message in enumerate(statuses) if "Preamble detected" in message),
                )
                self.assertEqual(demodulate(captured, profile=profile).data, data)

    def test_handshake_control_tones_alone_do_not_start_a_file_recording(self):
        profile = ToneProfile("auto-4000", 4_000.0, 200.0)
        wrong_profile = ToneProfile("auto-4800", 4_800.0, 200.0)
        signal = np.concatenate(
            (
                encode_control_message(CONTROL_PLAN, (wrong_profile,)),
                np.zeros(round(SAMPLE_RATE * 1.0), dtype=np.int16),
            )
        )
        stream = self._stream_for_signal(signal)
        module = _FakePyAudioModule(_FakeAudio(stream))
        with (
            patch("sonic_courier.live._pyaudio", return_value=module),
            patch("sonic_courier.live._play_handshake") as acknowledge,
            self.assertRaisesRegex(LiveAudioError, "no transmission detected"),
        ):
            record_transmission(
                timeout=3.0,
                profile=profile,
                handshake=True,
                status=lambda _message: None,
            )
        acknowledge.assert_not_called()

    def test_handshake_capture_keeps_extended_low_high_calibration(self):
        profile = ToneProfile("auto-4000", 4_000.0, 200.0)
        with patch("sonic_courier.protocol.PREAMBLE_DURATION", 5.0):
            payload = modulate(build_frame(b"extended", "long.bin"), profile=profile)
        signal = np.concatenate(
            (payload, np.zeros(round(SAMPLE_RATE * 0.10), dtype=np.int16))
        )
        module = _FakePyAudioModule(_FakeAudio(self._stream_for_signal(signal)))
        with patch("sonic_courier.live._pyaudio", return_value=module):
            captured = record_transmission(
                timeout=10.0,
                silence_duration=0.060,
                profile=profile,
                handshake=True,
                status=lambda _message: None,
            )
        self.assertEqual(demodulate(captured, profile=profile).data, b"extended")


if __name__ == "__main__":
    unittest.main()
