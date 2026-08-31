"""host/storage/query.py — 저장한 것을 시각 범위·최근접으로 다시 꺼낸다."""
from __future__ import annotations

from host.storage.query import find_nearest, query_range
from host.storage.store import RecordStore

T0 = 1_772_000_000_000  # 임의의 epoch_ms, 초 단위 경계에 맞춰 둔다
DAY_MS = 24 * 3600 * 1000


def _ain(seq, t, connector_id=3, value=3.98):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "ain",
        "connector_id": connector_id, "value": value, "status": 0,
    }


def _i2c(seq, t, connector_id=10, quantity="lux", value=400.0):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "i2c",
        "connector_id": connector_id, "quantity": quantity, "value": value,
        "status": 0,
    }


def _store_with(tmp_path, records):
    store = RecordStore(tmp_path, commit_batch=1)
    for rec in records:
        store.write(rec, "{}")
    store.close()
    return tmp_path


# --------------------------------------------------------------- query_range
def test_query_range_includes_both_endpoints(tmp_path):
    base = _store_with(tmp_path, [
        _ain(0, T0), _ain(1, T0 + 100), _ain(2, T0 + 200),
    ])
    out = query_range(base, T0, T0 + 200)
    assert [r["seq"] for r in out] == [0, 1, 2]


def test_query_range_excludes_records_outside_the_window(tmp_path):
    base = _store_with(tmp_path, [
        _ain(0, T0 - 1), _ain(1, T0), _ain(2, T0 + 100), _ain(3, T0 + 101),
    ])
    out = query_range(base, T0, T0 + 100)
    assert [r["seq"] for r in out] == [1, 2]


def test_query_range_filters_by_type(tmp_path):
    base = _store_with(tmp_path, [
        _ain(0, T0), _i2c(1, T0 + 1),
    ])
    out = query_range(base, T0, T0 + 1, type="i2c")
    assert [r["seq"] for r in out] == [1]


def test_query_range_filters_by_connector_and_quantity(tmp_path):
    base = _store_with(tmp_path, [
        _i2c(0, T0, connector_id=10, quantity="lux"),
        _i2c(1, T0, connector_id=10, quantity="temp"),
        _i2c(2, T0, connector_id=11, quantity="lux"),
    ])
    out = query_range(base, T0, T0, connector_id=10, quantity="lux")
    assert [r["seq"] for r in out] == [0]


def test_query_range_spans_multiple_rotated_files(tmp_path):
    base = _store_with(tmp_path, [
        _ain(0, T0), _ain(1, T0 + DAY_MS), _ain(2, T0 + 2 * DAY_MS),
    ])
    assert len(list(tmp_path.glob("*.sqlite3"))) == 3
    out = query_range(base, T0, T0 + 2 * DAY_MS)
    assert [r["seq"] for r in out] == [0, 1, 2]


def test_query_range_returns_records_in_t_order_even_across_files(tmp_path):
    base = _store_with(tmp_path, [
        _ain(1, T0 + DAY_MS), _ain(0, T0),
    ])
    out = query_range(base, T0, T0 + DAY_MS)
    assert [r["t"] for r in out] == [T0, T0 + DAY_MS]


# --------------------------------------------------------------- find_nearest
def test_find_nearest_picks_the_closer_of_before_and_after(tmp_path):
    base = _store_with(tmp_path, [_ain(0, T0), _ain(1, T0 + 100)])
    result = find_nearest(base, T0 + 90)
    assert result.record["seq"] == 1  # T0+100 이 T0+90 에 더 가깝다(10 vs 90)
    assert result.age_ms == 10


def test_find_nearest_prefers_before_on_an_exact_tie(tmp_path):
    """🔴 동률이면 이전 값을 고른다 — 카메라 프레임은 "그 순간까지 알려진
    값"으로 보정하는 것이 정상이고, 아직 오지 않은 미래 값을 쓰면
    인과관계가 뒤집힌다."""
    base = _store_with(tmp_path, [_ain(0, T0), _ain(1, T0 + 20)])
    result = find_nearest(base, T0 + 10)  # 양쪽 다 10ms 차이
    assert result.record["seq"] == 0
    assert result.age_ms == 10


def test_find_nearest_uses_only_the_exact_match_when_available(tmp_path):
    base = _store_with(tmp_path, [_ain(0, T0), _ain(1, T0 + 100)])
    result = find_nearest(base, T0)
    assert result.record["seq"] == 0
    assert result.age_ms == 0


def test_find_nearest_filters_by_type_and_connector(tmp_path):
    base = _store_with(tmp_path, [
        _ain(0, T0, connector_id=3),
        _i2c(1, T0, connector_id=10, quantity="lux"),
    ])
    result = find_nearest(base, T0, type="i2c", connector_id=10, quantity="lux")
    assert result.record["seq"] == 1


def test_find_nearest_marks_too_old_values_as_stale(tmp_path):
    base = _store_with(tmp_path, [_ain(0, T0)])
    result = find_nearest(base, T0 + 5000, max_age_ms=2000)
    assert result.found is True         # 값은 찾았다
    assert result.stale is True          # 다만 너무 묵었다
    assert result.age_ms == 5000


def test_find_nearest_within_tolerance_is_not_stale(tmp_path):
    base = _store_with(tmp_path, [_ain(0, T0)])
    result = find_nearest(base, T0 + 500, max_age_ms=2000)
    assert result.stale is False


def test_find_nearest_with_no_data_reports_not_found(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    result = find_nearest(tmp_path, T0)
    assert result.found is False
    assert result.record is None
    assert result.stale is True


def test_find_nearest_searches_across_adjacent_rotated_files(tmp_path):
    """조회 대상 시각이 자정 근처라 앞뒤 후보가 서로 다른 파일에 있을 수
    있다 — 날짜 하나만 보면 놓친다."""
    base = _store_with(tmp_path, [
        _ain(0, T0 - 100),         # 전날 파일
        _ain(1, T0 + DAY_MS + 100),  # 다음날 파일
    ])
    result = find_nearest(base, T0 + DAY_MS, max_age_ms=10**9)
    assert result.record["seq"] == 1


# --------------------------------------------------------------- 되돌림 검사
def test_query_plan_uses_the_time_index():
    """🔴 색인이 실제로 조회 경로에 쓰이는지 확인한다.

    값 비교(위 시험들)만으로는 색인을 빼도 통과한다 — sqlite 는 색인 없이
    풀스캔으로도 같은 결과를 낸다. 이 시험은 `EXPLAIN QUERY PLAN`으로
    실행 계획 자체를 본다. `store.py`의 `CREATE INDEX idx_records_t`를
    지우면 이 시험만 깨진다(직접 확인함 — storage-report.md).
    """
    import sqlite3

    from host.storage.store import _SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM records WHERE t BETWEEN ? AND ?",
        (0, 1),
    ).fetchall()
    conn.close()
    plan_text = " ".join(str(row) for row in plan)
    assert "idx_records_t" in plan_text
