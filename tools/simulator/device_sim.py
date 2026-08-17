"""가상 STM32 v2.0 보드.

펌웨어와 같은 상태 기계를 구현한다 — CONFIG/RUN 모드, 설정 검증, 인터록,
필드 마스크, seq 증가. 이후 펌웨어의 기준 구현이자 GUI 의 개발용 상대역이다.

시각을 스스로 읽지 않고 tick(now_ms) 로 받는다. 테스트가 결정적이 되고,
시리얼 어댑터가 시계를 소유하게 되어 계층이 깔끔해진다.
"""

import json
import math

from host.core.errors import ConfigError, ProtocolError, Reason
from host.core.framing import build_command, parse_line
from host.core.records import SCHEMA_VER
from tools.simulator.config_store import ConfigStore
from tools.simulator.telemetry import (
    ADS1256_FULL_SCALE,
    SIM_I2C_SENSORS,
    build_ain_record,
    build_i2c_record,
    render,
    synthetic_i2c_value,
)

#: 규격 §6 — 이 시간 안에 $HB 를 못 받으면 RUN 으로 내려간다.
HB_TIMEOUT_MS = 3000

#: 보드가 호스트에게 보내는 생존 신호 주기
HB_EMIT_PERIOD_MS = 1000

AIN_CHANNELS = 7


class Mode:
    """접속 모드 — 하트비트로 **관측**된다 (규격 §6.2)."""

    CONFIG = "CONFIG"
    RUN = "RUN"


class CtlMode:
    """제어 모드 — `$MODE` 로 **선언**된다 (규격 §6.4).

    🔴 `Mode` 와 다른 축이다. 같은 명령(밸브 열기)이 벤치에서는 배선 확인이고
       운전 중에는 공정을 돌리는 일이라, 항목의 성질로는 구분할 수 없다.
    """

    TEST = "TEST"
    ACTIVE = "ACTIVE"


#: CONFIG 모드에서만 받는 $CFG 하위 명령 (규격 §4)
_CONFIG_ONLY = frozenset({"SET", "SAVE", "RESET"})

#: 깨진 줄에서 건져낸 verb 에 허용되는 문자. 실제 verb 는 전부 대문자다.
_ALLOWED_VERB_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class DeviceSim:
    def __init__(self, store: ConfigStore, *, fw: str = "0.1.0",
                 board_rev: str = "2.0"):
        self.store = store
        self.fw = fw
        self.board_rev = board_rev

        self._seq = 0
        self._now_ms = 0
        self._last_hb_rx_ms: int | None = None
        self._last_hb_tx_ms = 0
        self._last_emit_ms = 0
        #: I2C 는 포트마다 주기가 따로다 — 마지막으로 낸 시각을 각각 센다.
        self._last_i2c_ms: dict[int, int] = {}
        self._boot_ms = 0
        # 🔴 부팅 기본값은 ACTIVE 다 (규격 §6.4). 보드는 혼자서도 제 일을
        #    해야 하고, 테스트는 사람이 명시적으로 들어가는 상태다.
        self._ctl_mode = CtlMode.ACTIVE

    # ------------------------------------------------------------------ 모드
    @property
    def mode(self) -> str:
        """접속 모드 — 하트비트로 **관측**된다 (규격 §6.2)."""
        if self._last_hb_rx_ms is None:
            return Mode.RUN
        if self._now_ms - self._last_hb_rx_ms > HB_TIMEOUT_MS:
            return Mode.RUN
        return Mode.CONFIG

    @property
    def ctl_mode(self) -> str:
        """제어 모드 — `$MODE` 로 **선언**된다 (규격 §6.4).

        🔴 접속 모드와 다른 축이다. 하나는 "사람이 보고 있나", 다른 하나는
           "이 명령이 무슨 뜻인가" 다. 한 변수에 섞지 않는다.
        """
        return self._ctl_mode

    def _on_mode(self, args: tuple[str, ...]) -> list[str]:
        want = (args[0].upper() if args else "")
        if want not in (CtlMode.TEST, CtlMode.ACTIVE):
            return [self._sack("MODE", "ERR", Reason.RANGE)]

        # 🔴 TEST 는 CONFIG 안에서만 산다. TEST 의 안전장치가 하트비트
        #    데드맨이므로, 하트비트 없이 들어가면 그 장치가 없는 상태가 된다.
        if want == CtlMode.TEST and self.mode != Mode.CONFIG:
            return [self._sack("MODE", "ERR", Reason.MODE)]

        if want != self._ctl_mode:
            self._leave_test_if_needed(want)
            self._ctl_mode = want
        return [self._sack("MODE", "OK")]

    def _leave_test_if_needed(self, going_to: str) -> None:
        """TEST 를 벗어나면 출력을 안전 상태로 되돌린다 (규격 §6.4)."""
        if self._ctl_mode == CtlMode.TEST and going_to != CtlMode.TEST:
            self.store.revert_outputs()

    # ------------------------------------------------------------- 수신 처리
    def feed(self, line: str) -> list[str]:
        """호스트가 보낸 줄 하나를 처리하고 응답 줄들을 반환한다."""
        try:
            cmd = parse_line(line)
        except ProtocolError:
            # verb 를 알 수 없으면 어느 명령에 대한 거부인지 말할 수 없다.
            # 체크섬만 틀린 경우는 verb 를 읽을 수 있으므로 아래에서 처리한다.
            verb = _verb_of_broken_line(line)
            if verb is None:
                return []
            # 🔴 $HB 는 예외다 (§3, §6.1). 체크섬이 틀려도 $SACK 를 보내지
            #    않는다. 1 Hz 로 오므로 링크가 나빠지면 초당 하나씩 쌓여
            #    이미 나쁜 링크를 더 나쁘게 만든다. 알릴 내용은 어차피
            #    3000 ms 뒤 RUN 전환으로 전달된다.
            if verb == "HB":
                return []
            return [self._sack(verb, "ERR", Reason.CHECKSUM)]

        handler = {
            "HB": self._on_hb,
            "ID": self._on_id,
            "STAT": self._on_stat,
            "CFG": self._on_cfg,
            "MODE": self._on_mode,
        }.get(cmd.verb)

        if handler is None:
            # 🔴 UNKNOWN_KEY 가 아니다. 그것은 "존재하지 않는 **설정 키**"라는
            #    뜻이고(§5), 여기서 없는 것은 명령이다. 규격 §5 의
            #    UNSUPPORTED 가 이 경우다 — 오타든 미구현이든 호스트가 할
            #    일은 같다.
            return [self._sack(cmd.verb, "ERR", Reason.UNSUPPORTED)]
        return handler(cmd.args)

    def _on_hb(self, _args: tuple[str, ...]) -> list[str]:
        self._last_hb_rx_ms = self._now_ms
        return []                                  # 하트비트에는 응답하지 않는다

    def _on_id(self, _args: tuple[str, ...]) -> list[str]:
        return [
            self._json(
                type="id",
                device_id=self.store.get("dev.id"),
                fw=self.fw,
                board_rev=self.board_rev,
            ),
            self._sack("ID", "OK"),
        ]

    def _on_stat(self, _args: tuple[str, ...]) -> list[str]:
        return [
            self._json(
                type="stat",
                mode=self.mode,
                ctl_mode=self.ctl_mode,
                fw=self.fw,
                board_rev=self.board_rev,
                # 🔴 `t` 의 기준점을 호스트가 알 수 있게 여기서 알려준다.
                # 규격 §7.1.2 대로 `t` 는 time_source 에 따라 UTC epoch 이거나
                # 부팅 후 경과 ms 다. 텔레메트리는 필드 마스크로 time_source 를
                # 실어 보낼 수 있지만 명령 응답에는 그 자리가 없다. $STAT 이
                # 그 답을 주는 곳이다 — 호스트는 연결 직후 한 번 물어보면 된다.
                time_source="device_clock",
                time_quality=0,
                uptime_ms=self._now_ms - self._boot_ms,
                rails={
                    "v24": self.store.get("pwr.24v"),
                    "v14v9": self.store.get("pwr.14v9"),
                    "v5": self.store.get("pwr.5v"),
                },
                queues=[
                    {"ch": ch, "depth": 0, "peak": 0, "drops": 0}
                    for ch in range(AIN_CHANNELS)
                    if self.store.get(f"ain{ch}.enabled")
                ],
            ),
            self._sack("STAT", "OK"),
        ]

    def _on_cfg(self, args: tuple[str, ...]) -> list[str]:
        if not args:
            return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]

        sub = args[0].upper()

        if sub in _CONFIG_ONLY and self.mode != Mode.CONFIG:
            return [self._sack("CFG", "ERR", Reason.MODE)]

        try:
            if sub == "LIST":
                return [*self.store.catalog_lines(), self._sack("CFG", "OK")]

            if sub == "GET":
                key = args[1] if len(args) > 1 else ""
                value = self.store.get(key)
                return [
                    self._json(type="cfg_value", key=key, cur=value),
                    self._sack("CFG", "OK"),
                ]

            if sub == "SET":
                if len(args) < 3:
                    return [self._sack("CFG", "ERR", Reason.RANGE)]
                # 값에 쉼표가 들어갈 수 있으므로 나머지를 전부 붙인다.
                self.store.set(args[1], ",".join(args[2:]))
                return [self._sack("CFG", "OK")]

            if sub == "SAVE":
                # 🔴 TEST 에서는 출력 항목을 건너뛴다 (규격 §6.4). 벤치에서
                #    배선을 보려고 밸브를 한 번 열어 본 것이 플래시에 남아
                #    다음 부팅에 되살아나면 안 된다.
                self.store.save(skip_outputs=self._ctl_mode == CtlMode.TEST)
                return [self._sack("CFG", "OK")]

            if sub == "RESET":
                self.store.reset()
                return [self._sack("CFG", "OK")]

        except ConfigError as exc:
            return [self._sack("CFG", "ERR", exc.reason)]

        return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]

    # --------------------------------------------------------------- 주기 처리
    def tick(self, now_ms: int) -> list[str]:
        """시각을 진행시키고 이번에 내보낼 줄들을 반환한다."""
        self._now_ms = now_ms
        out: list[str] = []

        # 🔴 호스트가 사라지면 테스트도 끝난다 (규격 §6.4). 하트비트가 이미
        #    데드맨이므로 방아쇠를 새로 만들지 않는다 — 그리고 그것이 옳은
        #    방아쇠다. 하트비트는 케이블이 아니라 **저쪽에서 사람이 보고
        #    있는지**를 알려 준다.
        if self._ctl_mode == CtlMode.TEST and self.mode != Mode.CONFIG:
            self.store.revert_outputs()
            self._ctl_mode = CtlMode.ACTIVE

        if now_ms - self._last_hb_tx_ms >= HB_EMIT_PERIOD_MS:
            self._last_hb_tx_ms = now_ms
            out.append(build_command("HB").rstrip("\r\n"))

        period = int(self.store.get("tx.period_ms"))
        if now_ms - self._last_emit_ms >= period:
            self._last_emit_ms = now_ms
            out.extend(self._emit_telemetry(now_ms))

        return out

    def _emit_telemetry(self, now_ms: int) -> list[str]:
        lines: list[str] = []
        for ch in range(AIN_CHANNELS):
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            self._seq += 1
            lines.append(
                render(
                    build_ain_record(
                        self.store,
                        channel=ch,
                        seq=self._seq,
                        t_ms=now_ms,
                        raw=_synthetic_raw(ch, now_ms),
                        capture_counter=now_ms * 1000,
                    )
                )
            )
        lines += self._emit_i2c(now_ms)
        return lines

    def _emit_i2c(self, now_ms: int) -> list[str]:
        """규격 §7.5 — 켜 둔 포트만 내보낸다.

        🔴 꺼졌거나 안 꽂힌 포트는 **아무것도 보내지 않는다**. 설계 원칙 3 —
           센서 미연결은 정상 상태이고 오류로 올리지 않는다.

        🔴 주기는 포트마다 다르다(`i2cN.period_ms`). 아날로그처럼 한 주기로
           묶으면 200 ms 짜리 온도계와 20 ms 짜리 조도계를 같은 속도로
           읽게 되어 설정이 뜻을 잃는다.
        """
        lines: list[str] = []
        for cid, quantities in SIM_I2C_SENSORS.items():
            if not self.store.get(f"i2c{cid}.enabled"):
                continue
            period = int(self.store.get(f"i2c{cid}.period_ms"))
            last = self._last_i2c_ms.get(cid, -period)
            if now_ms - last < period:
                continue
            self._last_i2c_ms[cid] = now_ms
            for quantity, unit in quantities:
                self._seq += 1
                lines.append(render(build_i2c_record(
                    self.store, connector_id=cid, quantity=quantity,
                    unit=unit, seq=self._seq, t_ms=now_ms,
                    value=synthetic_i2c_value(cid, quantity, now_ms),
                )))
        return lines

    # ------------------------------------------------------------------ 보조
    def _sack(self, verb: str, *rest: str) -> str:
        """$SACK 를 만든다. **모든 응답이 여기를 지난다.**

        🔴 verb 를 그대로 되싣지 않는다.

        길고 이상한 verb 는 깨진 줄로만 오는 게 아니다. `"$" + "A"*4000 + "*00"`
        은 4000개의 XOR 이 0x00 이라 **체크섬이 맞는 정상 줄**로 파싱되고,
        모르는 명령이므로 UNKNOWN_KEY 응답에 그 4000자가 그대로 실린다.

        파이썬에서는 그저 긴 문자열이지만, C 로 옮기면 공격자가 길이를 정하는
        데이터를 고정 버퍼에 넣는 전형적인 오버플로다. 이 모듈이 펌웨어 참조라
        길목에서 잘라둔다.
        """
        if len(verb) > _MAX_SALVAGED_VERB or any(
            c not in _ALLOWED_VERB_CHARS for c in verb
        ):
            verb = "?"
        return build_command("SACK", verb, *rest).rstrip("\r\n")

    def _json(self, **fields) -> str:
        rec = {"schema_ver": SCHEMA_VER, "seq": 0, "t": self._now_ms}
        rec.update(fields)
        return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


#: 건져낸 verb 의 최대 길이. 실제 verb 중 가장 긴 것이 `SACK`(4자)이므로
#: 넉넉히 잡아도 이 정도면 충분하다.
_MAX_SALVAGED_VERB = 12


def _verb_of_broken_line(line: str) -> str | None:
    """체크섬이 틀린 줄에서 verb 만 건져낸다. 못 건지면 None.

    🔴 **건져낸 verb 를 검증하지 않고 돌려주면 안 된다.**

    이 값은 곧바로 `_sack()` → `build_command()` → `build_line()` 로 들어가는데,
    `build_line` 은 제어문자를 거부하며 예외를 던진다. 잡음으로 깨진 줄 안에
    TAB 하나만 섞여 있어도 그 예외가 `feed()` 밖으로 튀어나가 **수신 루프가
    통째로 죽는다.** `strip()` 은 양끝만 지우므로 중간에 낀 제어문자는 남는다.

    규격 §3 은 이 경로를 "verb 를 읽을 수 없을 만큼 깨졌으면 조용히 버린다
    (링크는 유지)" 로 정한다. 깨진 입력에 크래시하는 것은 이 모듈이 펌웨어
    참조라는 점에서 가장 베끼면 안 되는 동작이다.

    길이도 제한한다. 파이썬에서는 무해하지만 C 로 옮기면 공격자가 정한
    길이의 문자열을 고정 버퍼에 넣는 전형적인 오버플로가 된다.
    """
    stripped = line.strip()
    if not stripped.startswith("$") or "*" not in stripped:
        return None
    payload = stripped[1 : stripped.rfind("*")]
    verb = payload.split(",")[0]
    if not verb or len(verb) > _MAX_SALVAGED_VERB:
        return None
    if any(c not in _ALLOWED_VERB_CHARS for c in verb):
        return None
    return verb


def _synthetic_raw(channel: int, now_ms: int) -> int:
    """채널마다 위상이 다른 사인파. 4~20 mA 범위를 오간다."""
    phase = now_ms / 5000.0 + channel * 0.7
    ma = 12.0 + 8.0 * math.sin(phase)             # 4 ~ 20 mA
    volts = ma / 1000.0 * 120.0                   # 션트 120 Ω
    return int(volts / 2.5 * ADS1256_FULL_SCALE)
