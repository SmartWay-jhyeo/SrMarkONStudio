import pytest

from host.core.errors import ProtocolError
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
