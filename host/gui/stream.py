"""텔레메트리 원문 스트림 — "정말 오고 있나" 를 보여주고 분석 가능하게 만든다.

🔴 이 파일은 **Qt 를 import 하지 않는다.** `host/gui/screen.py` 와 같은
   층이고, `host/tests/test_layer_boundaries.py` 가 AST 로 강제한다. 화면이
   무엇을 보여야 하는지가 여기 다 있어야 디스플레이 없이 시험할 수 있다.

왜 필요한가
----------
`BoardService` 는 원문 줄을 한정 버퍼(`raw_lines`)에 담아 두지만, 그 자체는
"초당 몇 줄", "타입별로 몇 줄", "언제 멈췄나" 를 말하지 않는다. 이 파일이
그 해석을 맡는다 — 화면(`qt/stream_view.py`)은 `StreamState` 만 보고 그린다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from host.core.errors import ProtocolError
from host.core.framing import parse_line
from host.core.records import SeqTracker, is_telemetry, parse_record
from host.gui.widgets.status_chip import Level

#: 화면에 보여줄 최근 줄 수이자 창(window) 기반 계산의 표본 상한.
#:
#: 🔴 근거: `host/core/records.py` 가 이미 쓰는 이론적 상한
#:    ("7채널 × 100 Hz = 700 레코드/초", ain 최소 주기 10ms 를 전 채널에
#:    걸었을 때)을 빌린다. 아래 `WINDOW_S`(2초) 창에서 그 첨두를 다 담으려면
#:    1400줄이 필요하다. 2000이면 여유를 두면서도 Qt 리스트 위젯이 매끈히
#:    그릴 수 있는 크기다 — `screen.py` 의 `TRACE_LEN=600` 도 "화면에서
#:    안 보이면 의미 없다" 는 같은 판단에서 나온 상한이다.
DISPLAY_MAXLEN = 2000

#: 초당 줄 수를 계산할 창의 길이(초).
#:
#: 🔴 **창 기반**으로 하는 이유: 누적 줄 수를 가동 시간으로 나누면 "방금
#:    멈췄다" 를 몇 분이 지나도 못 잡는다(분모가 계속 커진다). 너무 좁으면
#:    (예: 0.2초) 기본 ain 주기(100ms)에서 표본이 2개뿐이라 값이 들쭉날쭉
#:    튄다. 2초면 기본 설정(100ms 주기)에서도 표본이 스무 개 가까이 모여
#:    흔들림이 줄고, 링크가 끊기면 2초 안에 rate 가 0 으로 떨어져 화면이
#:    바로 반응한다.
WINDOW_S = 2.0

#: 마지막 수신 후 이 시간이 지나면 "느려지고 있다" 로 본다.
#:
#: 🔴 기본 ain 주기가 100ms 다. 1초면 이미 열 줄을 놓친 것이고, 사람이
#:    "어? 안 오나?" 하고 의심하기 시작하는 지점과 비슷하다.
STALE_WARN_S = 1.0

#: 이 시간이 지나면 "멈췄다" 로 본다.
#:
#: 🔴 `tools/simulator/device_sim.HB_TIMEOUT_MS` (3000ms) 과 맞춘다 — 그
#:    시간이 지나면 보드는 이미 CONFIG 에서 RUN 으로 떨어져 있다. 그 전은
#:    "느려짐" 이고 그 후는 "고장" 이다.
STALE_FAULT_S = 3.0


@dataclass(frozen=True)
class StreamRow:
    """줄 하나 — 원문 그대로 + 파싱 요약.

    🔴 파싱이 실패해도(`type == "corrupt"`) `line` 은 항상 채워진다.
       "정말 오고 있나" 를 보는 화면에서 깨진 줄을 숨기면 손상 자체를
       놓친다.
    """

    line: str
    arrived_s: float
    type: str = ""
    seq: int | None = None
    t: int | None = None
    connector: int | None = None
    value: float | None = None
    #: ADS1256 원시 카운트 (규격 §7.2 의 `raw` 필드). `value`·`ma` 는 편의용
    #: 파생값이고 **이것이 원본**이다 — 근처에서 65528/-65516 처럼 튀는 것이
    #: `value` 만 봐서는 안 보인다.
    raw_count: int | None = None
    ma: float | None = None
    unit: str = ""
    status: int | None = None


def _num(rec: dict, key: str) -> float | None:
    v = rec.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _int(rec: dict, key: str) -> int | None:
    v = rec.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def parse_row(line: str, arrived_s: float) -> StreamRow:
    """원문 한 줄을 `StreamRow` 로. 실패해도 원문은 항상 남는다."""
    if line.startswith("$"):
        try:
            parse_line(line)
            return StreamRow(line=line, arrived_s=arrived_s, type="cmd")
        except ProtocolError:
            return StreamRow(line=line, arrived_s=arrived_s, type="corrupt")

    try:
        rec = parse_record(line)
    except ProtocolError:
        return StreamRow(line=line, arrived_s=arrived_s, type="corrupt")

    return StreamRow(
        line=line,
        arrived_s=arrived_s,
        type=str(rec.get("type", "")),
        seq=_int(rec, "seq"),
        t=_int(rec, "t"),
        connector=_int(rec, "connector_id"),
        value=_num(rec, "value"),
        raw_count=_int(rec, "raw"),
        ma=_num(rec, "ma"),
        unit=str(rec["unit"]) if isinstance(rec.get("unit"), str) else "",
        status=_int(rec, "status"),
    )


def staleness_level(since_last_s: float | None) -> Level:
    """마지막 수신 후 경과에서 심각도를 정한다. 색·문구는 Qt 쪽이 입힌다.

    `host/gui/widgets/status_chip.py` 의 `Level` 을 그대로 쓴다 — 화면
    전체가 같은 심각도 어휘를 써야 이 화면만 다른 색 체계를 쓰지 않는다.
    """
    if since_last_s is None:
        return Level.IDLE
    if since_last_s >= STALE_FAULT_S:
        return Level.FAULT
    if since_last_s >= STALE_WARN_S:
        return Level.WARN
    return Level.OK


@dataclass(frozen=True)
class TypeSummary:
    """타입 하나의 요약 한 줄."""

    type: str
    rate_per_s: float
    count_total: int


@dataclass(frozen=True)
class StreamSummary:
    """상단 요약 전체. 🔴 `types`·`bytes_per_s` 는 창 기반이고, `total_*` ·
    `seq_missing` 은 누적이다 — 섞어 쓰지 않는다(`StreamState.summary` 머리말)."""

    types: tuple[TypeSummary, ...] = ()
    bytes_per_s: float = 0.0
    total_lines: int = 0
    total_bytes: int = 0
    seq_missing: int = 0
    last_line_at: float | None = None
    since_last_s: float | None = None


class StreamState:
    """스트림 화면이 보여줄 것 전부.

    🔴 Qt 를 모른다. 화면은 이 객체를 읽기만 한다(`screen.py` 와 같은 어법).
    """

    def __init__(self, *, maxlen: int = DISPLAY_MAXLEN,
                 window_s: float = WINDOW_S) -> None:
        self._rows: deque[StreamRow] = deque(maxlen=maxlen)
        self._window_s = window_s
        #: 🔴 새로 만들지 않고 재사용한다 — records.py 의 SeqTracker 가
        #:    중복·역순·재시작을 이미 다 다룬다.
        self._seq_tracker = SeqTracker()

        #: 창을 벗어나 `_rows` 에서 밀려나도 사라지면 안 되는 누적 수치.
        self.total_lines = 0
        self.total_bytes = 0
        self.type_counts: dict[str, int] = {}
        self.last_line_at: float | None = None

        self.paused = False
        #: 일시정지 시점의 스냅샷. `None` 이면 정지 중이 아니거나, 정지
        #: 시점에 아무 줄도 없었던 것이다(둘 다 "얼릴 것이 없다" 로 같다).
        self._frozen: tuple[StreamRow, ...] | None = None
        #: `None` = 전부 보여준다.
        self.filter_types: set[str] | None = None

    # -------------------------------------------------------------- 수신
    def ingest(self, lines: list[str], now_s: float) -> None:
        """새로 온 원문 줄들을 반영한다.

        🔴 **일시정지 중에도 불러야 한다.** 뒤에서는 계속 받고, 화면만
           멈춘다 — 다시 켜면 그동안 쌓인 것까지 한번에 보인다.
        """
        for line in lines:
            row = parse_row(line, now_s)
            self._rows.append(row)
            self.total_lines += 1
            self.total_bytes += len(line.encode("utf-8"))
            self.type_counts[row.type] = self.type_counts.get(row.type, 0) + 1
            self.last_line_at = now_s
            if row.seq is not None and is_telemetry({"type": row.type}):
                self._seq_tracker.observe(row.seq)

    # -------------------------------------------------------------- 표시
    def pause(self) -> None:
        """지금 보이는 줄에서 멈춘다. 뒤에서는 계속 쌓인다."""
        self.paused = True
        self._frozen = tuple(self._rows)

    def resume(self) -> None:
        """멈췄던 동안 쌓인 것까지 포함해 최신을 다시 보여준다."""
        self.paused = False
        self._frozen = None

    def set_filter(self, types: set[str] | None) -> None:
        """`None` 이면 전부 보여준다."""
        self.filter_types = types

    def visible_rows(self) -> tuple[StreamRow, ...]:
        """지금 화면에 그릴 줄들 — 일시정지·필터가 적용된 결과."""
        base = (self._frozen if (self.paused and self._frozen is not None)
                else tuple(self._rows))
        if self.filter_types is None:
            return base
        return tuple(r for r in base if r.type in self.filter_types)

    # -------------------------------------------------------------- 요약
    def summary(self, now_s: float) -> StreamSummary:
        """요약 수치.

        🔴 **`types`(초당 줄 수)·`bytes_per_s` 는 창 기반**이다(`WINDOW_S`
           머리말) — 누적/가동시간으로 나누면 "방금 멈춘 것" 을 못 잡는다.
           `total_lines`·`total_bytes`·`seq_missing` 은 **누적**이다 —
           `_rows` 가 상한을 넘겨 오래된 표본이 밀려나도 줄면 안 되는
           수치라서 따로 들고 있다(`type_counts`·`total_lines` 등).
        """
        window_start = now_s - self._window_s
        windowed = [r for r in self._rows if r.arrived_s >= window_start]

        counts: dict[str, int] = {}
        bytes_window = 0
        for r in windowed:
            counts[r.type] = counts.get(r.type, 0) + 1
            bytes_window += len(r.line.encode("utf-8"))

        # 🔴 창 밖으로 완전히 밀려난 타입도 목록에 남아야 한다 — 누적은
        #    있는데 지금 창에 표본이 없으면 "그 타입이 멈췄다" 는 뜻이고,
        #    그것도 화면이 말해야 할 사실이다(rate_per_s=0.0 으로).
        all_types = set(counts) | set(self.type_counts)
        types = tuple(
            TypeSummary(
                type=t,
                rate_per_s=counts.get(t, 0) / self._window_s,
                count_total=self.type_counts.get(t, 0),
            )
            for t in sorted(all_types)
        )

        since_last = (now_s - self.last_line_at
                     if self.last_line_at is not None else None)

        return StreamSummary(
            types=types,
            bytes_per_s=bytes_window / self._window_s,
            total_lines=self.total_lines,
            total_bytes=self.total_bytes,
            seq_missing=self._seq_tracker.missing_total,
            last_line_at=self.last_line_at,
            since_last_s=since_last,
        )

    # -------------------------------------------------------------- 저장
    def to_ndjson(self) -> str:
        """지금 버퍼에 있는 원문 줄을 그대로 이어붙인다. 파일 저장용.

        🔴 파싱한 값이 아니라 **원문**을 그대로 쓴다 — 나중에 뜯어볼 때
           호스트가 손댄 흔적이 있으면 그게 원본인지 의심해야 한다.
        """
        if not self._rows:
            return ""
        return "\n".join(r.line for r in self._rows) + "\n"
