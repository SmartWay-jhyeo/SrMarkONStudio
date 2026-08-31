"""확인이 끊겼을 때 "마지막으로 알던 것"을 들고 있는 층. Qt 를 import 하지 않는다.

🔴 이 모듈이 있는 이유
=====================

`chip_style(level, Verification.UNKNOWN)` 은 심각도를 버리고 회색을 낸다.
"확인 못 하는 것이 심각도를 주장하면 안 된다" 는 이유이고 그 자체는 맞다.

그런데 그러면 두 상황이 **똑같이 회색으로 보인다.**

    한 번도 확인된 적 없음
    FAULT 였다가 통신이 끊겨 확인이 안 됨

24V 가 걸린 벤치에서 "고장이었는데 지금 연락이 안 된다" 와 "원래 모른다" 가
같은 화면이면 안 된다. 앞의 것은 사람이 지금 가서 봐야 하는 상황이고,
뒤의 것은 아직 아무 일도 없는 상황이다.

`chip_style` 은 순수 함수라 이력을 모른다 — **거기서 풀 수 없고 풀어서도
안 된다.** 이력을 아는 쪽이 따로 들고 있다가, 확인이 끊기면 그 사실을 함께
표시한다. 그 "따로" 가 이 모듈이다.

칩의 모양은 여전히 `chip_style` 이 정한다. 여기서 만드는 것은 **곁들이는
문구** 다. 회색 테두리는 그대로 두고 옆에 `마지막: 오류 (12초 전)` 를 붙인다.

시각은 바깥에서 넣어 준다. `time.monotonic()` 을 안에서 부르지 않는 이유는
시험에서 12초를 기다릴 수 없기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from host.gui.widgets.status_chip import Level, Verification

#: 이보다 오래되면 "마지막으로 알던 것" 도 더는 말하지 않는다.
#:
#: 🔴 오래된 관측을 계속 붙여 두면 그것이 현재 상태처럼 읽힌다. 5분 전
#:    오류는 지금 무슨 일이 벌어지는지에 대해 아무것도 말해 주지 않는다.
STALE_AFTER_S = 300.0


@dataclass(frozen=True)
class Observation:
    """한 시점에 실제로 확인된 상태."""

    level: Level
    verification: Verification
    at_s: float


@dataclass(frozen=True)
class LastKnown:
    """지금 확인이 안 될 때 곁들일 내용.

    `text` 가 None 이면 곁들일 것이 없다 — 한 번도 확인된 적이 없거나,
    너무 오래되어 말할 가치가 없다.
    """

    text: str | None
    level: Level | None
    age_s: float | None
    #: 사람이 지금 확인하러 가야 하는가. 마지막으로 알던 것이 WARN·FAULT 면 참.
    was_bad: bool = False


_LEVEL_WORD = {
    Level.IDLE: "대기",
    Level.OK: "정상",
    Level.PROBING: "확인 중",
    Level.WARN: "경고",
    Level.FAULT: "오류",
}


def format_age(seconds: float) -> str:
    """경과 시간을 사람이 읽는 짧은 말로.

    자릿수를 늘리지 않는다. `12초 전` 이면 충분하고 `12.3초 전` 은 정밀도를
    주장한다 — 이 값은 확인이 끊긴 뒤로 흐른 시간이라 정밀할 수가 없다.
    """
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{int(seconds)}초 전"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    return f"{int(seconds // 3600)}시간 전"


class StateHistory:
    """항목별로 마지막으로 확인된 상태를 들고 있는다.

    "확인된" 이란 `Verification.UNKNOWN` 이 아닌 관측을 말한다. UNKNOWN 은
    관측이 아니라 관측의 부재이므로 기록하지 않는다 — 기록하면 그것이
    마지막으로 알던 것이 되어 진짜 이력을 덮는다.
    """

    def __init__(self, stale_after_s: float = STALE_AFTER_S) -> None:
        self._stale_after_s = stale_after_s
        self._last: dict[str, Observation] = {}

    def observe(self, key: str, level: Level, verification: Verification,
                at_s: float) -> None:
        """관측 하나를 기록한다. UNKNOWN 은 관측이 아니므로 버린다."""
        if verification is Verification.UNKNOWN:
            return
        self._last[key] = Observation(level, verification, at_s)

    def forget(self, key: str) -> None:
        """이력을 지운다. 보드가 바뀌었거나 새 연결을 시작할 때 쓴다."""
        self._last.pop(key, None)

    def clear(self) -> None:
        self._last.clear()

    def last_known(self, key: str, now_s: float) -> LastKnown:
        """지금 확인이 안 될 때 곁들일 내용을 만든다.

        호출 쪽은 현재 `Verification` 이 UNKNOWN 일 때만 이것을 쓴다.
        확인되고 있는 항목에는 곁들일 것이 없다 — 화면이 이미 사실을 말한다.
        """
        obs = self._last.get(key)
        if obs is None:
            return LastKnown(text=None, level=None, age_s=None)

        age = now_s - obs.at_s
        if age < 0:
            # 시계가 뒤로 갔다. 나이를 음수로 말하느니 방금으로 친다.
            age = 0.0
        if age > self._stale_after_s:
            return LastKnown(text=None, level=None, age_s=age)

        word = _LEVEL_WORD[obs.level]
        return LastKnown(
            text=f"마지막: {word} ({format_age(age)})",
            level=obs.level,
            age_s=age,
            was_bad=obs.level in (Level.WARN, Level.FAULT),
        )


@dataclass
class ChipState:
    """칩 하나를 그리는 데 필요한 전부.

    `chip_style` 이 정하는 모양과, 이력이 있어야 알 수 있는 곁들임을 한
    자리에 모은다. 위젯은 이 값을 받아 그리기만 한다.
    """

    key: str
    label: str
    level: Level
    verification: Verification
    detail: str | None = None
    last_known: LastKnown = field(
        default_factory=lambda: LastKnown(None, None, None)
    )

    @property
    def needs_attention(self) -> bool:
        """지금 사람이 봐야 하는가.

        지금 나쁘거나, 나빴는데 확인이 끊긴 경우다. 두 번째가 이 모듈의
        존재 이유다 — 확인이 끊겼다고 해서 문제가 사라지지 않는다.
        """
        if self.level in (Level.WARN, Level.FAULT):
            return True
        return (self.verification is Verification.UNKNOWN
                and self.last_known.was_bad)


def build_chip_state(history: StateHistory, key: str, label: str,
                     level: Level, verification: Verification,
                     now_s: float, detail: str | None = None) -> ChipState:
    """관측을 기록하고 그릴 준비가 된 값을 돌려준다.

    확인이 되고 있으면 곁들임은 비어 있다. 화면이 이미 사실을 말하는데
    옆에 과거를 붙이면 무엇이 지금인지 흐려진다.
    """
    history.observe(key, level, verification, now_s)
    lk = (history.last_known(key, now_s)
          if verification is Verification.UNKNOWN
          else LastKnown(None, None, None))
    return ChipState(
        key=key, label=label, level=level, verification=verification,
        detail=detail, last_known=lk,
    )
