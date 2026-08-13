"""NMEA 방식 $ 줄의 조립과 파싱.

체크섬은 $ 와 * 사이 문자 전부의 XOR, 대문자 2자리 16진수다.
규격: protocol/specification.md §3
"""

from dataclasses import dataclass

from host.core.errors import ChecksumError, MalformedLineError

LINE_END = "\r\n"


@dataclass(frozen=True)
class Command:
    """파싱된 $ 줄. verb 는 첫 토큰, args 는 나머지."""

    verb: str
    args: tuple[str, ...] = ()


def xor_checksum(payload: str) -> int:
    """payload 전체 문자의 XOR."""
    cs = 0
    for ch in payload:
        cs ^= ord(ch)
    return cs


def build_line(payload: str) -> str:
    """payload 를 체크섬과 줄끝이 붙은 완성된 줄로 만든다."""
    return f"${payload}*{xor_checksum(payload):02X}{LINE_END}"


def build_command(verb: str, *args: str) -> str:
    """verb 와 인자를 쉼표로 이어 완성된 줄로 만든다."""
    payload = ",".join((verb, *args))
    return build_line(payload)


def parse_line(line: str) -> Command:
    """완성된 줄을 Command 로 파싱한다.

    Raises:
        MalformedLineError: $ 또는 * 가 없는 줄.
        ChecksumError: 체크섬 불일치.
    """
    stripped = line.strip()
    if not stripped.startswith("$"):
        raise MalformedLineError(f"'$' 로 시작하지 않음: {line!r}")

    body = stripped[1:]
    star = body.rfind("*")
    if star < 0:
        raise MalformedLineError(f"'*' 없음: {line!r}")

    payload = body[:star]
    given = body[star + 1 :]
    expected = f"{xor_checksum(payload):02X}"
    if given.upper() != expected:
        raise ChecksumError(f"체크섬 불일치: 받음 {given!r}, 계산 {expected!r}")

    tokens = payload.split(",")
    return Command(verb=tokens[0], args=tuple(tokens[1:]))
