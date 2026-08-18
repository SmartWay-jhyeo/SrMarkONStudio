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
                              previous=previous.sensors),
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
    label: str
    value: float | None = None
    unit: str = ""
    status: int = 0

    trace: tuple[float | None, ...] = ()
    trace_t: tuple[int | None, ...] = ()

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


def build_sensors(records, *, reachable: bool,
                  previous: tuple[SensorState, ...] = (),
                  ) -> tuple[SensorState, ...]:
    """`type` 이 `i2c` 인 레코드를 센서 카드로.

    🔴 순서를 지킨다. 처음 본 순서대로 두고 새로 온 것만 뒤에 붙인다 —
       매번 정렬하면 센서가 하나 늘거나 조용해질 때 카드가 자리를 바꾸고,
       값을 읽던 사람이 무엇을 보고 있었는지 잃는다.
    """
    order: list[tuple[str, str]] = []
    base: dict[tuple[str, str], SensorState] = {}
    for s in previous:
        key = (s.connector, s.quantity)
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
