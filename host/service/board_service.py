"""보드와의 대화를 관리한다 — 명령/응답 매칭, 텔레메트리 수집, 유실 집계.

GUI 와 분리돼 있다. GUI 가 없어도 이 계층만으로 수집·저장이 가능하며,
나중에 Jetson 에서 GUI 없이 서비스만 돌리는 구성이 그대로 가능하다.
"""

import re
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

#: 응답을 기다리는 동안 다시 읽기까지의 간격. 짧게 잡아 응답 지연을 줄이되
#: CPU 를 태우지는 않는다.
_POLL_INTERVAL_S = 0.005

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ProtocolError
from host.core.limits import (
    DEFAULT_BAUD,
    LINK_BAUD_CHOICES,
    LINK_BAUD_CONFIRM_MS,
    LINK_BAUD_KEY,
)
from host.core.cloudnorm import normalize
from host.core.framing import Command, build_command, parse_line
from host.core.records import (
    COMMAND_RESPONSE_TYPES,
    SeqTracker,
    is_telemetry,
    parse_record,
)
from host.core.typemap import TypeMap

#: 이 키들이 바뀌면 역매핑(TypeMap)이 낡는다 — SET 성공 시 재구축한다.
_TYPEMAP_KEYS = re.compile(r"(ain|i2c|din)\d+\.(cloud|unit|kind|enabled)$")

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

    #: 지금 이 통로가 쓰고 있는 속도 (규격 §4.2).
    baud: int

    def write(self, data: str) -> None: ...
    def read_lines(self) -> Iterator[str]: ...
    def reopen(self, baud: int) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class BaudChangeResult:
    """링크 속도 변경 시도의 결말 (규격 §4.2).

    🔴 **거부·통신 실패·성공을 한 값으로 뭉개지 않는다.** 사용자가 알아야
       하는 것은 "지금 보드와 말이 되는가" 와 "어디서 멈췄는가" 둘이고,
       실패했을 때 그 둘을 말해 주지 못하면 화면 앞의 사람이 할 수 있는
       판단이 "전원을 뽑는다" 밖에 없다.
    """

    ok: bool
    #: 이 호출이 끝난 뒤 **호스트가 실제로 쓰고 있는** 속도.
    baud: int
    #: 어디까지 갔나 — "same"·"set"·"reopen"·"confirm"·"confirmed"·"reverted"
    stage: str
    #: 보드가 돌려준 거부 사유(RANGE·MODE·CAPACITY…). 거부일 때만.
    reason: str = ""
    #: 보드에 닿지 못한 경우. 거부와는 다른 사실이다.
    error: str = ""
    #: 옛 속도로 되돌아와 보드와 다시 말이 되는 것을 확인했는가.
    #: 🔴 실패했을 때 이 값이 거짓이면 사람이 손을 대야 한다.
    recovered: bool = False


#: 🔴 없어진 포트 이름 (시뮬레이터 제거, 2026-08-20).
#:
#:    조용히 이상하게 굴지 않고 진입점마다 여기서 잡아 말해 준다. 손가락과
#:    스크립트와 부팅 서비스 유닛에 아직 남아 있을 이름이라, 모르는 포트로
#:    그냥 넘기면 "COM 포트를 못 연다" 는 엉뚱한 오류가 되고 수집기는 그
#:    오류로 재접속을 무한히 되풀이한다.
RETIRED_SIM_PORT = "sim"
SIM_REMOVED_MSG = (
    "--port sim 은 없어졌다 (시뮬레이터 제거, 2026-08-20). "
    "실제 포트를 지정해라 (예: --port COM23)."
)


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
        self.baud = baud
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
        self._buf = b""

    def reopen(self, baud: int) -> None:
        """포트를 닫고 새 속도로 다시 연다 (규격 §4.2).

        🔴 **여기서도 DTR/RTS 를 열기 전에 내린다** (CLAUDE.md §4).

           `baudrate` 만 바꾸는 방법도 있지만 쓰지 않는다. USB CDC 에서
           그것이 실제로 line-coding 요청을 내보내는지는 드라이버에 달려
           있고, 안 내보내면 F103 브리지는 옛 속도로 남는다 — 그러면 확인이
           안 가고 보드가 되돌아간다. 닫았다 여는 것은 확실하다.

           그리고 닫았다 여는 순간이 바로 이 보드가 멈추는 순간이다. 한 번은
           보드가 죽어 GDB 로 리셋했고, 한 번은 F103 의 UART 브리지가 멈춰
           보드 전원을 끊었다 넣어야 했다(2026-08-14 실증). 순서가 전부다:
           닫고 → 속도를 바꾸고 → 두 선을 내리고 → 그 다음에 연다.
        """
        self._ser.close()
        # 🔴 옛 속도로 오다 만 바이트를 버린다. 남기면 새 속도로 온 첫 줄에
        #    앞에 붙어 그 줄까지 못 쓰게 만든다.
        self._buf = b""
        self._ser.baudrate = baud
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.reset_input_buffer()
        self.baud = baud

    def write(self, data: str) -> None:
        self._ser.write(data.encode("utf-8"))

    def read_lines(self) -> Iterator[str]:
        # 🔴 바이트로 모았다가 줄 단위로만 디코딩한다 [실증 2026-08-19].
        #
        # 예전에는 `self._ser.read()` 가 돌려준 조각마다 바로
        # `decode("utf-8", errors="replace")` 했다. 한글은 UTF-8 로
        # 3바이트인데 그 3바이트가 두 번의 `read()` 에 걸쳐 나뉘면(포트
        # 버퍼·드라이버가 아무 바이트 수에서나 끊어 줄 수 있다) 각 조각이
        # 그 자체로는 불완전한 바이트열이 되고, `errors="replace"` 가
        # 그것을 되돌릴 수 없는 `�` 로 바꿔 버린다. 실제로 "시간 품질"이
        # "???간 품질"로 깨져 GUI 에 떴다 — 초당 줄 수가 늘수록(100줄/초)
        # 조각 경계가 잦아져 더 자주 터진다.
        #
        # `\n`(0x0A)은 UTF-8 연속 바이트(0x80~0xBF 범위)와 값이 겹치지
        # 않으므로 항상 문자 경계이자 줄 경계다. 그래서 바이트 버퍼에
        # 계속 이어 붙이다가 `\n` 으로 자른 **완성된 줄**만 디코딩하면,
        # 조각이 어디서 끊기든 멀티바이트 문자는 항상 온전한 상태로
        # 디코딩된다.
        chunk = self._ser.read(self._ser.in_waiting or 1)
        if chunk:
            self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                # errors="replace" 는 여기서도 남긴다 — 다만 이제는 조각
                # 경계가 아니라 **물리 손상**(HANDOFF.md 가 실측한 링크
                # 유실·손상)만 걸린다. 줄을 통째로 버리면
                # `_record_raw()`(아래)의 불변조건("파싱 성패와 무관하게
                # 모든 수신 줄을 남긴다")이 깨지고 손상 자체가 원문 패널
                # 에서 사라진다. 대체문자로 채우면 줄은 보이고, JSON 파싱이
                # 깨지는 대부분의 경우 `corrupt_total` 로도 잡힌다(완전한
                # 보장은 아니다 — 대체문자가 우연히 유효한 JSON 문자열
                # 안에 들어가면 못 잡는다. 프로토콜에 CRC 가 없는 지금
                # 구조에서는 "지운다"보다 "보이게 남긴다"가 낫다는 판단).
                yield line.decode("utf-8", errors="replace").strip()

    def close(self) -> None:
        self._ser.close()


class BoardService:
    def __init__(self, transport: Transport, *, clock: Callable[[], int],
                 timeout_s: float = 2.0, raw_buffer_maxlen: int = RAW_BUFFER_MAXLEN,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        self.transport = transport
        self.clock = clock
        #: 🔴 링크 속도 변경(§4.2)이 쓰는 **벽시계**. `clock` 은 장치 시간을
        #:    주입하는 자리라 시험에서 멈춰 있을 수 있는데, 보드의
        #:    10초 시한은 장치 시간이 아니라 실제 경과 시간의 문제다.
        #:    시험이 10초를 진짜로 기다리지 않도록 주입 가능하게 둔다.
        self._sleep = sleep
        self._monotonic = monotonic
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

        # 🔴 [2026-08-31, HANDOFF_0831 결정 2] 역매핑 — 클라우드 레코드를
        #    내부형으로 되붙이는 표. 카탈로그가 오기 전에는 빈 표라
        #    사용자 타입 줄이 원문 그대로 흐른다(버리지 않는다).
        self._typemap = TypeMap({})
        self._schema = None
        #: 마지막으로 $HB 를 보낸 시각 — 게이트 재개 판정(heartbeat 주석).
        self._last_hb_ms: int | None = None

    # ------------------------------------------------------------- 역매핑
    def set_typemap(self, tmap: TypeMap) -> None:
        """역매핑을 바깥에서 주입한다 — 카탈로그 없이 수신만 하는 구성
        (시험·수집기)용. fetch_schema() 는 스스로 구축한다."""
        self._typemap = tmap

    def _refresh_typemap_for(self, key: str, value: str) -> None:
        """SET 성공이 역매핑을 낡게 했으면 저장된 스키마를 고쳐 재구축한다.

        이름 변경은 GUI 자신이 보내므로 이 갱신으로 스트림이 안 끊긴다
        (HANDOFF_0831 결정 2 보완)."""
        if self._schema is None or not _TYPEMAP_KEYS.match(key):
            return
        import dataclasses

        item = self._schema.items.get(key)
        if item is not None:
            self._schema.items[key] = dataclasses.replace(item, current=value)
        self._typemap = TypeMap.from_schema(self._schema)

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
        # 시험용 스텁은 즉시 답하므로 시험이 이걸 못 잡는다 — 즉 실물 포트로
        # 바꾸는 순간에만 드러나는 종류의 결함이다.
        #
        # 마감시각은 **벽시계**로 잰다. `self.clock()` 은 장치 시간을 주입하는
        # 자리라 시험에서 멈춰 있을 수 있고, 그걸로 마감을 재면 영원히
        # 돈다. 전송 타임아웃은 장치 시간이 아니라 실제 경과 시간의 문제다.
        # 🔴 `self._monotonic`/`self._sleep` 을 쓴다. 기본값은 `time` 의 그것
        #    이라 실기기 동작은 그대로이고, 시험은 링크 속도 변경의 실패
        #    경로(응답이 영영 안 오는 상태 × 재시도 3회)를 진짜 8초를
        #    기다리지 않고 밟을 수 있다.
        deadline = self._monotonic() + self.timeout_s
        while True:
            self.pump()
            for ack in self._acks:
                if ack.args and ack.args[0] == verb:
                    return ack
            if self._monotonic() >= deadline:
                break
            self._sleep(_POLL_INTERVAL_S)

        if self._acks:
            others = [a.args[0] if a.args else "?" for a in self._acks]
            raise ProtocolError(
                f"{verb} 응답이 오지 않음. 받은 응답: {others}"
            )
        raise ProtocolError(f"응답 없음: {verb} {args} ({self.timeout_s}s 초과)")

    #: 보드 침묵 게이트의 HB 신선도 시한 (규격 §6.2·§7.1.3 — 3초).
    _GATE_STALE_MS = 3000

    def heartbeat(self) -> None:
        """$HB 를 보낸다. 응답은 없다.

        🔴 [개정 2026-08-31, 규격 §7.1.3] 우리 HB 가 3초 넘게 끊겼다
        재개되면 그 사이 보드의 USB 게이트가 닫혔다 열린 것이다 — 침묵
        중에도 seq 는 오르므로(젯슨이 소비) 재개 후 첫 레코드를 새
        기준선으로 삼는다. 안 하면 게이트 점프 전체가 유실로 집계된다
        (실보드 검증에서 79건이 그렇게 잡혔다, 2026-08-31).
        """
        now = self.clock()
        if (self._last_hb_ms is not None
                and now - self._last_hb_ms > self._GATE_STALE_MS):
            # 🔴 reset() 이 아니라 forgive 다 — 게이트가 닫히기 전 레코드가
            #    OS 버퍼에 남아 연속으로 먼저 오고, 리셋은 거기에 기준선을
            #    잡아 정작 점프를 다시 센다(records.forgive_next_gap 주석,
            #    실보드 798건 오집계로 확인).
            self.seq_tracker.forgive_next_gap()
        self._last_hb_ms = now
        self.transport.write(build_command("HB"))
        self.pump()

    def set_config(self, key: str, value: str) -> tuple[bool, str]:
        """설정 변경을 시도하고 (성공여부, 거부사유) 를 반환한다."""
        ack = self.send("CFG", "SET", key, value)
        if ack.args[-1] == "OK":
            self._refresh_typemap_for(key, value)
            if key == "tx.seq":
                # 끔 구간에도 카운터는 오른다(mk_cloud.h) — 다시 켠 순간의
                # 점프 한 번을 용서한다(경계의 연속 잔량은 통과시키고).
                self.seq_tracker.forgive_next_gap()
            return True, ""
        return False, ack.args[-1]

    # ------------------------------------------------------- 링크 속도 (§4.2)
    def change_baud(self, new_baud: int, *, settle_s: float = 0.3,
                    attempts: int = 3) -> BaudChangeResult:
        """호스트 링크 속도를 바꾼다 — 규격 §4.2 의 절차를 그대로 밟는다.

        🔴 **이 메서드는 실패하는 것이 정상 경로에 있다.** 921600 보다 높은
           속도는 아무도 시험한 적이 없고(§4.2.5), F103(BMP) 브리지가 그것을
           견디는지 모른다. 그래서 여기서 하는 일의 절반은 "바꾸기" 이고
           나머지 절반은 **"안 되면 옛 속도로 돌아오기"** 다.

        순서 (한 단계라도 뒤집으면 링크를 잃는다):

          1. 옛 속도로 `$CFG,SET,link.baud,<new>` — 보드가 응답까지 옛
             속도로 마친 뒤에 스스로 속도를 바꾼다
          2. 포트를 닫고 **새 속도로** 다시 연다
          3. `$HB` 를 먼저 보낸다 — 포트를 여는 동안 하트비트가 끊겨 보드가
             RUN 으로 떨어졌을 수 있다(§6.2). `$BAUD,CONFIRM` 자체는 RUN
             에서도 되지만, 이어질 `$CFG,SAVE` 는 CONFIG 가 필요하다
          4. `$BAUD,CONFIRM,<new>` — 여기까지 오면 확정이다
          5. 안 되면 옛 속도로 되돌아가 **보드의 시한이 지나기를 기다린다.**
             그 뒤 `$ID` 로 링크가 살아났는지 확인한다

        반환값의 `recovered` 가 거짓이면 사람이 손을 대야 한다는 뜻이다.
        """
        old = getattr(self.transport, "baud", DEFAULT_BAUD)
        if new_baud == old:
            return BaudChangeResult(True, old, "same", recovered=True)
        if new_baud not in LINK_BAUD_CHOICES:
            # 🔴 보내기 전에 막는다. 보드도 거부하겠지만, 그 거부를 받으려면
            #    링크가 살아 있어야 하고 여기서 막으면 아예 안 건드린다.
            return BaudChangeResult(
                False, old, "set", reason="RANGE",
                error=f"낼 수 없는 속도다: {new_baud}", recovered=True)

        # 1) 옛 속도로 요청. 응답도 옛 속도로 온다 (§4.2.2 규칙 1).
        try:
            ack = self.send("CFG", "SET", LINK_BAUD_KEY, str(new_baud))
        except ProtocolError as exc:
            return BaudChangeResult(False, old, "set", error=str(exc),
                                    recovered=True)
        if not ack.args or ack.args[-1] != "OK":
            reason = ack.args[-1] if ack.args else "MALFORMED"
            # 🔴 보드가 거부했으면 **아무것도 안 바뀐다.** 포트를 다시 열지
            #    않는다 — 여는 순간이 이 보드가 멈추는 순간이다.
            return BaudChangeResult(False, old, "set", reason=reason,
                                    recovered=True)

        # 여기서부터 보드의 10초 시한이 흐른다.
        deadline = self._monotonic() + LINK_BAUD_CONFIRM_MS / 1000.0

        # 2) 새 속도로 다시 연다.
        try:
            self.transport.reopen(new_baud)
        except Exception as exc:                          # noqa: BLE001
            return self._baud_fallback(old, deadline, "reopen", error=str(exc))

        # 3~4) 확인. 포트가 늦게 열리는 일이 있으므로 몇 번 시도한다.
        last_error = ""
        for _ in range(max(1, attempts)):
            self._sleep(settle_s)
            try:
                self.heartbeat()
                ack = self.send("BAUD", "CONFIRM", str(new_baud))
            except ProtocolError as exc:
                last_error = str(exc)
                continue
            if ack.args and ack.args[-1] == "OK":
                return BaudChangeResult(True, new_baud, "confirmed",
                                        recovered=True)
            # 🔴 보드가 **응답은 했는데 거부**했다. 링크는 멀쩡하다는 뜻이라
            #    다시 시도해 봐야 답이 달라지지 않는다 — 곧장 되돌린다.
            reason = ack.args[-1] if ack.args else "MALFORMED"
            return self._baud_fallback(old, deadline, "confirm", reason=reason)

        return self._baud_fallback(
            old, deadline, "confirm",
            error=last_error or "새 속도로는 응답이 오지 않았다")

    def _baud_fallback(self, old_baud: int, deadline: float, stage: str, *,
                       reason: str = "", error: str = "") -> BaudChangeResult:
        """옛 속도로 돌아가 보드가 스스로 되돌아오기를 기다린다 (§4.2.2 규칙 4).

        🔴 **기다리는 것이 일의 절반이다.** 포트만 옛 속도로 열어 두면 보드는
           아직 새 속도로 말하고 있어서 그 사이 오가는 모든 것이 쓰레기다.
           호스트가 그것을 보고 "보드가 죽었다" 고 판단하면, 사람은 전원을
           뽑는다 — 10초만 기다리면 저절로 살아나는데도.
        """
        try:
            self.transport.reopen(old_baud)
        except Exception as exc:                          # noqa: BLE001
            return BaudChangeResult(False, old_baud, stage, reason=reason,
                                    error=error or str(exc), recovered=False)

        # 보드의 시한이 지날 때까지 기다린다. 여유 0.5초는 우리 시계와 보드
        # 시계가 정확히 같지 않기 때문이다.
        remaining = deadline + 0.5 - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)

        # 🔴 시한이 지났다고 곧바로 물으면 안 된다. 보드가 실제로 되돌아가는
        #    것은 슈퍼루프의 다음 바퀴이고, 그 전에 보낸 바이트는 아직 새
        #    속도로 도는 보드에게 잡음이다 — 첫 시도가 실패하는 것이 정상이다.
        #    한 번 더 물어본다.
        recovered = False
        for attempt in range(2):
            self.pump()
            try:
                self.send("ID")
                recovered = True
                break
            except ProtocolError:
                if attempt == 0:
                    self._sleep(0.3)
        return BaudChangeResult(False, old_baud, stage, reason=reason,
                                error=error, recovered=recovered)

    def fetch_schema(self) -> ConfigSchema:
        """$CFG,LIST 로 카탈로그를 받아 스키마를 만든다.

        🔴 역매핑(TypeMap)도 여기서 함께 구축한다 — 카탈로그가 곧 "타입
        문자열이 어느 채널인가"의 유일 출처다(HANDOFF_0831 결정 2 보완).
        """
        self._catalog = []
        self._collect_catalog = True
        try:
            self.send("CFG", "LIST")
        finally:
            self._collect_catalog = False
        schema = parse_catalog(self._catalog)
        self._schema = schema
        self._typemap = TypeMap.from_schema(schema)
        return schema

    def fetch_stat(self) -> dict:
        """$STAT 으로 지금 상태를 받는다.

        🔴 `din`(J18~J20)이 이걸 쓰는 이유(규격 §7.4, §7.6) — din 레코드는
           상태가 **바뀔 때만** 온다. 막 연결한 호스트는 엣지를 한 번도
           못 봤을 수 있어 그것만으로는 지금 상태를 모른다. `$STAT` 의
           `din` 배열은 그 순간의 실측이라 그 공백을 채운다. 호출부(GUI)는
           연결 직후·재연결 직후 이것을 **한 번만** 부르면 된다 — 그
           뒤의 변화는 `din` 레코드가 알려 주므로 주기적으로 다시 물을
           이유가 없다.

        `fetch_schema()` 와 같은 자리 — `send()` 로 명령을 보내고
        `last_payload` 를 그대로 돌려준다. `_ingest()` 가 `stat` 레코드를
        받으면 이미 `mode`/`ctl_mode` 도 갱신해 둔다.
        """
        self.send("STAT")
        return self.last_payload or {}

    # ------------------------------------------------------------- 수신 처리
    def pump(self) -> None:
        """트랜스포트에 쌓인 줄을 전부 처리한다."""
        now = self.clock()
        tick = getattr(self.transport, "tick", None)
        if tick is not None:
            tick(now)
        for line in self.transport.read_lines():
            self._ingest(line)

        # 시험용 루프백(host/tests/fake_board.LoopbackTransport)으로 스텁을
        # 직접 물린 경우에는 모드를 바로 읽어 온다. 실제 보드
        # (SerialTransport)에는 `sim` 이 없으므로 이 경로를 건너뛰고, 모드는
        # $STAT 응답으로만 갱신된다(_ingest 참조).
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

        # 🔴 $GNSSRAW 는 §3 체크섬이 없는 진단 줄이다(규격 §7.7 개정
        #    2026-08-31 — 원문의 NMEA 체크섬이 그대로 실려 있다). parse_line
        #    에 넣으면 켜는 순간 모든 에코가 corrupt 로 집계된다.
        if line.startswith("$GNSSRAW,"):
            self._record_raw(line, "gnssraw")
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

        # 🔴 명령 응답은 seq 시퀀스에 넣지 않는다 (규격 §7.1.1). 타입
        # 집합은 records.py 의 화이트리스트 하나만 쓴다.
        #
        # [개정 2026-08-31] is_telemetry() 로 가르면 안 된다 — 그 함수는
        # 이제 seq 존재까지 봐서, tx.seq 를 끈 보드의 **진짜 텔레메트리**가
        # 이 분기로 빨려 들어와 records 에서 사라진다. 제어 응답 판정은
        # 타입으로, 유실 집계 참여는 is_telemetry 로 — 둘은 다른 질문이다.
        if rtype in COMMAND_RESPONSE_TYPES:
            self.last_payload = rec
            if rtype == "stat":
                self.mode = rec.get("mode", self.mode)
                # 🔴 두 축은 독립이다 (규격 §6.4). 하나가 없다고 다른 하나를
                #    건드리지 않는다 — 구버전 펌웨어는 ctl_mode 를 안 보낸다.
                self.ctl_mode = rec.get("ctl_mode", self.ctl_mode)
            return

        # 🔴 [개정 2026-08-31, HANDOFF_0831 결정 2] 계약 레코드를 내부형으로
        #    정규화해 쌓는다. v3(굽기 전 실보드)·미해석 문자열은 그대로
        #    통과하므로 전환기에도 아무것도 잃지 않는다.
        #
        #    seq 관찰은 **줄 기준 한 번**이다 — 한 줄이 두 레코드로 펴질 수
        #    있고(temp_road + 주변 온도), 같은 seq 를 두 번 넣으면 duplicate
        #    통계가 오염된다.
        if is_telemetry(rec):
            self.seq_tracker.observe(rec["seq"])
        for out in normalize(rec, self._typemap):
            self.records.append(out)
            self._pending_records.append(out)

    def close(self) -> None:
        self.transport.close()
