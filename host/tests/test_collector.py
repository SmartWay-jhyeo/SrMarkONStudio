"""host/service/collector.py — GUI 없이 계속 도는 무인 수집기.

🔴 보드를 쓰지 않는다(작업 지시). 트랜스포트는 전부 가짜다 — 연결 성공/
실패/도중 끊김을 이 시험이 직접 결정한다.
"""
from __future__ import annotations

import json

from host.service.collector import Collector
from host.storage.query import query_range
from host.storage.store import RecordStore

T0 = 1_772_000_000_000


class FakeTransport:
    """줄 목록을 read_lines() 로 흘려보내는 가짜 트랜스포트.

    `broken=True` 로 만들면 `read_lines()`가 예외를 던진다 — 보드를
    뽑았을 때 pyserial 이 하는 일을 흉내 낸다.
    """

    def __init__(self, lines=None):
        self._queue = list(lines or [])
        self.broken = False
        self.closed = False
        self.writes = []

    def push(self, *lines):
        self._queue.extend(lines)

    def write(self, data):
        self.writes.append(data)

    def read_lines(self):
        if self.broken:
            raise ConnectionError("보드가 뽑혔다")
        out, self._queue = self._queue, []
        return iter(out)

    def close(self):
        self.closed = True


def _ain_line(seq, t, connector_id=3, value=3.98):
    rec = {
        "schema_ver": 3, "seq": seq, "t": t, "type": "ain",
        "connector_id": connector_id, "value": value, "status": 0,
    }
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


class ConnectorFactory:
    """연결 시도마다 다른 트랜스포트/예외를 순서대로 내주는 헬퍼."""

    def __init__(self):
        self._queue = []
        self.calls = 0

    def will_return(self, transport):
        self._queue.append(("ok", transport))
        return self

    def will_raise(self, exc):
        self._queue.append(("raise", exc))
        return self

    def __call__(self):
        self.calls += 1
        kind, value = self._queue.pop(0)
        if kind == "raise":
            raise value
        return value


def _clock_at(ms):
    box = {"t": ms}

    def clock():
        return box["t"]

    def advance(delta):
        box["t"] += delta

    clock.advance = advance
    return clock


# ------------------------------------------------------------ 연결·수집 기본
def test_collector_connects_and_ingests_telemetry(tmp_path):
    transport = FakeTransport([_ain_line(0, T0)])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()  # 연결
    c.step()  # 수집
    store.flush()

    rows = query_range(tmp_path, T0, T0)
    assert [r["seq"] for r in rows] == [0]


def test_collector_stores_corrupt_lines_too(tmp_path):
    """🔴 원문을 버리지 않는다 — 깨진 줄도 raw 로 남는다(type=corrupt)."""
    transport = FakeTransport(["not json at all"])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()
    c.step()
    store.flush()

    rows = query_range(tmp_path, T0, T0, type="corrupt")
    assert len(rows) == 1
    assert rows[0]["raw"] == "not json at all"


def test_collector_ignores_dollar_command_lines(tmp_path):
    """$SACK 등은 텔레메트리가 아니다 — 저장 대상에서 뺀다."""
    transport = FakeTransport(["$SACK,HB*00"])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()
    c.step()
    store.flush()

    rows = query_range(tmp_path, T0 - 1000, T0 + 1000)
    assert rows == []


# --------------------------------------------------------------- 재접속
def test_collector_retries_on_connect_failure_without_dying(tmp_path):
    connect = (
        ConnectorFactory()
        .will_raise(OSError("포트 없음"))
        .will_raise(OSError("포트 없음"))
        .will_return(FakeTransport([_ain_line(0, T0)]))
    )
    store = RecordStore(tmp_path, commit_batch=1)
    slept = []

    c = Collector(connect, store, clock=_clock_at(T0), sleep=slept.append)
    for _ in range(3):
        c.step()
    assert connect.calls == 3
    assert len(slept) == 2  # 실패마다 재시도 전에 쉬었다


def test_collector_backoff_grows_then_caps(tmp_path):
    from host.service.collector import RECONNECT_BACKOFF_MAX_S, RECONNECT_BACKOFF_START_S

    connect = ConnectorFactory()
    for _ in range(6):
        connect.will_raise(OSError("포트 없음"))
    connect.will_return(FakeTransport())
    store = RecordStore(tmp_path, commit_batch=1)
    slept = []

    c = Collector(connect, store, clock=_clock_at(T0), sleep=slept.append)
    for _ in range(7):
        c.step()

    assert slept[0] == RECONNECT_BACKOFF_START_S
    assert slept == sorted(slept)               # 단조 증가
    assert slept[-1] <= RECONNECT_BACKOFF_MAX_S  # 상한을 넘지 않는다


def test_collector_reconnects_after_a_mid_stream_disconnect(tmp_path):
    """🔴 끊겼다 붙으면 수집이 이어진다 — 보드를 뽑았다 꽂는 실제 상황."""
    t1 = FakeTransport([_ain_line(0, T0)])
    t2 = FakeTransport([_ain_line(1, T0 + 5000)])
    connect = ConnectorFactory().will_return(t1).will_return(t2)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()  # 연결(t1)
    c.step()  # seq0 수집
    t1.broken = True
    c.step()  # pump 실패 → 끊김 처리
    c.step()  # 재연결(t2)
    c.step()  # seq1 수집
    store.flush()

    rows = query_range(tmp_path, T0, T0 + 5000, type="ain")
    assert [r["seq"] for r in rows] == [0, 1]


def test_disconnect_writes_a_link_down_event(tmp_path):
    t1 = FakeTransport([_ain_line(0, T0)])
    connect = ConnectorFactory().will_return(t1).will_raise(OSError("아직 없음"))
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()
    c.step()
    t1.broken = True
    c.step()
    store.flush()

    rows = query_range(tmp_path, T0 - 1000, T0 + 1000, type="link_down")
    assert len(rows) == 1


def test_reconnect_writes_a_link_up_event_only_after_a_prior_drop(tmp_path):
    """🔴 처음 연결 성공은 link_up 이 아니다 — 끊긴 적이 없으면 "재"접속이
    아니다."""
    t1 = FakeTransport()
    connect = ConnectorFactory().will_return(t1)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()  # 첫 연결
    store.flush()

    rows = query_range(tmp_path, T0 - 1000, T0 + 1000, type="link_up")
    assert rows == []


def test_gap_between_link_down_and_link_up_is_visible_in_the_timeline(tmp_path):
    t1 = FakeTransport()
    t2 = FakeTransport()
    connect = ConnectorFactory().will_return(t1).will_return(t2)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()               # 연결(t1)
    t1.broken = True
    c.step()                # 끊김 감지 → link_down @ T0
    clock.advance(3000)
    c.step()                # 재연결(t2) → link_up @ T0+3000
    store.flush()

    rows = query_range(tmp_path, T0 - 1, T0 + 3000)
    kinds = [(r["type"], r["t"]) for r in rows]
    assert ("link_down", T0) in kinds
    assert ("link_up", T0 + 3000) in kinds


# --------------------------------------------------------------- 정상 종료
def test_stop_ends_run_forever_and_flushes(tmp_path):
    transport = FakeTransport([_ain_line(0, T0)])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1000, commit_interval_s=999.0)
    clock = _clock_at(T0)

    steps = {"n": 0}

    def sleep(_s):
        pass

    c = Collector(connect, store, clock=clock, sleep=sleep)

    real_step = c.step

    def counted_step():
        steps["n"] += 1
        if steps["n"] >= 3:
            c.stop()
        real_step()

    c.step = counted_step
    c.run_forever()
    c.close()

    rows = query_range(tmp_path, T0, T0)
    assert [r["seq"] for r in rows] == [0]


# --------------------------------------------------------------------- 상태
def test_status_reports_counts(tmp_path):
    transport = FakeTransport([_ain_line(0, T0), _ain_line(1, T0)])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)

    c = Collector(connect, store, clock=clock, sleep=lambda s: None)
    c.step()
    c.step()
    status = c.status()
    assert status["connected"] is True
    assert status["line_total"] == 2
    assert status["record_total"] == 2


def test_status_file_is_written_when_configured(tmp_path):
    import json as _json

    transport = FakeTransport([_ain_line(0, T0)])
    connect = ConnectorFactory().will_return(transport)
    store = RecordStore(tmp_path, commit_batch=1)
    clock = _clock_at(T0)
    status_path = tmp_path / "status.json"

    c = Collector(
        connect, store, clock=clock, sleep=lambda s: None,
        status_path=status_path, status_interval_s=0,
    )
    c.step()  # 연결
    c.step()  # 수집 + 상태 기록

    assert status_path.exists()
    data = _json.loads(status_path.read_text(encoding="utf-8"))
    assert data["line_total"] == 1
    assert not status_path.with_suffix(".json.tmp").exists()
