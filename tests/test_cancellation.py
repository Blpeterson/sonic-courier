import io
import os
import subprocess
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from sonic_courier import cancellation
from sonic_courier.cancellation import CancelledError, check_cancelled, keyboard_cancellation


class CancellationTests(unittest.TestCase):
    def tearDown(self):
        cancellation._cancelled.clear()

    def test_q_requests_cooperative_cancellation_and_restores_on_error(self):
        restored = Mock()

        @contextmanager
        def reader():
            try:
                yield lambda: "Q"
            finally:
                restored()

        with (
            patch("sonic_courier.cancellation._terminal_reader", reader),
            self.assertRaises(CancelledError),
            keyboard_cancellation(),
        ):
            self.assertTrue(cancellation._cancelled.wait(1.0))
            check_cancelled()

        restored.assert_called_once()
        self.assertFalse(
            any(thread.name == "sonic-courier-cancel" for thread in threading.enumerate())
        )

    def test_next_command_clears_previous_cancel_and_leaves_piped_input_untouched(self):
        cancellation._cancelled.set()
        piped = io.StringIO("q\nremaining input\n")
        with patch("sys.stdin", piped), keyboard_cancellation():
            check_cancelled()
        self.assertEqual(piped.read(), "q\nremaining input\n")

    def test_other_keys_do_not_cancel(self):
        stopped = threading.Event()

        def read_key():
            stopped.set()
            return "x"

        cancellation._watch_keys(read_key, stopped)
        check_cancelled()

    @unittest.skipUnless(os.name == "nt", "Windows single-key input")
    def test_windows_reader_only_reads_when_a_key_is_available(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        with (
            patch("sys.stdin", terminal),
            patch("msvcrt.kbhit", side_effect=(False, True)),
            patch("msvcrt.getwch", return_value="q") as read_key,
            cancellation._terminal_reader() as reader,
        ):
            self.assertIsNone(reader())
            read_key.assert_not_called()
            self.assertEqual(reader(), "q")

        read_key.assert_called_once()

    def test_supervisor_propagates_child_exit_and_protects_parent_stdin(self):
        process = Mock()
        process.wait.return_value = 7
        with (
            patch("sys.stdin", io.StringIO()),
            patch("subprocess.Popen", return_value=process) as launch,
        ):
            result = cancellation.supervise(["python", "-m", "pip", "install", ".[live]"])

        self.assertEqual(result, 7)
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(launch.call_args.args[0], ["python", "-m", "pip", "install", ".[live]"])
        if os.name == "nt":
            self.assertEqual(
                launch.call_args.kwargs["creationflags"], subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_supervisor_cancel_stops_child_tree_and_returns_130(self):
        process = Mock()
        with (
            patch("sys.stdin", io.StringIO()),
            patch("subprocess.Popen", return_value=process),
            patch("sonic_courier.cancellation.check_cancelled", side_effect=CancelledError),
            patch("sonic_courier.cancellation._terminate_process_tree") as terminate,
        ):
            result = cancellation.supervise(["python", "-m", "venv", ".venv"])

        self.assertEqual(result, 130)
        terminate.assert_called_once_with(process)

    def test_supervisor_ctrl_c_stops_child_tree(self):
        process = Mock()
        process.wait.side_effect = KeyboardInterrupt
        with (
            patch("sys.stdin", io.StringIO()),
            patch("subprocess.Popen", return_value=process),
            patch("sonic_courier.cancellation._terminate_process_tree") as terminate,
        ):
            result = cancellation.supervise(["python", "-m", "venv", ".venv"])

        self.assertEqual(result, 130)
        terminate.assert_called_once_with(process)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree termination")
    def test_windows_tree_cleanup_targets_only_the_supervised_pid(self):
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        result = Mock(returncode=0)
        with patch("subprocess.run", return_value=result) as run:
            cancellation._terminate_process_tree(process)

        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"])
        process.wait.assert_called_once_with(timeout=3.0)

    @unittest.skipIf(os.name == "nt", "POSIX terminal restoration")
    def test_posix_terminal_attributes_are_restored_on_command_error(self):
        import termios

        terminal = Mock()
        terminal.isatty.return_value = True
        terminal.fileno.return_value = 42
        original = [1, 2, 3]
        with (
            patch("sys.stdin", terminal),
            patch("termios.tcgetattr", return_value=original),
            patch("tty.setcbreak") as cbreak,
            patch("termios.tcsetattr") as restore,
            self.assertRaisesRegex(RuntimeError, "audio failed"),
            cancellation._terminal_reader(),
        ):
            raise RuntimeError("audio failed")

        cbreak.assert_called_once_with(42, when=termios.TCSANOW)
        restore.assert_called_once_with(42, termios.TCSANOW, original)


if __name__ == "__main__":
    unittest.main()
