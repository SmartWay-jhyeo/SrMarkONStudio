"""보드와의 대화를 관리한다 — 명령/응답 매칭, 텔레메트리 수집, 유실 집계.

GUI 와 분리돼 있다. GUI 가 없어도 이 계층만으로 수집·저장이 가능하며,
나중에 Jetson 에서 GUI 없이 서비스만 돌리는 구성이 그대로 가능하다.
"""

from collections.abc import Callable, Iterator
from typing import Protocol

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ProtocolError
from host.core.framing import Command, build_command, parse_line
from host.core.records import SeqTracker, parse_record


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
    def __init__(self, transport: Transport, *, clock: Callable[[], int]):
        self.transport = transport
        self.clock = clock

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
        """명령을 보내고 대응하는 $SACK 를 반환한다."""
        self.last_payload = None
        self._acks.clear()
        self.transport.write(build_command(verb, *args))
        self.pump()
        if not self._acks:
            raise ProtocolError(f"응답 없음: {verb} {args}")
        return self._acks[0]

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

        if getattr(self, "_collect_catalog", False):
            self._catalog.append(line)
            return

        rtype = rec.get("type")
        if rtype in ("cfg_value", "id"):
            self.last_payload = rec
            return
        if rtype == "stat":
            self.last_payload = rec
            self.mode = rec.get("mode", self.mode)
            return

        self.seq_tracker.observe(rec["seq"])
        self.records.append(rec)

    def close(self) -> None:
        self.transport.close()
