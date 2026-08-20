"""NDJSON 이 실제로 오는지 보여주는 스트림 화면의 상태 — Qt 없이 시험한다.

`host/gui/stream.py` 는 `screen.py` 와 같은 층이다(test_layer_boundaries.py
가 AST 로 강제한다).
"""

from host.core.framing import build_command
from host.gui.stream import (
    DEFAULT_HIDDEN_TYPES,
    KNOWN_CONNECTORS,
    INTERVAL_SAMPLES,
    RATE_BUCKET_COUNT,
    RATE_BUCKET_S,
    RECENT_GAPS_MAX,
    StreamState,
    connector_label,
    filter_note,
    format_gap,
    format_header,
    format_interval,
    format_row,
    staleness_level,
    toggle_all,
    toggle_all_label,
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


# ------------------------------------------------------------ 도착 간격 분포
#
# 🔴 HANDOFF.md §3 미해결 문제 — "워커 한 바퀴 200ms: 보드는 100ms로
#    보내는데 화면의 표본 간격이 202ms다." 이 원인이 링크/호스트인지
#    보드인지 가르는 것이 이 기능의 존재 이유다. 그래서 **도착 간격**
#    (host 가 받은 시각의 차)과 **보드 간격**(레코드의 `t` 값의 차)을
#    반드시 따로 재야 한다 — 되돌림 검사로 못박는다.

def test_interval_summary_separates_arrival_jitter_from_board_cadence():
    """🔴 되돌림 검사 — 둘을 같은 값으로 계산하면(예: board 간격도 도착
    시각으로 잰다) 이 시험이 깨진다. 보드는 정확히 100ms 마다 `t` 를
    찍었는데(단조 증가), 호스트 수신은 들쭉날쭉하게(80~260ms) 흔들리는
    상황을 흉내낸다 — 실제로 의심되는 상황과 같다."""
    s = StreamState()
    arrivals = [0.0, 0.08, 0.34, 0.42, 0.68]      # 들쭉날쭉한 도착
    for i, arrived in enumerate(arrivals):
        s.ingest([_ain_line(i, connector_id=4)], now_s=arrived)

    ci = next(c for c in s.summary(now_s=1.0).intervals
              if c.type == "ain" and c.connector == 4)

    # 보드 간격: t 가 seq*100 이므로 항상 정확히 100ms.
    assert ci.board.min_ms == 100.0
    assert ci.board.max_ms == 100.0
    assert ci.board.median_ms == 100.0

    # 도착 간격: 80,260,80,260ms — 흔들린다. 최대가 보드 간격보다 크다.
    assert ci.arrival.max_ms > ci.board.max_ms
    assert ci.arrival.min_ms < ci.board.min_ms
    assert ci.arrival.sample_count == 4


def test_interval_summary_is_grouped_per_type_and_connector():
    """🔴 "가능하면 커넥터별" — J4 만 이상해도 다른 채널에 묻히면 안 된다."""
    s = StreamState()
    for i in range(3):
        s.ingest([_ain_line(i, connector_id=4)], now_s=i * 0.1)
        s.ingest([_ain_line(i, connector_id=5)], now_s=i * 0.1)

    intervals = s.summary(now_s=1.0).intervals
    keys = {(c.type, c.connector) for c in intervals}
    assert ("ain", 4) in keys
    assert ("ain", 5) in keys


def test_repeated_t_is_counted_and_visible():
    """🔴 펌웨어가 "마지막 값을 주기마다 다시 보내는" 구조로 바뀌었다 —
    같은 `t` 가 반복되면 새 표본이 아니라 붙들고 있는 값이라는 사실이
    보여야 한다."""
    s = StreamState()
    # t 가 0,0,0,100 처럼 세 번 반복되다 한 번 전진한다.
    lines = [
        '{"schema_ver":3,"seq":0,"t":1000,"type":"ain","connector_id":4,'
        '"raw":1,"ma":1.0,"value":1.0,"unit":"bar","status":0}',
        '{"schema_ver":3,"seq":1,"t":1000,"type":"ain","connector_id":4,'
        '"raw":1,"ma":1.0,"value":1.0,"unit":"bar","status":0}',
        '{"schema_ver":3,"seq":2,"t":1000,"type":"ain","connector_id":4,'
        '"raw":1,"ma":1.0,"value":1.0,"unit":"bar","status":0}',
        '{"schema_ver":3,"seq":3,"t":1100,"type":"ain","connector_id":4,'
        '"raw":1,"ma":1.0,"value":1.0,"unit":"bar","status":0}',
    ]
    for i, line in enumerate(lines):
        s.ingest([line], now_s=i * 0.1)

    ci = next(c for c in s.summary(now_s=1.0).intervals
              if c.type == "ain" and c.connector == 4)
    assert ci.repeat_count == 2                # t 가 안 바뀐 관찰 두 번
    assert ci.board.min_ms == 0.0               # 반복은 간격 0 으로 실린다


def test_interval_stats_are_empty_before_two_samples():
    """표본이 하나뿐이면 간격을 잴 수 없다 — None 이어야지 0 이 아니다."""
    s = StreamState()
    s.ingest([_ain_line(0, connector_id=4)], now_s=0.0)
    ci = next(c for c in s.summary(now_s=1.0).intervals
              if c.type == "ain" and c.connector == 4)
    assert ci.arrival.sample_count == 0
    assert ci.arrival.median_ms is None
    assert ci.board.median_ms is None


def test_interval_window_is_bounded_by_sample_count_not_full_buffer():
    """🔴 성능 전제 — 창이 개수 기반(`INTERVAL_SAMPLES`)이라 표본이 아무리
    쌓여도 최근 것만 본다. 매 프레임 전체 버퍼를 다시 훑지 않는다는 것의
    관찰 가능한 결과다."""
    s = StreamState()
    for i in range(INTERVAL_SAMPLES * 5):
        s.ingest([_ain_line(i, connector_id=4)], now_s=i * 0.1)
    ci = next(c for c in s.summary(now_s=1000.0).intervals
              if c.type == "ain" and c.connector == 4)
    assert ci.arrival.sample_count == INTERVAL_SAMPLES


def test_format_interval_shows_type_connector_and_both_cadences():
    s = StreamState()
    for i in range(3):
        s.ingest([_ain_line(i, connector_id=4)], now_s=i * 0.1)
    ci = next(c for c in s.summary(now_s=1.0).intervals
              if c.type == "ain" and c.connector == 4)
    text = format_interval(ci)
    assert "ain" in text
    assert "4" in text


# --------------------------------------------------------------- seq 누락 위치
#
# 지금까지는 개수만 알았다. "어느 구간에서 몇 개가 빠졌는지" 최근 몇 건을
# 남긴다 — 링크를 의심할 때 필요한 건 총계가 아니라 위치다.

def test_seq_gap_records_the_missing_range():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.ingest([_ain_line(5)], now_s=0.5)             # 2,3,4 누락
    gaps = s.summary(now_s=1.0).recent_gaps
    assert len(gaps) == 1
    assert gaps[0].seq_lo == 2
    assert gaps[0].seq_hi == 4
    assert gaps[0].count == 3
    assert gaps[0].arrived_s == 0.5


def test_seq_gaps_accumulate_across_multiple_holes():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.ingest([_ain_line(3)], now_s=0.1)              # 2 누락
    s.ingest([_ain_line(6)], now_s=0.2)              # 4,5 누락
    gaps = s.summary(now_s=1.0).recent_gaps
    assert [(g.seq_lo, g.seq_hi) for g in gaps] == [(2, 2), (4, 5)]


def test_seq_gaps_are_bounded_to_the_most_recent():
    s = StreamState()
    seq = 0
    s.ingest([_ain_line(seq)], now_s=0.0)
    for i in range(RECENT_GAPS_MAX + 5):
        seq += 2                                     # 매번 하나씩 누락
        s.ingest([_ain_line(seq)], now_s=i * 0.1)
    gaps = s.summary(now_s=100.0).recent_gaps
    assert len(gaps) == RECENT_GAPS_MAX
    # 가장 최근 것이 마지막에 남는다.
    assert gaps[-1].seq_hi == seq - 1


def test_no_gap_recorded_when_no_seq_is_missing():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.ingest([_ain_line(2)], now_s=0.1)
    assert s.summary(now_s=1.0).recent_gaps == ()


def test_format_gap_shows_range_count_and_age():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.ingest([_ain_line(5)], now_s=10.0)             # 2,3,4 누락
    gap = s.summary(now_s=10.0).recent_gaps[0]
    text = format_gap(gap, now_s=22.3)
    assert "2" in text and "4" in text
    assert "3개" in text                              # 개수
    assert "12.3" in text                             # 경과 초


# ------------------------------------------------------------- 초당 줄 수 흐름

def test_rate_sparkline_reflects_recent_traffic_history():
    """🔴 그래프 라이브러리를 새로 안 쓴다 — 문자 스파크라인이다."""
    s = StreamState()
    # 첫 버킷(0~2초)에 몰아서 보내고, 이후로는 조용하다.
    for i in range(20):
        s.ingest([_ain_line(i)], now_s=0.0)
    spark = s.summary(now_s=RATE_BUCKET_S * (RATE_BUCKET_COUNT - 1)).rate_sparkline
    assert isinstance(spark, str)
    assert len(spark) >= 1
    # 트래픽이 있던 첫 문자가 조용해진 마지막 문자보다 "높이" 가 크다.
    assert spark[0] != spark[-1]


def test_rate_sparkline_is_empty_before_anything_arrives():
    assert StreamState().summary(now_s=0.0).rate_sparkline == ""


def test_rate_sparkline_length_is_bounded():
    s = StreamState()
    for i in range(200):
        s.ingest([_ain_line(i)], now_s=i * 0.5)      # 100초 분량
    spark = s.summary(now_s=100.0).rate_sparkline
    assert len(spark) <= RATE_BUCKET_COUNT


# ----------------------------------------------------------------- 커넥터 필터

def test_connector_filter_isolates_one_connector():
    s = StreamState()
    s.ingest([_ain_line(1, connector_id=4), _ain_line(2, connector_id=5)],
             now_s=0.0)
    s.set_connector_filter({4})
    connectors = {r.connector for r in s.visible_rows()}
    assert connectors == {4}


def test_connector_filter_none_shows_everything():
    s = StreamState()
    s.ingest([_ain_line(1, connector_id=4), _ain_line(2, connector_id=5)],
             now_s=0.0)
    s.set_connector_filter({4})
    s.set_connector_filter(None)
    assert len(s.visible_rows()) == 2


def test_connector_filter_combines_with_type_filter():
    s = StreamState()
    s.ingest([_ain_line(1, connector_id=4), _i2c_line(2)], now_s=0.0)
    s.set_filter({"ain"})
    s.set_connector_filter({4})
    rows = s.visible_rows()
    assert len(rows) == 1
    assert rows[0].type == "ain" and rows[0].connector == 4


def test_connector_counts_track_cumulative_arrivals_per_connector():
    s = StreamState()
    s.ingest([_ain_line(1, connector_id=4), _ain_line(2, connector_id=4),
              _ain_line(3, connector_id=5)], now_s=0.0)
    assert s.connector_counts[4] == 2
    assert s.connector_counts[5] == 1


# ----------------------------------------------------- 커넥터 목록 (카탈로그)

def test_known_connectors_come_from_the_same_source_as_the_dashboard():
    """🔴 되돌림 검사 — 커넥터 목록을 **도착한 레코드에서** 만들면 깨진다.

    대시보드는 값이 안 와도 자리를 깐다(`screen.py` 의 `empty_channels()` ·
    `seed_sensors()` · `seed_dins()` — 설계 원칙 3). 스트림만 도착분으로
    목록을 만들면 두 화면이 서로 다른 보드를 말하게 된다. 그래서 여기서는
    같은 상수를 쓴다 — 목록을 손으로 적지 않는다.
    """
    from host.gui.screen import (
        AIN_COUNT,
        CONNECTOR_OFFSET,
        DIN_PORTS,
        I2C_PORTS,
    )

    expected = ({CONNECTOR_OFFSET + i for i in range(AIN_COUNT)}
                | set(I2C_PORTS) | set(DIN_PORTS))
    assert set(KNOWN_CONNECTORS) == expected


def test_every_connector_is_listed_before_a_single_value_arrives():
    """값이 한 번도 안 온 커넥터도 목록에 있어야 한다 — 0 건이 곧 정보다."""
    s = StreamState()
    assert set(s.connector_counts) == set(KNOWN_CONNECTORS)
    assert set(s.connector_counts.values()) == {0}


def test_a_silent_connector_stays_at_zero_while_others_count_up():
    s = StreamState()
    s.ingest([_ain_line(1, connector_id=4), _ain_line(2, connector_id=4)],
             now_s=0.0)
    assert s.connector_counts[4] == 2
    # 🔴 J5 는 목록에 있고 0 이다. 없어진 것이 아니라 **값이 안 오는 것**이다.
    assert s.connector_counts[5] == 0


def test_connector_label_marks_the_ones_that_never_sent_anything():
    assert connector_label(5, 12) == "J5"
    assert connector_label(5, 0).startswith("J5")
    assert connector_label(5, 0) != "J5", "0 건이라는 사실이 이름표에 없다"


# ------------------------------------------------------------- 전체 선택/해제

def test_toggling_all_turns_everything_on_when_some_are_off():
    assert toggle_all({"ain"}, {"ain", "i2c", "din"}) == {"ain", "i2c", "din"}


def test_toggling_all_turns_everything_off_when_all_are_on():
    assert toggle_all({"ain", "i2c"}, {"ain", "i2c"}) == set()


def test_toggling_all_turns_everything_on_when_none_are_on():
    assert toggle_all(set(), {3, 4}) == {3, 4}


def test_toggle_all_label_says_what_the_click_will_do():
    assert toggle_all_label({"ain", "i2c"}, {"ain", "i2c"}) == "전체 해제"
    assert toggle_all_label({"ain"}, {"ain", "i2c"}) == "전체 선택"
    assert toggle_all_label(set(), set()) == "전체 선택"


# ------------------------------------------------- 전부 껐을 때 무엇을 보이나

def test_no_note_while_something_is_on_screen():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    assert filter_note(s, s.visible_rows()) == ""


def test_note_explains_an_empty_screen_when_every_type_is_off():
    """🔴 아무것도 안 보이는 화면은 고장으로 읽힌다. 필터를 자동으로
    되돌리지 않는 대신, **왜 비었는지**를 화면이 말한다."""
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.set_filter(set())
    note = filter_note(s, s.visible_rows())
    assert "타입" in note and "전체 선택" in note


def test_note_explains_an_empty_screen_when_every_connector_is_off():
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.set_connector_filter(set())
    note = filter_note(s, s.visible_rows())
    assert "커넥터" in note and "전체 선택" in note


def test_note_says_nothing_has_arrived_yet_when_nothing_has():
    s = StreamState()
    note = filter_note(s, s.visible_rows())
    assert note and "필터" not in note


def test_note_separates_a_filter_that_matches_nothing_from_one_turned_off():
    """필터는 켜져 있는데 걸리는 줄이 없는 것과, 전부 끈 것은 다른 사실이다."""
    s = StreamState()
    s.ingest([_ain_line(1)], now_s=0.0)
    s.set_filter({"i2c"})
    note = filter_note(s, s.visible_rows())
    assert note
    assert "전체 선택" not in note


def test_connector_filter_leaves_connectorless_rows_alone():
    """커넥터 하나를 꺼도 gnss(커넥터 없음)는 남는다.

    🔴 실기기에서 겪었다(2026-08-20): J3 를 끄니 gnss 줄이 통째로 사라져
       "gnss 가 J3 에 붙어 있나" 로 보였다. gnss·stat 은 장비 전체의
       것이라 커넥터 필터의 대상이 아니다 — 타입 필터로만 걸러진다.
    """
    st = StreamState()
    st.feed('{"schema_ver":3,"seq":1,"t":5,"type":"ain","connector_id":3,"value":1.0}', 0.0)
    st.feed('{"schema_ver":3,"seq":2,"t":6,"type":"gnss","lat":37.1,"lon":127.1}', 0.0)
    # J3 를 뺀 나머지만 켠 상태 (사용자가 J3 하나를 껐다)
    st.set_connector_filter({4, 5, 6})
    types = [r.type for r in st.visible_rows()]
    assert "gnss" in types, "커넥터 없는 줄이 커넥터 필터에 걸려 사라졌다"
    assert "ain" not in types, "J3 은 꺼졌으니 ain 은 없어야 한다"
