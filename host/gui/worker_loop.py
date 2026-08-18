"""워커 스레드가 반복 호출하는 한 스텝.

Qt 를 import 하지 않고 스레드도 만들지 않는다. `step(now)` 를 부르면
"이번에 무슨 일이 있었나" 를 돌려준다. Qt 스레드는 이걸 반복 호출하는
껍데기일 뿐이라 여기에 로직을 모아 두면 디스플레이 없이 시험된다.
"""

import time
from dataclasses import dataclass, field

from host.gui.command_queue import CommandQueue, Result


@dataclass
class StepResult:
    #: 이번 스텝에 걷은 텔레메트리. 이미 넘긴 것은 다시 나오지 않는다.
    records: list = field(default_factory=list)
    #: GUI 가 화면에 반영할 명령 결과.
    results: list[Result] = field(default_factory=list)
    heartbeat_sent: bool = False
    mode: str = "RUN"
    #: 제어 모드 (규격 §6.4). 접속 모드와 **다른 축**이다.
    ctl_mode: str = "ACTIVE"
    #: 하트비트 전송이 실패한 경우.
    heartbeat_error: str | None = None
    #: 텔레메트리 수신이 실패한 경우.
    #: 🔴 하트비트 실패와 따로 담는다. 하나로 합치면 계속 실패하는 하트비트가
    #: 펌프 실패를 영원히 가린다 — 둘은 다른 고장이다.
    pump_error: str | None = None
    #: 수집 커서가 끊긴 경우(서비스의 records 가 교체·절단됨).
    records_discontinuity: bool = False
    #: 이번 스텝에 걷은 원문 줄 (stream 화면용). 서비스가 `take_raw_lines()`
    #: 를 제공하지 않으면(구형 스텁 등) 빈 목록이다 — `records` 와 달리
    #: 커서 대체 경로가 없다. 원문 스트림이 없던 시절부터 있던 서비스
    #: 스텁까지 조용히 지원해야 하므로, 이 필드만은 없으면 없는 대로 둔다.
    raw_lines: list[str] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        """둘 중 아무거나 하나. 화면 요약용."""
        return self.heartbeat_error or self.pump_error


class WorkerLoop:
    def __init__(self, service, queue: CommandQueue, *,
                 hb_interval_s: float = 1.0,
                 max_commands_per_step: int = 4,
                 monotonic=time.monotonic):
        self.service = service
        self.queue = queue
        self.hb_interval_s = hb_interval_s
        #: 🔴 한 스텝에 보낼 명령 수 상한.
        #: send() 는 응답까지 최대 2초 기다린다. 큐에 20개가 쌓여 있으면
        #: 40초 동안 텔레메트리를 한 줄도 못 걷는다. 수집이 굶으면 안 된다.
        self.max_commands_per_step = max_commands_per_step
        #: 시험이 시간을 통제할 수 있게 주입받는다.
        self._monotonic = monotonic

        self._last_hb: float | None = None
        self._records_seen = 0
        self._records_id: int | None = None

    @property
    def dispatch_budget_s(self) -> float:
        """한 스텝이 명령 전송에 쓸 수 있는 최대 **실제 경과 시간**.

        🔴 개수 상한만으로는 부족하다. 상한 4 에 타임아웃 2초면 한 스텝이
        8초 걸릴 수 있는데, 하트비트는 스텝 맨 위에서만 나간다. 보드는
        3초면 RUN 으로 떨어지고 그때부터 설정 변경이 전부 거부된다.

        더 나쁜 것은 **이게 링크가 나쁠 때 정확히 터진다**는 점이다.
        여러 명령이 동시에 타임아웃되는 상황이 곧 하트비트가 가장
        필요한 상황이다.

        하트비트 주기의 절반으로 잡아, 예산을 다 써도 다음 하트비트가
        늦지 않게 한다.
        """
        return self.hb_interval_s * 0.5

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
                out.heartbeat_error = str(exc)

        # 2) 큐에 쌓인 명령을 **개수와 시간** 양쪽 상한까지만 보낸다.
        #    상한을 큐에 넘겨 애초에 그만큼만 꺼낸다. 꺼낸 뒤 되돌리면
        #    되돌리는 사이에 들어온 새 값을 오래된 값이 덮어쓸 수 있다.
        #    🔴 시간 예산도 **꺼내기 전에** 본다. 꺼낸 뒤 break 하면 이미
        #    큐에서 빠져나온 명령이 그대로 사라진다 — in_flight 로 표시됐는데
        #    아무도 보내지 않아 결과도 안 오고 재시도도 안 된다.
        started = self._monotonic()
        sent = 0
        while sent < self.max_commands_per_step:
            if self._monotonic() - started >= self.dispatch_budget_s:
                # 예산 소진. 남은 것은 큐에 그대로 두고 다음 스텝으로 넘긴다 —
                # 여기서 더 붙들면 하트비트가 늦어 보드가 RUN 으로 떨어진다.
                break
            batch = self.queue.drain_pending(limit=1)
            if not batch:
                break
            self._dispatch(batch[0])
            sent += 1

        out.results = self.queue.take_results()

        # 3) 텔레메트리를 걷는다.
        try:
            self.service.pump()
        except Exception as exc:                          # noqa: BLE001
            out.pump_error = str(exc)

        out.records, out.records_discontinuity = self._collect_records()

        take_raw = getattr(self.service, "take_raw_lines", None)
        out.raw_lines = list(take_raw()) if callable(take_raw) else []

        out.mode = getattr(self.service, "mode", "RUN")
        out.ctl_mode = getattr(self.service, "ctl_mode", "ACTIVE")
        return out

    def _collect_records(self) -> tuple[list, bool]:
        """서비스가 모아둔 텔레메트리를 가져온다.

        🔴 서비스가 `take_records()` 를 제공하면 그걸 쓴다.

        길이 커서(`records[seen:]`)는 **append 만 되는 리스트에서만** 맞다.
        `records` 가 `deque(maxlen=N)` 으로 바뀌면 — 이미 예정된 변경이다 —
        가득 찬 뒤 `len()` 이 N 에서 멈추므로 슬라이스가 영원히 빈 목록을
        낸다. 텔레메트리가 **예외도 오류 표시도 없이 조용히 끊긴다.**

        그래서 넘겨주는 방식(`take_records`)을 우선한다. 없을 때만 커서를
        쓰고, 객체가 바뀌거나 길이가 줄면 불연속으로 표시한 뒤 다시 잡는다.
        """
        take = getattr(self.service, "take_records", None)
        if callable(take):
            return list(take()), False

        records = getattr(self.service, "records", [])
        if self._records_id is None:
            self._records_id = id(records)

        replaced = id(records) != self._records_id
        shrunk = len(records) < self._records_seen
        if replaced or shrunk:
            self._records_id = id(records)
            self._records_seen = len(records)
            return list(records), True

        out = list(records[self._records_seen :])
        self._records_seen = len(records)
        return out, False

    def _dispatch(self, cmd) -> None:
        """명령 하나를 보내고 결과를 큐에 되돌린다.

        🔴 거부와 통신 실패를 구분한다. 보드가 INTERLOCK 으로 거부한 것과
        보드에 닿지 못한 것은 사용자에게 다른 사실이다.
        """
        try:
            ack = self.service.send(cmd.verb, *cmd.args)
        except Exception as exc:                          # noqa: BLE001
            self.queue.complete(cmd.send_id, ok=False, error=str(exc))
            return

        # 🔴 **성공을 적극적으로 확인한다.** 실패를 찾아내는 방식이면
        # 모르는 형태가 성공으로 떨어진다.
        #
        # 이전 판은 "OK 가 아니고 ERR 가 들어 있으면 거부" 였다. 그러면
        # 펌웨어 버그로 제3의 상태 문자열이 오거나, 약한 8비트 XOR 을
        # 우연히 통과한 손상 프레임이 오면 **거부를 성공으로 보고한다.**
        # 설정 쓰기의 조용한 거짓 성공은 이 시스템 최악의 실패 방식이다.
        if not ack.args or ack.args[-1] != "OK":
            reason = ack.args[-1] if ack.args else "MALFORMED"
            self.queue.complete(cmd.send_id, ok=False, reason=reason)
            return

        self.queue.complete(
            cmd.send_id, ok=True,
            payload=getattr(self.service, "last_payload", None),
        )
