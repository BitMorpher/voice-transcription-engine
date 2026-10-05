"""Lossless UTF-8 writes under simulated Windows text newline translation."""

import os

import pytest

from src.private_output import write_private


@pytest.mark.parametrize("text", ["LF\n", "CRLF\r\n", "CR\r", "न +²\n\r\n\r"])
@pytest.mark.parametrize("replace", [False, True])
def test_exact_bytes_with_windows_newline_translation(tmp_path, monkeypatch, text, replace):
    real_fdopen = os.fdopen

    def windows_fdopen(fd, mode, **kwargs):
        if "b" not in mode and kwargs.get("newline") is None:
            kwargs["newline"] = "\r\n"
        return real_fdopen(fd, mode, **kwargs)

    monkeypatch.setattr("src.private_output.os.fdopen", windows_fdopen)
    target = tmp_path / "synthetic-transcript.txt"
    if replace:
        target.write_bytes(b"Previous synthetic text.")
    write_private(target, text, replace=replace)
    assert target.read_bytes() == text.encode("utf-8")
