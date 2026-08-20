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

import statistics
from collections import deque
from dataclasses import dataclass, field, replace

from host.core.errors import ProtocolError
from host.core.framing import parse_line
from host.core.records import SeqTracker, is_telemetry, parse_record
from host.gui.screen import AIN_COUNT, CONNECTOR_OFFSET, DIN_PORTS, I2C_PORTS
from host.gui.widgets.status_chip import Level

#: 이 보드에 있는 커넥터 전부 — 값이 한 번도 안 와도 목록에 있어야 한다.
#:
#: 🔴 **도착한 레코드에서 만들지 않는다.** 예전에는 `connector_counts` 가
#:    실제로 온 줄에서만 자라서, 값이 안 오는 커넥터는 필터 목록에 아예
#:    없었다 — 사용자가 J12 를 찾다가 "왜 없냐" 고 물은 것이 이 상수가
#:    생긴 이유다. 0 건이라는 것 자체가 정보다("저기서 값이 안 온다").
#:
#: 🔴 대시보드와 **같은 출처**를 쓴다. `screen.py` 는 이미 값이 없어도
#:    자리를 깔아 둔다(`empty_channels()` · `seed_sensors()` ·
#:    `seed_dins()` — 설계 원칙 3, 센서 미연결은 정상 상태다). 목록을 여기서
#:    따로 적으면 두 화면이 서로 다른 보드를 말하게 되고, 커넥터가 늘 때
#:    한쪽만 고쳐진다. 배선은 PCB 가 고정한 사실이라 카탈로그가 아니라
#:    상수에 있다(설계 원칙 1).
KNOWN_CONNECTORS: tuple[int, ...] = tuple(sorted(
    {CONNECTOR_OFFSET + i for i in range(AIN_COUNT)}
    | set(I2C_PORTS) | set(DIN_PORTS)
))

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
#: 🔴 펌웨어 `mk_hostlink.h` 의 `MK_HB_TIMEOUT_MS` (3000ms) 과 맞춘다 — 그
#:    시간이 지나면 보드는 이미 CONFIG 에서 RUN 으로 떨어져 있다. 그 전은
#:    "느려짐" 이고 그 후는 "고장" 이다.
STALE_FAULT_S = 3.0

#: 기본으로 숨기는 타입 — `$CFG,LIST` 카탈로그 응답(규격 §7.3)이다.
#:
#: 🔴 연결하면 이 카탈로그가 91줄(항목마다 cfg_item + cfg_field 여러 줄 +
#:    마지막 cfg_end 하나)을 한꺼번에 쏟아낸다. "정말 오고 있나" 를 보는
#:    화면인데 정작 보고 싶은 텔레메트리가 그 사이에 파묻히면 이 화면의
#:    존재 이유가 없다. 체크박스로 언제든 다시 켤 수 있다(`qt/stream_view.py`).
DEFAULT_HIDDEN_TYPES: frozenset[str] = frozenset({"cfg_item", "cfg_field", "cfg_end"})

#: 도착·보드 간격 통계에 쓰는 최근 표본 수(타입·커넥터별로 각각).
#:
#: 🔴 **개수 기반**이지 시간 기반이 아니다. `WINDOW_S`(2초)처럼 시간으로
#:    자르면 느린 채널(예: I2C, 실효 주기가 100ms 를 훌쩍 넘는다 —
#:    HANDOFF.md §7.2)은 창 안에 표본이 한둘뿐이라 최소·중앙·최대가
#:    의미가 없다. 개수 창은 채널마다 실제 주기에 맞춰 저절로 시간
#:    폭이 늘어난다. 20 은 기본 ain 주기(100ms)가 `WINDOW_S` 안에
#:    채우는 표본 수(~20개)와 같은 근거를 재사용한 것이다.
#:
#: 🔴 **성능 전제**: 채널별 창은 고정 길이 `deque` 라 표본이 아무리
#:    쌓여도 계산이 이 상수를 넘지 않는다 — `ingest()` 가 들어오는 줄마다
#:    갱신하고, `summary()` 는 이미 계산된 작은 창만 읽는다. 매 프레임
#:    전체 버퍼(`DISPLAY_MAXLEN`)를 훑지 않는다.
INTERVAL_SAMPLES = 20

#: seq 누락 구간을 몇 개까지 기억하는가.
#:
#: 🔴 링크를 의심할 때 필요한 건 총계가 아니라 "최근에 어디가 비었나"다.
#:    무한히 쌓으면 오래된 것이 화면을 채워 정작 지금 문제가 되는 구간이
#:    묻힌다.
RECENT_GAPS_MAX = 8

#: 초당 줄 수 흐름(스파크라인) 버킷 하나의 길이(초)와 버킷 개수.
#:
#: 🔴 `WINDOW_S`(2초)와 같은 값을 재사용한다 — 그 창이 이미 "흔들림 없이
#:    반응하는 최소 폭"으로 확정된 값이다(머리말 참조). 30개 × 2초 = 60초.
#:    "30초 전에 잠깐 끊겼는지" 를 보기에 충분한 길이이면서, 문자
#:    스파크라인 한 줄에 다 들어갈 만큼 짧다.
RATE_BUCKET_S = WINDOW_S
RATE_BUCKET_COUNT = 30

#: 스파크라인에 쓰는 문자 — 유니코드 블록 요소 8단계(U+2581~U+2588).
#:
#: 🔴 그래프 라이브러리를 새로 들이지 않는다(요구사항). 대시보드의 흐름선
#:    (`qt/gauge.py`·`qt/sensor_card.py` 의 `QPainter` 기반 트레이스)은
#:    "센서 값 하나의 시계열"을 그리도록 짜여 있어 좌표축·스케일이 물리량
#:    기준이다 — "버킷별 줄 수" 라는 다른 종류의 시계열을 억지로 끼워
#:    맞추려면 그 위젯의 가정을 다시 풀어야 한다. 이 화면은 이미 콘솔
#:    (텍스트) 중심이라 문자 스파크라인이 시각 언어와도 맞고, `stream.py`
#:    가 Qt 를 몰라야 하는 제약(계산은 여기, 그리기는 `qt/`)과도 자연히
#:    맞는다 — 문자열이므로 계산 결과 자체가 이미 "그림"이다.
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


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
    #: 이 줄이 전체 스트림에서 몇 번째로 들어왔는지(`StreamState.total_lines`
    #: 와 같은 채번). `-1` 은 아직 `ingest()` 를 거치지 않은 값(시험에서
    #: `parse_row` 를 직접 부를 때)이라는 뜻이다.
    #:
    #: 🔴 콘솔(Qt, `qt/stream_view.py`)이 "어디까지 그렸는지" 를 판단하는
    #:    유일한 근거다. `deque(maxlen=...)` 가 오래된 줄을 밀어내도 이
    #:    번호는 흔들리지 않아야, 매 프레임 표를 통째로 다시 그리지 않고
    #:    새로 온 줄만 이어붙일 수 있다 — 그것이 이 필드가 있는 이유다.
    ordinal: int = -1


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


# ---------------------------------------------------------------- 콘솔 서식
#
# 🔴 표(`QTableWidget`)에 행을 하나씩 위젯으로 쌓던 것을 없앤 이유가 이
#    서식이 필요한 이유다 — 초당 수십 줄에서 위젯 생성 비용이 GUI 스레드를
#    못 따라가 이벤트 큐가 무한히 밀렸다(실측: 워커 틱 간격이 5초 → 8초 →
#    11초 → 23초 → 47초+ 로 발산). 텍스트 콘솔은 줄마다 위젯을 만들지
#    않으므로 이 문제가 구조적으로 없다.
#
#    "어떻게 찍을까" 는 Qt 와 무관한 판정이라 여기서 정한다 — 디스플레이
#    없이 정렬이 흔들리지 않는지 시험할 수 있어야 한다.

#: 각 열의 고정 폭. `seq`·`t` 는 uint32 상한(4294967295, 10자리)을 담고,
#: `type` 은 가장 긴 실제 타입 문자열("cfg_field"·"cfg_value", 9자)에
#: 맞춘다. `value`·`ma` 는 센서 물리량이라 설정에 따라 범위가 사람마다
#: 다르므로(§4 대시보드 게이지와 같은 사정) 넉넉히 잡는다 — 그래도 더 긴
#: 값이 오면 그 줄만 정렬이 밀릴 뿐 글자가 잘리지는 않는다(원문은 항상
#: 그대로 뒤에 붙는다).
_SEQ_W = 10
_T_W = 10
_TYPE_W = 9
_CONN_W = 2
_VALUE_W = 12
_RAW_W = 9
_MA_W = 9

#: 값이 없을 때 찍는 자리표시자. 빈 칸이면 "정렬이 깨졌나" 와 "값이
#: 원래 없나" 를 구분할 수 없다 — cmd·corrupt 줄은 seq·t·value·raw·ma 가
#: 전부 없는 것이 정상이다(규격에 그 필드가 없는 응답).
_BLANK = "—"


def _cell(v: object, width: int, *, decimals: int | None = None) -> str:
    if v is None:
        text = _BLANK
    elif decimals is not None:
        text = f"{v:.{decimals}f}"
    else:
        text = str(v)
    return text.rjust(width)


def format_header() -> str:
    """콘솔 맨 위에 한 번 고정으로 거는 열 이름표(`qt/stream_view.py`)."""
    return (
        f"{'seq'.rjust(_SEQ_W)} {'t'.rjust(_T_W)} {'type'.ljust(_TYPE_W)} "
        f"{'J'.rjust(_CONN_W)} {'value'.rjust(_VALUE_W)} {'raw'.rjust(_RAW_W)} "
        f"{'ma'.rjust(_MA_W)}  원문"
    )


def format_row(row: StreamRow) -> str:
    """줄 하나를 콘솔에 찍을 고정폭 문자열로. 맨 끝에 원문이 그대로 붙는다.

    🔴 raw(ADC 카운트)·ma 는 value 의 파생이 아니라 **원본**이다 — 실기기에서
       0 근처가 65528/-65516 처럼 튀는 것이 value 만 봐서는 안 보인다. 셋
       다 따로 찍는다(`StreamRow.raw_count` 머리말과 같은 이유).
    """
    seq = _cell(row.seq, _SEQ_W)
    t = _cell(row.t, _T_W)
    typ = (row.type or "").ljust(_TYPE_W)
    conn = _cell(row.connector, _CONN_W)
    value = _cell(row.value, _VALUE_W, decimals=4)
    raw = _cell(row.raw_count, _RAW_W)
    ma = _cell(row.ma, _MA_W, decimals=4)
    return f"{seq} {t} {typ} {conn} {value} {raw} {ma}  {row.line}"


def connector_label(connector: int, count: int, name: str = "") -> str:
    """커넥터 필터 한 칸의 이름표.

    🔴 **0 건을 이름표에 적는다.** 목록에 있기만 하면 "안 꽂은 것" 과 "꽂았는데
       값이 안 오는 것" 이 똑같이 보인다 — 0 이라고 말해 줘야 사용자가 그
       커넥터를 의심할 수 있다. 값이 오기 시작하면 표시를 뗀다(오는 것이
       정상이라 굳이 셀 이유가 없고, 이름표가 매 틱 바뀌면 눈이 아프다).

    🔴 사용자가 붙인 이름(`ainN.name` 등, 보드 저장)이 있으면 그것이 먼저다
       (사용자 요청 2026-08-20 — "J3" 가 아니라 "유압"). J 번호를 괄호로
       남기는 이유: 배선을 만질 때는 결국 보드의 실크(J3)를 찾아야 한다.
    """
    base = f"{name} (J{connector})" if name else f"J{connector}"
    return base if count else f"{base} (0)"


# ------------------------------------------------------- 전체 선택/해제 판정
#
# 🔴 판정을 여기 둔다. 위젯은 부르기만 한다 — "지금 전부 켜져 있나" 는 Qt 와
#    무관한 질문이고, 화면 없이 시험할 수 있어야 한다
#    (`host/tests/test_layer_boundaries.py` 가 이 경계를 AST 로 강제한다).


def toggle_all(selected, everything) -> set:
    """`전체` 버튼을 눌렀을 때의 다음 선택 집합.

    전부 켜져 있으면 전부 끄고, 하나라도 꺼져 있으면 전부 켠다 — 버튼 하나로
    두 방향을 다 쓰기 위해서다(타입 열몇 개, 커넥터 열여섯 개를 손으로
    하나씩 누르게 하지 않는다).
    """
    every = set(everything)
    return set() if every and set(selected) >= every else every


def toggle_all_label(selected, everything) -> str:
    """`전체` 버튼에 적을 말 — **누르면 무슨 일이 일어나는지**를 적는다.

    지금 상태를 적으면("전부 켜짐") 버튼이 무엇을 하는 물건인지 알 수 없다.
    """
    every = set(everything)
    return "전체 해제" if every and set(selected) >= every else "전체 선택"


def filter_note(state: "StreamState", rows: tuple) -> str:
    """콘솔이 비었을 때 그 자리에 적을 이유. 보일 줄이 있으면 빈 문자열.

    🔴 **전부 해제해도 필터를 자동으로 되돌리지 않는다.** 사용자가 명시적으로
       끈 것을 화면이 몰래 뒤집으면 "왜 안 꺼지지" 가 되고, 그때부터 필터를
       믿을 수 없다. 대신 빈 화면이 **고장으로 읽히지 않게** 이유를 적는다 —
       빈 콘솔과 "타입을 전부 껐다" 는 사용자에게 전혀 다른 사실이다.

    🔴 네 가지를 구분한다: 타입을 전부 껐다 / 커넥터를 전부 껐다 / 아직
       아무 줄도 안 왔다 / 필터는 켜져 있는데 걸리는 줄이 없다. 마지막 것은
       "링크가 조용하다" 가 아니라 "그 채널만 조용하다" 는 뜻이라, 뭉뚱그리면
       사용자가 엉뚱한 곳(케이블)을 의심하게 된다.
    """
    if rows:
        return ""
    if state.filter_types is not None and not state.filter_types:
        return "타입을 전부 껐다 — 보이는 줄이 없다. [전체 선택] 을 누르면 돌아온다."
    if state.filter_connectors is not None and not state.filter_connectors:
        return "커넥터를 전부 껐다 — 보이는 줄이 없다. [전체 선택] 을 누르면 돌아온다."
    if state.total_lines == 0:
        return "아직 아무 줄도 오지 않았다."
    return "지금 켜 둔 것에 걸리는 줄이 없다 — 그 채널이 조용하다는 뜻이다."


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
class IntervalStats:
    """간격(ms) 최소·중앙·최대. 표본이 2개 미만이면 전부 `None` 이다 —
    "아직 잴 수 없다" 를 0 과 섞으면 안 된다."""

    min_ms: float | None = None
    median_ms: float | None = None
    max_ms: float | None = None
    sample_count: int = 0


@dataclass(frozen=True)
class ChannelIntervals:
    """타입(·커넥터) 하나의 도착·보드 간격 요약.

    🔴 **이 화면의 핵심 판정.** `arrival` 은 호스트가 받은 시각의 간격,
       `board` 는 레코드 안 `t`(보드가 찍은 획득 시각)의 간격이다 — 둘을
       따로 재야 HANDOFF.md §3 의 "표본 간격 202ms" 원인이 링크/호스트
       (arrival 만 벌어짐)인지 보드(둘 다 벌어짐)인지 갈린다.
    """

    type: str
    connector: int | None
    arrival: IntervalStats
    board: IntervalStats
    #: 최근 창 안에서 `t` 가 안 바뀐(반복 전송, board 간격 0) 관찰 횟수.
    #: 펌웨어가 "마지막 값을 주기마다 다시 보내는" 구조라 값이 같아도
    #: 새 표본이 아니라 붙들고 있는 값일 수 있다 — 그 사실을 명시한다.
    repeat_count: int = 0


@dataclass(frozen=True)
class SeqGap:
    """seq 누락 구간 하나. 개수만이 아니라 **어디서** 빠졌는지를 남긴다."""

    seq_lo: int
    seq_hi: int
    count: int
    arrived_s: float


@dataclass(frozen=True)
class StreamSummary:
    """상단 요약 전체. 🔴 `types`·`bytes_per_s`·`intervals`·`rate_sparkline`
    은 창 기반이고, `total_*`·`seq_missing`은 누적이다 — 섞어 쓰지
    않는다(`StreamState.summary` 머리말). `recent_gaps` 는 개수 상한
    (`RECENT_GAPS_MAX`)이 있는 최근 이력이라 그 중간 성격이다."""

    types: tuple[TypeSummary, ...] = ()
    bytes_per_s: float = 0.0
    total_lines: int = 0
    total_bytes: int = 0
    seq_missing: int = 0
    last_line_at: float | None = None
    since_last_s: float | None = None
    intervals: tuple[ChannelIntervals, ...] = ()
    recent_gaps: tuple[SeqGap, ...] = ()
    rate_sparkline: str = ""


def _fmt_stats(s: IntervalStats) -> str:
    if s.sample_count == 0:
        return _BLANK
    return f"{s.min_ms:.0f}~{s.median_ms:.0f}~{s.max_ms:.0f}ms"


def format_interval(ci: ChannelIntervals) -> str:
    """채널 하나의 간격 요약 한 줄 — "최소~중앙~최대" 형식.

    🔴 도착과 보드를 **나란히, 이름표를 달아** 찍는다. 둘이 같으면 보드가
       늦게 보낸 것이고, 도착만 벌어지면 링크·호스트 지연이다 —
       사람이 두 숫자를 바로 대조할 수 있어야 그 판단을 할 수 있다.
    """
    label = ci.type if ci.connector is None else f"{ci.type}/J{ci.connector}"
    text = (f"{label:<10} 도착 {_fmt_stats(ci.arrival):<16} "
            f"보드 {_fmt_stats(ci.board):<16}")
    if ci.repeat_count:
        # 🔴 반복 전송(같은 t)이 섞여 있다는 것 자체가 신호다 — 못 보면
        #    "새 표본이 계속 온다" 로 착각한다(머리말 참조).
        text += f" 반복{ci.repeat_count}"
    return text


def format_gap(gap: SeqGap, now_s: float) -> str:
    """seq 누락 구간 한 줄 — "seq 1204~1207 (4개) · 12.3초 전" 형식."""
    ago = now_s - gap.arrived_s
    if gap.count == 1:
        span = f"seq {gap.seq_lo}"
    else:
        span = f"seq {gap.seq_lo}~{gap.seq_hi}"
    return f"{span} ({gap.count}개) · {ago:.1f}초 전"


class _ChannelWindow:
    """타입(·커넥터) 하나의 최근 간격 표본. `StreamState` 내부 전용.

    🔴 고정 길이 `deque` 둘(도착·보드)만 든다 — `observe()` 가 줄 하나당
       O(1)로 갱신하고, 통계는 그 작은 창에서만 계산한다. `StreamState`
       가 이 상한을 지키는 이유(성능 전제)와 같다.
    """

    __slots__ = ("_last_arrived_s", "_last_t", "_arrival_ms", "_t_ms")

    def __init__(self, maxlen: int) -> None:
        self._last_arrived_s: float | None = None
        self._last_t: int | None = None
        self._arrival_ms: deque[float] = deque(maxlen=maxlen)
        self._t_ms: deque[float] = deque(maxlen=maxlen)

    def observe(self, arrived_s: float, t: int) -> None:
        """🔴 도착 간격과 보드 간격을 **서로 다른 입력**에서 각각 계산한다
        — 이것이 흔들리면(예: 보드 간격도 arrived_s 로 잰다) 핵심 판정이
        무너진다(`test_interval_summary_separates_arrival_jitter_from_board_cadence`
        가 이걸 되돌림 검사로 잡는다)."""
        if self._last_arrived_s is not None:
            self._arrival_ms.append((arrived_s - self._last_arrived_s) * 1000.0)
        if self._last_t is not None:
            self._t_ms.append(float(t - self._last_t))
        self._last_arrived_s = arrived_s
        self._last_t = t

    def repeat_count(self) -> int:
        """`t` 간격이 0 인(=같은 `t` 가 반복된) 관찰 수. 창 안에서만 센다."""
        return sum(1 for d in self._t_ms if d == 0.0)

    @staticmethod
    def _stats(samples: deque[float]) -> IntervalStats:
        if not samples:
            return IntervalStats()
        xs = sorted(samples)
        return IntervalStats(min_ms=xs[0], median_ms=statistics.median(xs),
                              max_ms=xs[-1], sample_count=len(xs))

    def arrival_stats(self) -> IntervalStats:
        return self._stats(self._arrival_ms)

    def board_stats(self) -> IntervalStats:
        return self._stats(self._t_ms)


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
        #: `None` = 커넥터로 거르지 않는다. 타입 필터와 AND 로 겹친다.
        self.filter_connectors: set[int] | None = None
        #: 커넥터 번호 → 사용자가 붙인 이름 (카탈로그의 `*.name` 항목).
        #: 화면이 J 번호 대신 이것을 앞세운다. 비면 J 번호 그대로다.
        self.connector_names: dict[int, str] = {}

        #: (타입, 커넥터) 별 최근 간격 창. 커넥터가 없는 레코드(cmd 응답
        #: 등)는 키의 커넥터 자리가 `None` 이다.
        self._channels: dict[tuple[str, int | None], _ChannelWindow] = {}
        #: 커넥터별 누적 수신 수 — 필터 체크박스를 무엇으로 채울지 화면이
        #: 이걸 보고 정한다(`type_counts` 와 같은 역할, 커넥터 버전).
        #:
        #: 🔴 보드에 있는 커넥터를 **전부 0 으로 미리 깐다**(`KNOWN_CONNECTORS`
        #:    머리말). 도착한 것만 담으면 값이 안 오는 커넥터가 목록에서
        #:    사라지고, 그러면 화면이 "그런 커넥터는 없다" 고 말하는 셈이 된다.
        #:    카탈로그에 없는 커넥터가 오더라도 아래 `ingest()` 가 그대로
        #:    세므로 새 커넥터가 생겨도 화면에서 사라지지 않는다.
        self.connector_counts: dict[int, int] = {c: 0 for c in KNOWN_CONNECTORS}

        #: 최근 seq 누락 구간. 개수 상한(`RECENT_GAPS_MAX`)이 있는
        #: deque 라 오래된 구간은 스스로 밀려난다.
        self._recent_gaps: deque[SeqGap] = deque(maxlen=RECENT_GAPS_MAX)

        #: 초당 줄 수 흐름(스파크라인) 버킷. `_rate_bucket_start` 는 지금
        #: 채우고 있는 버킷의 시작 시각 — `None` 이면 아직 아무것도
        #: 안 왔다.
        self._rate_buckets: deque[int] = deque(maxlen=RATE_BUCKET_COUNT)
        self._rate_bucket_start: float | None = None

    # -------------------------------------------------------------- 수신
    def ingest(self, lines: list[str], now_s: float) -> None:
        """새로 온 원문 줄들을 반영한다.

        🔴 **일시정지 중에도 불러야 한다.** 뒤에서는 계속 받고, 화면만
           멈춘다 — 다시 켜면 그동안 쌓인 것까지 한번에 보인다.
        """
        for line in lines:
            row = replace(parse_row(line, now_s), ordinal=self.total_lines)
            self._rows.append(row)
            self.total_lines += 1
            self.total_bytes += len(line.encode("utf-8"))
            self.type_counts[row.type] = self.type_counts.get(row.type, 0) + 1
            self.last_line_at = now_s
            if row.connector is not None:
                self.connector_counts[row.connector] = (
                    self.connector_counts.get(row.connector, 0) + 1
                )
            if row.t is not None:
                # 🔴 O(1) 갱신 — 채널당 고정 길이 창만 만진다. 여기가
                #    "들어올 때 갱신" 쪽이다(성능 전제, 머리말 참조).
                key = (row.type, row.connector)
                window = self._channels.get(key)
                if window is None:
                    window = _ChannelWindow(INTERVAL_SAMPLES)
                    self._channels[key] = window
                window.observe(now_s, row.t)
            if row.seq is not None and is_telemetry({"type": row.type}):
                missing = self._seq_tracker.observe(row.seq)
                if missing > 0:
                    # observe() 가 돌려주는 missing 은 (이전 seq, 이번 seq)
                    # 사이에 비어 있던 개수다 — 그 범위를 그대로 구간으로
                    # 남긴다. SeqTracker 의 사생활(_last)을 들여다보지
                    # 않고도 seq_lo/seq_hi 를 셈으로 되짚을 수 있다.
                    self._recent_gaps.append(SeqGap(
                        seq_lo=row.seq - missing,
                        seq_hi=row.seq - 1,
                        count=missing,
                        arrived_s=now_s,
                    ))
        self._record_rate(now_s, len(lines))

    def _record_rate(self, now_s: float, n: int) -> None:
        """초당 줄 수 흐름 버킷을 채운다. `ingest()` 호출 하나당 한 번만
        — 워커 한 틱이 곧 하나의 시각(`now_s`)이라, 그 틱 안의 줄들을
        낱개 시각으로 쪼개는 것은 없는 정밀도를 지어내는 것이다.

        🔴 조용한 틱(빈 `lines`)도 반드시 부른다 — 안 그러면 "끊긴 30초"
           가 버킷에 안 새겨져 스파크라인이 끊김을 못 보여준다.
        """
        if self._rate_bucket_start is None:
            if n == 0:
                # 아직 실제로 온 줄이 하나도 없다 — `summary()` 가 매 틱
                # `n=0` 으로 부르는 것만으로 버킷을 만들면(=스파크라인이
                # 생기면) "연결 전"과 "연결됐는데 조용함" 을 구분할 수
                # 없어진다. 첫 실제 줄이 올 때까지는 아무것도 안 한다.
                return
            self._rate_bucket_start = now_s
            self._rate_buckets.append(0)
        elapsed = now_s - self._rate_bucket_start
        if elapsed >= RATE_BUCKET_S:
            # 버킷을 건너뛴 만큼만 0 으로 채운다. maxlen 을 넘는 만큼은
            # 어차피 곧바로 밀려나므로 그 이상 반복할 이유가 없다 —
            # 오래 멈춰 있던 스트림이 다시 살아나도 이 루프가 무한히
            # 돌지 않는다(성능 전제).
            missed = int(elapsed // RATE_BUCKET_S)
            for _ in range(min(missed, RATE_BUCKET_COUNT)):
                self._rate_buckets.append(0)
            self._rate_bucket_start += missed * RATE_BUCKET_S
        if self._rate_buckets:
            self._rate_buckets[-1] += n

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

    def set_connector_filter(self, connectors: set[int] | None) -> None:
        """`None` 이면 커넥터로 거르지 않는다.

        🔴 타입 필터와 별개의 축이다 — `ain` 7채널이 한 덩어리라 J4 만
           이상할 때 못 보던 것을 이걸로 잡는다. 커넥터가 없는 레코드
           (`row.connector is None`, cmd 응답 등)는 필터가 걸리면 함께
           숨는다 — 특정 커넥터를 지목한 것은 그 채널만 보겠다는 뜻이라,
           커넥터 정보가 아예 없는 줄까지 끼워 주면 격리가 흐려진다.
        """
        self.filter_connectors = connectors

    def visible_rows(self) -> tuple[StreamRow, ...]:
        """지금 화면에 그릴 줄들 — 일시정지·필터(타입 AND 커넥터)가
        적용된 결과."""
        rows = (self._frozen if (self.paused and self._frozen is not None)
                else tuple(self._rows))
        if self.filter_types is not None:
            rows = tuple(r for r in rows if r.type in self.filter_types)
        if self.filter_connectors is not None:
            # 🔴 커넥터가 **없는** 줄(gnss·stat·cmd — 장비 전체에 속하는
            #    것들)은 커넥터 필터의 대상이 아니다. `in` 만 보면 이런
            #    줄이 어느 목록에도 못 들어가 **아무 커넥터나 하나만 꺼도
            #    전부 사라진다** — 실제로 J3 를 끄니 gnss 가 사라져 "gnss
            #    가 J3 에 붙어 있나" 로 보였다(사용자 보고 2026-08-20).
            #    이런 줄은 타입 필터로만 걸러진다.
            rows = tuple(r for r in rows
                         if r.connector is None
                         or r.connector in self.filter_connectors)
        return rows

    # -------------------------------------------------------------- 요약
    def summary(self, now_s: float) -> StreamSummary:
        """요약 수치.

        🔴 **`types`(초당 줄 수)·`bytes_per_s` 는 창 기반**이다(`WINDOW_S`
           머리말) — 누적/가동시간으로 나누면 "방금 멈춘 것" 을 못 잡는다.
           `total_lines`·`total_bytes`·`seq_missing` 은 **누적**이다 —
           `_rows` 가 상한을 넘겨 오래된 표본이 밀려나도 줄면 안 되는
           수치라서 따로 들고 있다(`type_counts`·`total_lines` 등).
        """
        # 🔴 새 줄이 없는 틱에도 부른다(`n=0`) — 그래야 "조용해진 지 얼마나
        #    됐는가" 가 스파크라인에 반영된다. `ingest()` 가 이미 매 틱
        #    부르므로 보통은 아무 효과가 없지만(같은 now_s), summary() 를
        #    ingest() 보다 나중 시각으로 부르는 호출자(시험 등)도 버킷이
        #    실제 경과 시간을 따라가야 한다. 비용은 버킷 상한(30)에 묶여
        #    있어 행 버퍼 크기와 무관하다(성능 전제).
        self._record_rate(now_s, 0)

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
            intervals=self._interval_summaries(),
            recent_gaps=tuple(self._recent_gaps),
            rate_sparkline=self._rate_sparkline(),
        )

    def _interval_summaries(self) -> tuple[ChannelIntervals, ...]:
        """🔴 `self._channels` 만 읽는다 — 채널 수(수십 개 이하)에 비례할
        뿐 수신 줄 수(`_rows`)와 무관하다. 채널마다 안의 창도
        `INTERVAL_SAMPLES` 로 고정이라 이 함수 전체가 항상 상수에 가까운
        비용이다(성능 전제)."""
        out = [
            ChannelIntervals(
                type=t, connector=conn,
                arrival=window.arrival_stats(),
                board=window.board_stats(),
                repeat_count=window.repeat_count(),
            )
            for (t, conn), window in self._channels.items()
        ]
        # 정렬 기준을 고정해 둔다 — 화면이 매 프레임 순서를 다시 정해
        # 항목이 눈에서 튀는 것을 막는다.
        out.sort(key=lambda c: (c.type, c.connector if c.connector is not None else -1))
        return tuple(out)

    def _rate_sparkline(self) -> str:
        """최근 `RATE_BUCKET_COUNT`×`RATE_BUCKET_S` 초의 줄 수 흐름을 문자
        하나당 버킷 하나로 그린다. 🔴 버킷 개수가 고정이라(deque maxlen)
        이 계산도 상수 비용이다."""
        if not self._rate_buckets:
            return ""
        peak = max(self._rate_buckets) or 1
        top = len(_SPARK_CHARS) - 1
        return "".join(
            _SPARK_CHARS[min(top, round(count / peak * top))]
            for count in self._rate_buckets
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
