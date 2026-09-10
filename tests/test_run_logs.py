import sys
from pathlib import Path

import pytest

from core.run_logs import run_logged


def test_run_logs_preserve_console_and_mask_file(tmp_path: Path, capsys):
    key = "sk-" + "a" * 40

    def entrypoint():
        sys.stdout.write(key[:10])
        sys.stdout.write(key[10:] + "\n")
        print("http://user:password@proxy.test:8080", file=sys.stderr)
        print("last partial line", end="")
        return 1

    assert run_logged(entrypoint, tmp_path / "logs") == 1
    assert run_logged(lambda: 0, tmp_path / "logs") == 0
    files = sorted((tmp_path / "logs").glob("*.log"))
    assert len(files) == 2
    first = files[0].read_text(encoding="utf-8")
    assert key not in first
    assert "password" not in first
    assert "[REDACTED_KEY]" in first
    assert "http://[REDACTED]@proxy.test:8080" in first
    assert "last partial line" in first
    assert "Exit code: 1" in first
    assert "Exit code: 0" in files[1].read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert key in captured.out
    assert "proxy.test" in captured.err


def test_run_logs_record_parser_exit_and_unexpected_error(tmp_path: Path):
    original_stdout, original_stderr = sys.stdout, sys.stderr

    def parser_error():
        print("invalid count", file=sys.stderr)
        raise SystemExit(2)

    with pytest.raises(SystemExit) as error:
        run_logged(parser_error, tmp_path)
    assert error.value.code == 2
    assert sys.stdout is original_stdout and sys.stderr is original_stderr

    def unexpected_error():
        raise LookupError("secret response")

    with pytest.raises(LookupError):
        run_logged(unexpected_error, tmp_path)
    first, second = [path.read_text(encoding="utf-8") for path in sorted(tmp_path.glob("*.log"))]
    assert "invalid count" in first and "Exit code: 2" in first
    assert "LookupError" in second and "Exit code: 1" in second
    assert "secret response" not in second
