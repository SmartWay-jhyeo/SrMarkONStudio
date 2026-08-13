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


def test_checksum_is_computed_over_utf8_bytes_not_codepoints():
    """🔴 펌웨어는 바이트를 XOR 한다. ord() 로 계산하면 값이 갈린다.

    ain*.unit 은 사용자가 자유롭게 넣는 문자열이라 '℃' 같은 값이 실제로
    들어온다. 코드포인트(0x2103)로 계산하면 한 바이트를 넘어 프레임
    형식까지 깨진다.
    """
    payload = "CFG,SET,ain0.unit,℃"
    expected = 0
    for b in payload.encode("utf-8"):
        expected ^= b
    assert xor_checksum(payload) == expected
    assert xor_checksum(payload) <= 0xFF


def test_non_ascii_value_survives_roundtrip():
    """한글·기호 단위가 왕복해도 그대로 나온다."""
    cmd = parse_line(build_command("CFG", "SET", "ain0.unit", "℃"))
    assert cmd.args == ("SET", "ain0.unit", "℃")


def test_non_ascii_line_keeps_two_hex_digit_checksum():
    """프레임 형식이 깨지지 않는다 — 체크섬은 항상 2자리다."""
    line = build_command("CFG", "SET", "ain0.unit", "바")
    star = line.rfind("*")
    assert len(line[star + 1 :].strip()) == 2


def test_lowercase_checksum_is_accepted():
    """수신은 관대하게, 송신은 엄격하게.

    규격은 대문자 2자리로 '만든다'고 정하지만, 수신측이 소문자를 거부할
    이유는 없다. 이 관용을 시험으로 못 박아 나중에 바뀌지 않게 한다.
    """
    assert parse_line("$HB*0a\r\n") == Command(verb="HB", args=())


def test_build_line_always_emits_uppercase():
    """송신은 규격대로 대문자다."""
    line = build_line("HB")
    assert line == "$HB*0A\r\n"


def test_parse_line_rejects_empty_payload():
    """'$*00' 은 체크섬이 맞아도 verb 가 없어 의미가 없다."""
    with pytest.raises(MalformedLineError):
        parse_line("$*00\r\n")
