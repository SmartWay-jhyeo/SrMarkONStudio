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
from tools.simulator.config_store import I2C_PORTS, I2C_QUANTITIES, ConfigStore
from tools.simulator.telemetry import (
    ADS1256_FULL_SCALE,
    build_ain_record,
    build_din_record,
    build_i2c_record,
    render,
    synthetic_i2c_value,
)

#: 규격 §6 — 이 시간 안에 $HB 를 못 받으면 RUN 으로 내려간다.
HB_TIMEOUT_MS = 3000

#: 보드가 호스트에게 보내는 생존 신호 주기
HB_EMIT_PERIOD_MS = 1000

AIN_CHANNELS = 7

#: 디지털 입력 커넥터 (규격 §7.6, 데이터시트 §5.7). J18~J20 은 출력이 아니라
#: 입력이다 — 옵토커플러가 붙고 보드는 그 신호를 읽는다.
DIN_CONNECTORS: tuple[int, ...] = (18, 19, 20)

#: GNSS 데모가 "잠겼다"(gnss_pps)로 보이기까지 걸리는 시간(Phase 3).
#:
#: 🔴 실기기 콜드스타트 TTFF 를 흉내 낸 것이 **아니다** — `gnss.enabled` 를
#:    켜자마자 gnss_pps 로 뛰면 등급이 오르내리는 모습을 화면에서 한 번도
#:    못 본다. 그래서 일부러 넣은 시연용 지연이다(사용자 지시 —
#:    "가짜 fix 를 진짜처럼 내지 마라"). 이 시간·이후의 위성 수·pps_age
#:    는 전부 고정값/톱니 패턴이지 실제 수신기의 흔들림이 아니다.
GNSS_DEMO_WARMUP_MS = 5000

#: 🔴 [판단, 2026-08-19] `gnss.enabled` 가 켜지는 순간(NMEA 워밍업 단계부터
#:    — 규격 §7.1.2 상 device_clock 이 아니면 이미 `t` 는 UTC 다) `t` 를
#:    무엇에 잇는가에 쓰는 고정 anchor.
#:
#:    시뮬레이터는 시각을 스스로 읽지 않는다(클래스 docstring — 결정성을
#:    위해 `tick(now_ms)` 로만 받는다). 실제 벽시계(`time.time()`)를 쓰면
#:    같은 시나리오가 실행할 때마다 다른 `t` 를 내 시험을 비결정적으로
#:    만든다 — 그래서 진짜 UTC 를 흉내 내지 않고, 이미 이 파일이 쓰는
#:    "시뮬레이터임이 값에서도 드러나야 한다"는 원칙(GNSS_DEMO_WARMUP_MS
#:    주석)을 그대로 따라 고정된 상수 하나에 anchor 한다. 값 자체는
#:    firmware/stage1/tests/test_timeax.c 의 시험 fixture(1700000000000)와
#:    자릿수만 맞춘 임의의 수다 — 실제 어느 시각도 가리키지 않는다.
GNSS_DEMO_EPOCH_MS = 1_700_000_000_000

#: 🔴 시뮬레이터에는 옵토가 없다 — 실기기는 사람이 신호선을 흔들어야
#:    상태가 바뀐다. 이 주기는 화면 확인용 **데모**일 뿐이다. `ain` 처럼
#:    매 주기 값을 내면 규격 §7.6 이 금지하는 "주기 송신"이 돼 버리므로,
#:    아주 드물게만(몇 초에 한 번) 뒤집는다. 커넥터마다 위상을 다르게 둬
#:    셋이 한꺼번에 바뀌지 않게 한다(_emit_din 참조).
DIN_DEMO_PERIOD_MS = 9000


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

        # 🔴 [2026-08-19] 수집과 송신을 뗀다(사용자 설계) — 펌웨어
        #    mk_ads1256.c 의 MkAdsChannel.last · mk_i2c.c 의 last[][] 와 같은
        #    자리다. `tick()` 이 매번(= tx.period_ms 와 무관하게) 채널·포트
        #    주기(ain{ch}.period_ms · i2cN.period_ms)로 여기를 덮어쓰고,
        #    송신은 tx.period_ms 마다 이 값만 읽는다 — 새 수집이 없으면
        #    같은 값(과 같은 t_ms)이 반복해서 나간다.
        #: 채널별 마지막 표본 (t_ms, raw). 아직 없으면 키가 없다.
        self._last_ain: dict[int, tuple[int, int]] = {}
        #: 채널별 마지막 수집 시각 — ain{ch}.period_ms 게이트.
        self._last_ain_collect_ms: dict[int, int] = {}

        #: I2C 는 포트마다 주기가 따로다 — 마지막 **수집** 시각을 각각 센다
        #: (송신 시각이 아니다. i2cN.period_ms 게이트).
        self._last_i2c_ms: dict[int, int] = {}
        #: 포트별 마지막 값들 — {quantity: value}. 없으면 키가 없다.
        self._last_i2c_values: dict[int, dict[str, float]] = {}
        #: 포트별 마지막 수집 시각(=레코드의 t).
        self._last_i2c_t: dict[int, int] = {}
        #: 포트별로 마지막에 물려 있던 kind. 바뀌면 마지막 값을 비운다 —
        #: 옛 종류가 내던 양을 새 종류인 척 계속 내보내지 않기 위해서다
        #: (mk_i2c.c 의 clear_last 와 같은 이유).
        self._last_i2c_kind: dict[int, int] = {}
        #: 디지털 입력 지금 상태 (규격 §7.6). `$STAT` 이 이걸 그대로 싣는다.
        self._din_state: dict[int, int] = dict.fromkeys(DIN_CONNECTORS, 0)
        #: 데모 토글이 마지막으로 넘긴 구간 번호. 커넥터마다 위상이 달라
        #: 따로 센다 — DIN_DEMO_PERIOD_MS 주석·`_emit_din` 참조.
        self._din_slot: dict[int, int] = dict.fromkeys(DIN_CONNECTORS, 0)
        #: GNSS 데모가 `gnss.enabled` 를 언제부터 켠 채였는지(Phase 3).
        #: None 이면 꺼져 있다 — `_gnss_time_state()` 가 이 값으로
        #: 워밍업 경과를 잰다.
        self._gnss_enabled_at_ms: int | None = None
        #: `t` 변환의 anchor 쌍(장치 ms <-> "UTC" ms). `gnss.enabled` 가
        #: 켜지는 순간 한 번 세운다 — `_convert_t()` 참고. 꺼지면 비운다.
        self._gnss_anchor_dev_ms: int | None = None
        self._gnss_anchor_epoch_ms: int | None = None
        #: 역행 방지(`_convert_t()` 전용) — mk_timeax_now_ms_monotonic()
        #: 과 같은 이유·같은 방식(firmware/stage1/app/mk_timeax.c 참고).
        #: **전진 방향 질의**(raw t_ms 가 지금까지 본 것 이상)에서 마지막
        #: 으로 낸 값·그 raw t_ms 를 기억해 두고 작아지면 최소 폭만 민다.
        #: 더 느린 채널·포트의 오래된(작은) raw t_ms 로 온 질의는 이 값을
        #: 건드리지 않는다 — _convert_t() 의 되돌림 검사 주석 참고.
        self._last_reported_t_ms: int | None = None
        self._last_reported_raw_ms: int | None = None
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
        # 🔴 `t` 의 기준점을 호스트가 알 수 있게 여기서 알려준다.
        # 규격 §7.1.2 대로 `t` 는 time_source 에 따라 UTC epoch 이거나
        # 부팅 후 경과 ms 다. 텔레메트리는 필드 마스크로 time_source 를
        # 실어 보낼 수 있지만 명령 응답에는 그 자리가 없다. $STAT 이
        # 그 답을 주는 곳이다 — 호스트는 연결 직후 한 번 물어보면 된다.
        time_source, time_quality, pps_age_ms, sats = self._gnss_time_state(self._now_ms)
        return [
            self._json(
                type="stat",
                mode=self.mode,
                ctl_mode=self.ctl_mode,
                fw=self.fw,
                board_rev=self.board_rev,
                time_source=time_source,
                time_quality=time_quality,
                # 🔴 펌웨어(mk_cfgwire_stat)와 같은 모양 — 중첩 객체,
                #    모를 때는 null(0 을 지어내지 않는다).
                gnss={"pps_age_ms": pps_age_ms, "sats": sats},
                uptime_ms=self._now_ms - self._boot_ms,
                rails={
                    "v24": self.store.get("pwr.24v"),
                    "v14v9": self.store.get("pwr.14v9"),
                    "v5": self.store.get("pwr.5v"),
                },
                # 🔴 din 은 `rails` 와 달리 실측이다 — 엣지가 안 오는 동안에도
                #    화면이 지금 상태를 말할 수 있도록 여기서 채운다(규격 §7.6).
                din=[{"connector_id": c, "state": self._din_state[c]}
                     for c in DIN_CONNECTORS],
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
    def _gnss_time_state(self, now_ms: int) -> tuple[str, int, int | None, int | None]:
        """GNSS/PPS 시간축 등급을 흉내 낸다(Phase 3).

        반환: (time_source, time_quality, pps_age_ms, sats). pps_age_ms·
        sats 는 모를 때 None이다(0을 지어내지 않는다 — 설계 원칙 3·4).

        🔴 `GNSS_DEMO_WARMUP_MS` 주석 참고 — 시뮬레이터임이 값에서도
           드러나야 한다는 지시에 따라 고정된 진행만 흉내 낸다."""
        if not self.store.get("gnss.enabled"):
            return "device_clock", 0, None, None

        since = self._gnss_enabled_at_ms
        if since is None or now_ms - since < GNSS_DEMO_WARMUP_MS:
            # 켜는 중 — NMEA 문장은 오는데 아직 PPS 짝을 못 지은 상태로
            # 흉내 낸다.
            return "gnss_nmea", 1, None, 4

        # 🔴 1 Hz PPS 톱니를 흉내 낸다 — 실제 지터가 아니다.
        pps_age_ms = now_ms % 1000
        return "gnss_pps", 2, pps_age_ms, 9

    def _convert_t(self, t_ms: int, time_source: str) -> int:
        """획득 시각(t_ms, 부팅 후 경과 ms)을 등급에 따라 바꾼다.

        규격 §7.1.2 — device_clock 이면 그대로, gnss_* 면 anchor 로부터
        선형 변환한 "UTC" 값(GNSS_DEMO_EPOCH_MS 참고 — 데모용 고정값이라
        진짜 UTC 는 아니다). 어느 쪽이든 **뒤로 가지 않는다** — 등급이
        떨어지면(gnss_* -> device_clock) 변환 없는 원래 값이 방금 전 UTC
        값보다 훨씬 작아지므로, 마지막으로 낸 값보다 작아지면 최소 폭(+1ms)
        만 밀어 올린다(firmware/stage1/app/mk_timeax.c 의
        mk_timeax_now_ms_monotonic 과 같은 이유·같은 방식 — 카메라 프레임
        정렬·저장 데이터의 시간순 정렬이 뒤집히면 안 된다).

        🔴 [되돌림으로 찾은 회귀 — 2026-08-19] 처음에는 "마지막으로 낸
        값보다 작으면 무조건 민다"였다. ain·i2c·din 이 raw t_ms 순서와
        무관하게 한 DeviceSim 을 공유하고 채널·포트마다 수집 주기가 다르므로,
        수집이 빠른 채널(예: ain, 기본 100ms)이 큰 t_ms 로 먼저 나간 직후
        수집이 느린 포트(예: i2c, 최대 60000ms)가 **옛(작은) t_ms 표본을
        그대로 반복**하면 "더 작다"는 이유만으로 밀어 올려 버렸다 — 반복되는
        같은 표본의 t 가 호출될 때마다 달라지는 새 버그였다(device_clock
        상태에서도, 즉 등급 전환과 무관하게 벌어졌다).
        host/tests/test_device_sim.py 의
        test_i2c_repeats_last_value_and_never_touched_ports_stay_silent 가
        이것을 잡았다.

        그래서 지금은 **raw t_ms 가 지금까지 본 것보다 작은 질의**(다른
        채널의 더 오래된 표본)는 클램프하지 않는다 — "다른 채널의 과거
        시각"과 "같은 시간축이 진짜로 뒤로 간 것"을 raw t_ms 순서로
        구분한다. firmware/stage1/app/mk_timeax.c 의
        mk_timeax_now_ms_monotonic 과 같은 수정.

        🔴 [남는 위험, 해결 안 함] GNSS 가 내려간 뒤 오랫동안 재수집을 안
        한 느린 채널이 그 옛 표본을 반복하다가, 다른 채널의 raw t_ms 가
        그것을 앞지르는 순간부터는 클램프 밖(과거 질의)으로 빠져 그
        채널만 한 번 작은 값으로 보일 수 있다. 모든 채널·포트가 각자
        수집 시각에 변환값을 실어 저장하는 것이 완전한 해법이지만 이번
        범위 밖이다 — 시뮬레이터의 채널 주기가 전부 초 단위 미만이라
        실제로 마주칠 가능성은 낮다고 판단했다.
        """
        if time_source == "device_clock" or self._gnss_anchor_epoch_ms is None:
            converted = t_ms
        else:
            converted = (self._gnss_anchor_epoch_ms
                         + (t_ms - self._gnss_anchor_dev_ms))

        if (self._last_reported_raw_ms is not None
                and t_ms < self._last_reported_raw_ms):
            # 다른(더 느린) 채널·포트의 오래된 표본이다 — 그대로 낸다.
            return converted

        if (self._last_reported_t_ms is not None
                and converted < self._last_reported_t_ms):
            converted = self._last_reported_t_ms + 1
        self._last_reported_t_ms = converted
        self._last_reported_raw_ms = t_ms
        return converted

    def tick(self, now_ms: int) -> list[str]:
        """시각을 진행시키고 이번에 내보낼 줄들을 반환한다."""
        self._now_ms = now_ms
        out: list[str] = []

        # 🔴 켜고 끄는 시점을 여기서 잡는다 — `_gnss_time_state()` 는
        #    순수 조회라 상태를 바꾸지 않는다.
        if self.store.get("gnss.enabled"):
            if self._gnss_enabled_at_ms is None:
                self._gnss_enabled_at_ms = now_ms
                # 🔴 이 순간부터 t 가 UTC 로 바뀐다(gnss_nmea 워밍업 단계도
                #    포함 — 규격 §7.1.2 는 device_clock 만 아니면 UTC 라고
                #    한다). anchor 를 여기서 세워야 워밍업 중에 수집된
                #    표본도 곧바로 변환된다.
                self._gnss_anchor_dev_ms = now_ms
                self._gnss_anchor_epoch_ms = GNSS_DEMO_EPOCH_MS
        else:
            self._gnss_enabled_at_ms = None
            self._gnss_anchor_dev_ms = None
            self._gnss_anchor_epoch_ms = None

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

        # 🔴 수집은 송신 주기와 무관하게 매번 돈다 — "변수 a 에 계속 넣는다"
        #    (사용자 설계 2026-08-19). 각자의 채널·포트 주기로 게이트한다.
        self._collect_ain(now_ms)
        self._collect_i2c(now_ms)

        period = int(self.store.get("tx.period_ms"))
        if now_ms - self._last_emit_ms >= period:
            self._last_emit_ms = now_ms
            out.extend(self._emit_telemetry(now_ms))

        return out

    def _collect_ain(self, now_ms: int) -> None:
        """ain{ch}.period_ms 마다 마지막 표본을 덮어쓴다 (수집).

        🔴 꺼진 채널은 여기서 손대지 않는다 — 값이 있었다면 그대로 남는다.
           "보낼지 말지"는 `_emit_telemetry`가 `enabled`를 다시 확인해서
           정한다(펌웨어 mk_telem.c 와 같다: has_last 와 enabled 는 별개
           게이트다).
        """
        for ch in range(AIN_CHANNELS):
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            period = int(self.store.get(f"ain{ch}.period_ms")) or 1
            last = self._last_ain_collect_ms.get(ch, -period)
            if now_ms - last < period:
                continue
            self._last_ain_collect_ms[ch] = now_ms
            self._last_ain[ch] = (now_ms, _synthetic_raw(ch, now_ms))

    def _collect_i2c(self, now_ms: int) -> None:
        """i2cN.period_ms 마다 마지막 값들을 덮어쓴다 (수집).

        🔴 껐거나 지원 안 하는(양이 없는) 종류, 또는 종류가 막 바뀐
           포트는 마지막 값을 **비운다** — 옛 종류가 내던 양을 새 종류인
           척 계속 내보내면 안 된다(mk_i2c.c 의 clear_last 와 같은 이유).
        """
        for cid in I2C_PORTS:
            enabled = bool(self.store.get(f"i2c{cid}.enabled"))
            kind = int(self.store.get(f"i2c{cid}.kind"))
            quantities = I2C_QUANTITIES.get(kind, ()) if enabled else ()

            if not quantities:
                self._last_i2c_t.pop(cid, None)
                self._last_i2c_values.pop(cid, None)
                self._last_i2c_kind.pop(cid, None)
                continue

            if self._last_i2c_kind.get(cid) != kind:
                self._last_i2c_t.pop(cid, None)
                self._last_i2c_values.pop(cid, None)
                self._last_i2c_kind[cid] = kind

            period = int(self.store.get(f"i2c{cid}.period_ms")) or 1
            last = self._last_i2c_ms.get(cid, -period)
            if now_ms - last < period:
                continue
            self._last_i2c_ms[cid] = now_ms
            self._last_i2c_t[cid] = now_ms
            self._last_i2c_values[cid] = {
                q: synthetic_i2c_value(cid, q, now_ms) for q in quantities
            }

    def _emit_telemetry(self, now_ms: int) -> list[str]:
        lines: list[str] = []
        time_source, time_quality, _age, _sats = self._gnss_time_state(now_ms)
        for ch in range(AIN_CHANNELS):
            # 🔴 [2026-08-19] "지금 값을 만든다"가 아니라 "마지막 값을
            #    읽는다"다 — 새 수집이 없었으면 같은 raw·t_ms 가 그대로
            #    반복된다. `t` 는 수집 시각(t_ms)이지 지금(now_ms)이 아니다.
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            sample = self._last_ain.get(ch)
            if sample is None:
                continue                      # 한 번도 수집 안 됐다 — 지어내지 않는다
            t_ms, raw = sample
            self._seq += 1
            lines.append(
                render(
                    build_ain_record(
                        self.store,
                        channel=ch,
                        seq=self._seq,
                        # 🔴 t 는 획득 시각(t_ms)을 시간축으로 바꾼 값 —
                        #    capture_counter 는 그대로 원시 t_ms 다(펌웨어
                        #    mk_telem.c 와 같은 구분 — 변환 없는 원시
                        #    카운터는 따로 남긴다).
                        t_ms=self._convert_t(t_ms, time_source),
                        raw=raw,
                        capture_counter=t_ms * 1000,
                        time_source=time_source,
                        time_quality=time_quality,
                    )
                )
            )
        lines += self._emit_i2c(now_ms)
        lines += self._emit_din(now_ms)
        return lines

    def _emit_din(self, now_ms: int) -> list[str]:
        """규격 §7.6 — 상태가 바뀔 때만 한 줄 낸다.

        🔴 시뮬레이터에는 옵토가 없다. 이 토글은 화면 확인용 **데모**다 —
           `DIN_DEMO_PERIOD_MS` 주석 참조. 실기기에서는 사람이 신호선을
           흔들어야 상태가 바뀐다.

        커넥터마다 자기 구간 번호가 바뀔 때만 뒤집는다. 같은 구간 안에서는
        몇 번을 불러도 아무것도 내지 않는다 — "주기 송신이 아니다"(규격
        §7.6)를 시뮬레이터도 지킨다.
        """
        lines: list[str] = []
        time_source, time_quality, _age, _sats = self._gnss_time_state(now_ms)
        slot_period = DIN_DEMO_PERIOD_MS // len(DIN_CONNECTORS)
        for idx, cid in enumerate(DIN_CONNECTORS):
            phase_ms = idx * slot_period
            slot = (now_ms + phase_ms) // DIN_DEMO_PERIOD_MS
            if slot == self._din_slot[cid]:
                continue
            self._din_slot[cid] = slot
            self._din_state[cid] ^= 1
            self._seq += 1
            lines.append(render(build_din_record(
                self.store, connector_id=cid, state=self._din_state[cid],
                seq=self._seq, t_ms=self._convert_t(now_ms, time_source),
                time_source=time_source, time_quality=time_quality,
            )))
        return lines

    def _emit_i2c(self, now_ms: int) -> list[str]:
        """규격 §7.5 — tx.period_ms 마다 **마지막 값**을 낸다(수집·송신
        분리, 사용자 설계 2026-08-19). 값 자체는 `_collect_i2c` 가
        `i2cN.period_ms` 마다 미리 만들어 둔다 — 여기서는 그것을 읽기만
        한다. 한 번도 수집되지 않은(꺼졌거나 미지원 종류인) 포트는
        `_last_i2c_t` 에 자리가 없으므로 조용히 건너뛴다(설계 원칙 3).
        """
        lines: list[str] = []
        time_source, time_quality, _age, _sats = self._gnss_time_state(now_ms)
        for cid in I2C_PORTS:
            if cid not in self._last_i2c_t:
                continue
            t_ms = self._last_i2c_t[cid]
            # 🔴 포트 하나에 여러 양(quantity)이 같은 t_ms 에서 나올 수
            #    있다(예: 온습도) — 변환은 포트당 한 번만 한다. 양마다
            #    따로 부르면 역행 방지 보정이 첫 양에서 이미 last_reported
            #    를 밀어 올린 뒤라, 같은 순간의 두 번째 양이 다른 t 를 받는
            #    이상한 결과가 나온다.
            converted_t = self._convert_t(t_ms, time_source)
            for quantity, value in self._last_i2c_values[cid].items():
                self._seq += 1
                lines.append(render(build_i2c_record(
                    self.store, connector_id=cid, quantity=quantity,
                    seq=self._seq, t_ms=converted_t, value=value,
                    time_source=time_source, time_quality=time_quality,
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
