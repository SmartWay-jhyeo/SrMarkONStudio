import pytest

from host.core.errors import ProtocolError
from host.core.framing import build_command
from host.service.board_service import BoardService, LoopbackTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import HB_TIMEOUT_MS, DeviceSim, Mode


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
    sim = DeviceSim(default_store())
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
    svc.transport._pending.append(
        build_command("SACK", "STAT", "OK").rstrip("\r\n")
    )
    ack = svc.send("CFG", "SET", "tx.period_ms", "999999")
    assert ack.args == ("CFG", "ERR", "RANGE")     # 내 명령의 응답이다
    assert sim.store.get("tx.period_ms") == 100    # 보드는 실제로 거부했다


def test_send_raises_when_only_foreign_acks_arrive():
    """대응하는 응답이 아예 없으면 남의 것을 돌려주지 않고 예외를 던진다.

    시뮬레이터는 언제나 응답하므로 이 상황을 만들려면 스텁이 필요하다.
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
    시뮬레이터는 즉시 답하므로 이 결함은 `--port COM7` 로 바꾸는 순간에만
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
    assert schema.items["pwr.5v"].readonly is True


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
    ok, reason = svc.set_config("pwr.5v", "false")
    assert ok is False
    assert reason == "INTERLOCK"


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
    assert svc.records == []
