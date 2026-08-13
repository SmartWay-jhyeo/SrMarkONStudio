"""보드와의 대화를 관리한다 — 명령/응답 매칭, 텔레메트리 수집, 유실 집계.

GUI 와 분리돼 있다. GUI 가 없어도 이 계층만으로 수집·저장이 가능하며,
나중에 Jetson 에서 GUI 없이 서비스만 돌리는 구성이 그대로 가능하다.
"""

import time
from collections.abc import Callable, Iterator
from typing import Protocol

#: 응답을 기다리는 동안 다시 읽기까지의 간격. 짧게 잡아 응답 지연을 줄이되
#: CPU 를 태우지는 않는다.
_POLL_INTERVAL_S = 0.005

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ProtocolError
from host.core.framing import Command, build_command, parse_line
from host.core.records import SeqTracker, is_telemetry, parse_record


class Transport(Protocol):
    """보드와 줄 단위로 주고받는 통로."""

    def write(self, data: str) -> None: ...
    def read_lines(self) -> Iterator[str]: ...
    def close(self) -> None: ...


class LoopbackTransport:
    """DeviceSim 을 직접 물리는 트랜스포트. 시리얼 없이 전 구간을 시험한다."""

    def __init__(self, sim) -> None:
        self.sim = sim
        self._pending: list[str] = []

    def write(self, data: str) -> None:
        for line in data.splitlines():
            if line.strip():
                self._pending.extend(self.sim.feed(line + "\r\n"))

    def read_lines(self) -> Iterator[str]:
        pending, self._pending = self._pending, []
        yield from pending

    def tick(self, now_ms: int) -> None:
        self._pending.extend(self.sim.tick(now_ms))

    def close(self) -> None:
        pass


class SerialTransport:
    """pyserial 기반 트랜스포트."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.1):
        import serial  # 지연 import — 시리얼 없이도 테스트가 돌게 한다

        self._ser = serial.Serial(port, baud, timeout=timeout)
        self._buf = ""

    def write(self, data: str) -> None:
        self._ser.write(data.encode("utf-8"))

    def read_lines(self) -> Iterator[str]:
        chunk = self._ser.read(self._ser.in_waiting or 1)
        if chunk:
            self._buf += chunk.decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                yield line.strip()

    def close(self) -> None:
        self._ser.close()


class BoardService:
    def __init__(self, transport: Transport, *, clock: Callable[[], int],
                 timeout_s: float = 2.0):
        self.transport = transport
        self.clock = clock
        #: 명령 응답을 기다리는 최대 벽시계 시간. `$CFG,LIST` 는 7 KB 라
        #: 115200 baud 에서 600 ms 넘게 걸린다 — 여유를 두고 잡는다.
        self.timeout_s = timeout_s

        self.records: list[dict] = []
        self.seq_tracker = SeqTracker()
        self.corrupt_total = 0
        self.mode = "RUN"
        self.last_payload: dict | None = None

        self._acks: list[Command] = []
        self._catalog: list[str] = []
        self._collect_catalog = False

    # ------------------------------------------------------------- 명령 송신
    def send(self, verb: str, *args: str) -> Command:
        """명령을 보내고 **그 명령에 대응하는** $SACK 를 반환한다.

        🔴 verb 를 대조하지 않고 `_acks[0]` 을 돌려주면 안 된다.

        직렬 링크에서는 앞선 명령의 응답이 한 박자 늦게 도착하는 일이 흔하다.
        그 지연 응답이 버퍼 앞자리에 있으면, 지금 보낸 `$CFG,SET` 이 보드에서
        `RANGE` 로 거부됐는데도 남의 `$SACK,STAT,OK` 를 집어 **성공으로
        보고한다.** 설정 쓰기에서 조용한 거짓 성공은 이 시스템의 최악의
        실패 방식이고, GUI 가 이 계층 위에 올라간다.

        Raises:
            ProtocolError: 이 명령에 대응하는 응답이 오지 않은 경우.
        """
        self.last_payload = None
        self._acks.clear()
        self.transport.write(build_command(verb, *args))

        # 🔴 한 번만 pump 하면 실기기에서 거의 항상 실패한다.
        #
        # SerialTransport.read_lines() 는 그때 도착해 있는 바이트만 읽는다.
        # `$CFG,LIST` 응답은 약 7 KB 라 115200 baud 에서 600 ms 넘게 걸린다.
        # 한 번 읽고 판정하면 응답이 오는 중인데 "응답 없음" 이 된다.
        # 시뮬레이터는 즉시 답하므로 시험이 이걸 못 잡는다 — 즉 `--port COM7`
        # 로 바꾸는 순간에만 드러나는 종류의 결함이다.
        #
        # 마감시각은 **벽시계**로 잰다. `self.clock()` 은 시뮬레이터에 주입하는
        # 장치 시간이라 시험에서 멈춰 있을 수 있고, 그걸로 마감을 재면 영원히
        # 돈다. 전송 타임아웃은 장치 시간이 아니라 실제 경과 시간의 문제다.
        deadline = time.monotonic() + self.timeout_s
        while True:
            self.pump()
            for ack in self._acks:
                if ack.args and ack.args[0] == verb:
                    return ack
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_INTERVAL_S)

        if self._acks:
            others = [a.args[0] if a.args else "?" for a in self._acks]
            raise ProtocolError(
                f"{verb} 응답이 오지 않음. 받은 응답: {others}"
            )
        raise ProtocolError(f"응답 없음: {verb} {args} ({self.timeout_s}s 초과)")

    def heartbeat(self) -> None:
        """$HB 를 보낸다. 응답은 없다."""
        self.transport.write(build_command("HB"))
        self.pump()

    def set_config(self, key: str, value: str) -> tuple[bool, str]:
        """설정 변경을 시도하고 (성공여부, 거부사유) 를 반환한다."""
        ack = self.send("CFG", "SET", key, value)
        if ack.args[-1] == "OK":
            return True, ""
        return False, ack.args[-1]

    def fetch_schema(self) -> ConfigSchema:
        """$CFG,LIST 로 카탈로그를 받아 스키마를 만든다."""
        self._catalog = []
        self._collect_catalog = True
        try:
            self.send("CFG", "LIST")
        finally:
            self._collect_catalog = False
        return parse_catalog(self._catalog)

    # ------------------------------------------------------------- 수신 처리
    def pump(self) -> None:
        """트랜스포트에 쌓인 줄을 전부 처리한다."""
        now = self.clock()
        tick = getattr(self.transport, "tick", None)
        if tick is not None:
            tick(now)
        for line in self.transport.read_lines():
            self._ingest(line)

        # LoopbackTransport 로 시뮬레이터를 직접 물린 경우에는 모드를 바로
        # 읽어 온다. 실제 보드(SerialTransport)에는 `sim` 이 없으므로 이
        # 경로를 건너뛰고, 모드는 $STAT 응답으로만 갱신된다(_ingest 참조).
        sim = getattr(self.transport, "sim", None)
        if sim is not None:
            self.mode = sim.mode

    def _ingest(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        if line.startswith("$"):
            try:
                cmd = parse_line(line)
            except ProtocolError:
                self.corrupt_total += 1
                return
            if cmd.verb == "SACK":
                self._acks.append(cmd)
            return

        try:
            rec = parse_record(line)
        except ProtocolError:
            self.corrupt_total += 1
            return

        rtype = rec.get("type")

        # 🔴 카탈로그 수집 중이라도 **카탈로그 줄만** 가로챈다.
        #
        # 타입을 안 보고 전부 _catalog 로 보내면, $CFG,LIST 응답이 오는 동안
        # 흘러들어온 텔레메트리가 통째로 사라진다. parse_catalog 는 모르는
        # 타입을 무시하므로 아무도 눈치채지 못한다. GUI 가 카탈로그를 새로
        # 고칠 때마다 수집에 구멍이 뚫린다.
        if self._collect_catalog and rtype in ("cfg_item", "cfg_field", "cfg_end"):
            self._catalog.append(line)
            return

        # 🔴 명령 응답은 seq 시퀀스에 넣지 않는다 (규격 §7.1.1).
        # 타입을 손으로 나열하지 않고 is_telemetry() 를 쓴다 — 손으로 적으면
        # cfg_item/cfg_field/cfg_end 처럼 빠뜨리는 것이 생긴다.
        if not is_telemetry(rec):
            self.last_payload = rec
            if rtype == "stat":
                self.mode = rec.get("mode", self.mode)
            return

        self.seq_tracker.observe(rec["seq"])
        self.records.append(rec)

    def close(self) -> None:
        self.transport.close()
