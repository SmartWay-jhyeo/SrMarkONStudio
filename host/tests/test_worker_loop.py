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


#: 🔴 `_rig()` 헬퍼가 있었으나 지웠다.
#:
#:    `CommandQueue()` 를 두 번 만들어 시험이 명령을 넣는 큐와 워커가
#:    꺼내 쓰는 큐가 서로 다른 결함이 있었는데, **아무 시험도 그것을 쓰지
#:    않고 있었다.** 고치려다 호출자가 하나도 없다는 것을 알았다.
#:
#:    죽은 헬퍼는 남겨 두면 언젠가 누가 쓴다. 그때 그 결함이 되살아난다.


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


def test_unrecognized_ack_is_a_failure_not_a_success():
    """🔴 성공을 적극 확인한다. 실패를 찾는 방식이면 모르는 형태가 새어든다.

    펌웨어 버그로 제3의 상태 문자열이 오거나 약한 XOR 을 우연히 통과한
    손상 프레임이 오면, 거부를 성공으로 보고하게 된다. 설정 쓰기의 조용한
    거짓 성공은 이 시스템 최악의 실패 방식이다.
    """
    from host.core.framing import Command

    svc, q = FakeService(), CommandQueue()

    class Weird(FakeService):
        def send(self, verb, *args):
            return Command(verb="SACK", args=(verb, "MAYBE"))

    w = WorkerLoop(Weird(), q)
    q.submit("CFG", "SET", "tx.period_ms", "250")
    r = w.step(0.0)
    assert r.results[0].ok is False


def test_empty_ack_args_is_a_failure():
    from host.core.framing import Command

    class Empty(FakeService):
        def send(self, verb, *args):
            return Command(verb="SACK", args=())

    q = CommandQueue()
    w = WorkerLoop(Empty(), q)
    q.submit("STAT")
    r = w.step(0.0)
    assert r.results[0].ok is False
    assert r.results[0].reason == "MALFORMED"


def test_dispatch_stops_when_the_time_budget_runs_out():
    """🔴 개수 상한만으로는 하트비트를 못 지킨다.

    상한 4 에 타임아웃 2초면 한 스텝이 8초 걸릴 수 있는데 보드는 3초면
    RUN 으로 떨어진다. 더 나쁜 것은 이게 링크가 나쁠 때 터진다는 점이다 —
    하트비트가 가장 필요한 순간이 곧 그 순간이다.
    """
    clock = [0.0]

    class Slow(FakeService):
        def send(self, verb, *args):
            clock[0] += 0.4              # 한 번에 0.4초씩 먹는다
            return super().send(verb, *args)

    svc, q = Slow(), CommandQueue()
    w = WorkerLoop(svc, q, hb_interval_s=1.0, max_commands_per_step=10,
                   monotonic=lambda: clock[0])
    for i in range(10):
        q.submit("STAT", tag=f"t{i}")

    w.step(0.0)
    # 예산 0.5초 → 0.4초짜리 두 번이면 0.8초로 초과. 두 개만 나간다.
    assert len(svc.sent) == 2
    assert len(q.pending_tags) == 8      # 나머지는 큐에 남는다


def test_heartbeat_and_pump_failures_are_reported_separately():
    """🔴 하나로 합치면 계속 실패하는 하트비트가 펌프 실패를 영원히 가린다."""

    class BothFail(FakeService):
        def heartbeat(self):
            raise RuntimeError("hb 끊김")

        def pump(self):
            raise RuntimeError("pump 끊김")

    w = WorkerLoop(BothFail(), CommandQueue())
    r = w.step(0.0)
    assert r.heartbeat_error == "hb 끊김"
    assert r.pump_error == "pump 끊김"


def test_take_records_is_preferred_when_the_service_offers_it():
    """🔴 길이 커서는 append 전용 리스트에서만 맞다.

    records 가 deque(maxlen=N) 으로 바뀌면 — 예정된 변경이다 — 가득 찬 뒤
    len() 이 멈춰 슬라이스가 영원히 빈 목록을 낸다. 텔레메트리가 예외도
    오류 표시도 없이 조용히 끊긴다.
    """

    class Draining(FakeService):
        def __init__(self):
            super().__init__()
            self._buf = [{"type": "ain", "seq": 1}]

        def take_records(self):
            out, self._buf = self._buf, []
            return out

    w = WorkerLoop(Draining(), CommandQueue())
    assert len(w.step(0.0).records) == 1
    assert w.step(0.1).records == []


def test_replaced_records_container_is_reported_as_discontinuity():
    """객체가 통째로 바뀌면 커서가 의미를 잃는다. 조용히 넘어가지 않는다."""
    svc, q = FakeService(), CommandQueue()
    svc.records = [{"type": "ain", "seq": 1}, {"type": "ain", "seq": 2}]
    w = WorkerLoop(svc, q)
    w.step(0.0)
    svc.records = [{"type": "ain", "seq": 9}]      # 교체
    r = w.step(0.1)
    assert r.records_discontinuity is True
    assert len(r.records) == 1
