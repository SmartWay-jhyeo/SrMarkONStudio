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

    전선 위 값은 전부 ASCII 이므로(§3) 실제로는 `ord(ch)` 와 결과가 같다.
    그래도 바이트로 정의하는 이유는 펌웨어가 바이트를 XOR 하기 때문이다 —
    정의를 일치시켜 두면 나중에 누가 non-ASCII 를 흘려보내도 체크섬이
    3자리로 터지는 대신 조용히 불일치로 잡힌다.
    """
    cs = 0
    for b in payload.encode("utf-8"):
        cs ^= b
    return cs


def build_line(payload: str) -> str:
    """payload 를 체크섬과 줄끝이 붙은 완성된 줄로 만든다.

    🔴 **한 번 호출하면 정확히 한 줄이 나온다는 것을 여기서 보장한다.**

    payload 에 제어문자가 섞이면 이 함수가 만든 결과가 전송 도중 여러 줄로
    쪼개진다. 그러면 그 조각 하나가 완결된 명령이 될 수 있다:

        build_command("CFG", "SET", "dev.id", "x\\r\\n$HB*0A\\r\\n")
        → '$CFG,SET,dev.id,x\\r\\n$HB*0A\\r\\n*75\\r\\n'
        → 세 줄로 쪼개짐: ['$CFG,SET,dev.id,x', '$HB*0A', '*75']
                                                 ^^^^^^^^ 유효한 하트비트

    가운데 줄은 체크섬까지 맞는 진짜 `$HB` 라서 보드가 CONFIG 모드를 연다.
    설정값 하나로 명령이 주입된다.

    설정 계층에도 같은 검사가 있지만 그것만으로는 부족하다. 위 경로에서
    악성 값은 **보드의 설정 검증에 도달하기 전에** 줄이 쪼개지므로, 보드는
    그것을 하나의 값으로 본 적이 없다. 프레이밍 계층에서 막아야 모든
    호출자가 보호된다.

    Raises:
        MalformedLineError: payload 에 제어문자가 있는 경우.
    """
    bad = [c for c in payload if ord(c) < 0x20 or ord(c) == 0x7F]
    if bad:
        raise MalformedLineError(f"제어문자는 줄을 쪼갠다: {bad!r}")
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
