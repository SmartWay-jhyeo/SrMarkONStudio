"""화면 여러 곳이 함께 쓰는 작은 부품들.

🔴 여기 있는 것은 **모양**이지 판정이 아니다. 무엇을 보여야 하는가는
   `host/gui/screen.py` 가, 색과 간격은 `host/gui/theme.py` 가 정한다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from host.gui.theme import Color, Space


class TestBand(QFrame):
    """테스트 모드일 때만 보이는 띠 (규격 §6.4).

    🔴 배지 하나로는 부족하다. 테스트인 줄 알고 공정을 돌리거나 운전 중인
       줄 모르고 밸브를 흔드는 것이 이 시스템에서 가장 나쁜 사고인데,
       상단 구석의 작은 표시는 몇 분만 지나면 눈에서 사라진다. 화면 전체를
       가로지르는 띠는 그렇지 않다.

    🔴 무엇이 달라지는지도 함께 쓴다. "테스트 모드" 만 띄우면 그것이 무슨
       뜻인지(저장 안 됨·손 떼면 꺼짐) 알 수 없다.
    """

    TEXT = ("테스트 모드 — 출력은 저장되지 않고, GUI 를 닫으면 "
            "보드가 안전 상태로 되돌린다")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("testband")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        label = QLabel(self.TEXT)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout(self)
        row.setContentsMargins(Space.MD, Space.XS, Space.MD, Space.XS)
        row.addWidget(label)
        self.setVisible(False)

    def render(self, state) -> None:
        self.setVisible(state.ctl_mode == "TEST")


def hairline(colour: str = Color.LINE) -> QWidget:
    """가로 구획선 1 px.

    🔴 `QFrame.Shape.HLine` 을 쓰지 않는다. 그것은 새김 효과가 있는 두 줄짜리
       테두리라 **두꺼운 띠**로 렌더링된다. 어두운 면에서는 제목보다 눈에
       띄었고(qt/rail.py 가 먼저 겪고 직접 칠하는 쪽으로 바꿨다), 밝은 면에서는
       입력칸과 구분되지 않아 설정 화면의 구획 제목이 필드처럼 보였다.

       한 곳에서 만들어 네 화면이 같은 것을 쓴다 — 따로 만들면 언젠가
       한쪽만 고쳐지고 화면마다 선 두께가 달라진다.
    """
    line = QWidget()
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    line.setStyleSheet(f"background: {colour};")
    return line
