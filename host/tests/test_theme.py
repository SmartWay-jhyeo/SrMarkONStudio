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


def test_mono_font_has_fallback():
    """Cascadia Mono 가 없는 기기가 있다. 폴백이 있어야 자릿수가 흔들리지 않는다."""
    assert "," in Font.MONO


def test_space_scale_is_monotonic():
    vals = [Space.XS, Space.SM, Space.MD, Space.LG, Space.XL]
    assert vals == sorted(vals)
    assert len(set(vals)) == len(vals)
