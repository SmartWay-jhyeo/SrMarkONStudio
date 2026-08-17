"""설정 화면의 칸이 지키는 계약.

🔴 **왜 이 파일이 생겼나**

   설정 화면에는 성격이 다른 칸이 셋 있다 — 보통 한 줄(`RowWidget`),
   물리량 범위 두 칸(`RangeFields`), 전송 필드 체크박스 묶음
   (`FieldMaskCard`). 셋 다 같은 다섯 가지를 할 줄 알아야 하는데, 그것이
   **어디에도 적혀 있지 않았다.**

   결과가 실제로 나왔다. `RangeFields` 와 `FieldMaskCard` 에는 `note_text`
   가 없었고, 그것을 모으는 쪽은 `getattr(w, "note_text", "")` 로 빠진 것을
   조용히 덮고 있었다. 보드가 `ain*.zero` 에 사유를 붙이는 순간 그 문구는
   화면 어디에도 뜨지 않는다 — 그런데 아무도 그것을 알아채지 못한다.

   `self._rows` 의 타입도 `dict[str, object]` 였다. 무엇이 들어 있는지
   타입이 말해 주지 않으니, 새 칸을 만들 때 무엇을 구현해야 하는지 알려면
   기존 칸을 읽어 보는 수밖에 없었다.

🔴 Qt 를 import 하지 않는다. 계약은 모양이 아니라 **할 줄 아는 것**이다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Cell(Protocol):
    """설정 항목 하나를 맡는 무엇.

    한 위젯이 키를 **여럿** 맡을 수도 있다 — `RangeFields` 가 영점과
    스케일 둘을 함께 쓴다. 그래서 계약은 "키 하나 = 칸 하나" 가 아니라
    "이 다섯 가지를 할 줄 안다" 이다.
    """

    def set_value(self, text: str) -> None:
        """바깥에서 값을 밀어넣는다 (대시보드의 전원 레일 등).

        🔴 신호를 막고 넣어야 한다. 안 막으면 변경 신호가 돌아 나가 폼을
           또 고치고, 그 사이 사용자가 친 값을 덮는다.
        """

    def show_error(self, message: str) -> None:
        """보드가 거부했거나 값이 잘못됐다. 사유를 사용자에게 보인다."""

    def clear_error(self, fallback: str = "") -> None:
        """오류 표시를 걷는다."""

    def mark_dirty(self, dirty: bool) -> None:
        """아직 보드에 안 보낸 변경이 있는지 표시한다."""

    @property
    def note_text(self) -> str:
        """보드가 이 항목에 붙여 보낸 사유. 없으면 빈 문자열.

        🔴 기본값 있는 `getattr` 로 대신하지 않는다. 그러면 구현을 빠뜨린
           칸이 "사유 없음" 으로 조용히 지나간다.
        """
