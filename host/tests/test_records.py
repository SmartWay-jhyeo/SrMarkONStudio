import pytest

from host.core.errors import ProtocolError
from host.core.records import SCHEMA_VER, SeqTracker, parse_record


def _line(**fields) -> str:
    import json

    base = {"schema_ver": SCHEMA_VER, "seq": 1, "t": 1772200855875, "type": "ain"}
    base.update(fields)
    return json.dumps(base)


def test_parse_record_returns_dict():
    rec = parse_record(_line(raw=8388608))
    assert rec["type"] == "ain"
    assert rec["raw"] == 8388608


def test_parse_record_rejects_wrong_schema_ver():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":2,"seq":1,"t":0,"type":"ain"}')


def test_parse_record_rejects_missing_mandatory_field():
    """seq/t/type 는 마스크로 끌 수 없는 필수 필드다."""
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"t":0,"type":"ain"}')


def test_parse_record_rejects_broken_json():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,')


def test_parse_record_rejects_non_integer_seq():
    """펌웨어 직렬화 버그로 seq 가 문자열로 나올 수 있다.

    통과시키면 나중에 observe() 안에서 TypeError 가 터져 수집 루프가
    통째로 죽는다. ProtocolError 로 잡아야 그 줄만 버리고 계속할 수 있다.
    """
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":"1","t":0,"type":"ain"}')


def test_parse_record_rejects_bool_seq():
    """bool 은 int 의 서브클래스라 isinstance 만으로는 안 걸린다."""
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":true,"t":0,"type":"ain"}')


def test_parse_record_rejects_seq_outside_uint32():
    """음수나 uint32 를 넘는 값은 모듈로 연산을 조용히 망가뜨린다."""
    for bad in (-1, 1 << 32):
        with pytest.raises(ProtocolError):
            parse_record(f'{{"schema_ver":3,"seq":{bad},"t":0,"type":"ain"}}')


def test_parse_record_rejects_non_integer_t():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,"t":"now","type":"ain"}')


def test_parse_record_rejects_non_string_type():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,"t":0,"type":7}')


def test_seq_tracker_counts_no_loss():
    t = SeqTracker()
    for s in (10, 11, 12):
        assert t.observe(s) == 0
    assert t.missing_total == 0
    assert t.received_total == 3


def test_seq_tracker_detects_gap():
    t = SeqTracker()
    t.observe(10)
    assert t.observe(14) == 3          # 11, 12, 13 유실
    assert t.missing_total == 3


def test_seq_tracker_ignores_duplicate_and_reorder():
    """중복이나 역순은 유실이 아니다. 0 을 반환하고 누계도 늘지 않는다."""
    t = SeqTracker()
    t.observe(10)
    assert t.observe(10) == 0
    assert t.observe(9) == 0
    assert t.missing_total == 0


def test_seq_tracker_handles_uint32_wrap():
    """seq 는 uint32 라 4294967295 다음은 0 이다. 이를 유실로 세면 안 된다."""
    t = SeqTracker()
    t.observe(4294967295)
    assert t.observe(0) == 0
    assert t.missing_total == 0


def test_seq_tracker_reset_on_reconnect():
    t = SeqTracker()
    t.observe(100)
    t.reset()
    assert t.observe(5) == 0           # 재연결 후 첫 값은 기준점일 뿐
    assert t.missing_total == 0


def test_delta_of_exactly_wrap_tolerance_is_not_counted_as_loss():
    """🔴 delta 가 정확히 2^31 이면 전진 21억인지 후진 21억인지 알 수 없다.

    현재 경계가 `> _WRAP_TOLERANCE` 라 정확히 2^31 은 "전진" 으로 분류되고
    유실 2,147,483,647 개로 기록된다. 그 한 줄이 세션 전체 통계를 망친다.
    """
    from host.core.records import _WRAP_TOLERANCE

    t = SeqTracker()
    t.observe(0)
    assert t.observe(_WRAP_TOLERANCE) == 0
    assert t.missing_total == 0
    assert t.discontinuity_count == 1


def test_implausibly_large_gap_is_discontinuity_not_loss():
    """유실이라기엔 물리적으로 불가능한 크기는 통계에서 뺀다."""
    from host.core.records import MAX_PLAUSIBLE_GAP

    t = SeqTracker()
    t.observe(0)
    assert t.observe(MAX_PLAUSIBLE_GAP + 100) == 0
    assert t.missing_total == 0
    assert t.discontinuity_count == 1
    # 기준점은 옮겨졌으므로 이후 측정은 정상 동작한다
    assert t.observe(MAX_PLAUSIBLE_GAP + 103) == 2


def test_plausible_gap_is_still_counted():
    """경계 바로 아래는 정상적으로 유실로 센다."""
    t = SeqTracker()
    t.observe(0)
    assert t.observe(1000) == 999
    assert t.missing_total == 999
    assert t.discontinuity_count == 0


def test_three_duplicates_are_not_mistaken_for_reboot():
    """🔴 같은 값이 세 번 오는 것은 재전송이지 재시작이 아니다.

    구분하지 않으면 중복 3회가 재부팅으로 오판되어 resync_count 와
    유실률이 둘 다 신뢰할 수 없게 된다.
    """
    t = SeqTracker()
    t.observe(10)
    for _ in range(5):
        assert t.observe(10) == 0
    assert t.resync_count == 0
    assert t.duplicate_count == 5
    assert t.missing_total == 0
    # 이어지는 전진은 정상 측정된다
    assert t.observe(13) == 2


def test_non_advancing_backward_values_do_not_resync():
    """재시작은 값이 전진하는 형태다. 제자리 역방향은 잡음이다."""
    t = SeqTracker()
    t.observe(500)
    for _ in range(5):
        t.observe(400)          # 같은 자리로 계속 뒤로
    assert t.resync_count == 0


def test_seq_tracker_resyncs_after_unsignaled_board_reboot():
    """🔴 보드가 재부팅해 seq 가 0 부터 다시 시작해도 측정이 살아난다.

    이 방어가 없으면 _last=500 인 채로 모든 후속 값이 역순으로 분류되어
    남은 세션 내내 유실이 0 으로 보고된다. 유실 측정이 존재 이유인 모듈에서
    가장 나쁜 실패 방식이다.
    """
    from host.core.records import RESYNC_AFTER

    t = SeqTracker()
    t.observe(500)

    for s in range(RESYNC_AFTER):          # 재부팅 후 0, 1, 2 …
        assert t.observe(s) == 0
    assert t.resync_count == 1

    assert t.observe(RESYNC_AFTER) == 0            # 이어서 정상 전진
    assert t.observe(RESYNC_AFTER + 2) == 1        # 유실 검출이 되살아났다
    assert t.missing_total == 1


def test_single_duplicate_does_not_trigger_resync():
    """중복 하나로는 재동기화하지 않는다. 전진하면 카운터가 풀린다."""
    t = SeqTracker()
    t.observe(10)
    t.observe(10)                          # 중복
    t.observe(11)                          # 전진 → backward_run 리셋
    t.observe(11)
    assert t.resync_count == 0
    assert t.missing_total == 0


def test_is_telemetry_distinguishes_command_responses():
    """🔴 seq 는 텔레메트리 전용이다 (규격 §7.1.1).

    명령 응답 레코드의 seq 는 항상 0 이다. 그 0 을 시퀀스에 넣으면 매번
    거대한 역방향 점프로 보여 유실 통계가 망가진다.
    """
    from host.core.records import is_telemetry

    assert is_telemetry({"type": "ain"}) is True
    assert is_telemetry({"type": "i2c"}) is True
    assert is_telemetry({"type": "din"}) is True   # 규격 §7.6 — 엣지도 seq 를 탄다
    for t in ("id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end"):
        assert is_telemetry({"type": t}) is False, t


def test_din_record_parses_like_any_other_telemetry():
    """🔴 `din` 은 새 타입이지만 파싱 경로는 새로 만들지 않는다 — `parse_record`
    는 `type` 을 가리지 않고 공통 필드만 본다(규격 §7.1)."""
    line = ('{"schema_ver":3,"seq":7,"t":1000,"type":"din",'
            '"connector_id":18,"state":1}')
    rec = parse_record(line)
    assert rec["type"] == "din"
    assert rec["connector_id"] == 18
    assert rec["state"] == 1


def test_seq_tracker_is_not_polluted_by_command_responses():
    """명령 응답을 걸러내면 텔레메트리 시퀀스가 온전히 유지된다."""
    from host.core.records import is_telemetry

    t = SeqTracker()
    stream = [
        {"type": "ain", "seq": 10},
        {"type": "cfg_value", "seq": 0},   # 응답이 중간에 끼어든다
        {"type": "ain", "seq": 11},
    ]
    for rec in stream:
        if is_telemetry(rec):
            t.observe(rec["seq"])
    assert t.missing_total == 0
    assert t.received_total == 2
