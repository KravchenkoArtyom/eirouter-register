"""Mirror console output into a separate UTF-8 log for each CLI run.

`mask_secrets` is shared with the WebUI run log: neither file should keep API
keys or proxy credentials in plain text.
"""

import re
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path


def mask_secrets(line: str) -> str:
    """Скрыть ключи и пароли прокси в строке лога."""
    line = re.sub(r"sk-[A-Za-z0-9_*-]+", "[REDACTED_KEY]", line)
    return re.sub(r"(\w+://)[^\s/@]+@", r"\1[REDACTED]@", line)


class ConsoleLog:
    def __init__(self, console, logfile, lock):
        self.console = console
        self.logfile = logfile
        self.lock = lock
        self.pending = ""

    def write(self, text):
        with self.lock:
            self.console.write(text)
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self._record(line)
        return len(text)

    def _record(self, line):
        line = mask_secrets(line)
        self.logfile.write(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {line}\n")
        self.logfile.flush()

    def flush(self):
        with self.lock:
            self.console.flush()
            if self.pending:
                self._record(self.pending)
                self.pending = ""
            self.logfile.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def run_logged(entrypoint, directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run_{datetime.now():%Y-%m-%d_%H-%M-%S_%f}.log"
    with path.open("x", encoding="utf-8") as logfile:
        lock = threading.RLock()
        stdout = ConsoleLog(sys.stdout, logfile, lock)
        stderr = ConsoleLog(sys.stderr, logfile, lock)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = 1
            print(f"[log] File: {path}", flush=True)
            try:
                result = entrypoint()
            except SystemExit as error:
                result = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
                raise
            except Exception as error:
                # Exception bodies can contain credentials or response payloads.
                print(f"[log] Unexpected error: {type(error).__name__}", flush=True)
                raise
            finally:
                stdout.flush()
                stderr.flush()
                print(f"[log] Exit code: {result}", flush=True)
    return result
