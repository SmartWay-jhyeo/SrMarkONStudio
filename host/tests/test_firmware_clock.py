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


def _eval_c(expr: str, defs: dict[str, str]) -> int:
    """같은 식을 **C 의 정수 나눗셈 그대로** 푼다.

    🔴 위의 `_eval` 은 분수로 풀어 "정확히 64 MHz 인가" 를 본다. 이쪽은
       반대로 **컴파일러가 실제로 넣을 값**을 본다. 일부러 버림하는
       상수(MK_BUSY_WAIT_LOOPS_PER_US)의 불변조건은 이 값으로 봐야 한다.
    """
    value = _eval(expr, defs)      # 식별자 검사·순환 검사를 그대로 재사용
    del value
    expanded = _strip_comments(expr)
    expanded = re.sub(r"\(\s*(?:unsigned\s+|signed\s+)?"
                      r"(?:u?int(?:8|16|32|64)_t|unsigned|int|long|short)\s*\)",
                      " ", expanded)
    expanded = re.sub(r"\b(\d+)[uUlL]+\b", r"\1", expanded)

    def sub(m: re.Match[str]) -> str:
        return "(" + str(_eval_c(defs[m.group(0)], defs)) + ")"

    expanded = re.sub(r"\b(?!0[xX])[A-Za-z_]\w*\b", sub, expanded)
    expanded = expanded.replace("/", "//")
    return eval(expanded, {"__builtins__": {}}, {})  # noqa: S307


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
#:    **버림이 설계의 일부**라는 뜻이고, 그 대신 아래에서 따로 검사한다.
TRUNCATION_IS_INTENDED = {
    # 올림 아닌 버림 + 1 로 "넉넉한 쪽" 을 잡는 관용식이다.
    # test_busy_wait_loop_constant_is_derived 가 실제 불변조건을 본다.
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


# ── 파생 상수 ──────────────────────────────────────────────────────────────

def _assignment(path: Path, pattern: str) -> str:
    text = _strip_comments(path.read_text(encoding="utf-8"))
    m = re.search(pattern, text)
    assert m is not None, f"{path.name} 에서 {pattern} 를 못 찾았다"
    return m.group(1)


def test_tim8_prescaler_makes_a_1mhz_timebase(clk):
    """🔴 PPS 입력 캡처의 분해능이 여기서 정해진다.

    TIM8 은 APB2 에 있고 PSC 로 1 MHz(1 us)를 만든다. 클럭이 바뀌었는데
    이 프리스케일이 그대로면 캡처 값이 us 가 아닌 다른 단위가 되고,
    시간축은 **아무 오류 없이** 틀린 값을 낸다.
    """
    defs = _defines(FW / "bsp" / "mk_gnss_io.c")
    expr = _assignment(FW / "bsp" / "mk_gnss_io.c",
                       r"s_tim\.Init\.Prescaler\s*=\s*([^;]+);")
    psc = _eval(expr, {**defs, **{k: str(v) for k, v in clk.items()}})
    assert (clk["MK_APB2_TIMER_HZ"] / (psc + 1)) == 1_000_000, (
        f"TIM8 이 {float(clk['MK_APB2_TIMER_HZ'] / (psc + 1))} Hz 로 돈다"
    )


def test_ws2812_slot_is_1250ns(clk):
    """WS2812B 한 비트 = 1.25 us. TIM3 은 APB1 에 있고 PSC=0 이다.

    ARR 은 app/mk_ws2812.h 에 있다 — 그쪽은 HAL 도 bsp 도 모르는 층이라
    클럭 상수를 include 하지 못한다. 그래서 **여기서 묶는다.**
    """
    ws = _defines(FW / "app" / "mk_ws2812.h")
    arr = _eval(ws["MK_WS2812_ARR"], ws)
    psc = _eval(_assignment(FW / "bsp" / "mk_ws2812_io.c",
                            r"s_tim\.Init\.Prescaler\s*=\s*([^;]+);"),
                {**_defines(FW / "bsp" / "mk_ws2812_io.c"),
                 **{k: str(v) for k, v in clk.items()}})
    tick_hz = clk["MK_APB1_TIMER_HZ"] / (psc + 1)
    slot_ns = (arr + 1) * Fraction(1_000_000_000) / tick_hz
    assert slot_ns == 1250, f"한 슬롯이 {float(slot_ns)} ns 다"


def test_ads1256_spi_clock_is_within_the_chip_limit(clk):
    """SCLK ≤ fCLKIN/4 = 1.92 MHz — ADS1256.pdf.

    SPI4 커널 클럭은 APB2 다(D2CCIP1R.SPI45SEL 리셋값 000).
    """
    defs = {**_defines(FW / "bsp" / "mk_ads_io.c"),
            **{k: str(v) for k, v in clk.items()}}
    assert "MK_ADS_SPI_DIV" in defs, "SPI4 분주비가 이름을 갖고 있어야 한다"
    sclk = clk["MK_SPI4_KERNEL_HZ"] / _eval(defs["MK_ADS_SPI_DIV"], defs)
    assert sclk <= 1_920_000, f"SCLK = {float(sclk)} Hz 는 상한을 넘는다"
    # 🔴 ADS1256 의 t6(마지막 SCLK ~ DOUT 구동)를 이 속도 위에서 계산해
    #    두었다(app/mk_ads1256.c). 클럭이 바뀌면 그 계산도 다시 봐야 한다.
    assert sclk == 500_000, f"SCLK 가 500 kHz 에서 {float(sclk)} Hz 로 바뀌었다"


def test_i2c_timingr_is_anchored_to_the_clock_it_was_computed_for(clk):
    """🔴 TIMINGR 은 파생시킬 수 없다.

    SDADEL·SCLDEL·아날로그 필터 지연까지 함께 푸는 계산이라 ST 의 유틸리티
    (i2c_timing_utility.c)로 뽑는다. 그래서 **어느 클럭에서 뽑았는지**를
    상수로 남기고 여기서 대조한다 — 클럭이 바뀌면 이 시험이 깨지고,
    그것이 "유틸리티를 다시 돌려라" 는 신호다.
    """
    defs = {**_defines(FW / "bsp" / "mk_i2c_io.c"),
            **{k: str(v) for k, v in clk.items()}}
    assert "MK_I2C_TIMINGR_100K_KERNEL_HZ" in defs, (
        "TIMINGR 이 어느 커널 클럭에서 나왔는지 적혀 있지 않다"
    )
    assert _eval(defs["MK_I2C_TIMINGR_100K_KERNEL_HZ"], defs) == \
        clk["MK_I2C_KERNEL_HZ"]


def test_uart_baud_error_is_inside_the_usual_tolerance(clk):
    """USART3(PB10/PB11) — BRR 은 정수라 반드시 오차가 생긴다.

    보통 허용치는 2~3 % 다. 921600 에서 64 MHz 는 +0.64 % 였다.
    """
    defs = {**_defines(FW / "main.c"), **{k: str(v) for k, v in clk.items()}}
    baud = _eval(defs["UART_BAUD"], defs)
    brr = int(clk["MK_USART3_KERNEL_HZ"] / baud)
    assert brr >= 16, f"BRR={brr} — 16 미만이면 오버샘플 16 으로 못 낸다"
    actual = clk["MK_USART3_KERNEL_HZ"] / brr
    err = abs(actual - baud) / baud
    assert err < Fraction(2, 100), f"보율 오차 {float(err) * 100:.2f} %"


def test_lcd_spi_choices_come_from_the_spi2_kernel_clock(clk):
    """설정 카탈로그의 `lcd.spi_khz` 선택지는 분주비로 실제 낼 수 있는
    값이어야 한다.

    🔴 SPI2 의 커널 클럭은 sys_ck 가 아니라 **per_ck = hsi_ker_ck** 다
       (bsp/mk_lcd_io.c 가 직접 고른다). 그래서 시스템 클럭을 HSE 로
       옮겨도 이 표는 안 바뀐다 — 그 사실 자체를 여기 적어 둔다.
    """
    text = _strip_comments((FW / "app" / "mk_cfgtable.c").read_text(
        encoding="utf-8"))
    m = re.search(r"LCD_SPI_KHZ_CHOICES\[\]\s*=\s*\{([^}]*)\}", text)
    assert m is not None, "LCD_SPI_KHZ_CHOICES 를 못 찾았다"
    choices = sorted(int(x) for x in re.findall(r"\d+", m.group(1)))
    kernel_khz = clk["MK_SPI2_KERNEL_HZ"] / 1000
    expected = sorted(int(kernel_khz / d) for d in (4, 8, 16, 32))
    assert choices == expected, f"{choices} != {expected}"


def test_busy_wait_loop_constant_is_derived(clk):
    """`app/` 이 HAL 을 모르므로 us 대기는 bsp 의 콜백이 낸다. 그 루프
    횟수가 클럭에 매여 있어야 한다 — 안 그러면 ADS1256 의 t6 도 AM2320 의
    깨우기 대기도 조용히 짧아진다."""
    for name in ("mk_ads_io.c", "mk_i2c_io.c"):
        text = _strip_comments((FW / "bsp" / name).read_text(encoding="utf-8"))
        assert "MK_BUSY_WAIT_LOOPS_PER_US" in text, (
            f"{name} 의 us 대기가 클럭 상수를 안 쓴다"
        )

    # 🔴 이 상수만 일부러 버림한다. 그러니 **C 가 실제로 넣을 값**으로
    #    불변조건을 본다: 한 바퀴 3사이클로 쳐서 1 us 를 채워야 한다.
    #    모자라면 ADS1256 의 t6 를 다시 어긴다(값이 조용히 틀린다).
    defs = _defines(CLOCK_H)
    loops = _eval_c(defs["MK_BUSY_WAIT_LOOPS_PER_US"], defs)
    cycles_per_us = clk["MK_SYSCLK_HZ"] / 1_000_000
    assert loops * 3 >= cycles_per_us, (
        f"1 us 대기가 {float(loops * 3 / cycles_per_us):.2f} us 밖에 안 된다"
    )


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


def test_every_selectable_link_baud_is_reachable_at_this_clock(clk):
    """🔴 카탈로그에 있는 링크 속도가 **실제로 나오는 값**인지 (규격 §4.2.6).

    BRR 은 정수라 반드시 오차가 생기고, 오차가 2 % 를 넘으면 프레임이
    깨진다. 못 내는 값을 목록에 두면 그것은 사용자가 고르는 순간에만
    드러나고 — 그 순간은 이미 링크가 끊긴 뒤다. 되돌리는 길은 보드가
    10초 뒤 스스로 돌아오는 것뿐이니 치명적이지는 않지만, 애초에 고를 수
    없어야 한다.

    🔴 **반올림**으로 계산한다. HAL 의 `UART_DIV_SAMPLING16` 이 그렇고
       (`(pclk + baud/2) / baud`), 실제로 레지스터에 들어가는 값이 그것이다.
       버림으로 계산하면 검사는 통과하는데 전선은 다른 속도로 도는 조합이
       생긴다.

    이 시험이 `test_uart_baud_error_...`(부팅 기본값 하나)를 대체하지 않는다 —
    저쪽은 main.c 의 상수를, 이쪽은 카탈로그의 목록 전체를 본다.
    """
    text = _strip_comments(
        (FW / "app" / "mk_linkbaud.h").read_text(encoding="utf-8"))
    m = re.search(r"#define\s+MK_LINKBAUD_CHOICE_LIST\(X\)((?:.*\\s*\n)*.*)",
                  text)
    assert m, "MK_LINKBAUD_CHOICE_LIST 를 못 찾았다"
    choices = [int(v) for v in re.findall(r"X\((\d+)u?\)", m.group(1))]
    assert choices, "목록에서 값을 하나도 못 뽑았다"

    kernel = clk["MK_USART3_KERNEL_HZ"]
    for baud in choices:
        brr = (kernel + baud // 2) // baud           # HAL 과 같은 반올림
        assert 16 <= brr <= 65535, (
            f"{baud}: BRR={brr} — 오버샘플 16 으로 낼 수 있는 범위 밖이다"
        )
        actual = Fraction(kernel, brr)
        err = abs(actual - baud) / baud
        assert err < Fraction(2, 100), (
            f"{baud}: 실제 {float(actual):.1f}, 오차 {float(err) * 100:.3f} % "
            f"— 목록에서 빼야 한다"
        )


def test_the_documented_baud_error_table_is_the_real_calculation(clk):
    """🔴 규격 §4.2.6 의 오차 표가 계산과 맞는지.

    표는 사람이 손으로 적은 것이고, 클럭이 바뀌면 조용히 틀린 값이 된다.
    사용자가 "1 Mbaud 는 오차가 없다" 를 보고 그것을 고르는데 실제로는
    아니라면, 이 문서가 사람을 잘못된 자리로 데려간 것이다.
    """
    spec = (FW.parents[1] / "protocol" / "specification.md").read_text(
        encoding="utf-8")
    rows = re.findall(
        r"^\|\s*\**([\d  ]+?)\**\s*\|\s*(\d+)\s*\|\s*([\d  ]+\.\d)\s*\|"
        r"\s*([+−-]?[\d.]+)\s*%\s*\|",
        spec, re.M)
    assert len(rows) >= 6, f"§4.2.6 표에서 행을 못 뽑았다 (찾은 것 {len(rows)})"

    kernel = clk["MK_USART3_KERNEL_HZ"]
    for baud_s, brr_s, actual_s, err_s in rows:
        baud = int(baud_s.replace(" ", "").replace(" ", ""))
        brr = (kernel + baud // 2) // baud
        assert brr == int(brr_s), f"{baud}: BRR 표 {brr_s}, 계산 {brr}"
        actual = Fraction(kernel, brr)
        assert abs(float(actual) - float(actual_s.replace(" ", ""))) < 0.1, (
            f"{baud}: 실제 속도 표 {actual_s}, 계산 {float(actual):.1f}")
        # 표는 부호를 함께 적는다 — 빠르냐 느리냐가 사람에게는 다른 정보다.
        want = float(err_s.replace("−", "-"))
        got = float((actual - baud) / baud * 100)
        assert abs(got - want) < 0.001, (
            f"{baud}: 오차 표 {err_s} %, 계산 {got:.3f} %")
