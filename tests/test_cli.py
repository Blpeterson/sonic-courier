import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from sonic_courier.cli import main
from sonic_courier.live import AudioDeviceSummary, LiveAudioError
from sonic_courier.protocol import build_frame, modulate


class CliTests(unittest.TestCase):
    @patch("sonic_courier.cli.inspect_devices")
    def test_doctor_reports_ready(self, inspect_devices):
        inspect_devices.return_value = AudioDeviceSummary(4, 2, 2, "Test Mic", "Test Speaker")
        output = StringIO()
        with redirect_stdout(output):
            result = main(["doctor"])
        self.assertEqual(result, 0)
        self.assertIn("Live audio: READY", output.getvalue())
        self.assertIn("Test Mic", output.getvalue())
        self.assertIn("12.2-15.2 kHz (compatible)", output.getvalue())

    @patch("sonic_courier.cli.inspect_devices", side_effect=LiveAudioError("PyAudio missing"))
    def test_doctor_reports_missing_live_support(self, _inspect_devices):
        output = StringIO()
        with redirect_stdout(output):
            result = main(["doctor"])
        self.assertEqual(result, 1)
        self.assertIn("Live audio: NOT READY", output.getvalue())

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
            self.assertIn("compatible profile", encoded.stdout)
            decoded = subprocess.run(
                [sys.executable, "-m", "sonic_courier", "decode", str(wav), "-o", str(recovered)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(recovered.read_bytes(), source.read_bytes())

    def test_ultrasonic_profile_wav_remains_cli_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.bin"
            wav = root / "legacy.wav"
            recovered = root / "recovered.bin"
            source.write_bytes(b"legacy profile")
            encoded = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sonic_courier",
                    "encode",
                    str(source),
                    "-o",
                    str(wav),
                    "--profile",
                    "ultrasonic",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(encoded.returncode, 0, encoded.stderr)
            self.assertIn("ultrasonic profile", encoded.stdout)
            decoded = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sonic_courier",
                    "decode",
                    str(wav),
                    "-o",
                    str(recovered),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertIn("17000.0/21250.0 Hz", decoded.stdout)
            self.assertEqual(recovered.read_bytes(), source.read_bytes())

    @patch("sonic_courier.cli.record_transmission")
    def test_listen_can_write_an_exact_extensionless_filename(self, record_transmission):
        record_transmission.return_value = modulate(build_frame(b"received", "original.bin"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chosen-name"
            result = main(["listen", "--output-file", str(output), "--timeout", "1"])

            self.assertEqual(result, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), b"received")

    def test_confirmed_send_encodes_before_playing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tiny.bin"
            source.write_bytes(b"x")
            output = StringIO()
            with (
                patch("builtins.input", return_value="yes"),
                patch("sonic_courier.cli.play") as play,
                redirect_stdout(output),
            ):
                result = main(["send", str(source), "--confirm"])

            self.assertEqual(result, 0)
            self.assertIn("Encoded 1 bytes", output.getvalue())
            play.assert_called_once()

    def test_declined_send_does_not_play(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tiny.bin"
            source.write_bytes(b"x")
            output = StringIO()
            with (
                patch("builtins.input", return_value="n"),
                patch("sonic_courier.cli.play") as play,
                redirect_stdout(output),
            ):
                result = main(["send", str(source), "--confirm"])

            self.assertEqual(result, 0)
            self.assertIn("Playback cancelled", output.getvalue())
            play.assert_not_called()

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
