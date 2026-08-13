import pytest

from host.core.framing import (
    Command,
    build_command,
    build_line,
    parse_line,
    xor_checksum,
)
from host.core.errors import ChecksumError, MalformedLineError


def test_xor_checksum_known_vectors():
    """규격서 protocol/specification.md §3 의 검증 벡터."""
    assert xor_checksum("HB") == 0x0A
    assert xor_checksum("ID") == 0x0D


def test_build_line_appends_checksum_and_crlf():
    assert build_line("HB") == "$HB*0A\r\n"


def test_build_command_joins_args_with_comma():
    line = build_command("CFG", "GET", "tx.period_ms")
    assert line.startswith("$CFG,GET,tx.period_ms*")
    assert line.endswith("\r\n")


def test_parse_line_returns_verb_and_args():
    cmd = parse_line(build_command("CFG", "SET", "tx.period_ms", "250"))
    assert cmd == Command(verb="CFG", args=("SET", "tx.period_ms", "250"))


def test_parse_line_accepts_bare_lf():
    """수신측은 \\n 만 와도 받아들인다 (규격 §2)."""
    assert parse_line("$HB*0A\n") == Command(verb="HB", args=())


def test_parse_line_rejects_bad_checksum():
    with pytest.raises(ChecksumError):
        parse_line("$HB*FF\r\n")


def test_parse_line_rejects_missing_dollar():
    with pytest.raises(MalformedLineError):
        parse_line("HB*0A\r\n")


def test_parse_line_rejects_missing_star():
    with pytest.raises(MalformedLineError):
        parse_line("$HB0A\r\n")


def test_roundtrip_preserves_args():
    original = ("SET", "ain0.unit", "bar")
    cmd = parse_line(build_command("CFG", *original))
    assert cmd.args == original
