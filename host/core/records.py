"""NDJSON 텔레메트리 레코드 파싱과 seq 누락 추적.

규격: protocol/specification.md §7
"""

import json

from host.core.errors import ProtocolError

SCHEMA_VER = 3

#: 필드 마스크로 끌 수 없는 필드 (규격 §7.1)
MANDATORY_FIELDS = ("schema_ver", "seq", "t", "type")

#: seq 는 uint32
SEQ_MODULO = 1 << 32

#: 이 값보다 큰 역방향 점프는 되감기(wrap)가 아니라 재시작으로 본다
_WRAP_TOLERANCE = 1 << 31


def parse_record(line: str) -> dict:
    """NDJSON 한 줄을 dict 로 파싱한다.

    Raises:
        ProtocolError: JSON 이 깨졌거나, schema_ver 가 다르거나,
            필수 필드가 빠진 경우.
    """
    try:
        rec = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"JSON 파싱 실패: {exc}") from exc

    if not isinstance(rec, dict):
        raise ProtocolError(f"객체가 아님: {line!r}")

    missing = [f for f in MANDATORY_FIELDS if f not in rec]
    if missing:
        raise ProtocolError(f"필수 필드 누락: {missing}")

    if rec["schema_ver"] != SCHEMA_VER:
        raise ProtocolError(
            f"schema_ver 불일치: 받음 {rec['schema_ver']}, 기대 {SCHEMA_VER}"
        )

    return rec


class SeqTracker:
    """seq 연속성을 감시해 유실 개수를 센다.

    중복·역순은 유실로 세지 않는다. 링크 위에서 재전송이나 순서 뒤바뀜이
    일어나도 유실 통계가 오염되지 않게 하기 위해서다.
    """

    def __init__(self) -> None:
        self._last: int | None = None
        self.missing_total = 0
        self.received_total = 0

    def reset(self) -> None:
        """재연결 시 호출. 다음 seq 를 새 기준점으로 삼는다."""
        self._last = None

    def observe(self, seq: int) -> int:
        """seq 하나를 관찰하고 이번에 발견한 누락 개수를 반환한다."""
        self.received_total += 1

        if self._last is None:
            self._last = seq
            return 0

        delta = (seq - self._last) % SEQ_MODULO

        if delta == 0 or delta > _WRAP_TOLERANCE:
            # 중복이거나 역순. 유실로 세지 않는다.
            return 0

        missing = delta - 1
        self._last = seq
        self.missing_total += missing
        return missing
