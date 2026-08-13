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
    """payload 를 UTF-8 로 인코딩한 **바이트**들의 XOR.

    🔴 문자가 아니라 바이트다. `ord(ch)` 로 계산하면 코드포인트가 0xFF 를
    넘는 문자에서 결과가 한 바이트를 벗어나 `$...*XX` 프레임이 깨지고,
    바이트를 XOR 하는 펌웨어와 값이 갈린다.

    `ain*.unit` 은 사용자가 자유롭게 넣는 문자열이라 `℃`·`바` 같은 값이
    실제로 들어온다. 가정이 아니라 닿는 경로다.
    """
    cs = 0
    for b in payload.encode("utf-8"):
        cs ^= b
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
    if not payload:
        raise MalformedLineError(f"빈 payload: {line!r}")
    given = body[star + 1 :]
    expected = f"{xor_checksum(payload):02X}"
    if given.upper() != expected:
        raise ChecksumError(f"체크섬 불일치: 받음 {given!r}, 계산 {expected!r}")

    tokens = payload.split(",")
    return Command(verb=tokens[0], args=tuple(tokens[1:]))
