"""펌웨어의 클럭 상수가 한 곳에서 파생되는지 검사한다.

🔴 **왜 이 파일이 생겼나**

   시스템 클럭 64 MHz 라는 사실이 여섯 파일에 **숫자로** 흩어져 있었다:
   TIM8 프리스케일 63, SPI4 분주비 128, I2C TIMINGR 0x60702729, UART
   BRR 계산, 바쁜 대기 루프의 `64u / 3u + 1u`, WS2812 의 ARR 79.

   다음에 누가 클럭을 올리면 그것들이 **조용히** 틀어진다. 컴파일도 되고
   링크도 되고 부팅도 한다. 증상은 "I2C 가 가끔 안 읽힌다" · "ADS1256 값이
   가끔 이상하다" 로 나오고, 그때는 아무도 클럭을 의심하지 않는다.

   그래서 `bsp/mk_clock.h` 를 진실의 한 곳으로 두고, 여기서 **파생값이
   실제로 그 값에 매여 있는지** 확인한다. 매크로로 파생시킨 것은 클럭을
   바꿔도 따라오고, 파생시킬 수 없는 것(I2C TIMINGR)은 어느 클럭에서
   뽑았는지 못박아 두어 클럭이 바뀌면 **여기서 깨진다.**

🔴 **왜 호스트에서 검사하나**

   `_Static_assert` 도 mk_clock.h 에 함께 넣어 두었지만, 그것은 ARM
   빌드를 돌려야만 돈다. 이 저장소의 기본 확인 절차(`python -m pytest`)는
   보드도 크로스 컴파일러도 없이 도는 것이 원칙이라, 같은 불변조건을
   여기서도 본다 — 계산 자체는 정수 나눗셈이 아니라 **분수**로 하므로
   64.000 MHz 가 아니라 63.9999 MHz 인 조합은 여기서 먼저 걸린다.

근거
  RM0468 Rev 3 p.377~383  (RCC_PLLCKSELR · RCC_PLLCFGR · RCC_PLL1DIVR)
  STM32H723ZGT6.pdf p.107 Table 32 (HSE) · p.110 Table 35 (HSI)
                    p.112 Table 38 (PLL1, wide VCO)
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import pytest

FW = Path(__file__).resolve().parents[2] / "firmware" / "stage1"
CLOCK_H = FW / "bsp" / "mk_clock.h"

#: 🔴 이 시스템이 서 있는 숫자. 카메라 동기(최대 33 ms)를 위해 센서값을
#:    10 ms 간격으로 찍는 것이 목표이고, PPS 사이는 타이머로 보간한다.
TARGET_SYSCLK_HZ = 64_000_000


# ── C 상수 읽기 ────────────────────────────────────────────────────────────

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _defines(path: Path) -> dict[str, str]:
    """`#define NAME <식>` 을 모은다. 함수형 매크로는 건너뛴다."""
    out: dict[str, str] = {}
    text = _strip_comments(path.read_text(encoding="utf-8"))
    # 줄 끝 백슬래시로 이어지는 정의를 한 줄로 붙인다.
    text = re.sub(r"\\\s*\n", " ", text)
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+(.*)$",
                         text, re.MULTILINE):
        out[m.group(1)] = m.group(2).strip()
    return out


def _eval(expr: str, defs: dict[str, str], depth: int = 0) -> Fraction:
    """C 상수식을 **분수로** 푼다.

    🔴 정수 나눗셈으로 풀면 안 된다. 640/10 이 64 인 것과 641/10 이 64 인
       것을 구분하지 못하고, 그러면 "정확히 64.000 MHz" 라는 이 시험의
       존재 이유가 사라진다.
    """
    assert depth < 32, f"매크로가 순환한다: {expr}"
    expr = _strip_comments(expr)
    # `((uint32_t)25000000)` 같은 C 캐스트를 지운다 — HAL 원본이 그렇게 쓴다.
    expr = re.sub(r"\(\s*(?:unsigned\s+|signed\s+)?"
                  r"(?:u?int(?:8|16|32|64)_t|unsigned|int|long|short)\s*\)",
                  " ", expr)
    expr = re.sub(r"\b(\d+)[uUlL]+\b", r"\1", expr)          # 0u -> 0
    expr = re.sub(r"\b0[xX]([0-9a-fA-F]+)\b", r"0x\1", expr)

    def sub(m: re.Match[str]) -> str:
        name = m.group(0)
        if name in defs:
            return "(" + str(_eval(defs[name], defs, depth + 1)) + ")"
        raise AssertionError(f"모르는 식별자: {name}")

    expr = re.sub(r"\b(?!0[xX])[A-Za-z_]\w*\b", sub, expr)
    # Fraction 으로 계산해 나눗셈이 정확한지 아래에서 확인할 수 있게 한다.
    expr = re.sub(r"\b(\d+)\b", r"Fraction(\1)", expr)
    expr = re.sub(r"Fraction\(0\)[xX]([0-9a-fA-F]+)",
                  lambda m: str(int(m.group(1), 16)), expr)
    return eval(expr, {"__builtins__": {}}, {"Fraction": Fraction})  # noqa: S307


@pytest.fixture(scope="module")
def clk() -> dict[str, Fraction]:
    """mk_clock.h 의 상수를 분수로 푼 표."""
    assert CLOCK_H.exists(), f"클럭 상수의 한 곳이 없다: {CLOCK_H}"
    defs = _defines(CLOCK_H)
    wanted = [n for n in defs if n.startswith("MK_")]
    out: dict[str, Fraction] = {}
    for name in wanted:
        try:
            out[name] = _eval(defs[name], defs)
        except AssertionError:
            continue    # 값이 아닌 정의(문자열·타입 이름 등)는 건너뛴다
    return out


#: 🔴 일부러 버림하는 상수. 여기 넣는 것은 "정수가 아니어도 된다" 가 아니라
#:    **버림이 설계의 일부**라는 뜻이다 — 버림 + 1 로 "넉넉한 쪽" 을 잡는
#:    관용식이라 분수로 보면 정수가 아니다.
TRUNCATION_IS_INTENDED = {
    "MK_BUSY_WAIT_LOOPS_PER_US",
}


def test_every_derived_constant_is_a_whole_number(clk):
    """🔴 C 의 `/` 는 정수 나눗셈이라 **말없이 버림한다.**

    여기서는 분수로 풀기 때문에 63.5 같은 값이 그대로 보이지만, 컴파일러는
    63 으로 잘라 넣고 아무 경고도 안 낸다. 그러면 타이머가 1 MHz 가 아닌
    엉뚱한 주파수로 돌면서 **아무것도 실패하지 않는다.**
    """
    for name, value in sorted(clk.items()):
        if name in TRUNCATION_IS_INTENDED:
            continue
        assert value.denominator == 1, (
            f"{name} = {float(value)} 는 정수가 아니다 — C 는 여기서 버림한다"
        )


def test_the_single_source_exists(clk):
    """🔴 경로가 바뀌면 아래 시험들이 조용히 통과하는 것을 막는다."""
    for must in ("MK_HSE_HZ", "MK_SYSCLK_HZ", "MK_PLL1_DIVM",
                 "MK_PLL1_DIVN", "MK_PLL1_DIVP", "MK_APB1_HZ", "MK_APB2_HZ"):
        assert must in clk, f"{must} 가 mk_clock.h 에 없다"


# ── PLL 계산 ───────────────────────────────────────────────────────────────

def test_pll_makes_exactly_64mhz(clk):
    """🔴 어림수로 맞추면 안 된다.

    기존 타이밍 상수(TIMINGR·프리스케일·BRR)가 전부 64 MHz 위에 서 있다.
    63.98 MHz 로 맞추면 전부 미세하게 틀어지고, 그 틀어짐은 시험이 아니라
    실기기에서 "가끔" 으로 나타난다.
    """
    sysclk = Fraction(clk["MK_HSE_HZ"], 1) / clk["MK_PLL1_DIVM"]
    sysclk = sysclk * clk["MK_PLL1_DIVN"] / clk["MK_PLL1_DIVP"]
    assert sysclk == TARGET_SYSCLK_HZ, f"PLL 출력이 {float(sysclk)} Hz 다"
    assert clk["MK_SYSCLK_HZ"] == TARGET_SYSCLK_HZ


def test_pll_reference_is_inside_the_characterised_range(clk):
    """fPLL_IN = 2~16 MHz — STM32H723ZGT6.pdf p.112 Table 38.

    🔴 RM0468 p.383 은 1~16 MHz 라고 적지만 **데이터시트가 더 좁다.**
       DIVM=25(ref 1 MHz)로도 64 MHz 가 나오는데, 그 조합은 특성표
       바깥이라 쓰면 안 된다.
    """
    ref = clk["MK_PLL1_REF_HZ"]
    assert clk["MK_HSE_HZ"] % clk["MK_PLL1_DIVM"] == 0, "ref1_ck 가 정수가 아니다"
    assert 2_000_000 <= ref <= 16_000_000, f"ref1_ck = {float(ref)} Hz"


def test_vco_is_inside_the_wide_range(clk):
    """fVCO_OUT = 192~836 MHz (PLL1VCOSEL=0) — RM0468 p.381·p.383,
    STM32H723ZGT6.pdf p.112 Table 38."""
    vco = clk["MK_PLL1_VCO_HZ"]
    assert 192_000_000 <= vco <= 836_000_000, f"VCO = {float(vco)} Hz"


def test_pll_divider_fields_are_encodable(clk):
    """RM0468 p.377(DIVM1) · p.383(DIVN1·DIVP1)."""
    assert 1 <= clk["MK_PLL1_DIVM"] <= 63
    assert 4 <= clk["MK_PLL1_DIVN"] <= 512
    divp = clk["MK_PLL1_DIVP"]
    assert 1 <= divp <= 128
    # 🔴 "odd division factors are not allowed, except for 1" (p.383)
    assert divp == 1 or divp % 2 == 0, f"DIVP1={divp} 은 홀수라 쓸 수 없다"


def test_p_output_is_inside_vos1_limit(clk):
    """fPLL_P_OUT ≤ 400 MHz @ VOS1 — STM32H723ZGT6.pdf p.112 Table 38."""
    assert Fraction(1_500_000) <= clk["MK_SYSCLK_HZ"] <= 400_000_000


def test_hse_frequency_is_a_supported_crystal(clk):
    """4~50 MHz — STM32H723ZGT6.pdf p.107 Table 32. Y1 = 25 MHz."""
    assert clk["MK_HSE_HZ"] == 25_000_000, "넷리스트의 Y1 은 25MHz 다"
    assert 4_000_000 <= clk["MK_HSE_HZ"] <= 50_000_000


def test_hal_conf_hse_value_matches(clk):
    """🔴 HSE_VALUE 가 틀리면 **시각이 통째로 틀린다.**

    HAL 은 이 값으로 SystemCoreClock 을 계산하고, 그것이 SysTick 재적재
    값이 된다. 8 MHz 로 남겨 두면 HAL_GetTick() 이 실제의 25/8 배로
    흐르고 — 그러면 하트비트 타임아웃도 PPS 나이도 전부 틀린다.
    """
    defs = _defines(FW / "bsp" / "stm32h7xx_hal_conf.h")
    assert "HSE_VALUE" in defs
    assert _eval(defs["HSE_VALUE"], defs) == clk["MK_HSE_HZ"]


# ── HSE 가 안 뜰 때 ────────────────────────────────────────────────────────

def test_hse_wait_is_bounded(clk):
    """🔴 크리스털이 안 뜨면 벽돌이 된다.

    HSERDY 를 무한히 기다리면 화면도 시리얼도 안 살아나 사람이 원인을 알
    방법이 없다. HAL 의 대기는 HSE_STARTUP_TIMEOUT 으로 잘리므로, 그 값이
    실제로 유한한지 그리고 우리가 의도한 값인지 본다.
    """
    conf = _defines(FW / "bsp" / "stm32h7xx_hal_conf.h")
    assert "HSE_STARTUP_TIMEOUT" in conf
    timeout = _eval(conf["HSE_STARTUP_TIMEOUT"], conf)
    assert 0 < timeout <= 1000, f"HSE 시한이 {float(timeout)} ms 다"
    assert timeout == clk["MK_HSE_TIMEOUT_MS"], (
        "시한이 두 곳에 따로 적혀 있다 — mk_clock.h 가 한 곳이어야 한다"
    )


def test_clock_init_falls_back_instead_of_hanging():
    """시한이 지나면 HSI 로 계속 부팅한다.

    🔴 `for (;;)` 로 멈추면 안 된다. 예전 main.c 의 SystemClock_Config 가
       바로 그랬다 — HAL_RCC_OscConfig 가 실패하면 영영 섰다. 그때는 PLL 도
       HSE 도 안 써서 실패할 일이 없었지만, 크리스털을 물린 지금은 다르다.
    """
    src = _strip_comments((FW / "bsp" / "mk_clock.c").read_text(
        encoding="utf-8"))
    assert not re.search(r"for\s*\(\s*;\s*;\s*\)", src), (
        "mk_clock.c 에 무한 루프가 있다 — 크리스털이 안 뜨면 벽돌이 된다"
    )
    assert not re.search(r"while\s*\(\s*1\s*\)", src)
    assert "MK_CLOCK_SRC_HSI" in src and "MK_CLOCK_SRC_HSE_PLL" in src, (
        "폴백 경로가 없다"
    )


def test_main_no_longer_owns_the_clock():
    """클럭 설정은 bsp/ 다. main.c 에 두 벌이 있으면 어느 쪽이 도는지
    읽어서는 알 수 없다."""
    src = _strip_comments((FW / "main.c").read_text(encoding="utf-8"))
    assert "SystemClock_Config" not in src, "main.c 가 아직 클럭을 설정한다"
    assert "mk_clock_init" in src
