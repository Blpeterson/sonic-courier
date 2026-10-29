import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_wav_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tiny.bin"
            wav = root / "courier.wav"
            recovered = root / "out.bin"
            source.write_bytes(bytes(range(64)))
            encoded = subprocess.run(
                [sys.executable, "-m", "sonic_courier", "encode", str(source), "-o", str(wav)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(encoded.returncode, 0, encoded.stderr)
            decoded = subprocess.run(
                [sys.executable, "-m", "sonic_courier", "decode", str(wav), "-o", str(recovered)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(recovered.read_bytes(), source.read_bytes())

    def test_missing_input_returns_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "sonic_courier", "encode", "missing.file"],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
