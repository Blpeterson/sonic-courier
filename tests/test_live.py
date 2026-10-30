import unittest
from unittest.mock import patch

import numpy as np

from sonic_courier.audio import SAMPLE_RATE, generate_sine_wave
from sonic_courier.live import record_transmission


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.stopped = False
        self.closed = False

    def read(self, _chunk_size, *, exception_on_overflow):
        if exception_on_overflow:
            raise AssertionError("recording must tolerate input overflow")
        try:
            return next(self._chunks)
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

    def open(self, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
