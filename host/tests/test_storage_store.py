"""host/storage/store.py — 레코드를 sqlite 파일에 쌓는 층.

🔴 보드를 쓰지 않는다. 여기서 만드는 레코드는 규격 §7 모양을 손으로
흉내 낸 dict 다.
"""
from __future__ import annotations

import sqlite3

import pytest

from host.storage.store import RecordStore

T0 = 1_772_000_000_000  # 임의의 epoch_ms (2026년 근처)


def _ain(seq, t, connector_id=3, value=3.98):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "ain",
        "connector_id": connector_id, "raw": 8_000_000, "ma": 12.0,
        "value": value, "status": 0,
    }


def _i2c(seq, t, connector_id=10, quantity="lux", value=400.0):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "i2c",
        "connector_id": connector_id, "quantity": quantity, "value": value,
        "status": 0,
    }


def _din(seq, t, connector_id=18, state=1):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "din",
        "connector_id": connector_id, "state": state,
    }


# ------------------------------------------------------------- 왕복(round trip)
def test_write_then_read_back_same_value(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1)
    rec = _ain(1, T0, value=3.98)
    store.write(rec, '{"raw":"line"}')
    store.close()

    conn = sqlite3.connect(str(next(tmp_path.glob("*.sqlite3"))))
    row = conn.execute(
        "SELECT t, seq, type, connector_id, value, raw FROM records"
    ).fetchone()
    conn.close()
    assert row == (T0, 1, "ain", 3, 3.98, '{"raw":"line"}')


def test_round_trip_preserves_i2c_quantity(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1)
    store.write(_i2c(1, T0, quantity="temp", value=23.5), "{}")
    store.close()
    conn = sqlite3.connect(str(next(tmp_path.glob("*.sqlite3"))))
    row = conn.execute("SELECT quantity, value FROM records").fetchone()
    conn.close()
    assert row == ("temp", 23.5)


def test_round_trip_preserves_din_state(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1)
    store.write(_din(1, T0, state=1), "{}")
    store.close()
    conn = sqlite3.connect(str(next(tmp_path.glob("*.sqlite3"))))
    row = conn.execute("SELECT type, state FROM records").fetchone()
    conn.close()
    assert row == ("din", 1)


def test_write_many_then_read_all_in_order(tmp_path):
    store = RecordStore(tmp_path, commit_batch=10)
    for i in range(25):
        store.write(_ain(i, T0 + i), "{}")
    store.close()
    conn = sqlite3.connect(str(next(tmp_path.glob("*.sqlite3"))))
    seqs = [r[0] for r in conn.execute("SELECT seq FROM records ORDER BY t")]
    conn.close()
    assert seqs == list(range(25))


# --------------------------------------------------------- 회전(rotation)
def test_rotation_actually_splits_files_across_days(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1)
    day1 = T0
    day2 = T0 + 2 * 24 * 3600 * 1000
    store.write(_ain(1, day1), "{}")
    store.write(_ain(2, day2), "{}")
    store.close()
    files = sorted(p.name for p in tmp_path.glob("*.sqlite3"))
    assert len(files) == 2


def test_rotation_by_size_splits_within_the_same_day(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1, max_file_bytes=4096)
    for i in range(400):  # 넉넉히 4096바이트를 넘긴다
        store.write(_ain(i, T0 + i, value=float(i)), "{}")
    store.close()
    files = list(tmp_path.glob("records_*.sqlite3"))
    assert len(files) >= 2


# --------------------------------------------------------------- 되돌림 검사
def test_without_rotation_everything_lands_in_one_file(tmp_path):
    """회전 조건이 없으면(=상한이 무한대면) 며칠치가 파일 하나에 몰린다.

    회전 조건이 실제로 파일 개수를 좌우한다는 것을 반대 방향으로 확인한다
    — `max_bytes`를 사실상 무한대로 주면 날짜가 바뀌어도 크기로는 안
    넘어가지만, 날짜 회전은 `max_bytes`와 무관하므로 여전히 나뉜다.
    날짜 자체를 고정해 크기 조건만 빠진 상태를 만든다.
    """
    store = RecordStore(tmp_path, commit_batch=1, max_file_bytes=10**18)
    for i in range(50):
        store.write(_ain(i, T0 + i), "{}")  # 전부 같은 날
    store.close()
    files = list(tmp_path.glob("records_*.sqlite3"))
    assert len(files) == 1


# ------------------------------------------------------------- 끊김(crash) 안전성
def test_committed_rows_survive_without_a_clean_close(tmp_path):
    """🔴 프로세스가 죽어도(=`close()`를 못 부르고 끝나도) **커밋된** 것은 산다.

    `close()`를 부르지 않고 객체를 버리는 것으로 "정리 없이 끝난 프로세스"를
    흉내 낸다 — WAL 저널이 커밋된 트랜잭션을 보증하므로, 다음에 그 파일을
    새로 여는 쪽(다른 `RecordStore`, 또는 이 시험처럼 맨 sqlite3 연결)이
    커밋된 것까지는 읽을 수 있어야 한다. 마지막 미커밋 배치만 사라지는
    것은 설계상 허용한다(`commit_batch`/`commit_interval_s` 주석).
    """
    store = RecordStore(tmp_path, commit_batch=5, commit_interval_s=999.0)
    for i in range(12):
        store.write(_ain(i, T0 + i), "{}")
    # close() 를 부르지 않는다 — 마지막 배치(2건, 10~11)는 커밋 문턱(5)을
    # 못 채워 대기 중이다. 커넥션 객체를 그냥 버린다(프로세스 종료 흉내).
    path = store._current_path
    del store

    conn = sqlite3.connect(str(path))
    seqs = [r[0] for r in conn.execute("SELECT seq FROM records ORDER BY seq")]
    conn.close()
    # 10건(0~9)은 commit_batch=5 로 두 번 커밋됐다. 마지막 2건은 없어도 된다.
    assert seqs == list(range(10))


def test_flush_makes_the_pending_batch_visible_immediately(tmp_path):
    store = RecordStore(tmp_path, commit_batch=1000, commit_interval_s=999.0)
    store.write(_ain(1, T0), "{}")
    store.flush()
    path = store._current_path
    conn2 = sqlite3.connect(str(path))
    n = conn2.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    conn2.close()
    store.close()
    assert n == 1


# --------------------------------------------------------------------- 이벤트
def test_write_event_is_flushed_immediately(tmp_path):
    """🔴 끊김 이벤트는 배치를 기다리지 않는다 — 끊긴 직후 전원이 나가도
    "끊겼다"는 사실 자체는 남아야 한다."""
    store = RecordStore(tmp_path, commit_batch=1000, commit_interval_s=999.0)
    store.write_event("link_down", T0, note="시험")
    path = store._current_path
    conn2 = sqlite3.connect(str(path))
    row = conn2.execute("SELECT type, seq, raw FROM records").fetchone()
    conn2.close()
    store.close()
    assert row[0] == "link_down"
    assert row[1] is None
    assert "시험" in row[2]


# --------------------------------------------------------------------- 보존
def test_apply_retention_deletes_old_files_and_keeps_recent(tmp_path):
    from host.storage.store import apply_retention

    store = RecordStore(tmp_path, commit_batch=1)
    old_t = T0 - 30 * 24 * 3600 * 1000
    store.write(_ain(1, old_t), "{}")
    store.close()

    store2 = RecordStore(tmp_path, commit_batch=1)
    store2.write(_ain(2, T0), "{}")
    store2.close()

    assert len(list(tmp_path.glob("*.sqlite3"))) == 2
    deleted = apply_retention(tmp_path, older_than_days=7, now_ms=T0)
    assert len(deleted) == 1
    assert len(list(tmp_path.glob("*.sqlite3"))) == 1
