"""SerialTransport 가 조각난 UTF-8 바이트열을 올바르게 복원하는지 시험한다.

🔴 회귀 배경 (HANDOFF.md, 2026-08-19 실증) — GUI 의 NDJSON 필드 마스크 패널에
"시간 품질"이 "???간 품질"로 깨져 나타났다. 원인은 `SerialTransport.read_lines()`
가 `self._ser.read()` 로 읽은 조각을 **조각마다** `decode("utf-8", errors="replace")`
했기 때문이다. 한글은 UTF-8 로 3바이트인데 그 3바이트가 두 번의 `read()` 에
걸쳐 나뉘면 각 조각이 불완전한 바이트열이 되고, `errors="replace"` 가 그것을
되돌릴 수 없는 `�`(물음표로 보이는 대체문자)로 바꿔 버린다.

고친 방식은 바이트로 모았다가 `\n` 로 자른 **완성된 줄만** 디코딩하는 것이다.
`\n`(0x0A)은 UTF-8 연속 바이트(0x80~0xBF)와 값이 겹치지 않으므로 항상 문자
경계이자 줄 경계다 — 조각이 어디서 끊기든 줄 전체가 모인 다음에만
디코딩하면 멀티바이트 문자가 갈라질 수 없다.
"""

import pytest

from host.service.board_service import SerialTransport


class FakeSerialSource:
    """조각으로 나뉜 바이트를 순서대로 내놓는 가짜 시리얼.

    실기기에서 `in_waiting`이 항상 완결된 문자 수만큼만 차 있다는 보장은
    없다 — DMA/드라이버 버퍼가 아무 바이트 수에서나 끊어 넘겨줄 수 있다.
    이 페이크는 주어진 조각을 `read()` 호출마다 하나씩 내놓아 그 상황을
    재현한다.
    """

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    @property
    def in_waiting(self):
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, _n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def _make_transport(chunks: list[bytes]) -> SerialTransport:
    """`SerialTransport.__init__` 을 건너뛴다 — 실제 포트를 열지 않기 위해서다."""
    st = SerialTransport.__new__(SerialTransport)
    st._ser = FakeSerialSource(chunks)
    st._buf = b""
    return st


def _pump_all(st: SerialTransport, calls: int) -> list[str]:
    """`read_lines()` 를 여러 번 불러 페이크가 가진 조각을 전부 소비한다."""
    out: list[str] = []
    for _ in range(calls):
        out.extend(st.read_lines())
    return out


LABEL = "시간 품질"
_PREFIX = '{"label":"'  # 전부 ASCII — 그 뒤 첫 한글 문자의 바이트 시작 위치를 잡는 기준
LINE = f'{_PREFIX}{LABEL}"}}'
LINE_BYTES = (LINE + "\n").encode("utf-8")
#: 첫 한글 문자("시", UTF-8 3바이트)가 시작하는 바이트 오프셋.
_FIRST_KOREAN_CHAR_OFFSET = len(_PREFIX.encode("utf-8"))


@pytest.mark.parametrize(
    "offset_in_char", [1, 2], ids=["1바이트째에서 절단", "2바이트째에서 절단"]
)
def test_multibyte_char_split_across_chunks_is_reconstructed(offset_in_char):
    """한글 3바이트 문자 중간에서 조각이 끊겨도 온전히 복원된다.

    🔴 이 시험은 고치기 전 코드(조각마다 디코딩)에서는 실패해야 한다 —
    각 조각이 불완전한 바이트열이라 `�` 로 깨진다.
    """
    split_at = _FIRST_KOREAN_CHAR_OFFSET + offset_in_char
    chunks = [LINE_BYTES[:split_at], LINE_BYTES[split_at:]]
    st = _make_transport(chunks)
    lines = _pump_all(st, calls=2)
    assert lines == [LINE]
    assert "�" not in lines[0]


def test_line_split_into_many_small_chunks_is_reconstructed():
    """줄이 여러 조각(1바이트씩)에 걸쳐 와도 한 줄로 합쳐진다."""
    chunks = [LINE_BYTES[i:i + 1] for i in range(len(LINE_BYTES))]
    st = _make_transport(chunks)
    lines = _pump_all(st, calls=len(chunks))
    assert lines == [LINE]


def test_multiple_lines_in_one_chunk_are_all_yielded():
    """한 조각에 여러 줄이 들어와도 다 나온다."""
    data = (LINE + "\n" + LINE + "\n").encode("utf-8")
    st = _make_transport([data])
    lines = _pump_all(st, calls=1)
    assert lines == [LINE, LINE]


def test_blank_lines_and_crlf_are_handled_like_before():
    """빈 줄은 걸러지고 `\\r\\n` 은 strip 으로 지워진다 — 기존 동작과 같다."""
    data = b"\r\n" + LINE_BYTES[:-1] + b"\r\n" + b"\n"
    st = _make_transport([data])
    lines = _pump_all(st, calls=1)
    assert lines == [LINE]


def test_corrupted_bytes_are_replaced_not_silently_dropped():
    """물리 손상으로 유효하지 않은 UTF-8 이 오면 줄 자체는 남기고 대체문자로 채운다.

    근거: `_record_raw()`(board_service.py)는 "파싱 성패와 무관하게 모든 수신
    줄"을 통계·raw_lines 에 남기는 것을 불변조건으로 삼는다(손상을 놓치지
    않기 위해서). 여기서 줄을 통째로 버리면 그 불변조건이 깨지고 GUI 의
    원문 패널에서 손상 자체가 사라진다. 반대로 대체문자로 채워 두면 줄은
    보이고, JSON 파싱이 깨지는 대부분의 경우 `corrupt_total` 로도 잡힌다
    (host/core/records.py 의 json.loads 실패 경로). 완전한 무결성 보장은
    아니지만(대체문자가 우연히 유효한 JSON 문자열 안에 들어가면 못 잡는다),
    프로토콜에 CRC 가 없는 현재 구조에서는 "줄을 지운다"보다 "보이게 남긴다"
    쪽이 이 코드베이스의 원칙에 맞는다.
    """
    # 0xFF 는 어떤 UTF-8 시퀀스로도 유효하지 않다.
    data = b'{"label":"\xffAB"}\n'
    st = _make_transport([data])
    lines = _pump_all(st, calls=1)
    assert len(lines) == 1
    assert "�" in lines[0]
