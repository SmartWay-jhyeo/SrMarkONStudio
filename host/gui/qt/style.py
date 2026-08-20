"""Qt 전역 스타일시트. 색은 전부 `theme` 의 토큰에서 온다.

🔴 **여기에 색 리터럴을 쓰지 않는다.** 한 곳에 모여 있어야 흰 바탕에서의
   대비를 한 번에 검사할 수 있고, `test_theme.py` 가 그것을 강제한다.

🔴 `theme.py` 에서 떼어 왔다. 토큰은 Qt 없이 시험되는 층이고 이것은 Qt 의
   문법이다 — 한 파일에 있으면 색 하나를 확인하려고 200 줄짜리 f-string 을
   지나야 한다.
"""
from __future__ import annotations

from pathlib import Path

from host.gui.theme import Color, Font, Space


_ARROW_SVG = (Path(__file__).parent / "assets" / "arrow_down.svg"
              ).as_posix()


def stylesheet() -> str:
    """앱 전역 Qt 스타일시트."""
    return f"""
QWidget {{
    background: {Color.GROUND};
    color: {Color.INK};
    font-family: {Font.UI};
    font-size: {Font.SIZE_MD}pt;
}}
/* 🔴 위의 전역 규칙이 **모든** 위젯에 걸린다. 라벨은 글자를 얹는 물건이지
 *    면을 칠하는 물건이 아닌데, 그대로 두면 흰 카드 위에서 라벨마다
 *    GROUND 사각형을 찍는다 — 설정 화면 오른쪽 절반이 회색 띠밭이었던
 *    이유가 이것이다. QLineEdit 만 배경을 명시해서 혼자 하얗게 보였다.
 *
 *    같은 함정을 어두운 면에서는 이미 겪고 고쳐 두었는데(qt/rail.py 머리말)
 *    밝은 면에서도 똑같이 일어나고 있었다. 한 번에 막는다.
 *
 *    🔴 반드시 QWidget **뒤에** 온다. 특이도가 같아 뒤에 온 것이 이긴다. */
QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {Color.LINE};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {Color.UNKNOWN}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1.5px solid {Color.LINE};
    border-radius: 4px;
    background: {Color.SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {Color.PROBING};
    border-color: {Color.PROBING};
}}
QCheckBox::indicator:disabled {{ background: {Color.GROUND}; }}
QFrame#card {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 6px;
}}

/* ── 어두운 면 ─────────────────────────────────────────────
 *
 * 🔴 전역 `QWidget {{ background: GROUND }}` 이 **모든** 위젯에 걸린다.
 *    그래서 어두운 면 위의 자식들은 배경을 명시적으로 투명하게 돌려놓지
 *    않으면 각자 흰 상자로 찍힌다. 실제로 그랬다 — 레일의 5V·14.9V·24V
 *    글자와 구획 제목이 밝은 네모에 가려 안 보였다.
 *
 *    자손 선택자(공백)를 쓴다. `>` 는 직계 자식만이라 레이아웃 안에
 *    한 겹 더 들어간 라벨을 놓친다. */
QFrame#shell {{
    background: {Color.SHELL};
    border: none;
    border-right: 1px solid {Color.SHELL_LINE};
}}
QFrame#shell QWidget {{ background: transparent; }}
QFrame#shell QLabel {{
    background: transparent;
    color: {Color.SHELL_INK};
}}
QLabel#shellDim {{
    background: transparent;
    color: {Color.SHELL_DIM};
    font-size: {Font.SIZE_SM}pt;
}}
QLabel#shellMono {{
    background: transparent;
    color: {Color.SHELL_DIM};
    font-family: {Font.MONO};
    font-size: {Font.SIZE_SM}pt;
}}
/* 레일 안의 구획 제목 */
QLabel#shellSection {{
    background: transparent;
    color: {Color.SHELL_DIM};
    font-size: {Font.SIZE_SM}pt;
    font-weight: 700;
    letter-spacing: 1.4px;
}}
/* 명령 상태 꼬리표. 채운 알약이 아니라 글자만 — 어두운 면에서 상자는
 * 무거워지고, 여기서 중요한 것은 상태이지 상자가 아니다. */
QLabel#shellState {{
    background: transparent;
    color: {Color.SHELL_DIM};
    font-size: {Font.SIZE_SM}pt;
}}
QLabel#h1 {{
    font-size: {Font.SIZE_XL}pt;
    font-weight: 600;
}}
QLabel#dim {{
    color: {Color.INK_DIM};
    font-size: {Font.SIZE_SM}pt;
}}
QLabel#value {{
    font-family: {Font.MONO};
    font-size: {Font.SIZE_LG}pt;
}}
QPushButton {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 4px;
    padding: {Space.SM}px {Space.MD}px;
}}
QPushButton:hover {{
    border-color: {Color.PROBING};
}}
QPushButton:focus {{
    outline: none;
    border: 2px solid {Color.PROBING};
}}
QPushButton:disabled {{
    color: {Color.UNKNOWN};
}}
/* 🔴 버튼 넷이 같은 무게로 서 있으면 무엇이 주 동작인지 알 수 없다.
 *    이 화면에서 가장 중요한 것은 **저장**이다 — 적용은 보드의 RAM 이고
 *    저장은 Flash 다. 저장 없이 화면을 떠나면 설정이 사라진다. */
QPushButton#primary {{
    background: {Color.PROBING};
    color: {Color.SURFACE};
    border: 1px solid {Color.PROBING};
    font-weight: 600;
}}
QPushButton#primary:disabled {{
    background: {Color.WELL};
    color: {Color.UNKNOWN};
    border-color: {Color.LINE};
}}
/* 되돌릴 수 없는 동작. 채우지 않는다 — 눈에 띄되 손이 먼저 가지 않게. */
QPushButton#danger {{
    background: transparent;
    color: {Color.FAULT};
    border: 1px solid {Color.LINE};
}}
QPushButton#danger:hover {{ border-color: {Color.FAULT}; }}
QPushButton#danger:disabled {{ color: {Color.UNKNOWN}; }}
/* 제어 모드 스위치 (규격 §6.4).
 *
 * 🔴 `QPushButton#tab` 과 생김새를 다르게 한다. 탭은 화면 이동이고 이것은
 *    보드의 동작을 바꾼다 — 같아 보이면 탭 누르듯 모드를 바꾼다. */
QPushButton#ctlmode {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 4px;
    padding: {Space.XS}px {Space.MD}px;
    color: {Color.INK_DIM};
    font-size: {Font.SIZE_SM}pt;
}}
QPushButton#ctlmode:checked {{
    background: {Color.INK};
    border-color: {Color.INK};
    color: {Color.SURFACE};
    font-weight: 700;
}}
/* 테스트 쪽만 경고색으로 채운다. 운전이 평상 상태이므로 거기에 색을 쓰면
 * 색이 뜻을 잃는다. */
QPushButton#ctlmode[danger="true"] {{
    background: {Color.WARN};
    border-color: {Color.WARN};
    color: {Color.SURFACE};
}}
/* 🔴 테스트 중임을 화면 전체에 건다. 테스트인 줄 알고 공정을 돌리거나
 *    운전 중인 줄 모르고 밸브를 흔드는 것이 이 시스템에서 가장 나쁜
 *    사고다 — 배지 하나로는 부족하다. */
QFrame#testband {{
    background: {Color.WARN};
    border: none;
}}
QFrame#testband QLabel {{
    color: {Color.SURFACE};
    font-size: {Font.SIZE_SM}pt;
    font-weight: 600;
}}

/* 설정 그룹 탭.
 *
 * 🔴 상단 바의 대시보드/설정 탭과 **다르게 생겨야 한다.** 둘이 같은 밑줄
 *    모양이면 어느 쪽이 상위 이동인지 알 수 없다. 이쪽은 알약으로 묶어
 *    한 덩어리(구획 선택)로 읽히게 한다. */
QPushButton#tab {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: {Space.XS}px {Space.MD}px;
    color: {Color.INK_DIM};
    font-size: {Font.SIZE_SM}pt;
}}
QPushButton#tab:hover {{ color: {Color.INK}; }}
QPushButton#tab:checked {{
    background: {Color.WELL};
    border-color: {Color.LINE};
    color: {Color.INK};
    font-weight: 600;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 4px;
    padding: {Space.XS}px {Space.SM}px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {Color.PROBING};
}}

/* 🔴 콤보박스의 화살표 버튼 — 본체만 입히면 화살표가 Qt 기본(구식 회색
   사각형)으로 남는다(사용자 지적 2026-08-20). 그림 파일 없이 테두리
   삼각형 기법으로 그린다. */
QComboBox::drop-down {{
    border: none;
    background: transparent;
    width: 22px;
}}
QComboBox::down-arrow {{
    /* 🔴 테두리 삼각형 기법은 이 Qt 에서 네모 얼룩으로 나온다(실물 확인
       2026-08-20) — 진짜 그림(SVG 갈매기표)을 쓴다. 경로는 stylesheet()
       가 실행 시점에 채운다. */
    image: url("{_ARROW_SVG}");
    width: 10px; height: 6px;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 4px;
    selection-background-color: {Color.WELL};
    selection-color: {Color.INK};
    outline: none;
}}

/* 🔴 표 — 기본 Qt 표는 회색 격자에 옛날 헤더라 앱과 따로 논다(사용자
   피드백 2026-08-20 "디자인이 구리다"). 격자 대신 아래 실선 한 줄,
   납작한 헤더, 고정폭 숫자로 이 앱의 시각 언어에 맞춘다. */
QTableWidget {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 6px;
    gridline-color: transparent;
    font-family: {Font.MONO};
    font-size: {Font.SIZE_SM}pt;
    selection-background-color: transparent;
}}
QTableWidget::item {{
    padding: 3px {Space.SM}px;
    border-bottom: 1px solid {Color.GROUND};
}}
QHeaderView::section {{
    background: {Color.SURFACE};
    color: {Color.INK_DIM};
    border: none;
    border-bottom: 1px solid {Color.LINE};
    padding: 4px {Space.SM}px;
    font-family: {Font.UI};
    font-size: {Font.SIZE_SM}pt;
    font-weight: 600;
}}
QTableWidget QTableCornerButton::section {{
    background: {Color.SURFACE};
    border: none;
}}
"""
