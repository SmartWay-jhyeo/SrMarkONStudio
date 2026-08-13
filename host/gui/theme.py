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

    #: 본문. 순검정이 아니다 — 흰 바탕에서 대비가 과해 눈이 피로하다.
    INK = "#1C2024"
    #: 보조 라벨
    INK_DIM = "#5F6A73"

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
QFrame#card {{
    background: {Color.SURFACE};
    border: 1px solid {Color.LINE};
    border-radius: 6px;
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
