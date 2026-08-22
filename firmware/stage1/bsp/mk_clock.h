/* MarkON 클럭 — 이 보드의 클럭 사실이 있는 **한 곳**.
 *
 * 🔴 왜 한 곳인가
 *
 *    예전에는 "64 MHz" 가 여섯 파일에 숫자로 흩어져 있었다: TIM8
 *    프리스케일 63, SPI4 분주비 128, I2C TIMINGR, UART BRR, 바쁜 대기의
 *    `64u / 3u + 1u`, WS2812 의 ARR 79. 클럭을 올리면 그것들이 **조용히**
 *    틀어진다 — 컴파일도 링크도 부팅도 되고, 증상은 "I2C 가 가끔 안
 *    읽힌다" · "ADS1256 값이 가끔 이상하다" 로 나온다. 그때 클럭을
 *    의심하는 사람은 없다.
 *
 *    그래서 값은 여기에만 두고 나머지는 파생시킨다. 파생시킬 수 없는
 *    것(I2C TIMINGR)은 **어느 클럭에서 뽑았는지**를 상수로 남겨 두어
 *    클럭이 바뀌면 시험이 깨지게 한다(host/tests/test_firmware_clock.py).
 *
 * 🔴 이 헤더는 HAL 도 CMSIS 도 include 하지 않는다. 순수한 숫자만 있어야
 *    호스트의 파이썬 시험이 그대로 읽고 계산할 수 있다.
 *
 * 근거
 *   RM0468 Rev 3 p.377  RCC_PLLCKSELR (DIVM1 = 1~63)
 *                 p.381  RCC_PLLCFGR  (PLL1RGE · PLL1VCOSEL)
 *                 p.383  RCC_PLL1DIVR (DIVN1 = 4~512, DIVP1 홀수 금지)
 *   STM32H723ZGT6.pdf p.107 Table 32  HSE 4~50 MHz
 *                     p.110 Table 35  HSI ±0.47 %(교정점) · ±1 %(온도)
 *                     p.112 Table 38  fPLL_IN 2~16 MHz · fVCO 192~836 MHz
 *                                     · fPLL_P_OUT ≤ 400 MHz @VOS1
 *   넷리스트 STM32_v2.0_current.net  Y1 = 25MHz, HSE_IN/HSE_OUT ↔ U5.23/24
 */
#ifndef MK_CLOCK_H
#define MK_CLOCK_H

#include <stdint.h>

/* ── 발진기 ──────────────────────────────────────────────────────────────
 *
 * 🔴 시스템 클럭을 HSI 가 아니라 HSE 로 잡는 이유는 **정확도 하나**다.
 *
 *    이 시스템의 존재 이유는 카메라 동기다(최대 33 ms, 목표는 센서값
 *    10 ms 간격). 시간축은 PPS 로 1초마다 맞추고 **그 사이는 타이머로
 *    보간**하므로, 타이머의 정확도가 곧 초 안쪽 오차다.
 *
 *    HSI 는 교정 지점에서도 ±0.47 %, 온도 −20~105 °C 에서 ±1 % 다
 *    (STM32H723ZGT6.pdf p.110 Table 35: fHSI 63.7~64.3 MHz,
 *     ΔTEMP(HSI) −1 %~+1 %). 1 % 면 1초 끝에서 10 ms — 목표 분해능
 *    전체와 같다.
 *
 *    Y1(25 MHz)은 부하 커패시터 C90·C91 까지 갖춰 이미 물려 있다. */
#define MK_HSE_HZ              25000000u

/* HSI 는 끄지 않는다. SPI2(LCD)의 커널 클럭이 per_ck = hsi_ker_ck 이고
 * (bsp/mk_lcd_io.c 가 직접 고른다), HSE 가 안 떴을 때의 폴백이기도 하다. */
#define MK_HSI_HZ              64000000u

/* 🔴 HSE 가 안 뜨면 이 시간만큼만 기다리고 HSI 로 계속 부팅한다.
 *
 *    영영 기다리면 화면도 시리얼도 안 살아나 사람이 원인을 알 방법이
 *    없다 — 크리스털 하나 때문에 보드가 벽돌이 된다. tSU(HSE) 는 부품에
 *    따라 ms 단위이므로 100 ms 는 넉넉하다.
 *
 *    HAL 은 이 값을 HSE_STARTUP_TIMEOUT 이라는 이름으로 읽는다
 *    (bsp/stm32h7xx_hal_conf.h 가 여기에 맞춘다). */
#define MK_HSE_TIMEOUT_MS      100u

/* ── PLL1 ────────────────────────────────────────────────────────────────
 *
 * 🔴 주파수는 올리지 않는다(사용자 결정 2026-08-19). 기존 타이밍 상수가
 *    전부 64 MHz 위에 서 있어서, 주파수까지 같이 바꾸면 무엇이 깨졌는지
 *    가릴 수 없다. 이번 작업의 이득은 정확도 하나면 충분하다.
 *
 * sys_ck = pll1_p_ck = HSE / DIVM × DIVN / DIVP
 *        = 25 MHz / 5 × 128 / 10 = 64.000000 MHz  (정확히)
 *
 * 🔴 왜 이 조합인가 — 정확히 64 MHz 를 내는 조합은 몇 개뿐이다.
 *
 *    25·DIVN = 64·DIVM·DIVP 를 풀면 gcd(25,64)=1 이므로 DIVN = 64t,
 *    DIVM·DIVP = 25t 다. 후보는 셋뿐이었다:
 *
 *      t=1  DIVN=64,  DIVM=25, DIVP=1   → VCO 64 MHz   ✗ 192 MHz 미만
 *      t=2  DIVN=128, DIVM=5,  DIVP=10  → VCO 640 MHz  ✓  ← 채택
 *      t=4  DIVN=256, DIVM=25, DIVP=4   → VCO 256 MHz  △ ref 가 1 MHz
 *
 *    t=4 를 버린 이유: ref1_ck 가 1 MHz 가 된다. RM0468 p.383 은 1~16 MHz
 *    라고 적지만 **데이터시트가 더 좁다** — STM32H723ZGT6.pdf p.112
 *    Table 38 의 fPLL_IN 은 Min 2 MHz 다. 특성표 바깥의 조합은 쓰지
 *    않는다. 위상 비교 주파수가 높을수록 지터에도 유리하다(같은 표의
 *    Cycle-to-cycle jitter 는 VCO 가 높을수록 작다).
 *
 *    DIVP 는 홀수를 쓸 수 없다(RM0468 p.383: "odd division factors are
 *    not allowed, except for 1"). 10 은 짝수라 통과한다. */
#define MK_PLL1_DIVM           5u
#define MK_PLL1_DIVN           128u
#define MK_PLL1_DIVP           10u

/* Q·R 출력은 아무도 안 쓴다. HAL_RCC_OscConfig 가 셋을 다 켜므로
 * (stm32h7xx_hal_rcc.c) 유효한 값이어야 한다 — 640/8 = 80 MHz. */
#define MK_PLL1_DIVQ           8u
#define MK_PLL1_DIVR           8u

#define MK_PLL1_REF_HZ         (MK_HSE_HZ / MK_PLL1_DIVM)        /*   5 MHz */
#define MK_PLL1_VCO_HZ         (MK_PLL1_REF_HZ * MK_PLL1_DIVN)   /* 640 MHz */

/* ── 버스 ────────────────────────────────────────────────────────────────
 *
 * 분주는 전부 1 이다. 그래서 sys_ck = HCLK = APB1 = APB2 = 64 MHz 이고,
 * 타이머 클럭도 APB 클럭과 같다 — APB 프리스케일이 1 이면 ×2 배율이
 * 걸리지 않는다(RM0468 §8.5.5, D2CFGR). */
#define MK_SYSCLK_HZ           (MK_PLL1_VCO_HZ / MK_PLL1_DIVP)
#define MK_D1CPRE              1u
#define MK_HPRE                1u
#define MK_D2PPRE1             1u
#define MK_D2PPRE2             1u

#define MK_HCLK_HZ             (MK_SYSCLK_HZ / MK_D1CPRE / MK_HPRE)
#define MK_APB1_HZ             (MK_HCLK_HZ / MK_D2PPRE1)
#define MK_APB2_HZ             (MK_HCLK_HZ / MK_D2PPRE2)
#define MK_APB1_TIMER_HZ       MK_APB1_HZ
#define MK_APB2_TIMER_HZ       MK_APB2_HZ

/* ── 주변장치 커널 클럭 ──────────────────────────────────────────────────
 *
 * 🔴 커널 클럭은 버스 클럭과 다를 수 있다. 여기 적힌 것은 **이 펌웨어가
 *    실제로 고른 것**이지 일반론이 아니다. 리셋값에 기대는 것은 그렇게
 *    적어 둔다 — 누가 D2CCIP*R 을 건드리면 여기부터 다시 봐야 한다. */
#define MK_SPI4_KERNEL_HZ      MK_APB2_HZ   /* D2CCIP1R.SPI45SEL=000 리셋값 */
#define MK_I2C_KERNEL_HZ       MK_APB1_HZ   /* D2CCIP2R.I2C1235SEL=00 리셋값 */
#define MK_USART3_KERNEL_HZ    MK_APB1_HZ   /* 호스트 링크(PB10/PB11) */
#define MK_USART2_KERNEL_HZ    MK_APB1_HZ   /* Jetson 링크(PA2/PA3) — USART3 과
                                               같은 D2CCIP2R.USART234578SEL 리셋값 */
#define MK_USART6_KERNEL_HZ    MK_APB2_HZ   /* GNSS(PC6/PC7) */

/* 🔴 SPI2(LCD)만 sys_ck 계열이 아니다. per_ck = hsi_ker_ck 를
 *    bsp/mk_lcd_io.c 가 직접 골라 준다. 그래서 시스템 클럭을 HSE 로
 *    옮겨도 화면 SPI 클럭 표(app/mk_cfgtable.c 의 lcd.spi_khz)는
 *    안 바뀐다. */
#define MK_SPI2_KERNEL_HZ      MK_HSI_HZ

/* ── 파생 ────────────────────────────────────────────────────────────────*/

/* 1 us 분해능(1 MHz) 타이머를 만드는 프리스케일. TIM8(PPS 입력 캡처)이
 * 쓴다 — 캡처 값의 단위가 여기서 정해진다. */
#define MK_TIM_PSC_1US_APB1    (MK_APB1_TIMER_HZ / 1000000u - 1u)
#define MK_TIM_PSC_1US_APB2    (MK_APB2_TIMER_HZ / 1000000u - 1u)

/* 바쁜 대기 한 바퀴 수. `app/` 은 HAL 을 모르므로 us 대기를 bsp 콜백으로
 * 받는데(MkAdsIo.delay_us · MkI2cIo.delay_us), 그 루프 횟수가 클럭에
 * 매여 있어야 한다.
 *
 * 🔴 한 바퀴를 **최소 3사이클**로 보아 넉넉히 잡는다. 남는 것은 무해하고
 *    모자라면 값이 틀린다 — ADS1256 의 t6 가 실제로 그 증상을 냈다. */
#define MK_BUSY_WAIT_LOOPS_PER_US  (MK_SYSCLK_HZ / 1000000u / 3u + 1u)

/* SPI_CFG1.MBR 필드값 → 분주비 (RM0468 §55, MBR = log2(div) − 1). */
#define MK_SPI_DIV_FROM_MBR(mbr)   (1u << ((mbr) + 1u))

/* ── 컴파일 시 확인 ──────────────────────────────────────────────────────
 *
 * 🔴 같은 불변조건이 host/tests/test_firmware_clock.py 에도 있다. 이쪽은
 *    ARM 빌드에서만 돌고 저쪽은 보드도 크로스 컴파일러도 없이 돈다.
 *    저쪽은 정수 나눗셈이 아니라 분수로 풀어 63.9999 MHz 를 잡아낸다. */
_Static_assert(MK_SYSCLK_HZ == 64000000u,
               "sys_ck != 64 MHz: every timing constant below was computed for 64 MHz");
_Static_assert(MK_HSE_HZ % MK_PLL1_DIVM == 0u,
               "ref1_ck is not an integer: derived values would be rounded");
_Static_assert(MK_PLL1_VCO_HZ % MK_PLL1_DIVP == 0u,
               "sys_ck is not an integer");
_Static_assert(MK_PLL1_REF_HZ >= 2000000u && MK_PLL1_REF_HZ <= 16000000u,
               "fPLL_IN out of range (2..16 MHz) - STM32H723ZGT6.pdf p.112 Table 38");
_Static_assert(MK_PLL1_VCO_HZ >= 192000000u && MK_PLL1_VCO_HZ <= 836000000u,
               "fVCO_OUT out of range (192..836 MHz) - RM0468 p.383, PLL1VCOSEL=0");
_Static_assert(MK_SYSCLK_HZ >= 1500000u && MK_SYSCLK_HZ <= 400000000u,
               "fPLL_P_OUT out of range at VOS1 - STM32H723ZGT6.pdf p.112 Table 38");
_Static_assert(MK_PLL1_DIVM >= 1u && MK_PLL1_DIVM <= 63u,
               "DIVM1 must be 1..63 (6 bit field) - RM0468 p.377");
_Static_assert(MK_PLL1_DIVN >= 4u && MK_PLL1_DIVN <= 512u,
               "DIVN1 must be 4..512 - RM0468 p.383");
_Static_assert(MK_PLL1_DIVP == 1u || (MK_PLL1_DIVP % 2u) == 0u,
               "DIVP1 odd division factors are not allowed except 1 - RM0468 p.383");
_Static_assert(MK_HSE_HZ >= 4000000u && MK_HSE_HZ <= 50000000u,
               "HSE out of range (4..50 MHz) - STM32H723ZGT6.pdf p.107 Table 32");
_Static_assert(MK_APB1_TIMER_HZ % 1000000u == 0u &&
               MK_APB2_TIMER_HZ % 1000000u == 0u,
               "cannot make a 1 us timebase with an integer prescaler");

/* ── 클럭 출처 ───────────────────────────────────────────────────────────
 *
 * 🔴 이것은 진단 정보가 아니라 **시간축 신뢰도의 일부**다(규격 §7.4).
 *    HSI 로 떨어지면 초 안쪽 보간이 두 자릿수 나빠진다 — 호스트가 그것을
 *    모르고 저장하면 안 된다. */
typedef enum {
    MK_CLOCK_SRC_HSI = 0,      /* 크리스털이 안 떴다. 계속은 돌지만 ±1 % */
    MK_CLOCK_SRC_HSE_PLL = 1   /* Y1(25 MHz) → PLL1 → 64 MHz */
} MkClockSource;

/* 전원 스케일링부터 시작해 sys_ck 를 세운다. HSE 가 MK_HSE_TIMEOUT_MS
 * 안에 안 뜨면 HSI 로 계속 부팅한다 — 절대 서지 않는다. */
void mk_clock_init(void);

/* 지금 무엇으로 도는가. mk_clock_init() 뒤에만 뜻이 있다. */
MkClockSource mk_clock_source(void);

/* 규격 §7.4 의 `clock.src` 문자열: "hse_pll" 또는 "hsi". */
const char *mk_clock_source_name(void);

/* 실제로 세워진 sys_ck. 폴백해도 64 MHz 지만, 값을 지어내지 않고
 * 세워진 것을 그대로 돌려준다. */
uint32_t mk_clock_sysclk_hz(void);

#endif /* MK_CLOCK_H */
