"""워커 스레드가 반복 호출하는 한 스텝.

Qt 를 import 하지 않고 스레드도 만들지 않는다. `step(now)` 를 부르면
"이번에 무슨 일이 있었나" 를 돌려준다. Qt 스레드는 이걸 반복 호출하는
껍데기일 뿐이라 여기에 로직을 모아 두면 디스플레이 없이 시험된다.
"""

from dataclasses import dataclass, field

from host.core.errors import ProtocolError
from host.gui.command_queue import CommandQueue, Result


@dataclass
class StepResult:
    #: 이번 스텝에 걷은 텔레메트리. 이미 넘긴 것은 다시 나오지 않는다.
    records: list = field(default_factory=list)
    #: GUI 가 화면에 반영할 명령 결과.
    results: list[Result] = field(default_factory=list)
    heartbeat_sent: bool = False
    mode: str = "RUN"
    #: 이번 스텝 자체가 실패한 경우 (개별 명령 실패는 results 안에 있다).
    error: str | None = None


class WorkerLoop:
    def __init__(self, service, queue: CommandQueue, *,
                 hb_interval_s: float = 1.0,
                 max_commands_per_step: int = 4):
        self.service = service
        self.queue = queue
        self.hb_interval_s = hb_interval_s
        #: 🔴 한 스텝에 보낼 명령 수 상한.
        #: send() 는 응답까지 최대 2초 기다린다. 큐에 20개가 쌓여 있으면
        #: 40초 동안 텔레메트리를 한 줄도 못 걷는다. 수집이 굶으면 안 된다.
        self.max_commands_per_step = max_commands_per_step

        self._last_hb: float | None = None
        self._records_seen = 0

    def step(self, now: float) -> StepResult:
        out = StepResult()

        # 1) 하트비트. 멈추면 보드가 3초 뒤 RUN 으로 떨어지고 그때부터
        #    설정 변경이 전부 ERR,MODE 로 거부된다.
        if self._last_hb is None or now - self._last_hb >= self.hb_interval_s:
            try:
                self.service.heartbeat()
                self._last_hb = now
                out.heartbeat_sent = True
            except Exception as exc:                      # noqa: BLE001
                out.error = str(exc)

        # 2) 큐에 쌓인 명령을 상한까지만 보낸다.
        #    상한을 큐에 넘겨 **애초에 그만큼만 꺼낸다.** 꺼낸 뒤 되돌리면
        #    되돌리는 사이에 들어온 새 값을 오래된 값이 덮어쓸 수 있다.
        for cmd in self.queue.drain_pending(limit=self.max_commands_per_step):
            self._dispatch(cmd)

        out.results = self.queue.take_results()

        # 3) 텔레메트리를 걷는다.
        try:
            self.service.pump()
        except Exception as exc:                          # noqa: BLE001
            out.error = out.error or str(exc)

        records = getattr(self.service, "records", [])
        out.records = list(records[self._records_seen :])
        self._records_seen = len(records)

        out.mode = getattr(self.service, "mode", "RUN")
        return out

    def _dispatch(self, cmd) -> None:
        """명령 하나를 보내고 결과를 큐에 되돌린다.

        🔴 거부와 통신 실패를 구분한다. 보드가 INTERLOCK 으로 거부한 것과
        보드에 닿지 못한 것은 사용자에게 다른 사실이다.
        """
        try:
            ack = self.service.send(cmd.verb, *cmd.args)
        except ProtocolError as exc:
            self.queue.complete(cmd.tag, ok=False, error=str(exc))
            return
        except Exception as exc:                          # noqa: BLE001
            self.queue.complete(cmd.tag, ok=False, error=str(exc))
            return

        if ack.args and ack.args[-1] != "OK" and "ERR" in ack.args:
            reason = ack.args[-1]
            self.queue.complete(cmd.tag, ok=False, reason=reason)
            return

        self.queue.complete(
            cmd.tag, ok=True, payload=getattr(self.service, "last_payload", None)
        )
