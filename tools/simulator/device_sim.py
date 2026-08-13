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
from tools.simulator.telemetry import ADS1256_FULL_SCALE, build_ain_record, render

#: 규격 §6 — 이 시간 안에 $HB 를 못 받으면 RUN 으로 내려간다.
HB_TIMEOUT_MS = 3000

#: 보드가 호스트에게 보내는 생존 신호 주기
HB_EMIT_PERIOD_MS = 1000

AIN_CHANNELS = 7


class Mode:
    CONFIG = "CONFIG"
    RUN = "RUN"


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
        self._boot_ms = 0

    # ------------------------------------------------------------------ 모드
    @property
    def mode(self) -> str:
        if self._last_hb_rx_ms is None:
            return Mode.RUN
        if self._now_ms - self._last_hb_rx_ms > HB_TIMEOUT_MS:
            return Mode.RUN
        return Mode.CONFIG

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
            return [self._sack(verb, "ERR", Reason.CHECKSUM)]

        handler = {
            "HB": self._on_hb,
            "ID": self._on_id,
            "STAT": self._on_stat,
            "CFG": self._on_cfg,
        }.get(cmd.verb)

        if handler is None:
            return [self._sack(cmd.verb, "ERR", Reason.UNKNOWN_KEY)]
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
                self.store.save()
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
