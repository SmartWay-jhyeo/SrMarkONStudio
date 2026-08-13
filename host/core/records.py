"""NDJSON 텔레메트리 레코드 파싱과 seq 누락 추적.

규격: protocol/specification.md §7
"""

import json

from host.core.errors import ProtocolError

SCHEMA_VER = 3

#: 필드 마스크로 끌 수 없는 필드 (규격 §7.1)
MANDATORY_FIELDS = ("schema_ver", "seq", "t", "type")

#: seq 시퀀스에 참여하지 않는 레코드 (규격 §7.1.1).
#: 요청이 있을 때만 나가므로 "빠졌다"는 개념이 성립하지 않고,
#: 응답 자체가 $SACK 로 보증된다. seq 는 항상 0 이다.
COMMAND_RESPONSE_TYPES = frozenset(
    {"id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end"}
)

#: seq 는 uint32
SEQ_MODULO = 1 << 32

#: 이 값보다 큰 역방향 점프는 되감기(wrap)가 아니라 재시작으로 본다
_WRAP_TOLERANCE = 1 << 31

#: 이만큼 연속으로 "전진하지 않는" 관찰이 이어지면 스트림이 재시작된 것으로 본다.
#:
#: 🔴 이게 없으면 보드가 재부팅한 뒤 유실 측정이 영구히 침묵한다.
#: _last=500 인 상태에서 보드가 재부팅해 seq 가 0 부터 다시 시작하면, 이후
#: 모든 값이 "역순 도착"으로 분류되고 _last 는 갱신되지 않는다. 새 카운터가
#: 500 을 다시 넘을 때까지 — 사실상 세션이 끝날 때까지 — 유실이 0 으로
#: 보고된다. 유실 측정이 존재 이유인 모듈에서 가장 나쁜 실패 방식이다.
#:
#: 3 으로 잡는 근거: 시리얼 링크에는 순서 뒤바뀜이 없다(바이트 스트림이다).
#: 뒤로 간 값이 연달아 세 번 나오면 재전송이 아니라 재시작이다.
#:
#: 🔴 단, **완전한 중복(delta == 0)은 이 카운터에 넣지 않는다.** 같은 값이
#: 세 번 오는 것은 재전송이지 재시작이 아니다. 재시작은 0,1,2 처럼 값이
#: 전진하는 형태로 나타난다. 이를 구분하지 않으면 중복 3회가 재부팅으로
#: 오판되어 resync_count 와 유실률이 둘 다 신뢰할 수 없게 된다.
RESYNC_AFTER = 3

#: 이보다 큰 유실은 물리적으로 불가능하다고 보고 통계에 넣지 않는다.
#:
#: 🔴 delta 가 정확히 _WRAP_TOLERANCE(2^31)면 앞으로 21억 개를 잃은 것인지
#: 뒤로 21억 간 것인지 수학적으로 구분할 수 없다. 그런데 현재 규칙은
#: `delta > _WRAP_TOLERANCE` 이므로 정확히 2^31 은 "전진"으로 분류되어
#: **유실 2,147,483,647 개**로 기록된다. 그 한 줄이 세션 전체 통계를 망친다.
#:
#: 7채널 × 100 Hz = 700 레코드/초 기준으로 2^24 는 약 6.6 시간 분량이다.
#: 그보다 큰 값은 유실이 아니라 시퀀스 불연속(재시작·링크 재연결)으로 본다.
MAX_PLAUSIBLE_GAP = 1 << 24


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

    # 🔴 필수 필드의 타입까지 검사한다. 존재만 확인하면 부족하다.
    #
    # 펌웨어의 직렬화 버그로 seq 가 문자열로 나오는 경우가 실제로 있다.
    # 그러면 parse_record 는 통과하고, 나중에 SeqTracker.observe() 안에서
    # TypeError 가 터진다. ProtocolError 가 아니라서 호출측이 "이 줄만 버리고
    # 계속" 처리를 못 하고 수집 루프 전체가 죽는다. 장시간 무인 수집이
    # 목적인데 한 줄 때문에 세션을 잃는다.
    #
    # bool 은 int 의 서브클래스라 따로 걸러야 한다.
    seq = rec["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ProtocolError(f"seq 가 정수가 아님: {seq!r}")
    if not 0 <= seq < SEQ_MODULO:
        raise ProtocolError(f"seq 가 uint32 범위 밖: {seq}")

    t = rec["t"]
    if isinstance(t, bool) or not isinstance(t, int):
        raise ProtocolError(f"t 가 정수가 아님: {t!r}")

    if not isinstance(rec["type"], str):
        raise ProtocolError(f"type 이 문자열이 아님: {rec['type']!r}")

    return rec


def is_telemetry(rec: dict) -> bool:
    """이 레코드가 seq 시퀀스에 참여하는가 (규격 §7.1.1).

    명령 응답의 seq(항상 0)를 SeqTracker 에 넣으면 매번 거대한 역방향
    점프로 보여 유실 통계가 망가진다. 호출측은 observe() 앞에 이걸 건다.
    """
    return rec.get("type") not in COMMAND_RESPONSE_TYPES


class SeqTracker:
    """seq 연속성을 감시해 유실 개수를 센다.

    중복·역순은 유실로 세지 않는다. 링크 위에서 재전송이나 순서 뒤바뀜이
    일어나도 유실 통계가 오염되지 않게 하기 위해서다.
    """

    def __init__(self) -> None:
        self._last: int | None = None
        self._backward_run = 0
        #: 연속 역방향 관찰 중 마지막으로 본 seq. 재시작(전진하는 역방향)과
        #: 잡음(전진하지 않는 역방향)을 구분하는 데 쓴다.
        self._restart_from: int | None = None
        self.missing_total = 0
        self.received_total = 0
        #: 보드 재시작을 감지해 기준점을 다시 잡은 횟수.
        #: 0 이 아니면 그 사이 구간의 유실은 셀 수 없었다는 뜻이다.
        self.resync_count = 0
        #: 같은 seq 를 다시 받은 횟수 (재전송). 유실이 아니다.
        self.duplicate_count = 0
        #: 유실이라기엔 너무 큰 점프를 만난 횟수. 통계에서 제외했다는 표시다.
        self.discontinuity_count = 0

    def reset(self) -> None:
        """재연결 시 호출. 다음 seq 를 새 기준점으로 삼는다."""
        self._last = None
        self._backward_run = 0
        self._restart_from = None

    def observe(self, seq: int) -> int:
        """seq 하나를 관찰하고 이번에 발견한 누락 개수를 반환한다."""
        self.received_total += 1

        if self._last is None:
            self._last = seq
            return 0

        delta = (seq - self._last) % SEQ_MODULO

        if delta == 0:
            # 완전한 중복. 재전송이지 재시작이 아니므로 재시작 카운터를
            # 건드리지 않는다 — 안 그러면 중복 3회가 재부팅으로 오판된다.
            self.duplicate_count += 1
            return 0

        if delta > _WRAP_TOLERANCE:
            # 뒤로 갔다. 재시작 후보지만 아직 확정은 아니다.
            #
            # 재시작은 0,1,2 처럼 값이 **전진하는** 형태로 나타난다.
            # 앞선 역방향 값보다 크지 않으면 연속 재시작 신호가 아니므로
            # 카운터를 다시 세운다.
            if self._restart_from is None or seq > self._restart_from:
                self._backward_run += 1
            else:
                self._backward_run = 1
            self._restart_from = seq

            if self._backward_run >= RESYNC_AFTER:
                self._last = seq
                self._backward_run = 0
                self._restart_from = None
                self.resync_count += 1
            return 0

        # 전진했다.
        self._backward_run = 0
        self._restart_from = None
        missing = delta - 1

        if missing > MAX_PLAUSIBLE_GAP:
            # 물리적으로 불가능한 크기다. 유실이 아니라 시퀀스 불연속으로
            # 보고 기준점만 옮긴다. 이걸 유실로 세면 한 줄이 세션 전체
            # 통계를 망친다(MAX_PLAUSIBLE_GAP 주석 참조).
            self.discontinuity_count += 1
            self._last = seq
            return 0

        self._last = seq
        self.missing_total += missing
        return missing
