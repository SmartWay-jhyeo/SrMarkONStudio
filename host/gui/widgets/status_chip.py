"""상태 칩 — 색은 심각도, 채움은 확인 여부.

🔴 이 구분이 이 앱의 안전 원칙이다 (스펙 §2.5).

전원 레일에는 피드백 회로가 없다. GPIO 를 High 로 올렸다는 것과 실제로
24V 가 나온다는 것은 다른 사실이고, 보드는 후자를 모른다. 화면이 둘을
같은 초록 점으로 그리면 사용자는 확인된 것으로 읽는다.

그래서 색과 채움을 분리했다:
    색   = 심각도 (정상/경고/오류)
    채움 = 확인 여부 (실측됨 / 명령만 나감)

피드백 없는 항목은 **영원히 테두리**다. 절대 채워지지 않는다.
"""

from dataclasses import dataclass
from enum import Enum

from host.gui.theme import Color


class Level(Enum):
    """심각도. 색을 정한다."""

    IDLE = "idle"
    OK = "ok"
    PROBING = "probing"
    WARN = "warn"
    FAULT = "fault"


class Verification(Enum):
    """이 상태를 실제로 확인했는가. 채움을 정한다."""

    #: 실측·피드백으로 확인됨
    VERIFIED = "verified"
    #: 명령은 나갔으나 확인할 방법이 없음
    COMMANDED = "commanded"
    #: 아예 모름
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChipStyle:
    fill: str
    border: str
    text: str
    filled: bool


_LEVEL_COLOR = {
    Level.IDLE: Color.UNKNOWN,
    Level.OK: Color.VERIFIED,
    Level.PROBING: Color.PROBING,
    Level.WARN: Color.WARN,
    Level.FAULT: Color.FAULT,
}


def chip_style(level: Level, verification: Verification) -> ChipStyle:
    """심각도와 확인 여부로 칩 모양을 정한다."""
    if verification is Verification.UNKNOWN:
        # 확인 불가는 심각도를 주장하지 않는다. 회색 테두리다.
        return ChipStyle(
            fill=Color.SURFACE, border=Color.UNKNOWN,
            text=Color.INK_DIM, filled=False,
        )

    color = _LEVEL_COLOR[level]

    # 오류는 확인이 필요 없다 — 이미 일어난 일이라 관측 자체가 증거다.
    if level is Level.FAULT or verification is Verification.VERIFIED:
        return ChipStyle(fill=color, border=color, text=Color.SURFACE, filled=True)

    return ChipStyle(fill=Color.SURFACE, border=color, text=color, filled=False)


def rail_label(on: bool, verification: Verification) -> str:
    """전원 레일 문구. 확인 여부가 단어에도 드러나야 한다."""
    if verification is Verification.UNKNOWN:
        return "확인 불가"
    state = "ON" if on else "OFF"
    if verification is Verification.VERIFIED:
        return f"정상 {state}"
    return f"{state} 명령됨"
