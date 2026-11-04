import os
import queue
import stat
import subprocess
import sys
import tempfile
import threading
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

    def _run(
        self,
        *arguments: str,
        runner_exit: int = 0,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
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
            input=input_text,
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
            ["send", str(source), "--auto", "--confirm"],
        )

    def test_encode_accepts_the_file_before_the_flag(self):
        source = self.temp / "reordered.bin"
        source.write_bytes(b"test")

        result = self._run(str(source), "-e")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["send", str(source), "--auto", "--confirm"],
        )

    def test_fixed_profiles_keep_the_receiver_handshake(self):
        source = self.temp / "fixed.bin"
        source.write_bytes(b"test")

        for profile in ("compatible", "ultrasonic"):
            with self.subTest(profile=profile):
                self.arguments_file.unlink(missing_ok=True)
                result = self._run("-e", str(source), "--profile", profile)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self._forwarded_arguments(),
                    ["send", str(source), "--auto", "--profile", profile, "--confirm"],
                )

    def test_decode_forwards_exact_output_and_timeout(self):
        output = self.temp / "received file.bin"

        result = self._run("-d", "-o", str(output), "--timeout", "12.5")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["listen", "--auto", "--output-file", str(output), "--timeout", "12.5"],
        )

    def test_invalid_argument_combinations_do_not_run_courier(self):
        invalid_commands = (
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

    def test_no_arguments_opens_receive_menu_with_defaults(self):
        result = self._run(input_text="1\n\n\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            ["listen", "--auto", "--output-file", "received.bin", "--timeout", "120"],
        )

    def test_diagnostic_modes_are_forwarded(self):
        if self.script_name != "sonic-courier.sh":
            self.skipTest("Linux diagnostic wrapper modes")
        result = self._run("--doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._forwarded_arguments(), ["doctor"])

        result = self._run("--probe", "4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._forwarded_arguments(), ["probe", "--seconds", "4"])


@unittest.skipUnless(os.name == "nt", "Windows command wrapper test")
class WindowsWrapperTests(_WrapperContract, unittest.TestCase):
    script_name = "sonic-courier.cmd"

    def test_setup_cancellation_stops_before_environment_creation(self):
        # A stand-in supervisor cancels before executing venv, so this verifies
        # --setup's paths and exit handling without changing the real .venv.
        copied_script = self.temp / self.script_name
        copied_script.write_text(
            (PROJECT_ROOT / self.script_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        package = self.temp / "sonic_courier"
        package.mkdir()
        (package / "cancellation.py").write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['SONIC_COURIER_TEST_ARGS']).write_text("
            "'\\n'.join(sys.argv[1:]), encoding='utf-8')\n"
            "raise SystemExit(130)\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "SONIC_COURIER_PYTHON": sys.executable,
                "SONIC_COURIER_TEST_ARGS": str(self.arguments_file),
            }
        )
        result = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "call",
                str(copied_script),
                "--setup",
            ],
            cwd=self.temp,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(
            self._forwarded_arguments(),
            [sys.executable, "-m", "venv", "--clear", str(self.temp / ".venv")],
        )
        self.assertFalse((self.temp / ".venv").exists())

    def test_ultrasonic_menu_keeps_the_receiver_handshake(self):
        source = self.temp / "menu file with spaces.bin"
        source.write_bytes(b"menu payload")
        self._answer_menu(
            [
                ("Choose [1]: ", "2"),
                ("File to send: ", str(source)),
                ("Choose [1]: ", "3"),
            ]
        )
        self.assertEqual(
            self._forwarded_arguments(),
            ["send", str(source), "--auto", "--profile", "ultrasonic", "--confirm"],
        )

    def test_q_cancels_at_every_menu_prompt_before_starting_audio(self):
        paths = (
            [("Choose [1]: ", "q")],
            [("Choose [1]: ", "2"), ("File to send: ", "Q")],
            [
                ("Choose [1]: ", "2"),
                ("File to send: ", "unused.bin"),
                ("Choose [1]: ", "q"),
            ],
            [("Choose [1]: ", "1"), ("Save received file as [received.bin]: ", "q")],
            [
                ("Choose [1]: ", "1"),
                ("Save received file as [received.bin]: ", "unused.bin"),
                ("Listening timeout in seconds [120]: ", "q"),
            ],
            [("Choose [1]: ", "4"), ("Microphone test length in seconds [5]: ", "q")],
        )
        for answers in paths:
            with self.subTest(answers=answers):
                self._answer_menu(answers)
                self.assertFalse(self.arguments_file.exists())

    def _answer_menu(self, answers: list[tuple[str, str]]):
        # Feed each answer after its prompt. cmd.exe's set /p may discard later
        # lines if all answers are written to its input pipe at once.
        environment = os.environ.copy()
        environment.update(
            {
                "SONIC_COURIER_EXECUTABLE": str(self.fake_runner),
                "SONIC_COURIER_TEST_ARGS": str(self.arguments_file),
                "SONIC_COURIER_TEST_EXIT": "0",
            }
        )
        received: queue.Queue[str] = queue.Queue()
        with subprocess.Popen(
            self._wrapper_command([]),
            cwd=self.temp,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as process:
            def collect_output():
                for character in iter(lambda: process.stdout.read(1), ""):
                    received.put(character)

            reader = threading.Thread(target=collect_output, daemon=True)
            reader.start()
            try:
                for prompt, answer in answers:
                    output = ""
                    while not output.endswith(prompt):
                        output += received.get(timeout=10)
                    process.stdin.write(answer + "\n")
                    process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                reader.join(timeout=10)

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

    def test_q_cancels_at_every_menu_prompt_before_starting_audio(self):
        for answers in (
            "q\n",
            "2\nQ\n",
            "2\nunused.bin\nq\n",
            "1\nq\n",
            "1\nunused.bin\nq\n",
            "4\nq\n",
        ):
            with self.subTest(answers=answers):
                result = self._run(input_text=answers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.arguments_file.exists())

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
