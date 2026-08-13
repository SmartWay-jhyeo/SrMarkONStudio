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


def test_is_telemetry_distinguishes_command_responses():
    """🔴 seq 는 텔레메트리 전용이다 (규격 §7.1.1).

    명령 응답 레코드의 seq 는 항상 0 이다. 그 0 을 시퀀스에 넣으면 매번
    거대한 역방향 점프로 보여 유실 통계가 망가진다.
    """
    from host.core.records import is_telemetry

    assert is_telemetry({"type": "ain"}) is True
    for t in ("id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end"):
        assert is_telemetry({"type": t}) is False, t


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
