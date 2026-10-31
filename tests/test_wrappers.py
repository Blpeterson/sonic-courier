import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _WrapperContract:
    script_name: str

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)
        self.arguments_file = self.temp / "forwarded arguments.txt"
        self.fake_runner = self._create_fake_runner()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_fake_runner(self) -> Path:
        raise NotImplementedError

    def _wrapper_command(self, arguments: list[str]) -> list[str]:
        raise NotImplementedError

    def _run(self, *arguments: str, runner_exit: int = 0) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "SONIC_COURIER_EXECUTABLE": str(self.fake_runner),
                "SONIC_COURIER_TEST_ARGS": str(self.arguments_file),
                "SONIC_COURIER_TEST_EXIT": str(runner_exit),
            }
        )
        return subprocess.run(
            self._wrapper_command(list(arguments)),
            cwd=self.temp,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
        )

    def _forwarded_arguments(self) -> list[str]:
        return self.arguments_file.read_text(encoding="utf-8").splitlines()

    def test_help_succeeds_without_running_courier(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-e", result.stdout)
        self.assertIn("-d", result.stdout)
        self.assertFalse(self.arguments_file.exists())

    def test_encode_forwards_a_file_path_containing_spaces(self):
        source = self.temp / "file with spaces.bin"
        source.write_bytes(b"test")

        result = self._run("-e", str(source))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["send", str(source), "--profile", "compatible", "--confirm"],
        )

    def test_encode_accepts_the_file_before_the_flag(self):
        source = self.temp / "reordered.bin"
        source.write_bytes(b"test")

        result = self._run(str(source), "-e")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["send", str(source), "--profile", "compatible", "--confirm"],
        )

    def test_decode_forwards_exact_output_and_timeout(self):
        output = self.temp / "received file.bin"

        result = self._run("-d", "-o", str(output), "--timeout", "12.5")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["listen", "--output-file", str(output), "--timeout", "12.5"],
        )

    def test_invalid_argument_combinations_do_not_run_courier(self):
        invalid_commands = (
            (),
            ("-e",),
            ("-d",),
            ("-e", "-d", "file.bin", "-o", "received.bin"),
            ("-d", "unexpected.bin", "-o", "received.bin"),
        )
        for command in invalid_commands:
            with self.subTest(command=command):
                self.arguments_file.unlink(missing_ok=True)
                result = self._run(*command)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.arguments_file.exists())

    def test_courier_exit_status_is_propagated(self):
        source = self.temp / "failure.bin"
        source.write_bytes(b"test")

        result = self._run("-e", str(source), runner_exit=7)

        self.assertEqual(result.returncode, 7)


@unittest.skipUnless(os.name == "nt", "Windows command wrapper test")
class WindowsWrapperTests(_WrapperContract, unittest.TestCase):
    script_name = "sonic-courier.cmd"

    def _create_fake_runner(self) -> Path:
        runner = self.temp / "fake courier.cmd"
        runner.write_text(
            "@echo off\n"
            ":next_argument\n"
            "if \"%~1\"==\"\" goto finished\n"
            ">>\"%SONIC_COURIER_TEST_ARGS%\" echo(%~1\n"
            "shift\n"
            "goto next_argument\n"
            ":finished\n"
            "exit /b %SONIC_COURIER_TEST_EXIT%\n",
            encoding="utf-8",
        )
        return runner

    def _wrapper_command(self, arguments: list[str]) -> list[str]:
        command_processor = os.environ.get("COMSPEC", "cmd.exe")
        return [
            command_processor,
            "/d",
            "/c",
            "call",
            str(PROJECT_ROOT / self.script_name),
            *arguments,
        ]


@unittest.skipUnless(os.name == "posix", "POSIX shell wrapper test")
class PosixWrapperTests(_WrapperContract, unittest.TestCase):
    script_name = "sonic-courier.sh"

    def _create_fake_runner(self) -> Path:
        runner = self.temp / "fake courier"
        runner.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$@" > "$SONIC_COURIER_TEST_ARGS"\n'
            'exit "${SONIC_COURIER_TEST_EXIT:-0}"\n',
            encoding="utf-8",
        )
        runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
        return runner

    def _wrapper_command(self, arguments: list[str]) -> list[str]:
        return [str(PROJECT_ROOT / self.script_name), *arguments]


if __name__ == "__main__":
    unittest.main()
