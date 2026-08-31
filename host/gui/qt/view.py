"""뷰가 지키는 계약.

🔴 뷰는 **`ScreenState` 만 안다.** 서비스도, 워커도, 다른 뷰도 모른다.

   이 규칙 하나가 "나중에 UI 를 바꿔도 쉽게" 를 만든다. 배치를 바꾸는 일이
   뷰를 새로 쓰고 `render` 를 부르는 것으로 끝나기 때문이다 — 데이터가
   어디서 오는지, 누가 누구에게 전달하는지는 건드릴 필요가 없다.

   반대 방향(뷰 -> 바깥)은 **시그널**로만 간다. 뷰가 서비스를 직접 부르면
   그 뷰는 서비스 없이 못 뜨고, 그러면 화면을 시험할 수 없다.

지키는 법
--------

    class 무엇View(QWidget):
        def render(self, state: ScreenState) -> None:
            ...  # state 를 읽어 위젯에 옮긴다. 그게 전부다.

`render` 는 **여러 번 불려도 같아야 한다.** 워커가 주기마다 부르므로,
호출마다 위젯을 새로 만들면 화면이 깜빡이고 스크롤이 튄다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from host.gui.screen import ScreenState


@runtime_checkable
class View(Protocol):
    """`ScreenState` 를 그리는 무엇."""

    def render(self, state: ScreenState) -> None: ...
