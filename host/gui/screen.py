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
from host.gui.widgets.status_chip import Level, Verification, rail_label

#: AIN0 이 J3 이다 (데이터시트 §5.3).
CONNECTOR_OFFSET = 3
AIN_COUNT = 7

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


@dataclass(frozen=True)
class ScreenState:
    """화면 전체. 뷰는 이것만 받는다."""

    identity: Identity = field(default_factory=Identity)
    mode: str = ""
    link: Link = field(default_factory=Link)
    reachable: bool = False
    rails: tuple[RailState, ...] = ()
    channels: tuple[ChannelState, ...] = ()

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
                 history: StateHistory,
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
        link=link,
        reachable=reachable,
        rails=build_rails(rail_values, reachable=reachable,
                          history=history, now_s=now),
        channels=build_channels(records, reachable=reachable,
                                previous=previous.channels),
    )


def build_channels(records, *, reachable: bool,
                   previous: tuple[ChannelState, ...] = ()) -> tuple[ChannelState, ...]:
    """텔레메트리 레코드를 채널 상태로.

    🔴 채널 장애 격리 — 레코드가 오지 않은 채널은 **건드리지 않는다.**
       한 채널이 조용하다고 나머지를 지우면, 센서 하나가 빠졌을 때 화면이
       통째로 비는 것처럼 보인다.

    🔴 연결이 끊기면 전부 모르는 상태로 되돌린다. 마지막 값을 계속 띄우면
       그것이 지금 값으로 읽힌다.
    """
    base = {ch.index: ch for ch in (previous or empty_channels())}
    if not reachable:
        return tuple(
            ChannelState(i, f"J{i + CONNECTOR_OFFSET}") for i in sorted(base)
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
        value = rec.get("value")
        base[index] = replace(
            base[index],
            ma=float(ma) if isinstance(ma, (int, float)) else None,
            level=Level.OK if not rec.get("status", 0) else Level.WARN,
            verification=Verification.VERIFIED,
            value=float(value) if isinstance(value, (int, float)) else None,
            unit=str(rec.get("unit", "")),
        )
    return tuple(base[i] for i in sorted(base))
