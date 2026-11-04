"""Cooperative single-key cancellation for interactive live audio commands."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

_cancelled = threading.Event()
_POLL_INTERVAL = 0.05


class CancelledError(Exception):
    """Raised at a safe checkpoint after the user presses Q."""


def check_cancelled() -> None:
    """Stop a live operation without throwing into an unrelated thread."""
    if _cancelled.is_set():
        raise CancelledError("Cancelled by user.")


@contextmanager
def _terminal_reader() -> Iterator[Callable[[], str | None] | None]:
    """Temporarily allow one-key input, leaving redirected stdin untouched."""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError, ValueError):
        interactive = False
    if not interactive:
        yield None
        return

    if os.name == "nt":
        import msvcrt

        def read_windows() -> str | None:
            return msvcrt.getwch() if msvcrt.kbhit() else None

        yield read_windows
        return

    import select
    import termios
    import tty

    try:
        descriptor = sys.stdin.fileno()
        original = termios.tcgetattr(descriptor)
        tty.setcbreak(descriptor, when=termios.TCSANOW)
    except (AttributeError, OSError, ValueError, termios.error):
        yield None
        return

    def read_posix() -> str | None:
        ready, _, _ = select.select([descriptor], [], [], 0)
        if not ready:
            return None
        raw = os.read(descriptor, 1)
        if not raw:
            raise EOFError
        return raw.decode("ascii", errors="ignore")

    try:
        yield read_posix
    finally:
        try:
            termios.tcsetattr(descriptor, termios.TCSANOW, original)
        except (OSError, termios.error):
            # A closed/disconnected terminal must not obscure cancellation.
            pass


def _watch_keys(read_key: Callable[[], str | None], stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            key = read_key()
        except (EOFError, OSError, ValueError):
            return
        if key in {"q", "Q"}:
            _cancelled.set()
            return
        stopped.wait(_POLL_INTERVAL)


@contextmanager
def keyboard_cancellation() -> Iterator[None]:
    """Watch for Q until the command finishes and always restore the terminal.

    Use this around the live part of one command, after line-oriented prompts.
    Audio loops call ``check_cancelled`` between short reads or writes. Native
    audio drivers must return from the current read/write before cancellation
    can be handled. Ctrl+C continues to work when stdin is not a terminal.
    """
    _cancelled.clear()
    with _terminal_reader() as read_key:
        if read_key is None:
            yield
            return
        stopped = threading.Event()
        watcher = threading.Thread(
            target=_watch_keys,
            args=(read_key, stopped),
            name="sonic-courier-cancel",
            daemon=True,
        )
        watcher.start()
        try:
            yield
        finally:
            stopped.set()
            watcher.join()


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop only the process group started by this supervisor."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode and process.poll() is None:
                process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=3.0)


def supervise(command: Sequence[str]) -> int:
    """Run a noninteractive installer with Q/Ctrl+C cancellation of its tree.

    This file can be executed directly with a system Python before the project
    and its dependencies have been installed. No package imports are needed.
    """
    if not command:
        print("usage: cancellation.py COMMAND [ARG ...]", file=sys.stderr)
        return 2
    process = None
    try:
        with keyboard_cancellation():
            print("[setup] Press Q or Ctrl+C to cancel.", flush=True)
            process_options = {"stdin": subprocess.DEVNULL}
            if os.name == "nt":
                process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                process_options["start_new_session"] = True
            process = subprocess.Popen(list(command), **process_options)
            while True:
                check_cancelled()
                try:
                    return process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    continue
    except (CancelledError, KeyboardInterrupt):
        if process is not None:
            _terminate_process_tree(process)
        print("\nCancelled.", flush=True)
        return 130
    except OSError as exc:
        print(f"error: could not run setup command: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1:]))
