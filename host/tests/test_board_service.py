import pytest

from host.core.errors import ProtocolError
from host.core.framing import build_command
from host.service.board_service import BoardService
from host.tests.fake_board import (
    HB_TIMEOUT_MS,
    FakeBoard,
    LoopbackTransport,
    Mode,
)


class FakeClock:
    def __init__(self) -> None:
        self.now_ms = 0

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


@pytest.fixture
def rig():
    clock = FakeClock()
    sim = FakeBoard()
    svc = BoardService(LoopbackTransport(sim), clock=clock)
    return svc, sim, clock


def test_send_returns_matching_sack(rig):
    svc, _sim, _clock = rig
    ack = svc.send("CFG", "GET", "tx.period_ms")
    assert ack.args == ("CFG", "OK")


def test_send_ignores_a_stale_ack_for_another_command(rig):
    """🔴 verb 를 대조하지 않으면 남의 응답을 내 성공으로 착각한다.

    직렬 링크에서 앞선 명령의 응답이 한 박자 늦게 도착하는 것은 정상이다.
    그게 버퍼 앞자리에 있으면 보드가 RANGE 로 거부한 설정 쓰기를
    성공으로 보고한다. 설정 쓰기의 조용한 거짓 성공은 최악의 실패 방식이고
    GUI 가 이 계층 위에 올라간다.
    """
    svc, sim, _clock = rig
    svc.heartbeat()
    # 🔴 `_pending` 은 `(내보낸 속도, 줄)` 이다 — 보드가 응답을 옛 속도로
    #    내보낸 다음 속도를 바꾸기 때문에(규격 §4.2.2 규칙 1) 줄마다 속도를
    #    붙들고 있어야 한다. 여기서는 지금 속도로 온 것처럼 끼워 넣는다.
    svc.transport._pending.append(
        (svc.transport.baud, build_command("SACK", "STAT", "OK").rstrip("\r\n"))
    )
    ack = svc.send("CFG", "SET", "tx.period_ms", "999999")
    assert ack.args == ("CFG", "ERR", "RANGE")     # 내 명령의 응답이다
    assert sim.store.get("tx.period_ms") == 100    # 보드는 실제로 거부했다


def test_send_raises_when_only_foreign_acks_arrive():
    """대응하는 응답이 아예 없으면 남의 것을 돌려주지 않고 예외를 던진다.

    스텁 보드는 언제나 응답하므로 이 상황을 만들려면 더 좁은 대역이 필요하다.
    실기기에서는 명령이 유실되고 앞선 응답만 도착하면 그대로 재현된다.
    """

    class OnlyForeignAcks:
        """무엇을 보내든 남의 $SACK 하나만 돌려주는 트랜스포트."""

        def __init__(self):
            self._sent = False

        def write(self, data: str) -> None:
            self._sent = True

        def read_lines(self):
            if self._sent:
                self._sent = False
                yield build_command("SACK", "STAT", "OK").rstrip("\r\n")

        def close(self) -> None:
            pass

    svc = BoardService(OnlyForeignAcks(), clock=lambda: 0, timeout_s=0.05)
    with pytest.raises(ProtocolError):
        svc.send("CFG", "GET", "tx.period_ms")


def test_send_keeps_pumping_until_the_response_arrives():
    """🔴 한 번만 pump 하면 실기기에서 거의 항상 실패한다.

    `$CFG,LIST` 응답은 7 KB 라 115200 baud 에서 600 ms 넘게 걸린다.
    스텁은 즉시 답하므로 이 결함은 `--port COM7` 로 바꾸는 순간에만
    드러난다 — 시험이 그걸 흉내내야 한다.
    """

    class SlowTransport:
        """세 번째 read 에서야 응답을 내놓는 트랜스포트."""

        def __init__(self):
            self.reads = 0
            self._pending = None

        def write(self, data: str) -> None:
            self._pending = build_command("SACK", "CFG", "OK").rstrip("\r\n")

        def read_lines(self):
            self.reads += 1
            if self.reads >= 3 and self._pending:
                yield self._pending
                self._pending = None

        def close(self) -> None:
            pass

    tr = SlowTransport()
    svc = BoardService(tr, clock=lambda: 0, timeout_s=1.0)
    ack = svc.send("CFG", "GET", "tx.period_ms")
    assert ack.args == ("CFG", "OK")
    assert tr.reads >= 3                       # 한 번으로 끝내지 않았다


def test_catalog_collection_does_not_swallow_telemetry(rig):
    """🔴 $CFG,LIST 를 받는 동안 흘러온 텔레메트리가 사라지면 안 된다.

    타입을 안 보고 전부 _catalog 로 보내면 수집에 구멍이 뚫리는데,
    parse_catalog 가 모르는 타입을 무시하므로 아무도 눈치채지 못한다.

    ⚠️ **시계를 진행시켜야 실제로 끼어들기가 생긴다.** fetch_schema 안의
    pump 가 tick 을 부를 때 방출 주기가 지나 있어야 카탈로그 줄과 텔레메트리가
    같은 pump 에서 섞인다. 시계를 안 옮기면 끼어드는 텔레메트리가 애초에
    없어서, 이 시험은 수정을 되돌려도 통과한다 — 아무것도 검증하지 않는다.
    """
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()                                     # 텔레메트리가 먼저 쌓인다
    before = len(svc.records)
    assert before > 0

    clock.advance(100)                             # 카탈로그와 겹치도록 주기를 넘긴다
    svc.fetch_schema()
    assert len(svc.records) > before               # 끼어든 텔레메트리를 잃지 않았다


def test_catalog_is_still_complete_when_telemetry_interleaves(rig):
    """끼어들기가 있어도 카탈로그 자체는 온전히 조립된다."""
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    clock.advance(100)
    schema = svc.fetch_schema()
    assert "pwr.5v" in schema.items
    assert len(schema.items) > 40


def test_send_surfaces_error_reason(rig):
    svc, _sim, _clock = rig
    ack = svc.send("CFG", "GET", "nope")
    assert ack.args == ("CFG", "ERR", "UNKNOWN_KEY")


def test_send_collects_json_payload(rig):
    svc, _sim, _clock = rig
    svc.send("CFG", "GET", "tx.period_ms")
    assert svc.last_payload["cur"] == 100


def test_fetch_schema_builds_from_catalog(rig):
    svc, _sim, _clock = rig
    schema = svc.fetch_schema()
    assert "pwr.5v" in schema.items
    # 🔴 5V 는 이제 끌 수 있다(사용자 확정 2026-08-14). 카탈로그가 실어
    #    오는 것은 사유(note)이고, 화면이 그것을 그대로 띄운다.
    assert "팬" in schema.items["pwr.5v"].note


def test_fetch_stat_returns_the_stat_payload(rig):
    """🔴 규격 §7.4 — `din` 은 상태가 바뀔 때만 오는 레코드(§7.6)와 달리
    `$STAT` 응답 안의 실측이다. `fetch_stat()` 은 그 응답을 그대로
    돌려줘야 GUI 가 연결 직후 지금 상태를 세울 수 있다(사용자 확정
    2026-08-18 — "연결이 끊기면... 바로 읽을 수 있으니까 괜찮아")."""
    svc, _sim, _clock = rig
    stat = svc.fetch_stat()
    assert stat.get("type") == "stat"
    assert stat.get("mode") in ("CONFIG", "RUN")
    din = stat.get("din")
    assert isinstance(din, list) and len(din) == 3
    assert {d["connector_id"] for d in din} == {18, 19, 20}
    # 스텁은 전부 꺼짐(raw HIGH = 신호 없음)으로 답한다.
    assert all(d["state"] == 0 for d in din)


def test_heartbeat_puts_board_in_config_mode(rig):
    svc, sim, _clock = rig
    svc.heartbeat()
    assert sim.mode == Mode.CONFIG


def test_pump_collects_telemetry_records(rig):
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    assert any(r["type"] == "ain" for r in svc.records)


def test_pump_tracks_seq_gaps(rig):
    """전송 중 유실이 생기면 서비스가 세어야 한다."""
    svc, _sim, _clock = rig
    svc.seq_tracker.observe(10)
    svc.seq_tracker.observe(14)
    assert svc.seq_tracker.missing_total == 3


def test_mode_follows_heartbeat_timeout(rig):
    svc, _sim, clock = rig
    svc.heartbeat()
    clock.advance(HB_TIMEOUT_MS + 1)
    svc.pump()
    assert svc.mode == Mode.RUN


def test_set_config_helper_returns_reason_on_reject(rig):
    svc, _sim, _clock = rig
    svc.heartbeat()
    # 🔴 인터록 항목을 예시로 쓰지 않는다 — 5V 를 끌 수 있게 하면서 제품
    #    표에 인터록이 하나도 남지 않았다. 거부 사유가 그대로 올라오는지가
    #    요점이므로 범위 밖 값으로 확인한다.
    ok, reason = svc.set_config("tx.period_ms", "999999")
    assert ok is False
    assert reason == "RANGE"


def test_set_config_helper_succeeds(rig):
    svc, sim, _clock = rig
    svc.heartbeat()
    ok, reason = svc.set_config("tx.period_ms", "250")
    assert ok is True and reason == ""
    assert sim.store.get("tx.period_ms") == 250


def test_malformed_telemetry_is_counted_not_raised(rig):
    """깨진 줄이 서비스를 죽이면 안 된다. 세고 넘어간다."""
    svc, _sim, _clock = rig
    svc._ingest('{"schema_ver":3,"seq":')
    assert svc.corrupt_total == 1
    assert list(svc.records) == []


# ------------------------------------------------------- 원문 줄 · 통계 (스트림)

def _ain_line(seq: int) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":0,"type":"ain","connector_id":3,'
        f'"raw":0,"ma":0,"value":0,"status":0}}'
    )


def test_raw_lines_buffer_is_bounded_to_the_most_recent():
    """🔴 무한히 자라면 안 된다 — 오래된 줄은 밀려나고 최근 것만 남는다.

    벤치에 하루 켜 두는 사용을 상정하므로 상한이 필수다(CLAUDE.md 의
    "정형화된 부채" 항목, HANDOFF.md §7.4).
    """
    svc = BoardService(LoopbackTransport(FakeBoard()),
                       clock=lambda: 0, raw_buffer_maxlen=3)
    for seq in range(5):
        svc._ingest(_ain_line(seq))
    assert len(svc.raw_lines) == 3
    assert '"seq":4' in svc.raw_lines[-1]     # 가장 최근이 마지막에
    assert '"seq":2' in svc.raw_lines[0]      # 0, 1 은 밀려났다


def test_records_buffer_is_bounded_too():
    """🔴 `self.records` 도 원문 버퍼와 뿌리가 같은 부채였다 — 함께 고친다."""
    svc = BoardService(LoopbackTransport(FakeBoard()),
                       clock=lambda: 0, raw_buffer_maxlen=3)
    for seq in range(5):
        svc._ingest(_ain_line(seq))
    assert len(svc.records) == 3
    assert svc.records[-1]["seq"] == 4


def test_take_records_returns_only_whats_new_since_last_call(rig):
    """🔴 `self.records` 가 bounded 가 되면 워커의 슬라이스 커서
    (`records[seen:]`)가 못 쓰게 된다(worker_loop.py 머리말). `take_records`
    가 그 대안이다."""
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    first = svc.take_records()
    assert len(first) > 0
    assert svc.take_records() == []            # 두 번 넘어오지 않는다

    clock.advance(100)
    svc.pump()
    second = svc.take_records()
    assert len(second) > 0


def test_line_stats_count_lines_bytes_types_and_last_seen(rig):
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    assert svc.line_total > 0
    assert svc.byte_total > 0
    assert svc.type_counts.get("ain", 0) > 0
    assert svc.last_line_at == clock.now_ms


def test_corrupt_lines_are_recorded_with_a_corrupt_type():
    """줄 자체가 깨졌어도 원문·통계에는 남아야 한다 — 손상을 눈으로 봐야 한다."""
    svc = BoardService(LoopbackTransport(FakeBoard()),
                       clock=lambda: 0)
    svc._ingest('{"schema_ver":3,"seq":')
    assert svc.type_counts.get("corrupt") == 1
    assert svc.raw_lines[-1] == '{"schema_ver":3,"seq":'


def test_take_raw_lines_returns_only_whats_new_since_last_call(rig):
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    first = svc.take_raw_lines()
    assert len(first) > 0
    assert svc.take_raw_lines() == []


# ---- 링크 속도 변경 (규격 §4.2) ---------------------------------------------
#
# 🔴 여기서 보는 것은 "속도가 바뀌는가" 가 아니라 **"안 될 때 돌아오는가"**
#    다. 921600 보다 높은 속도는 아무도 시험한 적이 없고(규격 §4.2.5),
#    F103(BMP) 브리지가 견디는지 모른다 — 실패가 정상 경로에 있다.


class FakeMonotonic:
    """`sleep()` 을 부를 때만 흐르는 벽시계.

    🔴 진짜 `time.sleep` 을 쓰면 실패 경로 하나가 10초를 잡아먹는다(보드의
       확인 시한). 그렇다고 시계를 멈춰 두면 `send()` 의 타임아웃 루프가
       영영 돌므로, **잔 만큼 흐르게** 한다 — 실제 동작과 같은 인과다.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture
def baud_rig():
    clock = FakeClock()
    mono = FakeMonotonic()
    sim = FakeBoard()
    svc = BoardService(LoopbackTransport(sim), clock=clock,
                       sleep=mono.sleep, monotonic=mono)
    return svc, sim, clock, mono


def test_loopback_drops_everything_when_the_two_sides_disagree(baud_rig):
    """트랜스포트가 속도 불일치를 실제로 흉내 내는지부터 확인한다.

    🔴 이것이 성립하지 않으면 아래 실패 경로 시험들이 **전부 거짓 통과**한다
       — 링크가 끊긴 적이 없으니 되돌아올 일도 없다.
    """
    svc, sim, _clock, _mono = baud_rig
    svc.transport.reopen(1500000)          # 호스트만 새 속도로
    assert sim.link_baud == 921600         # 보드는 옛 속도 그대로
    with pytest.raises(ProtocolError):
        svc.send("ID")


def test_baud_change_confirms_and_the_board_keeps_it(baud_rig):
    svc, sim, clock, _mono = baud_rig
    svc.heartbeat()

    result = svc.change_baud(1500000)

    assert result.ok
    assert result.stage == "confirmed"
    assert result.baud == 1500000
    assert svc.transport.baud == 1500000
    assert sim.link_baud == 1500000

    # 시한이 한참 지나도 되돌아가지 않는다 — 확정됐기 때문이다.
    clock.advance(60000)
    svc.pump()
    assert sim.link_baud == 1500000
    assert svc.send("ID").args == ("ID", "OK")


def test_the_sack_goes_out_before_the_board_switches(baud_rig):
    """🔴 규격 §4.2.2 규칙 1 — 순서를 뒤집으면 응답을 못 읽는다.

    이 통로는 **속도가 다른 줄을 버린다**(LoopbackTransport). 그래서 호스트가
    아직 921600 인 채로 `$SACK,CFG,OK` 를 읽었다는 사실 자체가 "그 응답이 옛
    속도로 나갔다" 는 증거다. 보드가 응답보다 먼저 속도를 바꿨다면 그 줄은
    1500000 으로 태그돼 여기서 사라지고, `send()` 는 응답 없음으로 죽는다.
    """
    svc, sim, _clock, _mono = baud_rig
    svc.heartbeat()

    ack = svc.send("CFG", "SET", "link.baud", "1500000")

    assert svc.transport.baud == 921600, "호스트는 아직 옛 속도다"
    assert ack.args == ("CFG", "OK"), "그런데도 응답을 읽었다 — 옛 속도로 나갔다"
    assert sim.link_baud == 1500000, "보드는 응답을 마친 뒤 속도를 바꿨다"


def test_nothing_is_saved_before_the_change_is_confirmed(baud_rig):
    """🔴 규격 §4.2.2 규칙 5 — 이 규칙이 없으면 못 붙는 보드가 만들어진다.

    그 실패는 굽기로만 풀린다(CLAUDE.md §4).
    """
    svc, sim, _clock, _mono = baud_rig
    svc.heartbeat()
    svc.send("CFG", "SET", "link.baud", "1500000")
    # 보드는 이미 새 속도다 — 호스트도 따라가야 말이 통한다.
    svc.transport.reopen(1500000)
    svc.heartbeat()

    ack = svc.send("CFG", "SAVE")
    assert ack.args == ("CFG", "ERR", "BUSY")
    assert sim.store.saved_value("link.baud") is None

    # 확인한 뒤에는 저장된다.
    assert svc.send("BAUD", "CONFIRM", "1500000").args == ("BAUD", "OK")
    assert svc.send("CFG", "SAVE").args == ("CFG", "OK")
    assert sim.store.saved_value("link.baud") == 1500000


# ── Cloud 형식 수신 (계획 2 Task 4 — HANDOFF_0831 결정 2) ────────────────


class _LinesTransport:
    """시험이 밀어 넣은 줄을 그대로 흘리는 트랜스포트."""

    baud = 921600

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, data: str) -> None:
        pass

    def read_lines(self):
        out, self.lines = self.lines, []
        yield from out

    def reopen(self, baud: int) -> None:
        pass

    def close(self) -> None:
        pass


def _cloud_svc():
    from host.core.typemap import TypeMap
    from host.tests.test_typemap import _field_wiring

    svc = BoardService(_LinesTransport(), clock=lambda: 0)
    svc.set_typemap(TypeMap.from_schema(_field_wiring()))
    return svc


def test_cloud_vectors_land_as_internal_records():
    from host.tests import cloud_vectors as V

    svc = _cloud_svc()
    svc.transport.lines = list(V.WITH_SEQ)
    svc.pump()
    assert svc.corrupt_total == 0
    recs = svc.take_records()
    ain = [r for r in recs if r["type"] == "ain"]
    assert {r["connector_id"] for r in ain} == {3, 4, 5}
    assert any(r["type"] == "i2c" and r["quantity"] == "temp" for r in recs)
    assert any(r["type"] == "din" and r["state"] == 1 for r in recs)
    assert svc.seq_tracker.received_total == len(V.WITH_SEQ)


def test_seqless_records_are_kept_but_not_tracked():
    """tx.seq 를 끈 보드 — 레코드는 살고 유실 집계만 불참한다."""
    from host.tests import cloud_vectors as V

    svc = _cloud_svc()
    svc.transport.lines = [V.FLOW1]            # 캡쳐 원문 — seq 없음
    svc.pump()
    recs = svc.take_records()
    assert len(recs) == 1 and recs[0]["type"] == "ain"
    assert svc.seq_tracker.received_total == 0


def test_gnssraw_line_is_not_counted_corrupt():
    """$GNSSRAW 는 §3 체크섬이 없는 진단 줄이다(규격 §7.7 개정) —
    parse_line 에 넣으면 전부 corrupt 로 오염된다."""
    svc = _cloud_svc()
    svc.transport.lines = [
        "$GNSSRAW,$GNRMC,235959.999,A,4807.038,N,01131.000,E,0.0,0.0,180826,,,A*73",
    ]
    svc.pump()
    assert svc.corrupt_total == 0
    assert svc.type_counts.get("gnssraw") == 1


def test_set_config_refreshes_typemap():
    """이름을 바꾸는 SET 은 GUI 자신이 보내므로 역매핑을 즉시 갱신할 수
    있다(HANDOFF_0831 결정 2 보완)."""
    from host.tests import cloud_vectors as V

    svc, sim, _clock = (lambda c=FakeClock(): (
        BoardService(LoopbackTransport(FakeBoard()), clock=c), None, c))()
    svc.heartbeat()
    svc.fetch_schema()                          # 카탈로그 → 역매핑 구축
    ok, _ = svc.set_config("ain0.cloud", "flow1")
    assert ok
    svc.transport._pending.append(
        (svc.transport.baud, V.FLOW1))
    svc.pump()
    recs = [r for r in svc.take_records() if r.get("cloud_type") == "flow1"]
    assert recs and recs[0]["type"] == "ain" and recs[0]["connector_id"] == 3


def test_the_board_reverts_on_its_own_when_no_one_confirms(baud_rig):
    """🔴 사람이 아무것도 안 해도 링크가 살아난다 — 이 절차의 존재 이유."""
    svc, sim, clock, _mono = baud_rig
    svc.heartbeat()
    svc.send("CFG", "SET", "link.baud", "2000000")
    svc.pump()
    assert sim.link_baud == 2000000

    clock.advance(10000)
    svc.pump()
    assert sim.link_baud == 921600
    assert sim.store.get("link.baud") == 921600     # 설정표도 따라 돌아온다


def test_host_falls_back_when_the_new_speed_does_not_work(baud_rig, monkeypatch):
    """새 속도로 말이 안 될 때 호스트가 옛 속도로 돌아오는가.

    🔴 실기기에서 이렇게 될 수 있는 이유가 실재한다 — F103(BMP) 브리지가
       그 속도를 통과시키는지 아무도 확인한 적이 없다(규격 §4.2.5).
       여기서는 "보드는 바꿨는데 호스트 쪽 포트가 새 속도로 안 열린다" 를
       흉내 낸다.
    """
    svc, sim, clock, mono = baud_rig
    svc.heartbeat()

    real_reopen = svc.transport.reopen

    def reopen_that_never_reaches_the_new_speed(baud: int) -> None:
        # 옛 속도로 돌아가는 것만 실제로 해 준다. 새 속도로는 안 열린다.
        if baud == 921600:
            real_reopen(baud)

    monkeypatch.setattr(svc.transport, "reopen",
                        reopen_that_never_reaches_the_new_speed)

    # 보드 쪽 시계도 함께 흐르게 한다 — 안 그러면 보드가 시한을 못 넘긴다.
    real_sleep = mono.sleep

    def sleep_both(seconds: float) -> None:
        real_sleep(seconds)
        clock.advance(int(seconds * 1000))

    svc._sleep = sleep_both

    result = svc.change_baud(2000000)

    assert not result.ok
    assert result.stage == "confirm"
    assert result.baud == 921600
    assert svc.transport.baud == 921600
    assert result.recovered, "옛 속도로 돌아와 보드와 다시 말이 돼야 한다"
    assert sim.link_baud == 921600, "보드도 스스로 되돌아왔다"


def test_a_rejected_set_leaves_the_port_alone(baud_rig):
    """🔴 보드가 거부했으면 포트를 다시 열지 않는다.

    포트를 닫았다 여는 순간이 이 보드가 멈추는 순간이다(CLAUDE.md §4).
    아무것도 안 바뀌었는데 그 위험을 감수할 이유가 없다.
    """
    svc, sim, _clock, _mono = baud_rig
    # RUN 모드다 — $CFG,SET 은 ERR,MODE 로 거부된다 (규격 §6.2).
    result = svc.change_baud(1500000)

    assert not result.ok
    assert result.stage == "set"
    assert result.reason == "MODE"
    assert svc.transport.baud == 921600
    assert sim.link_baud == 921600
    assert result.recovered


def test_an_unreachable_baud_is_refused_before_anything_is_sent(baud_rig):
    svc, sim, _clock, _mono = baud_rig
    svc.heartbeat()

    result = svc.change_baud(250000)

    assert not result.ok
    assert result.reason == "RANGE"
    assert svc.transport.baud == 921600
    assert sim.link_baud == 921600


def test_changing_to_the_same_baud_does_nothing(baud_rig):
    svc, _sim, _clock, _mono = baud_rig
    result = svc.change_baud(921600)
    assert result.ok and result.stage == "same"


def test_confirming_a_different_value_is_refused(baud_rig):
    """🔴 규격 §4.2.3 — 값이 되돌아오는 것 자체가 증거다."""
    svc, sim, _clock, _mono = baud_rig
    svc.heartbeat()
    svc.send("CFG", "SET", "link.baud", "460800")
    svc.pump()
    svc.transport.reopen(460800)

    assert svc.send("BAUD", "CONFIRM", "921600").args == ("BAUD", "ERR", "RANGE")
    assert sim.link_baud == 460800, "거부됐어도 시한은 계속 흐른다"


def test_confirm_works_after_the_board_has_dropped_to_run(baud_rig):
    """🔴 규격 §4.2.3 — CONFIG 전용이 아니다.

    포트를 여는 데 3초가 넘게 걸리면 보드는 RUN 으로 떨어진다. 거기서
    확인을 거부하면 **그 거부가 곧 우리가 막으려던 되돌림을 부른다.**
    """
    svc, sim, clock, _mono = baud_rig
    svc.heartbeat()
    svc.send("CFG", "SET", "link.baud", "460800")
    svc.pump()
    svc.transport.reopen(460800)

    clock.advance(HB_TIMEOUT_MS + 500)
    svc.pump()
    assert sim.mode == Mode.RUN

    assert svc.send("BAUD", "CONFIRM", "460800").args == ("BAUD", "OK")


def test_confirming_with_nothing_pending_is_refused(baud_rig):
    svc, _sim, _clock, _mono = baud_rig
    assert svc.send("BAUD", "CONFIRM", "921600").args == ("BAUD", "ERR", "MODE")


def test_stat_reports_the_link_state(baud_rig):
    """규격 §7.4 — 호스트는 `baud` 와 `confirmed` 가 같은지로 "지금 저장해도
    되는가" 를 판단한다."""
    svc, _sim, _clock, _mono = baud_rig
    svc.heartbeat()

    stat = svc.fetch_stat()
    assert stat["link"] == {"baud": 921600, "confirmed": 921600,
                            "pending": None, "remaining_ms": None,
                            "applied": 0, "confirmed_count": 0, "reverted": 0}

    svc.send("CFG", "SET", "link.baud", "1500000")
    svc.pump()
    svc.transport.reopen(1500000)
    stat = svc.fetch_stat()
    assert stat["link"]["baud"] == 1500000
    assert stat["link"]["confirmed"] == 921600
    assert stat["link"]["pending"] == 1500000
    assert stat["link"]["applied"] == 1
