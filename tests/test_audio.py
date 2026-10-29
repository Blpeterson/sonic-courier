import tempfile
import unittest
from pathlib import Path

import numpy as np

from sonic_courier.audio import (
    SAMPLE_RATE,
    AudioFormatError,
    find_dominant_frequency,
    generate_sine_wave,
    read_wav,
    write_wav,
)


class AudioTests(unittest.TestCase):
    def test_tone_frequency_and_shape(self):
        tone = generate_sine_wave(18_750, 0.04)
        self.assertEqual(tone.dtype, np.int16)
        self.assertEqual(len(tone), 1764)
        self.assertAlmostEqual(find_dominant_frequency(tone), 18_750, delta=2)
        self.assertLess(abs(int(tone[0])), 2)

    def test_frequency_validation(self):
        with self.assertRaises(ValueError):
            generate_sine_wave(SAMPLE_RATE / 2, 0.04)

    def test_wav_round_trip(self):
        samples = generate_sine_wave(17_000, 0.1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            write_wav(path, samples)
            actual, rate = read_wav(path)
        self.assertEqual(rate, SAMPLE_RATE)
        np.testing.assert_array_equal(actual, samples)

    def test_rejects_non_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.wav"
            path.write_bytes(b"not a wav")
            with self.assertRaises(AudioFormatError):
                read_wav(path)


if __name__ == "__main__":
    unittest.main()
