import unittest

import numpy as np

from sonic_courier.audio import SAMPLE_RATE
from sonic_courier.protocol import (
    HEADER,
    ProtocolError,
    build_frame,
    bytes_to_nibbles,
    demodulate,
    modulate,
    nibbles_to_bytes,
    parse_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_nibble_conversion_all_values(self):
        data = bytes(range(256))
        self.assertEqual(nibbles_to_bytes(bytes_to_nibbles(data)), data)

    def test_frame_round_trip(self):
        data = b"binary\x00\xff payload"
        self.assertEqual(parse_frame(build_frame(data, "demo.bin")), ("demo.bin", data))

    def test_filename_is_sanitized(self):
        name, _ = parse_frame(build_frame(b"x", "../../unsafe.bin"))
        self.assertEqual(name, "unsafe.bin")

    def test_crc_detects_corruption(self):
        frame = bytearray(build_frame(b"important", "data.bin"))
        frame[-1] ^= 1
        with self.assertRaisesRegex(ProtocolError, "CRC-32"):
            parse_frame(bytes(frame))

    def test_clean_signal_round_trip(self):
        data = bytes(range(32)) + b"Sonic Courier"
        result = demodulate(modulate(build_frame(data, "sample.bin")))
        self.assertEqual(result.filename, "sample.bin")
        self.assertEqual(result.data, data)

    def test_empty_file_round_trip(self):
        result = demodulate(modulate(build_frame(b"", "empty.dat")))
        self.assertEqual((result.filename, result.data), ("empty.dat", b""))

    def test_all_byte_values_over_audio(self):
        data = bytes(range(256))
        result = demodulate(modulate(build_frame(data, "all-bytes.bin")))
        self.assertEqual(result.data, data)

    def test_leading_silence_and_noise(self):
        rng = np.random.default_rng(42)
        data = b"works despite low background noise"
        signal = modulate(build_frame(data, "noise.txt")).astype(np.int32)
        signal += rng.normal(0, 80, signal.size).astype(np.int32)
        signal = np.clip(signal, -32768, 32767).astype(np.int16)
        leading = rng.normal(0, 30, round(SAMPLE_RATE * 0.17)).astype(np.int16)
        result = demodulate(np.concatenate((leading, signal)))
        self.assertEqual(result.data, data)

    def test_frequency_drift_calibrates_relatively(self):
        # Resampling without changing the declared rate simulates clock/pitch drift.
        data = b"relative calibration"
        original = modulate(build_frame(data, "drift.txt"))
        positions = np.arange(0, len(original) - 1, 1.003)
        drifted = np.interp(positions, np.arange(len(original)), original).astype(np.int16)
        result = demodulate(drifted, SAMPLE_RATE)
        self.assertEqual(result.data, data)

    def test_noise_and_clock_drift_matrix(self):
        rng = np.random.default_rng(1234)
        data = b"matrix-test-0123456789"
        original = modulate(build_frame(data, "matrix.bin"))
        for speed in (0.997, 1.0, 1.003):
            positions = np.arange(0, len(original) - 1, speed)
            resampled = np.interp(positions, np.arange(len(original)), original)
            for noise_rms in (0, 100, 300):
                noisy = resampled + rng.normal(0, noise_rms, resampled.size)
                signal = np.clip(noisy, -32768, 32767).astype(np.int16)
                with self.subTest(speed=speed, noise_rms=noise_rms):
                    self.assertEqual(demodulate(signal).data, data)

    def test_truncation_is_reported(self):
        signal = modulate(build_frame(b"not complete", "cut.bin"))
        with self.assertRaisesRegex(ProtocolError, "incomplete"):
            demodulate(signal[:-2000])

    def test_bad_magic_is_reported(self):
        frame = bytearray(build_frame(b"x", "x.bin"))
        frame[: HEADER.size] = b"XX01" + frame[4 : HEADER.size]
        with self.assertRaisesRegex(ProtocolError, "magic"):
            demodulate(modulate(bytes(frame)))


if __name__ == "__main__":
    unittest.main()
