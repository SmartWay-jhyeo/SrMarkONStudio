import pytest

from host.core.errors import ProtocolError
from host.gui.command_queue import CommandQueue
from host.gui.worker_loop import WorkerLoop


class FakeService:
    """BoardService 의 최소 대역. 무엇이 불렸는지 기록한다."""

    def __init__(self):
        self.sent = []
        self.heartbeats = 0
        self.records = []
        self.mode = "RUN"
        self.last_payload = None
        self._reply = ("OK", None)      # (status, reason)
        self._raise = None

    def heartbeat(self):
        self.heartbeats += 1

    def set_reply(self, status, reason=None, payload=None):
        self._reply = (status, reason)
        self.last_payload = payload

    def set_error(self, exc):
        self._raise = exc

    def send(self, verb, *args):
        self.sent.append((verb, *args))
        if self._raise is not None:
            raise self._raise
        from host.core.framing import Command

        status, reason = self._reply
        rest = (status,) if reason is None else (status, reason)
        return Command(verb="SACK", args=(verb, *rest))

    def pump(self):
        pass


def _rig():
    svc = FakeService()
    return svc, CommandQueue(), WorkerLoop(svc, CommandQueue())


def test_heartbeat_is_sent_on_the_first_step():
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q)
    r = w.step(0.0)
    assert r.heartbeat_sent is True
    assert svc.heartbeats == 1


def test_heartbeat_is_not_resent_before_the_interval():
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q, hb_interval_s=1.0)
    w.step(0.0)
    assert w.step(0.5).heartbeat_sent is False
    assert svc.heartbeats == 1


def test_heartbeat_resumes_after_the_interval():
    """🔴 멈추면 보드가 3초 뒤 RUN 으로 떨어지고 설정 변경이 거부된다."""
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q, hb_interval_s=1.0)
    w.step(0.0)
    w.step(1.0)
    assert svc.heartbeats == 2


def test_queued_commands_are_sent():
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q)
    q.submit("CFG", "SET", "tx.period_ms", "250")
    w.step(0.0)
    assert svc.sent == [("CFG", "SET", "tx.period_ms", "250")]


def test_success_comes_back_as_a_result():
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q)
    tag = q.submit("CFG", "SET", "tx.period_ms", "250")
    r = w.step(0.0)
    assert [x.tag for x in r.results] == [tag]
    assert r.results[0].ok is True


def test_rejection_carries_the_board_reason():
    """🔴 사용자는 왜 거부됐는지 알아야 한다."""
    svc, q = FakeService(), CommandQueue()
    svc.set_reply("ERR", "INTERLOCK")
    w = WorkerLoop(svc, q)
    q.submit("CFG", "SET", "pwr.5v", "false")
    r = w.step(0.0)
    assert r.results[0].ok is False
    assert r.results[0].reason == "INTERLOCK"
    assert r.results[0].error is None


def test_transport_failure_is_reported_as_error_not_rejection():
    """🔴 보드가 거부한 것과 보드에 못 닿은 것은 다른 사실이다."""
    svc, q = FakeService(), CommandQueue()
    svc.set_error(ProtocolError("응답 없음"))
    w = WorkerLoop(svc, q)
    q.submit("STAT")
    r = w.step(0.0)
    assert r.results[0].ok is False
    assert r.results[0].reason is None
    assert "응답 없음" in r.results[0].error


def test_one_failing_command_does_not_stop_the_others():
    """한 명령이 실패해도 나머지가 처리돼야 한다."""
    svc, q = FakeService(), CommandQueue()
    svc.set_error(ProtocolError("끊김"))
    w = WorkerLoop(svc, q)
    q.submit("STAT")
    q.submit("ID")
    r = w.step(0.0)
    assert len(r.results) == 2
    assert all(x.ok is False for x in r.results)


def test_commands_per_step_are_capped():
    """🔴 send() 는 최대 2초 기다린다.

    20개가 쌓여 있으면 40초 동안 텔레메트리를 한 줄도 못 걷는다.
    상한을 두어 수집이 굶지 않게 한다.
    """
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q, max_commands_per_step=3)
    for i in range(10):
        q.submit("STAT", tag=f"t{i}")
    r = w.step(0.0)
    assert len(svc.sent) == 3
    assert len(r.results) == 3


def test_remaining_commands_are_sent_on_later_steps():
    """상한에 걸린 나머지가 사라지면 안 된다."""
    svc, q = FakeService(), CommandQueue()
    w = WorkerLoop(svc, q, max_commands_per_step=3)
    for i in range(7):
        q.submit("STAT", tag=f"t{i}")
    total = 0
    for t in range(5):
        total += len(w.step(float(t)).results)
    assert total == 7


def test_telemetry_is_collected_and_handed_over_once():
    svc, q = FakeService(), CommandQueue()
    svc.records = [{"type": "ain", "seq": 1}, {"type": "ain", "seq": 2}]
    w = WorkerLoop(svc, q)
    r = w.step(0.0)
    assert len(r.records) == 2
    assert w.step(0.1).records == []        # 두 번 넘어오지 않는다


def test_mode_is_carried_through():
    svc, q = FakeService(), CommandQueue()
    svc.mode = "CONFIG"
    w = WorkerLoop(svc, q)
    assert w.step(0.0).mode == "CONFIG"
