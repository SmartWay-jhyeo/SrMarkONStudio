"""NDJSON 이 실제로 오는지 보여주는 스트림 화면의 상태 — Qt 없이 시험한다.

`host/gui/stream.py` 는 `screen.py` 와 같은 층이다(test_layer_boundaries.py
가 AST 로 강제한다).
"""

from host.core.framing import build_command
from host.gui.stream import (
    DEFAULT_HIDDEN_TYPES,
    StreamState,
    format_header,
    format_row,
    staleness_level,
)
from host.gui.widgets.status_chip import Level


def _ain_line(seq: int, *, connector_id: int = 3, raw: int = 8388608,
              ma: float = 12.0, value: float = 3.4, status: int = 0) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":{seq * 100},"type":"ain",'
        f'"connector_id":{connector_id},"raw":{raw},"ma":{ma},'
        f'"value":{value},"unit":"bar","status":{status}}}'
    )


def _i2c_line(seq: int) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":{seq * 100},"type":"i2c",'
        f'"connector_id":10,"quantity":"lux","value":123.4,"status":0}}'
    )


# --------------------------------------------------------------- 파싱 요약

def test_ain_row_exposes_raw_adc_count_separately_from_value():
    """🔴 raw(ADC 카운트)와 ma·value 는 다른 것이다.

    실기기에서 raw 가 0 근처에서 65528/-65516 처럼 튀는 것을 봤는데,
    value 만 보여주면 그 튐이 화면에서 안 보인다.
    """
    s = StreamState()
    s.ingest([_ain_line(1, raw=65528, ma=0.0001, value=0.0)], now_s=0.0)
    row = s.visible_rows()[0]
    assert row.raw_count == 65528
    assert row.ma == 0.0001
    assert row.value == 0.0
    assert row.type == "ain"
    assert row.connector == 3
    assert row.seq == 1


def test_line_kept_verbatim_alongside_the_parsed_summary():
    line = _ain_line(1)
    s = StreamState()
    s.ingest([line], now_s=0.0)
    assert s.visible_rows()[0].line == line


def test_corrupt_line_is_kept_visible_with_corrupt_type():
    """🔴 파싱이 깨져도 줄 자체는 보여야 한다 — 숨기면 손상을 놓친다."""
    s = StreamState()
    s.ingest(['{"schema_ver":3,"seq":'], now_s=0.0)
    row = s.visible_rows()[0]
    assert row.type == "corrupt"
    assert row.line == '{"schema_ver":3,"seq":'


def test_command_response_line_is_bucketed_as_cmd():
    s = StreamState()
    line = build_command("SACK", "CFG", "OK").rstrip("\r\n")
    s.ingest([line], now_s=0.0)
    assert s.visible_rows()[0].type == "cmd"


# --------------------------------------------------------------------- 버퍼

def test_row_buffer_is_bounded():
    s = StreamState(maxlen=3)
    for seq in range(5):
        s.ingest([_ain_line(seq)], now_s=float(seq))
    rows = s.visible_rows()
    assert len(rows) == 3
    assert rows[-1].seq == 4
    assert rows[0].seq == 2


# --------------------------------------------------------------------- 필터

def test_type_filter_hides_other_types():
    s = StreamState()
    s.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)
    s.set_filter({"ain"})
    types = {r.type for r in s.visible_rows()}
    assert types == {"ain"}


def test_disabling_the_filter_shows_everything_again():
    """🔴 되돌림 검사 — 필터를 무력화하면 다시 전부 보여야 한다."""
    s = StreamState()
    s.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)
    s.set_filter({"ain"})
    assert len(s.visible_rows()) == 1
    s.set_filter(None)
    assert len(s.visible_rows()) == 2


# ------------------------------------------------------------------ 일시정지

def test_pause_freezes_the_visible_rows():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.pause()
    s.ingest([_ain_line(2)], now_s=1.0)          # 뒤에서는 계속 받는다
    assert [r.seq for r in s.visible_rows()] == [1]


def test_resume_reveals_everything_received_while_paused():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.pause()
    s.ingest([_ain_line(2)], now_s=1.0)
    s.resume()
    assert [r.seq for r in s.visible_rows()] == [1, 2]


def test_pause_does_not_stop_cumulative_counters():
    """멈춰 있는 동안에도 통계는 계속 쌓여야 한다 — 화면만 멈춘다."""
    s = StreamState()
    s.pause()
    s.ingest([_ain_line(1), _ain_line(2)], now_s=0.0)
    assert s.total_lines == 2


# --------------------------------------------------------------------- 요약

def test_summary_rate_is_window_based_not_cumulative():
    """🔴 되돌림 검사 — 창 기반이 아니라 누적/가동시간으로 계산하면
    "방금 멈춘 것" 을 못 잡는다. 창을 벗어난 옛 표본은 rate 계산에서
    빠져야 한다."""
    s = StreamState(window_s=1.0)
    for seq in range(5):
        s.ingest([_ain_line(seq)], now_s=0.0)    # 창 안에서 5줄
    summary_inside = s.summary(now_s=0.5)
    ain = next(t for t in summary_inside.types if t.type == "ain")
    assert ain.rate_per_s == 5.0                  # 5줄 / 1.0초 창

    summary_after = s.summary(now_s=10.0)          # 창(1초)을 한참 벗어났다
    ain_after = next((t for t in summary_after.types if t.type == "ain"), None)
    assert ain_after is None or ain_after.rate_per_s == 0.0
    # 누적은 창과 무관하게 그대로다.
    assert s.summary(now_s=10.0).total_lines == 5


def test_summary_keeps_cumulative_total_per_type():
    s = StreamState(window_s=1.0)
    for seq in range(3):
        s.ingest([_ain_line(seq)], now_s=0.0)
    summary = s.summary(now_s=100.0)               # 창 밖
    ain = next(t for t in summary.types if t.type == "ain")
    assert ain.count_total == 3


def test_summary_reports_seq_gaps_reusing_seq_tracker():
    """🔴 새로 만들지 않고 SeqTracker 를 재사용한다."""
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.ingest([_ain_line(5)], now_s=0.1)             # 2,3,4 누락
    assert s.summary(now_s=1.0).seq_missing == 3


def test_summary_since_last_reflects_elapsed_time():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=10.0)
    summary = s.summary(now_s=12.5)
    assert summary.since_last_s == 2.5


def test_summary_since_last_is_none_before_anything_arrives():
    s = StreamState()
    assert s.summary(now_s=5.0).since_last_s is None


def test_summary_bytes_per_second_is_also_window_based():
    s = StreamState(window_s=1.0)
    line = _ain_line(1)
    s.ingest([line], now_s=0.0)
    summary = s.summary(now_s=0.5)
    assert summary.bytes_per_s == len(line.encode("utf-8")) / 1.0


# --------------------------------------------------------------- 멈춤 색상

def test_staleness_is_ok_when_fresh():
    assert staleness_level(0.1) == Level.OK


def test_staleness_warns_after_a_second():
    assert staleness_level(1.5) == Level.WARN


def test_staleness_faults_after_the_heartbeat_timeout():
    assert staleness_level(4.0) == Level.FAULT


def test_staleness_is_idle_when_nothing_has_arrived_yet():
    assert staleness_level(None) == Level.IDLE


# ----------------------------------------------------------------- 파일 저장

def test_to_ndjson_joins_buffered_lines_with_newlines():
    s = StreamState()
    s.ingest([_ain_line(1), _ain_line(2)], now_s=0.0)
    text = s.to_ndjson()
    assert text == _ain_line(1) + "\n" + _ain_line(2) + "\n"


def test_to_ndjson_is_empty_string_when_nothing_buffered():
    assert StreamState().to_ndjson() == ""


# ----------------------------------------------------------------- ordinal
#
# 🔴 콘솔(Qt)이 "어디까지 그렸는지" 를 판단하려면 줄마다 흔들리지 않는
#    번호가 있어야 한다(매 프레임 표를 통째로 다시 그리던 것을 없애는
#    이유 자체가 이것 — stream_view.py 머리말 참조). deque 가 밀려나도
#    (오래된 줄이 사라져도) 새로 붙는 번호는 계속 이어져야 한다.

def test_rows_get_monotonically_increasing_ordinals():
    s = StreamState()
    s.ingest([_ain_line(1), _ain_line(2), _ain_line(3)], now_s=0.0)
    rows = s.visible_rows()
    assert [r.ordinal for r in rows] == [0, 1, 2]


def test_ordinals_keep_increasing_even_after_buffer_eviction():
    """🔴 되돌림 검사 — maxlen 을 넘겨 오래된 줄이 밀려나도, 남은 줄의
    번호가 0 부터 다시 매겨지면 안 된다(그러면 콘솔이 이미 그린 줄을
    새 줄로 착각해 중복으로 다시 찍는다)."""
    s = StreamState(maxlen=2)
    for seq in range(4):
        s.ingest([_ain_line(seq)], now_s=float(seq))
    rows = s.visible_rows()
    assert [r.ordinal for r in rows] == [2, 3]


# ------------------------------------------------------------- 콘솔 서식
#
# 🔴 Qt 를 모르는 층에서 문자열 서식을 확정한다 — "무엇을 어떻게 찍는가"
#    는 판정이지 그리기가 아니고, 디스플레이 없이 시험할 수 있어야 한다.

def test_format_row_keeps_raw_and_ma_as_separate_fixed_width_fields():
    """🔴 raw(ADC 카운트)와 ma·value 는 다른 것이다 — 콘솔 한 줄에서도
    셋 다 읽을 수 있어야 한다(테이블일 때의 요구사항과 같다)."""
    s = StreamState()
    s.ingest([_ain_line(1, raw=65528, ma=0.0001, value=0.0)], now_s=0.0)
    row = s.visible_rows()[0]
    line = format_row(row)
    assert "65528" in line
    assert "0.0001" in line
    assert "0.0000" in line  # value


def test_format_row_ends_with_the_verbatim_original_line():
    """🔴 원시값과 NDJSON 원문을 둘 다 유지한다 — 파싱 요약 뒤에 원문이
    그대로 붙어야 사람이 눈으로 대조할 수 있다."""
    s = StreamState()
    line_text = _ain_line(1)
    s.ingest([line_text], now_s=0.0)
    row = s.visible_rows()[0]
    assert format_row(row).endswith(line_text)


def test_format_row_uses_a_placeholder_for_missing_numeric_fields():
    """cmd·corrupt 줄은 seq·t·value·raw·ma 가 전부 없다. 빈 칸이면 정렬이
    깨진 것인지 값이 원래 없는 것인지 구분이 안 되므로 자리표시자를 찍는다."""
    s = StreamState()
    line_text = build_command("SACK", "CFG", "OK").rstrip("\r\n")
    s.ingest([line_text], now_s=0.0)
    row = s.visible_rows()[0]
    line = format_row(row)
    assert "—" in line
    assert line.endswith(line_text)


def test_format_row_lines_up_columns_for_different_rows():
    """🔴 값의 자릿수가 달라도(음수·큰 seq) 열 경계가 흔들리면 안 된다 —
    다른 줄이라도 같은 위치에서 원문이 시작해야 한다."""
    s = StreamState()
    s.ingest([_ain_line(3, connector_id=3, raw=-65516, ma=-8.0, value=-4.0008),
              _ain_line(37196, connector_id=3, raw=8388607, ma=12.0, value=99.9999)],
             now_s=0.0)
    rows = s.visible_rows()
    lines = [format_row(r) for r in rows]
    original_starts = [len(ln) - len(r.line) for ln, r in zip(lines, rows)]
    assert original_starts[0] == original_starts[1]


def test_format_header_names_the_columns():
    header = format_header()
    for col in ("seq", "t", "type", "value", "raw", "ma"):
        assert col in header


# ------------------------------------------------------------- 기본 필터
#
# 🔴 연결 직후 `$CFG,LIST` 카탈로그가 91줄을 쏟아낸다 — cfg_item·cfg_field·
#    cfg_end. 텔레메트리를 보려는 화면인데 그 사이에 파묻히면 이 화면의
#    존재 이유가 없다. 기본으로 꺼 두고 체크박스로 켤 수 있게 한다.

def test_default_hidden_types_are_exactly_the_catalog_record_types():
    assert DEFAULT_HIDDEN_TYPES == frozenset({"cfg_item", "cfg_field", "cfg_end"})
