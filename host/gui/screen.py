"""화면이 보여야 할 것 전부 — 한 객체로.

🔴 이 층이 있는 이유: **배치를 바꿀 때 데이터 배선을 다시 짜지 않기 위해.**

   그전에는 뷰끼리 서로를 알았다:

       self._rail.set_rails(self._dashboard.rail_states(...))
       form = getattr(self._settings, "_form", None)   # 남의 private

   레일 값을 얻으려고 대시보드를 거치고, 설정 화면의 내부를 뒤졌다. 이러면
   레일을 왼쪽에서 위로 옮기는 것 같은 **배치 변경**이 데이터 흐름 변경이
   된다. 화면을 다시 그리려면 배선을 처음부터 다시 해야 한다.

   이제 순서가 이렇다:

       보드 응답 ──> build_screen() ──> ScreenState ──> 뷰들이 각자 render()

   뷰는 `ScreenState` 만 안다. 서로를 모르고, 서비스도 모르고, 워커도
   모른다. 배치를 바꾸는 것은 뷰를 새로 쓰고 `render` 를 부르는 일이
   전부가 된다.

🔴 이 파일은 **Qt 를 import 하지 않는다.** `host/gui/widgets` · `theme` 와
   같은 규칙이다(CLAUDE.md §0). 화면이 무엇을 보여야 하는지는 디스플레이
   없이 시험할 수 있어야 하고, 실제로 그렇게 시험한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from host.gui.last_known import StateHistory, build_chip_state
from host.gui.widgets.loop_gauge import LOOP_MAX_MA, LOOP_MIN_MA
from host.gui.widgets.status_chip import Level, Verification, rail_label

#: AIN0 이 J3 이다 (데이터시트 §5.3).
CONNECTOR_OFFSET = 3
AIN_COUNT = 7

#: 채널마다 들고 있는 이력 표본 수.
#:
#: 🔴 무한히 자라면 안 된다 — 벤치에 하루 켜 두면 그만큼 먹는다.
#:    600 은 100 ms 주기에서 1 분이다. 카드의 트레이스 폭이 300 px 남짓이라
#:    그보다 촘촘해져도 화면에서 구분되지 않는다.
TRACE_LEN = 600

#: 전원 레일. 순서는 **기동 순서**다 — 5V 가 먼저 올라가고 24V 가 마지막이다.
#: 화면도 그 순서로 늘어놓아야 사용자가 순차 기동을 눈으로 따라갈 수 있다.
RAILS: tuple[tuple[str, str], ...] = (
    ("pwr.5v", "5V"),
    ("pwr.14v9", "14.9V"),
    ("pwr.24v", "24V"),
)


@dataclass(frozen=True)
class Identity:
    """지금 보고 있는 보드가 무엇인가.

    🔴 벤치에서 여러 보드를 옮겨 다닌다. 헷갈리면 24V 를 엉뚱한 보드에 켠다.
    """

    port: str = ""
    device_id: str = ""
    fw: str = ""
    board_rev: str = ""


@dataclass(frozen=True)
class Link:
    """링크 한 줄. `bad` 면 화면이 경고색으로 그린다."""

    text: str = ""
    bad: bool = False


@dataclass(frozen=True)
class RailState:
    """레일 하나의 **명령 상태**.

    🔴 `commanded` 가 `None` 이면 "모른다" 이지 "꺼짐" 이 아니다. 연결이
       끊긴 동안 마지막 값을 켜진 것처럼 보여 주면 화면이 거짓말을 한다.

    🔴 실측이 아니다. 피드백 회로가 없으므로 보드도 실제 전압을 모른다
       (설계 원칙 4). 뷰는 이것을 `정상 ON` 이 아니라 `ON 명령됨` 으로
       그려야 한다.
    """

    key: str
    label: str
    commanded: bool | None = None
    last_known: str = ""


@dataclass(frozen=True)
class ChannelState:
    """채널 하나."""

    index: int
    connector: str
    ma: float | None = None
    level: Level = Level.IDLE
    verification: Verification = Verification.UNKNOWN
    value: float | None = None
    unit: str = ""
    note: str = ""

    trace: tuple[float | None, ...] = ()
    """최근 이력. 오래된 것이 앞, 방금 것이 뒤. **레코드 하나가 표본 하나다.**

    🔴 `None` 은 **그때 선이 끊겼다** 는 뜻이다(연결 끊김). 직전 값을 복사해
       채우지 않는다 — 선은 매끈해지지만 화면이 오지 않은 값을 온 것처럼
       그리게 된다.

    🔴 채널마다 길이가 다를 수 있다. 수집 주기가 채널마다 따로이기 때문이다
       (`ainN.period_ms`). 길이를 억지로 맞추려고 조용한 채널에 구멍을
       채우면 **오지도 않은 시각**을 화면이 지어내게 된다.
    """

    span: tuple[float, float] | None = None
    """이 채널 센서의 물리량 범위 — (4 mA 일 때, 20 mA 일 때).

    🔴 **설정이지 텔레메트리가 아니다.** 사용자가 정한 것이고, 값이 잠깐
       끊겼다고 사라지지 않는다. 게이지가 `0 – 150 bar` 눈금을 그리는 근거다.
    """

    trace_t: tuple[int | None, ...] = ()
    """`trace` 와 같은 길이의, 보드가 찍은 시각(ms).

    🔴 화면이 시각을 **지어내지 않는다**(설계 원칙 2). 주기를 100 ms 로
       가정해 인덱스에 곱하면 주기를 바꾸는 순간 화면이 조용히 틀린 시각을
       말한다. 규격 §7.1 에 따라 `t` 는 마스크와 무관하게 항상 실리므로
       가정할 이유가 없다.

    🔴 공유 크로스헤어가 이 위에 서 있다. 일곱 계기를 같은 순간으로
       돌려세우는 기준은 인덱스가 아니라 **이 시각**이다.
    """


@dataclass(frozen=True)
class Collection:
    """수집이 지금 어떻게 되고 있나 — 카드 일곱 장을 한 줄로 요약한 것.

    🔴 여기 있는 수는 전부 **온 것에서** 센다. 설정값은 요청이고 이것은
       실제다. 둘이 다르면 그 차이가 곧 사용자가 알아야 할 사실이다.
    """

    total: int = AIN_COUNT
    live: int = 0
    broken: int = 0
    over: int = 0
    interval_ms: float | None = None


def summarize(channels: tuple[ChannelState, ...]) -> Collection:
    """채널들을 한 줄 요약으로.

    🔴 값이 안 오는 채널을 고장으로 세지 않는다 (설계 원칙 3 — 센서 미연결은
       정상 상태다). 비활성 커넥터를 고장으로 세면 화면이 늘 빨갛고,
       그러면 진짜 고장이 묻힌다.
    """
    live = broken = over = 0
    gaps: list[int] = []
    for ch in channels:
        if ch.ma is None:
            continue
        live += 1
        if ch.ma < LOOP_MIN_MA:
            broken += 1
        elif ch.ma > LOOP_MAX_MA:
            over += 1
        stamps = [t for t in ch.trace_t if t is not None]
        gaps += [b - a for a, b in zip(stamps, stamps[1:]) if b > a]

    gaps.sort()
    #: 중앙값을 쓴다 — 한 번 늦은 것이 평균을 끌고 가면 안 된다.
    interval = float(gaps[len(gaps) // 2]) if gaps else None
    return Collection(total=len(channels) or AIN_COUNT, live=live,
                      broken=broken, over=over, interval_ms=interval)


@dataclass(frozen=True)
class ScreenState:
    """화면 전체. 뷰는 이것만 받는다."""

    identity: Identity = field(default_factory=Identity)
    mode: str = ""
    ctl_mode: str = "ACTIVE"
    """제어 모드 (규격 §6.4). `mode` 와 **다른 축**이다.

    🔴 화면이 이것을 눈에 띄게 말해야 한다. 테스트인 줄 알고 공정을 돌리거나
       운전 중인 줄 모르고 밸브를 흔드는 것이 이 시스템에서 가장 나쁜 사고다.
    """
    link: Link = field(default_factory=Link)
    reachable: bool = False
    rails: tuple[RailState, ...] = ()
    channels: tuple[ChannelState, ...] = ()
    #: I2C 센서 카드 (규격 §7.5). 아날로그와 따로 두는 이유는 정체가
    #: 번호가 아니라 (커넥터, 양) 짝이기 때문이다 — `SensorState` 머리말.
    sensors: tuple[SensorState, ...] = ()
    #: 디지털 입력 카드 J18~J20 (규격 §7.6). `DinState` 머리말 참조.
    dins: tuple[DinState, ...] = ()
    #: GNSS 측위 구획 J16 (규격 §7.8). 카드 여럿이 아니라 하나인 이유는
    #: `GnssState` 머리말 참조 — 위치는 값들이 한 덩어리로만 뜻이 있다.
    gnss: "GnssState" = field(default_factory=lambda: GnssState())

    def channel(self, index: int) -> ChannelState | None:
        for ch in self.channels:
            if ch.index == index:
                return ch
        return None


def empty_channels() -> tuple[ChannelState, ...]:
    return tuple(
        ChannelState(i, f"J{i + CONNECTOR_OFFSET}") for i in range(AIN_COUNT)
    )


def build_rails(values: dict[str, bool], *, reachable: bool,
                history: StateHistory, now_s: float) -> tuple[RailState, ...]:
    """설정값을 레일 상태로. 마지막으로 알던 값을 함께 싣는다.

    🔴 `history` 를 여기서 쓰는 이유: 연결이 끊겼을 때 "24V 가 켜져 있었다"
       를 화면이 계속 말해야 한다. 확인이 끊겼다고 문제가 사라지지 않는다.
    """
    out: list[RailState] = []
    for key, label in RAILS:
        on = bool(values.get(key, False))
        if reachable:
            verification = Verification.COMMANDED
            level = Level.OK if on else Level.IDLE
        else:
            verification = Verification.UNKNOWN
            level = Level.IDLE
        chip = build_chip_state(
            history, key, label, level, verification, now_s,
            detail=rail_label(on, verification),
        )
        out.append(RailState(
            key=key,
            label=label,
            commanded=on if reachable else None,
            last_known=chip.last_known.text or "",
        ))
    return tuple(out)


def build_screen(previous: ScreenState, *, identity: Identity, mode: str,
                 error, rail_values: dict[str, bool], records,
                 history: StateHistory, ctl_mode: str = "ACTIVE",
                 ranges: dict[int, tuple[float, float]] | None = None,
                 units: dict[int, str] | None = None,
                 i2c_ports: dict[int, tuple[int, bool]] | None = None,
                 now_s: float | None = None) -> ScreenState:
    """워커 결과 하나를 **다음 화면 상태**로.

    🔴 화면이 무엇을 보여야 하는지가 전부 이 함수 안에 있다. Qt 가 없으므로
       디스플레이 없이 시험할 수 있고, 배치를 바꿔도 여기는 그대로다.

    `previous` 를 받는 이유는 **채널 장애 격리** 때문이다 — 이번에 레코드가
    오지 않은 채널은 지난 값을 그대로 둔다. 지우면 센서 하나가 조용할 때
    화면이 통째로 비는 것처럼 보인다.
    """
    import time as _time

    now = _time.monotonic() if now_s is None else now_s
    reachable = error is None
    link = (Link(f"통신 오류: {error}", bad=True) if error
            else Link(f"{identity.port} · 연결됨"))
    return ScreenState(
        identity=identity,
        mode=mode,
        ctl_mode=ctl_mode,
        link=link,
        reachable=reachable,
        rails=build_rails(rail_values, reachable=reachable,
                          history=history, now_s=now),
        channels=build_channels(records, reachable=reachable,
                                previous=previous.channels, ranges=ranges,
                                units=units),
        sensors=build_sensors(records, reachable=reachable,
                              previous=previous.sensors, ports=i2c_ports),
        dins=build_dins(records, reachable=reachable,
                        previous=previous.dins, history=history, now_s=now),
        gnss=build_gnss(records, reachable=reachable,
                        previous=previous.gnss, history=history, now_s=now),
    )


def build_channels(records, *, reachable: bool,
                   previous: tuple[ChannelState, ...] = (),
                   ranges: dict[int, tuple[float, float]] | None = None,
                   units: dict[int, str] | None = None,
                   ) -> tuple[ChannelState, ...]:
    """텔레메트리 레코드를 채널 상태로.

    🔴 채널 장애 격리 — 레코드가 오지 않은 채널은 **건드리지 않는다.**
       한 채널이 조용하다고 나머지를 지우면, 센서 하나가 빠졌을 때 화면이
       통째로 비는 것처럼 보인다.

    🔴 연결이 끊기면 전부 모르는 상태로 되돌린다. 마지막 값을 계속 띄우면
       그것이 지금 값으로 읽힌다.
    """
    base = {ch.index: ch for ch in (previous or empty_channels())}
    # 🔴 범위는 설정이라 레코드와 무관하게 매번 다시 얹는다. 값이 안 온
    #    채널도, 연결이 끊긴 동안에도 눈금은 그대로여야 한다.
    spans = ranges or {}
    labels = units or {}
    for i in base:
        base[i] = replace(base[i], span=spans.get(i),
                          unit=labels.get(i, base[i].unit))

    if not reachable:
        # 🔴 현재값은 지우고 **이력은 남긴다.** 현재값을 계속 띄우면 지금
        #    값으로 읽히지만, 이력은 과거라 남겨도 거짓말이 아니다 —
        #    오히려 "끊기기 직전에 이랬다" 가 필요한 정보다.
        return tuple(
            ChannelState(i, f"J{i + CONNECTOR_OFFSET}",
                         span=base[i].span,
                         trace=_gap(base[i].trace),
                         trace_t=_gap_t(base[i].trace, base[i].trace_t))
            for i in sorted(base)
        )

    for rec in records or ():
        if not isinstance(rec, dict) or rec.get("type") != "ain":
            continue
        cid = rec.get("connector_id")
        if not isinstance(cid, int):
            continue
        index = cid - CONNECTOR_OFFSET
        if index not in base:
            continue
        ma = rec.get("ma")
        ma = float(ma) if isinstance(ma, (int, float)) else None
        value = rec.get("value")
        stamp = rec.get("t")
        # 🔴 **레코드 하나가 표본 하나다.** 한 스텝에 두 개가 오면 둘 다
        #    쌓는다. 스텝당 하나만 담았더니, 워커 주기(100 ms)와 텔레메트리
        #    주기(100 ms)가 겹치는 스텝에서 절반이 조용히 버려졌고 화면은
        #    보드가 실제보다 느린 것처럼 그렸다.
        #
        #    채널마다 길이가 달라져도 상관없다 — 시각을 맞추는 일은
        #    인덱스가 아니라 보드가 찍은 `t` 가 한다.
        base[index] = replace(
            base[index],
            ma=ma,
            level=Level.OK if not rec.get("status", 0) else Level.WARN,
            verification=Verification.VERIFIED,
            value=float(value) if isinstance(value, (int, float)) else None,
            # 🔴 레코드가 단위를 안 실었으면 **덮지 않는다.** 기본 마스크에서
            #    꺼져 있는 필드라(규격 §7.2), 덮으면 설정에서 채워 둔 단위를
            #    매 레코드마다 지우게 된다.
            unit=(str(rec["unit"]) if "unit" in rec
                  else base[index].unit),
            trace=_push(base[index].trace, ma),
            trace_t=_push(base[index].trace_t,
                          stamp if isinstance(stamp, int) else None),
        )

    return tuple(base[i] for i in sorted(base))


# ---- I2C 센서 (규격 §7.5) ---------------------------------------------------

#: `quantity` 키 → 사람이 읽는 이름표.
#:
#: 🔴 고정 어휘다 (규격 §7.5.1). 설정 항목이 아니라 프로토콜이 정한 값이라
#:    호스트가 알고 있어도 된다 — `time_source` 의 `gnss`/`device_clock` 과
#:    같은 성격이다.
QUANTITY_LABELS = {
    "lux": "조도",
    "temp": "온도",
    "humidity": "습도",
    "temp_object": "대상 온도",
}

#: `quantity` 키 → 표준 단위 (규격 §7.5.1).
#:
#: 🔴 레코드의 `unit` 은 마스크로 꺼져 있는 것이 기본이다. 그런데 단위가
#:    없으면 온도와 습도가 화면에서 똑같아 보인다 — 둘 다 그냥 숫자다.
#:    규격이 양마다 단위를 정해 두었으므로 여기서 채운다. 지어내는 것이
#:    아니라 규격을 읽는 것이다.
QUANTITY_UNITS = {
    "lux": "lx",
    "temp": "°C",
    "humidity": "%RH",
    "temp_object": "°C",
}


#: I2C 센서 포트 (데이터시트 §5.4). J10~J15.
I2C_PORTS: tuple[int, ...] = (10, 11, 12, 13, 14, 15)

#: 카탈로그 `i2cN.kind` 의 "미지정" 값.
I2C_KIND_NONE = 0

#: 종류(카탈로그 `i2cN.kind`) → 그 종류가 내는 양들.
#:
#: 🔴 카탈로그 열거값의 뜻이다. 펌웨어 `mk_i2c.c` 의
#:    `mk_i2c_kind_quantities` 와 **순서·개수가 같아야 한다** — 값이 둘인
#:    종류는 온습도뿐이라는 것은 사용자 확정 2026-08-17. 어긋나면 대시보드가
#:    엉뚱한 수의 카드를 세운다.
#:
#: 🔴 [2026-08-20] 이 표를 자동으로 대조하는 자리가 **지금 없다.**
#:    예전에는 시뮬레이터의 같은 표와 맞춰 봤지만(`test_i2c_quantities_
#:    crosscheck.py`) 시뮬레이터를 지우면서 상대가 사라졌다. 종류를 늘릴
#:    때는 여기와 펌웨어를 사람이 함께 본다.
I2C_KIND_QUANTITIES: dict[int, tuple[str, ...]] = {
    I2C_KIND_NONE: (),
    1: ("lux",),
    2: ("temp", "humidity"),
    3: ("temp_object",),
    4: ("temp",),
}


def quantity_unit(quantity: str) -> str:
    """🔴 모르는 양의 단위는 비워 둔다. 아무 단위나 붙이면 화면이 틀린
       물리량을 말하게 되고, 그건 값이 없는 것보다 나쁘다."""
    return QUANTITY_UNITS.get(quantity, "")


def quantity_label(quantity: str) -> str:
    """🔴 모르는 양에 이름을 지어내지 않는다. 키를 그대로 보여 주면
       사용자가 펌웨어와 대조해 무엇이 어긋났는지 찾을 수 있다."""
    return QUANTITY_LABELS.get(quantity, quantity)


@dataclass(frozen=True)
class SensorState:
    """I2C 센서 카드 하나.

    🔴 정체가 **(커넥터, 양) 짝**이다. 아날로그처럼 번호 하나로 못 센다 —
       온습도 센서 하나가 `temp` 와 `humidity` 를 함께 내기 때문이다.
       커넥터만으로 묶으면 둘이 같은 카드에 덮어써져 값이 번갈아 튄다.
    """

    connector: str
    quantity: str
    label: str = ""
    value: float | None = None
    unit: str = ""
    status: int = 0

    enabled: bool = True
    """`i2cN.enabled` 설정. 아날로그 채널의 `enabled` 와 같은 자리다.

    🔴 `False` 면 보드가 애초에 레코드를 안 보낸다(규격 §7.5 — 미연결은
       정상 상태). 화면은 이것을 "값 없음" 이 아니라 "채널 꺼짐" 으로
       구분해 말해야 한다 — 켜면 잴 수 있다는 뜻이 다르기 때문이다
       (`host/gui/tare.py` 의 `blocked_reason` 과 같은 구분).
    """

    trace: tuple[float | None, ...] = ()
    trace_t: tuple[int | None, ...] = ()

    @property
    def no_kind(self) -> bool:
        """이 포트에 아직 종류를 고르지 않았다 (`i2cN.kind` = 없음).

        🔴 정체가 (커넥터, 양) 짝이므로, 종류가 없는 포트는 양도 없다 —
           `quantity` 가 빈 문자열이다. 고장도 지원 안 함도 아니고, 그냥
           아직 "무엇을 잴지" 가 정해지지 않은 자리다.
        """
        return self.quantity == ""

    @property
    def broken(self) -> bool:
        """센서가 대답을 안 하거나 값이 깨졌다.

        🔴 꺼진 포트는 여기 오지 않는다. 규격 §7.5 대로 아예 레코드를 안
           보내기 때문이다 — 미연결은 정상 상태다(설계 원칙 3).

        🔴 `status=3`(지원하지 않는 종류)은 고장이 아니다. 배선을 뜯을
           일이 아니라 펌웨어가 아직 그 칩을 모르는 것이다.
        """
        return self.status not in (0, 3)

    @property
    def unsupported(self) -> bool:
        """펌웨어에 이 종류의 드라이버가 없다 (규격 §7.5, status=3)."""
        return self.status == 3


def sensor_status_text(sensor: SensorState) -> str:
    """값 자리에 값 대신 무엇을 적을지. 값이 있으면 빈 문자열이다 — 그때는
    Qt 가 값을 그린다.

    🔴 새 낱말을 짓지 않는다. "채널 꺼짐" · "값 없음" 은 아날로그
       (`host/gui/tare.py` 의 `blocked_reason`)가 이미 쓰는 말이고,
       "지원 안 함" 은 이 파일이 이미 쓰던 말이다.

    순서가 뜻을 가진다 — 종류가 없으면 켜졌는지는 안 물어도 되고, 꺼져
    있으면 값이 왜 없는지는 안 물어도 된다.
    """
    if sensor.no_kind:
        return "종류 없음"
    if not sensor.enabled:
        return "채널 꺼짐"
    if sensor.value is None:
        return "지원 안 함" if sensor.unsupported else "값 없음"
    return ""


def seed_sensors(ports: dict[int, tuple[int, bool]]) -> tuple[SensorState, ...]:
    """설정에서 온 포트별 (종류, 사용 여부) 로 카드를 미리 깐다.

    🔴 아날로그의 `empty_channels()` 와 같은 이유다(설계 원칙 3 — 센서
       미연결은 정상 상태다). 레코드가 하나도 안 와도 여섯 자리가 화면에
       있어야, "안 꽂았다" 와 "설정을 아직 못 읽었다" 를 구분할 수 있다.

    🔴 `ports` 에 없는 포트는 종류 없음·꺼짐으로 본다 — 카탈로그를 아직
       못 읽었을 때(`ports={}`)도 여섯 자리는 그대로 있어야 한다.
    """
    out: list[SensorState] = []
    for port in I2C_PORTS:
        kind, enabled = ports.get(port, (I2C_KIND_NONE, False))
        connector = f"J{port}"
        quantities = I2C_KIND_QUANTITIES.get(kind, ())
        if not quantities:
            out.append(SensorState(connector=connector, quantity="",
                                   enabled=enabled))
        else:
            for q in quantities:
                out.append(SensorState(
                    connector=connector, quantity=q,
                    label=quantity_label(q), unit=quantity_unit(q),
                    enabled=enabled))
    return tuple(out)


def build_sensors(records, *, reachable: bool,
                  previous: tuple[SensorState, ...] = (),
                  ports: dict[int, tuple[int, bool]] | None = None,
                  ) -> tuple[SensorState, ...]:
    """`type` 이 `i2c` 인 레코드를 센서 카드로.

    🔴 순서를 지킨다. 처음 본 순서대로 두고 새로 온 것만 뒤에 붙인다 —
       매번 정렬하면 센서가 하나 늘거나 조용해질 때 카드가 자리를 바꾸고,
       값을 읽던 사람이 무엇을 보고 있었는지 잃는다.

    `ports` 는 `host/gui/settings_form.py` 의 `i2c_ports()` 가 뽑아 주는
    포트별 (종류, 사용 여부)다. **주지 않으면(`None`) 예전처럼 아무것도
    미리 깔지 않는다** — 기존 호출부(설정을 모르는 시험 등)를 깨지 않기
    위해서다. 화면(`build_screen`)은 항상 값을 준다.
    """
    order: list[tuple[str, str]] = []
    base: dict[tuple[str, str], SensorState] = {}

    seed_keys: set[tuple[str, str]] | None = None
    if ports is not None:
        for s in seed_sensors(ports):
            key = (s.connector, s.quantity)
            order.append(key)
            base[key] = s
        seed_keys = set(base)

    for s in previous:
        key = (s.connector, s.quantity)
        if seed_keys is not None:
            if key not in seed_keys:
                # 🔴 종류가 바뀌어 더 이상 유효하지 않은 카드다. 그대로
                #    남기면 보드가 다시는 안 낼 양이 화면에 계속 떠 있는다.
                continue
            # 시드는 기본값일 뿐이다 — 이력·실측은 이전 상태에서 이어
            # 받는다. `enabled` 만은 시드(=지금 설정)를 따른다.
            base[key] = replace(base[key], value=s.value, status=s.status,
                                unit=s.unit, trace=s.trace,
                                trace_t=s.trace_t)
            continue
        if key not in base:
            order.append(key)
        base[key] = s

    if not reachable:
        # 🔴 현재값은 지우고 이력은 남긴다 — build_channels 와 같은 이유다.
        return tuple(
            replace(base[k], value=None,
                    trace=_gap(base[k].trace),
                    trace_t=_gap_t(base[k].trace, base[k].trace_t))
            for k in order
        )

    for rec in records or ():
        if not isinstance(rec, dict) or rec.get("type") != "i2c":
            continue
        cid = rec.get("connector_id")
        quantity = rec.get("quantity")
        # 🔴 양이 없으면 무엇을 잰 값인지 알 수 없다. 지어내지 않고 버린다.
        if not isinstance(cid, int) or not isinstance(quantity, str) \
                or not quantity:
            continue

        key = (f"J{cid}", quantity)
        if key not in base:
            order.append(key)
            base[key] = SensorState(connector=key[0], quantity=quantity,
                                    label=quantity_label(quantity),
                                    unit=quantity_unit(quantity))

        raw = rec.get("value")
        value = float(raw) if isinstance(raw, (int, float)) else None
        status = rec.get("status")
        status = status if isinstance(status, int) else 0
        unit = rec.get("unit")

        prev = base[key]
        base[key] = replace(
            prev,
            value=value,
            status=status,
            unit=unit if isinstance(unit, str) else prev.unit,
            # 🔴 값이 없으면 이력에도 `None` 을 남긴다. 직전 값을 복사하면
            #    선이 매끈해지고, 화면이 오지 않은 값을 온 것처럼 그린다.
            trace=_push(prev.trace, value),
            trace_t=_push(prev.trace_t, rec.get("t")),
        )
    return tuple(base[k] for k in order)


def _push(seq: tuple, value) -> tuple:
    return (seq + (value,))[-TRACE_LEN:]


def _gap(trace: tuple[float | None, ...]) -> tuple[float | None, ...]:
    """끊긴 자리를 하나만 남긴다.

    🔴 워커는 끊긴 동안에도 초당 열 번 돈다. 스텝마다 구멍을 넣으면 1 분 뒤
       트레이스가 통째로 `None` 이 되어 마지막으로 알던 값이 사라진다.
    """
    if trace and trace[-1] is None:
        return trace
    return _push(trace, None)


def _gap_t(trace: tuple[float | None, ...],
           trace_t: tuple[int | None, ...]) -> tuple[int | None, ...]:
    """시각 쪽도 값 쪽과 **똑같이** 민다.

    🔴 판정 기준은 `trace` 다. 둘이 따로 판단하면 길이가 어긋나고, 그때
       커서가 한 칸 옆의 시각을 읽는다.
    """
    if trace and trace[-1] is None:
        return trace_t
    return _push(trace_t, None)


# ---- 디지털 입력 J18~J20 (규격 §7.6) -----------------------------------------
#
# 🔴 이 셋은 **출력이 아니라 입력**이다(사용자 확정 2026-08-18). 넷리스트
#    확인 결과 옵토커플러가 커넥터 반대편에 붙고 보드는 그 신호를 읽는다.
#    그래서 `sol.j18` 같은 설정 항목이 없다 — I2C 처럼 카탈로그에서 시드할
#    "종류·사용 여부" 자체가 존재하지 않는다. 세 자리는 **보드 리비전이
#    고정**이므로 `build_dins` 가 설정과 무관하게 항상 깐다.

#: J18~J20 (데이터시트 §5.7, 규격 §7.6).
DIN_PORTS: tuple[int, ...] = (18, 19, 20)


@dataclass(frozen=True)
class DinState:
    """디지털 입력 하나. 보드가 옵토 신호를 EXTI 로 읽어 보낸다.

    🔴 `rails`(RailState)와 반대다 — 레일은 **명령**이고 이건 **실측**이다.
       그런데도 `RailState` 와 같은 화면 어법(`commanded`/`state` +
       `last_known`)을 쓴다. 이유는 값이 아니라 **상태**(켜짐/꺼짐 둘뿐)
       이기 때문이다 — 흐름 이력(`trace`)보다 "지금 상태 + 마지막으로
       알던 것" 쪽이 뜻이 통한다(아래 `build_dins` 판단 참조).

    🔴 `state` 가 `None` 이면 "모른다"다. 두 가지 경우가 있다 —
       (1) 아직 한 번도 엣지가 온 적이 없다(규격 §7.6 — 상태 변화에만
       오므로, 막 연결했는데 신호가 한 번도 안 바뀌었으면 지금 상태를
       모른다), (2) 연결이 끊겼다. 둘 다 화면은 "확인 불가"로 그리되,
       (1)은 `last_known` 이 비어 있고 (2)는 끊기기 전에 알았다면 채워
       진다 — `Verification.UNKNOWN` 이 두 경우를 가리지 않는 것과 같은
       한계이고, `StateHistory` 가 있어야 나머지를 구분할 수 있다.
    """

    connector: str
    key: int
    state: bool | None = None
    changed_at: int | None = None
    """마지막으로 상태가 바뀐 시각 — 보드가 찍은 `t`(ms). 한 번도 안
    바뀌었으면 `None` 이고, 화면은 그 사실을 그대로 말해야 한다.

    🔴 연결이 끊겨도 지우지 않는다. `state` 와 달리 이것은 **과거의
       사실**이라 지금 모른다고 없던 일이 되지 않는다 — `trace` 를 남기는
       것과 같은 이유(`build_channels`·`build_sensors` 머리말).
    """
    last_known: str = ""
    """연결이 끊긴 동안 곁들일 문구. `RailPill` 이 쓰는 것과 같은 어법."""


def seed_dins() -> tuple[DinState, ...]:
    """J18~J20 세 자리를 깐다. `empty_channels()` 와 같은 이유(설계 원칙 3)
    이지만, I2C 의 `seed_sensors()` 와 달리 시드할 설정이 없다 — 이 셋은
    카탈로그에 아예 없는 항목이라, 채울 수 있는 것은 커넥터 번호뿐이다."""
    return tuple(DinState(connector=f"J{c}", key=c) for c in DIN_PORTS)


def build_dins(records, *, reachable: bool,
              previous: tuple[DinState, ...] = (),
              history: StateHistory, now_s: float) -> tuple[DinState, ...]:
    """`type` 이 `din` 인 레코드를 디지털 입력 카드로 (규격 §7.6).

    🔴 **주기 송신이 아니다** — `build_channels`·`build_sensors` 와 달리
       "이번에 안 온 것은 지운다"가 아니라 "이번에 안 온 것은 **그대로
       둔다**"가 맞다. din 은 상태가 바뀔 때만 오므로, 조용한 포트는
       값이 없는 게 아니라 마지막 상태 그대로다.

    🔴 연결이 끊기면 전원 레일과 같은 결로 다룬다 — 지금 상태는 지우고
       `StateHistory` 로 "마지막으로 알던 것 (n초 전)"을 곁들인다. 판단:
       아날로그·I2C 처럼 이력 트레이스를 남기는 대신 이 어법을 고른
       이유는 din 이 값이 아니라 두 상태뿐인 신호라 레일과 성격이
       같기 때문이다(위 `DinState` 머리말).
    """
    base = {d.key: d for d in (previous or seed_dins())}

    for rec in records or ():
        if not isinstance(rec, dict) or rec.get("type") != "din":
            continue
        cid = rec.get("connector_id")
        state = rec.get("state")
        if not isinstance(cid, int) or cid not in base:
            continue
        if isinstance(state, bool) or not isinstance(state, int):
            continue                     # bool 은 int 서브클래스라 별도 취급
        t = rec.get("t")
        base[cid] = replace(
            base[cid], state=bool(state),
            changed_at=t if isinstance(t, int) else base[cid].changed_at,
        )

    out: list[DinState] = []
    for cid in DIN_PORTS:
        d = base[cid]
        state = d.state if reachable else None
        verification = (Verification.VERIFIED
                        if (reachable and d.state is not None)
                        else Verification.UNKNOWN)
        level = Level.OK if d.state else Level.IDLE
        chip = build_chip_state(history, f"din.{cid}", d.connector,
                                level, verification, now_s)
        out.append(replace(d, state=state,
                           last_known=chip.last_known.text or ""))
    return tuple(out)


# ---- GNSS 측위 (규격 §7.8) ---------------------------------------------------
#
# 🔴 왜 이 구획이 생겼나 (사용자 지적, 2026-08-20):
#
#     "왜 스트림에 GNSS는 안보이는데? 모든 센서 데이터가 다 보여야 한다니까?"
#
#    도로 표시 장비에서 **RTK 위치는 가장 중요한 측정값**이다. 그런데 규격에
#    레코드 종류가 없어서(§7.7 의 `gnss_raw` 는 진단용 원문 에코다) 화면에
#    올릴 것 자체가 없었다. §7.8 이 그것을 만들었고 여기가 그 화면 쪽이다.
#
# 🔴 아날로그·I2C 카드와 어법이 다르다. 저쪽은 "값 하나 + 흐름선" 인데
#    위치는 **여러 값이 한 덩어리로만 뜻이 있다** — 위도만 있고 경도가
#    없으면 아무 말도 안 되고, 위성 수·측위 품질은 그 위치를 믿어도 되는지를
#    말하는 곁가지다. 그래서 카드 여럿이 아니라 구획 하나다.

#: 측위 품질 코드 → 사람이 읽는 이름표 (규격 §7.8.5).
#:
#: 🔴 고정 어휘다 — `QUANTITY_LABELS`(§7.5.1)와 같은 성격이라 호스트가 알고
#:    있어도 된다. 설정 항목이 아니라 프로토콜이 정한 값이다.
FIX_LABELS: dict[int, str] = {
    0: "측위 없음",
    1: "단독 측위",
    2: "DGPS",
    4: "RTK 고정",
    5: "RTK 부동",
}


def fix_label(fix: int | None) -> str:
    """🔴 모르는 코드는 지어내지 않는다 — 숫자를 그대로 보여 준다.

    수신기마다 6~9 번대에 자기 정의를 두므로(규격 §7.8.5 "그 밖은 수신기
    정의"), 표에 없는 값을 "알 수 없음" 으로 뭉개면 무엇을 봤는지조차
    사라진다.
    """
    if fix is None:
        return "—"
    return FIX_LABELS.get(fix, f"코드 {fix}")


@dataclass(frozen=True)
class GnssState:
    """J16 의 지금 측위 (규격 §7.8).

    🔴 `lat` 이 `None` 이면 **위치를 모른다**는 뜻이고, 그것은 고장이 아니다
       (설계 원칙 3·4). 세 경우가 있다 — (1) `gnss.enabled` 가 꺼져 있어
       레코드가 아예 안 온다, (2) 켜져 있는데 아직 fix 가 없다(레코드는
       오고 `fix` 가 0 이다), (3) 연결이 끊겼다. `seen` 이 (1)과 나머지를
       가르고, `fix` 가 (2)를 말한다.

    🔴 fix 를 잃었다고 마지막 위치를 계속 띄우지 않는다. 보드가 이미 `null`
       을 보내고(§7.8.4) 화면도 그것을 그대로 따른다 — 그러지 않으면 차량이
       마지막으로 하늘을 본 자리에 서 있는 것처럼 보인다.
    """

    seen: bool = False
    """측위 레코드를 한 번이라도 받았는가. 거짓이면 GNSS 가 꺼져 있거나
    모듈이 아직 아무 말도 안 했다 — `$STAT` 의 `gnss.sentence_seen` 이
    둘을 가른다."""

    lat: float | None = None
    lon: float | None = None
    fix_t: int | None = None
    """이 위치가 **측정된** 순간(UTC epoch_ms). `t`(보드가 줄을 확정한
    시각)와 다른 것을 잰다 — 규격 §7.8.3."""

    t: int | None = None
    """보드가 이 줄을 확정한 시각. `fix_t` 와의 차이가 "문장이 얼마나 늦게
    도착했나" 다."""

    alt: float | None = None
    sats: int | None = None
    fix: int | None = None
    hdop: float | None = None
    speed: float | None = None
    course: float | None = None
    time_source: str = ""
    last_known: str = ""
    """연결이 끊긴 동안 곁들일 문구. `RailPill`·`DinState` 와 같은 어법."""


def gnss_position_text(g: GnssState) -> str:
    """위·경도를 한 줄로. 🔴 소수 7자리를 지킨다 (규격 §7.8.2).

    화면에서 자릿수를 줄이면 사용자가 보는 값과 저장된 값이 달라진다 —
    그리고 4자리로 줄이면 그 차이가 **11 m** 다.
    """
    if g.lat is None or g.lon is None:
        return "위치 없음"
    return f"{g.lat:.7f}, {g.lon:.7f}"


def gnss_status_text(g: GnssState) -> str:
    """왜 위치가 없는지(또는 있는지)를 한 줄로.

    🔴 "없음" 만 띄우면 사용자는 배선을 뜯는다. 세 상태를 구분해 말한다 —
       설계 원칙 3·4 그대로다.
    """
    if not g.seen:
        return "GNSS 꺼짐 · 레코드 없음"
    if g.lat is None:
        sats = "—" if g.sats is None else str(g.sats)
        return f"{fix_label(g.fix)} · 위성 {sats}"
    sats = "—" if g.sats is None else str(g.sats)
    return f"{fix_label(g.fix)} · 위성 {sats}"


def build_gnss(records, *, reachable: bool,
               previous: GnssState | None = None,
               history: StateHistory, now_s: float) -> GnssState:
    """`type` 이 `gnss` 인 레코드를 측위 구획으로 (규격 §7.8).

    🔴 **주기 송신이 아니다**(§7.8.6) — `din` 과 같은 성격이라 "이번에 안
       온 것은 그대로 둔다" 가 맞다. 1 Hz 인데 화면이 그보다 자주 갱신되면
       매 프레임 절반은 비어 있게 된다.

    🔴 연결이 끊기면 지금 값은 지운다. 마지막 위치를 계속 띄우면 그것이
       지금 위치로 읽힌다 — `build_channels` 와 같은 판단이다.
    """
    state = previous if previous is not None else GnssState()

    for rec in records or ():
        if not isinstance(rec, dict) or rec.get("type") != "gnss":
            continue

        def num(name):
            v = rec.get(name)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        def integer(name):
            v = rec.get(name)
            return v if isinstance(v, int) and not isinstance(v, bool) else None

        state = replace(
            state,
            seen=True,
            lat=num("lat"), lon=num("lon"),
            fix_t=integer("fix_t"), t=integer("t"),
            alt=num("alt"), sats=integer("sats"), fix=integer("fix"),
            hdop=num("hdop"), speed=num("speed"), course=num("course"),
            time_source=rec.get("time_source") or state.time_source,
        )

    # 🔴 `Level` 은 "위치를 알고 있나" 다. 측위 품질 코드를 등급으로 옮기지
    #    않는다 — RTK 부동(5)이 단독(1)보다 정확한데 숫자는 더 크다.
    known = reachable and state.lat is not None
    verification = Verification.VERIFIED if known else Verification.UNKNOWN
    level = Level.OK if known else Level.IDLE
    chip = build_chip_state(history, "gnss.fix", "GNSS", level, verification,
                            now_s)

    if not reachable:
        # 지금 값은 지우되 `seen`·`fix_t` 는 남긴다 — 과거의 사실이라
        # 지금 모른다고 없던 일이 되지 않는다(`DinState.changed_at` 과 같은
        # 이유).
        state = replace(state, lat=None, lon=None, alt=None, sats=None,
                        fix=None, hdop=None, speed=None, course=None)

    return replace(state, last_known=chip.last_known.text or "")
