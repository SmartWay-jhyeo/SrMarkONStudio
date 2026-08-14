"""색·글꼴·간격 토큰과 Qt 스타일시트.

🔴 **색은 여기서만 정의한다.** 화면 코드에 색 리터럴을 쓰지 않는다.
한 곳에 모여 있어야 흰 바탕에서의 대비를 한 번에 검사할 수 있다.

방향: 차가운 오프화이트의 계기 패널. 따뜻한 크림색은 편집 디자인의
기본값이고 전자 계기와 무관하다. 그림자를 쓰지 않고 경계선과 여백으로
층을 나눈다.
"""


class Color:
    #: 창 바탕. 표면(흰색)과 미세하게 달라 카드 경계가 생긴다.
    GROUND = "#F7F8F9"
    #: 카드·패널
    SURFACE = "#FFFFFF"
    #: 경계선. 그림자 대신 이것으로 층을 나눈다.
    LINE = "#E2E6E9"
    #: 카드 안쪽의 한 겹 더 들어간 면 (게이지 트랙 등)
    WELL = "#EEF1F3"

    #: 본문. 순검정이 아니다 — 흰 바탕에서 대비가 과해 눈이 피로하다.
    INK = "#1C2024"
    #: 보조 라벨
    INK_DIM = "#5F6A73"
    #: 아주 흐린 보조 — 눈금 숫자처럼 있어야 하지만 읽을 일이 드문 것
    INK_FAINT = "#98A2AA"

    # ── 어두운 면 ─────────────────────────────────────────────
    # 🔴 화면에 **무게중심**이 필요하다. 전부 흰 바탕에 얇은 선이면 눈이
    #    앉을 데가 없고, 어디부터 읽어야 할지 알 수 없다. 정체성 바와
    #    좌측 레일을 어둡게 깔아 나머지를 그 위에 얹는다.
    #
    #    순검정이 아니라 청록이 도는 먹빛이다 — 계기 패널의 실크스크린
    #    색이고, 아래의 신호 초록과 같은 계열이라 화면이 하나로 묶인다.
    SHELL = "#16202A"
    #: 어두운 면 위의 글자
    SHELL_INK = "#E8EDF0"
    #: 어두운 면 위의 보조 글자
    SHELL_DIM = "#8A9AA6"
    #: 어두운 면 안의 구획선
    SHELL_LINE = "#2A3945"

    # ── 상태색 ────────────────────────────────────────────────
    # 스펙 §8.3. 흰 바탕에서 읽히는 톤으로 조정했다.
    #: 비활성 / 확인 불가.
    #: 채운 칩 위에 흰 글자를 얹어도 읽히도록 어둡게 잡았다 — 밝은 회색이면
    #: 대비가 3:1 근처로 떨어져 글자가 사라진다.
    UNKNOWN = "#6E7780"
    #: 탐색·초기화·시작 중
    PROBING = "#2563C9"
    #: 정상 — 실제로 확인된 경우에만 쓴다
    VERIFIED = "#1B7F4B"
    #: 경고. 노랑은 흰 바탕에서 안 보이므로 앰버로 내렸다.
    WARN = "#A6620A"
    #: 오류·안전 정지
    FAULT = "#B4232A"


class Font:
    #: 측정값·raw·seq. 고정폭이라 값이 갱신돼도 자릿수가 안 흔들린다.
    #: 실시간 계기에서는 미관이 아니라 기능이다.
    MONO = "Cascadia Mono, Consolas, monospace"
    #: UI 라벨. 한글 폴백 포함.
    UI = "Segoe UI Variable, Segoe UI, Malgun Gothic, sans-serif"

    SIZE_XL = 22
    SIZE_LG = 15
    SIZE_MD = 12
    SIZE_SM = 10


class Space:
    XS = 4
    SM = 8
    MD = 14
    LG = 22
    XL = 34


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 대비율. 상태색이 흰 바탕에서 읽히는지 검사하는 데 쓴다."""
    l1, l2 = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def stylesheet() -> str:
    """앱 전역 Qt 스타일시트."""
    return f"""
QWidget {{
    background: {Color.GROUND};
    color: {Color.INK};
    font-family: {Font.UI};
    font-size: {Font.SIZE_MD}pt;
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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 4px;
    padding: {Space.XS}px {Space.SM}px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {Color.PROBING};
}}
"""
