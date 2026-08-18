"""보드와의 대화를 관리한다 — 명령/응답 매칭, 텔레메트리 수집, 유실 집계.

GUI 와 분리돼 있다. GUI 가 없어도 이 계층만으로 수집·저장이 가능하며,
나중에 Jetson 에서 GUI 없이 서비스만 돌리는 구성이 그대로 가능하다.
"""

import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Protocol

#: 응답을 기다리는 동안 다시 읽기까지의 간격. 짧게 잡아 응답 지연을 줄이되
#: CPU 를 태우지는 않는다.
_POLL_INTERVAL_S = 0.005

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ProtocolError
from host.core.limits import DEFAULT_BAUD
from host.core.framing import Command, build_command, parse_line
from host.core.records import SeqTracker, is_telemetry, parse_record

#: 원문 줄 버퍼(`raw_lines`)와 파싱된 레코드 버퍼(`records`)의 기본 상한.
#:
#: 🔴 둘 다 예전에는 무한히 자랐다(HANDOFF.md §7.4). 근거: `ain` 최소 주기는
#: 10ms 라 7채널을 전부 최소 주기로 돌리면 700줄/초까지 나온다(이론적
#: 상한 — `host/core/records.py` 의 `MAX_PLAUSIBLE_GAP` 주석과 같은 계산).
#: I2C·din·명령 응답까지 얹어도 그 첨두를 오래 유지하는 설정은 실용적이지
#: 않다 — 링크가 못 따라간다(같은 문서 §7.4의 대역폭 유실 실측 참고).
#: 기본 설정(ain 100ms 1채널)에서는 초당 10줄 안팎이다.
#:
#: 5000줄이면 최악 첨두(700/s)에서도 7초, 기본 설정에서는 8분 넘게
#: 담는다 — 벤치에서 이상 현상을 보고 "방금 그거 뭐였지" 하고 돌아볼
#: 여유는 되면서, NDJSON 한 줄이 150~250바이트이니 5000줄이면 1MB
#: 안팎이라 하루 종일 켜 둬도 무한 성장 걱정이 없다.
RAW_BUFFER_MAXLEN = 5000


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

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 0.1):
        import serial  # 지연 import — 시리얼 없이도 테스트가 돌게 한다

        # 🔴 DTR/RTS 를 **열기 전에** 내린다.
        #
        #    `serial.Serial(port, baud, ...)` 한 줄 생성자는 생성과 동시에
        #    포트를 열면서 두 선을 세운다. 이 보드는 그러면 멈춘다
        #    (CLAUDE.md §4, 2026-08-14 실증). 한 번은 보드가 죽어 GDB 로
        #    리셋해야 했고, 한 번은 F103 의 UART 브리지가 멈춰 보드 전원을
        #    끊었다 넣어야 했다.
        #
        #    포트를 만들고 → 두 선을 내리고 → 그 다음에 연다. 순서가 전부다.
        self._ser = serial.Serial()
        self._ser.port = port
        self._ser.baudrate = baud
        self._ser.timeout = timeout
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        # 연 뒤에도 한 번 더 내린다 — 드라이버에 따라 열면서 되살아난다.
        self._ser.dtr = False
        self._ser.rts = False
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
                 timeout_s: float = 2.0, raw_buffer_maxlen: int = RAW_BUFFER_MAXLEN):
        self.transport = transport
        self.clock = clock
        #: 명령 응답을 기다리는 최대 벽시계 시간. `$CFG,LIST` 는 7 KB 라
        #: 115200 baud 에서 600 ms 넘게 걸린다 — 여유를 두고 잡는다.
        self.timeout_s = timeout_s

        #: 🔴 `deque(maxlen=...)` — 예전에는 `list` 라 무한히 자랐다.
        #:    상한 근거는 위 `RAW_BUFFER_MAXLEN` 주석.
        self.records: deque[dict] = deque(maxlen=raw_buffer_maxlen)
        #: 받은 줄을 **원문 그대로** 담는다 — "정말 오고 있나" 를 눈으로
        #: 보려면 파싱한 값이 아니라 실제로 온 바이트가 있어야 한다.
        self.raw_lines: deque[str] = deque(maxlen=raw_buffer_maxlen)
        self.seq_tracker = SeqTracker()
        self.corrupt_total = 0
        self.mode = "RUN"
        #: 제어 모드 (규격 §6.4). 부팅 기본값은 ACTIVE — 보드는 혼자서도
        #: 제 일을 해야 하고, 테스트는 사람이 명시적으로 들어가는 상태다.
        self.ctl_mode = "ACTIVE"
        self.last_payload: dict | None = None

        #: 원문 줄 통계. `raw_lines`/`records` 는 상한이 있어 오래된 것을
        #: 밀어내지만, 이 수치들은 세션 전체를 말해야 하므로 **누적**이다.
        self.line_total = 0
        self.byte_total = 0
        #: 타입별 누적 줄 수. `$` 명령 응답은 verb 를, 파싱 실패는
        #: "corrupt" 를 키로 쓴다 — parse_record 가 주는 `type` 은 텔레메트리
        #: 에만 있다.
        self.type_counts: dict[str, int] = {}
        #: 마지막으로 줄 하나를 받은 시각(`self.clock()` 기준). 아직 아무것도
        #: 못 받았으면 `None` 이다.
        self.last_line_at: int | None = None

        self._acks: list[Command] = []
        self._catalog: list[str] = []
        self._collect_catalog = False
        #: `take_records()`/`take_raw_lines()` 가 비우는 대기열.
        #:
        #: 🔴 `records`/`raw_lines` 가 `deque(maxlen=N)` 이 되면서 워커의
        #:    슬라이스 커서(`records[seen:]`)가 못 쓰게 됐다 — 가득 찬 뒤
        #:    `len()` 이 상한에서 멈추므로 슬라이스가 영원히 빈 목록을 낸다
        #:    (worker_loop.py 머리말 참고). 이 대기열이 그 대안이다: 넘겨준
        #:    만큼 비우므로 상한과 무관하게 "이번에 새로 온 것" 을 안다.
        self._pending_records: deque[dict] = deque(maxlen=raw_buffer_maxlen)
        self._pending_raw: deque[str] = deque(maxlen=raw_buffer_maxlen)

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
            self.ctl_mode = sim.ctl_mode

    def _record_raw(self, line: str, rtype: str) -> None:
        """원문 줄 하나를 통계·버퍼에 반영한다.

        🔴 파싱 성패와 무관하게 **모든** 수신 줄에 대해 불린다. "정말 오고
        있나" 를 보는 화면에서 깨진 줄을 숨기면 손상 자체를 놓친다.
        """
        self.raw_lines.append(line)
        self._pending_raw.append(line)
        self.line_total += 1
        self.byte_total += len(line.encode("utf-8"))
        self.type_counts[rtype] = self.type_counts.get(rtype, 0) + 1
        self.last_line_at = self.clock()

    def take_records(self) -> list[dict]:
        """아직 아무도 안 가져간 텔레메트리를 반환하고 비운다.

        `records` 가 상한 있는 버퍼가 되면서 워커의 슬라이스 커서가 못 쓰게
        된 것의 대안이다(`_pending_records` 주석). `worker_loop.WorkerLoop`
        는 이 메서드가 있으면 우선 쓴다.
        """
        out = list(self._pending_records)
        self._pending_records.clear()
        return out

    def take_raw_lines(self) -> list[str]:
        """아직 아무도 안 가져간 원문 줄을 반환하고 비운다. `take_records`
        와 같은 이유·같은 모양이다."""
        out = list(self._pending_raw)
        self._pending_raw.clear()
        return out

    def _ingest(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        if line.startswith("$"):
            try:
                cmd = parse_line(line)
            except ProtocolError:
                self.corrupt_total += 1
                self._record_raw(line, "corrupt")
                return
            self._record_raw(line, cmd.verb)
            if cmd.verb == "SACK":
                self._acks.append(cmd)
            return

        try:
            rec = parse_record(line)
        except ProtocolError:
            self.corrupt_total += 1
            self._record_raw(line, "corrupt")
            return

        rtype = rec.get("type")
        self._record_raw(line, rtype if isinstance(rtype, str) else "?")

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
                # 🔴 두 축은 독립이다 (규격 §6.4). 하나가 없다고 다른 하나를
                #    건드리지 않는다 — 구버전 펌웨어는 ctl_mode 를 안 보낸다.
                self.ctl_mode = rec.get("ctl_mode", self.ctl_mode)
            return

        self.seq_tracker.observe(rec["seq"])
        self.records.append(rec)
        self._pending_records.append(rec)

    def close(self) -> None:
        self.transport.close()
