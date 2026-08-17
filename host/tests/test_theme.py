import pytest

from host.gui.theme import Color, Font, Space, contrast_ratio, stylesheet


def test_all_colors_are_hex():
    for name in dir(Color):
        if name.startswith("_"):
            continue
        v = getattr(Color, name)
        assert isinstance(v, str) and v.startswith("#") and len(v) == 7, name


def test_body_text_meets_wcag_aa_on_surface():
    """흰 바탕에서 본문이 읽혀야 한다. 4.5:1 이 기준선이다."""
    assert contrast_ratio(Color.INK, Color.SURFACE) >= 4.5


def test_dim_text_meets_wcag_aa_on_surface():
    assert contrast_ratio(Color.INK_DIM, Color.SURFACE) >= 4.5


def test_every_status_color_is_legible_on_white():
    """🔴 노랑 계열이 흰 바탕에서 안 보이는 것이 이 검사의 이유다.

    상태색은 장식이 아니라 정보다. 흰 배경에서 안 읽히면 정보가 사라진다.
    """
    for name in ("UNKNOWN", "PROBING", "VERIFIED", "WARN", "FAULT"):
        c = getattr(Color, name)
        assert contrast_ratio(c, Color.SURFACE) >= 3.0, f"{name} {c}"


def test_ink_is_not_pure_black():
    """순검정은 흰 바탕에서 대비가 과해 눈이 피로하다."""
    assert Color.INK != "#000000"


def test_ground_and_surface_differ():
    """바탕과 표면이 같으면 카드 경계가 사라진다."""
    assert Color.GROUND != Color.SURFACE


def test_stylesheet_contains_no_raw_hex_outside_tokens():
    """모든 색이 토큰에서 나와야 한다."""
    import re

    css = stylesheet()
    known = {getattr(Color, n) for n in dir(Color) if not n.startswith("_")}
    for found in set(re.findall(r"#[0-9A-Fa-f]{6}", css)):
        assert found in known, f"토큰에 없는 색: {found}"


def test_labels_do_not_paint_the_window_ground():
    """🔴 이 검사가 있는 이유 — 설정 화면 오른쪽 절반이 회색 띠밭이었다.

    전역 `QWidget { background: GROUND }` 이 **모든** QLabel 에도 걸린다.
    카드는 SURFACE(흰색)인데 그 위의 라벨·단위·사유 라벨이 각자 GROUND
    사각형을 찍어, 흰 카드에 회색 상자가 줄줄이 박혔다. QLineEdit 만
    배경을 명시해서 혼자 하얗게 보였다.

    같은 함정을 어두운 면에서는 이미 겪고 고쳐 두었는데(rail.py 머리말),
    밝은 카드 위에서도 똑같이 일어나고 있었다.
    """
    import re

    css = stylesheet()
    # 🔴 줄머리에서 시작하는 규칙만 센다. `QFrame#shell QLabel` 같은 자손
    #    선택자는 어두운 면에만 걸리므로 흰 카드를 구해 주지 않는다.
    rule = re.search(r"^Q[\w, ]*\{[^}]*background: transparent[^}]*\}",
                     css, re.MULTILINE)
    assert rule, "면을 칠하지 않아야 할 위젯들의 전역 규칙이 없다"

    # 글자·컨트롤을 얹는 위젯은 자기 면을 칠하지 않는다. 체크박스가 빠져
    # 있으면 전원 항목 자리에 회색 상자가 남는다 — 실제로 그랬다.
    for kind in ("QLabel", "QCheckBox", "QRadioButton"):
        assert kind in rule.group(0), kind

    # 🔴 순서가 곧 규칙이다. Qt 스타일시트는 특이도가 같으면 **뒤에 온 것**이
    #    이긴다 — QWidget 앞에 두면 아무 효과가 없다.
    blanket = re.search(r"^QWidget \{[^}]*\}", css, re.MULTILINE)
    assert blanket and rule.start() > blanket.start()


def test_mono_font_has_fallback():
    """Cascadia Mono 가 없는 기기가 있다. 폴백이 있어야 자릿수가 흔들리지 않는다."""
    assert "," in Font.MONO


def test_space_scale_is_monotonic():
    vals = [Space.XS, Space.SM, Space.MD, Space.LG, Space.XL]
    assert vals == sorted(vals)
    assert len(set(vals)) == len(vals)
