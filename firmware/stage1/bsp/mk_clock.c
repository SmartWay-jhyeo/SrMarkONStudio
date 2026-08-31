/* 시스템 클럭 세우기 — HSE(Y1, 25 MHz) → PLL1 → 64 MHz.
 *
 * 여기가 HAL 을 아는 유일한 클럭 코드다. 숫자는 전부 mk_clock.h 에 있고
 * 이 파일은 그것을 레지스터로 옮기기만 한다.
 *
 * 🔴 **절대 서지 않는다.**
 *
 *    크리스털이 안 뜨면(부품 불량·납땜·부하 커패시터) HSERDY 가 영영 안
 *    온다. 거기서 무한히 기다리면 화면도 시리얼도 안 살아나 사람이 원인을
 *    알 방법이 없다 — 크리스털 하나 때문에 보드가 벽돌이 된다.
 *
 *    그래서 시한(MK_HSE_TIMEOUT_MS)을 두고, 지나면 HSI 로 계속 부팅한다.
 *    HAL 이 그 대기를 HSE_STARTUP_TIMEOUT 으로 잘라 HAL_TIMEOUT 을
 *    돌려주므로(stm32h7xx_hal_rcc.c), 반환값을 보는 것으로 충분하다.
 *    PLL 락 대기도 PLL_TIMEOUT_VALUE(2 ms)로 잘린다 — tLOCK 은 최악
 *    150 us 다(STM32H723ZGT6.pdf p.112 Table 38).
 *
 *    그리고 그 사실을 $STAT 에 싣는다(규격 §7.4). HSI 로 떨어지면 초
 *    안쪽 보간이 두 자릿수 나빠지므로, 호스트가 모르고 저장하면 안 된다.
 *
 * 🔴 HSI 를 끄지 않는다. SPI2(LCD)의 커널 클럭이 per_ck = hsi_ker_ck 이고
 *    (bsp/mk_lcd_io.c 의 spi_init), 그것이 꺼지면 화면이 통째로 죽는다.
 *    아래 어느 경로도 HSI 를 건드리지 않는다 — RCC_OSCILLATORTYPE_HSI 를
 *    OscillatorType 에 넣지 않으면 HAL 은 HSI 를 그대로 둔다.
 */
#include "mk_clock.h"

#include "stm32h7xx_hal.h"

/* 🔴 HAL 이 SystemCoreClock 을 이 값으로 계산하고, 그것이 SysTick 재적재
 *    값이 된다. 틀리면 HAL_GetTick() 이 통째로 틀리고 — 하트비트 타임아웃도
 *    PPS 나이도 함께 틀린다. 두 곳에 적히므로 여기서 못박는다. */
_Static_assert(HSE_VALUE == MK_HSE_HZ,
               "HSE_VALUE in hal_conf differs from MK_HSE_HZ in mk_clock.h");
_Static_assert(HSE_STARTUP_TIMEOUT == MK_HSE_TIMEOUT_MS,
               "HSE_STARTUP_TIMEOUT differs from MK_HSE_TIMEOUT_MS");

static MkClockSource s_src = MK_CLOCK_SRC_HSI;

/* 🔴 FLASH 대기 사이클. 64 MHz · VOS1 에서 1 이다. 클럭을 올리면 여기부터
 *    본다 — 모자라면 읽기가 조용히 틀린다. */
#define MK_FLASH_LATENCY   FLASH_LATENCY_1

/* HSE 를 켜고 PLL1 을 건다. 성공하면 HAL_OK.
 *
 * 이 함수는 sys_ck 를 바꾸지 않는다 — 락이 걸린 뒤에 select_sysclk() 이
 * 옮긴다. 순서를 지켜야 PLL 이 안 떴을 때 되돌아갈 자리가 남는다. */
static HAL_StatusTypeDef start_hse_pll(void)
{
    RCC_OscInitTypeDef osc = {0};

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    /* 🔴 BYPASS 가 아니라 크리스털이다. 넷리스트에서 Y1(25MHz)의 양단이
     *    PH0-OSC_IN(U5.23)·PH1-OSC_OUT(U5.24)에 부하 커패시터 C90·C91 과
     *    함께 물려 있다 — 발진 회로를 MCU 가 돌려야 한다. */
    osc.HSEState = RCC_HSE_ON;

    osc.PLL.PLLState  = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLM      = MK_PLL1_DIVM;
    osc.PLL.PLLN      = MK_PLL1_DIVN;
    osc.PLL.PLLP      = MK_PLL1_DIVP;
    osc.PLL.PLLQ      = MK_PLL1_DIVQ;
    osc.PLL.PLLR      = MK_PLL1_DIVR;
    /* ref1_ck = 25/5 = 5 MHz → 4~8 MHz 구간 (RM0468 p.381, PLL1RGE=10) */
    osc.PLL.PLLRGE    = RCC_PLL1VCIRANGE_2;
    /* VCO = 5 × 128 = 640 MHz → wide 192~836 MHz (RM0468 p.381, VCOSEL=0) */
    osc.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
    /* 🔴 정수 모드다. 소수부를 쓰면 시그마-델타 변조가 붙어 지터가
     *    커지는데(같은 표의 "Sigma-delta mode" 행), 25 MHz 는 소수부 없이
     *    정확히 64 MHz 를 내므로 쓸 이유가 없다. */
    osc.PLL.PLLFRACN  = 0;

    return HAL_RCC_OscConfig(&osc);
}

/* HSE 를 끄고 HSI 를 sys_ck 소스로 되돌릴 준비를 한다.
 *
 * 🔴 HSE 를 끄는 것이 중요하다. 안 뜬 발진기를 켜 둔 채로 두면 계속
 *    전류를 먹고, 나중에 늦게 뜨면 아무도 안 보는 사이에 상태가 바뀐다. */
static void stop_hse(void)
{
    RCC_OscInitTypeDef osc = {0};
    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState       = RCC_HSE_OFF;
    osc.PLL.PLLState   = RCC_PLL_NONE;
    (void)HAL_RCC_OscConfig(&osc);   /* 실패해도 할 수 있는 것이 없다 */
}

/* sys_ck 를 고르고 버스 분주를 세운다. 분주는 전부 1 이다(mk_clock.h). */
static HAL_StatusTypeDef select_sysclk(uint32_t source)
{
    RCC_ClkInitTypeDef clk = {0};

    clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                    RCC_CLOCKTYPE_D1PCLK1 | RCC_CLOCKTYPE_PCLK1 |
                    RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_D3PCLK1;
    clk.SYSCLKSource   = source;
    clk.SYSCLKDivider  = RCC_SYSCLK_DIV1;
    clk.AHBCLKDivider  = RCC_HCLK_DIV1;
    clk.APB3CLKDivider = RCC_APB3_DIV1;
    clk.APB1CLKDivider = RCC_APB1_DIV1;
    clk.APB2CLKDivider = RCC_APB2_DIV1;
    clk.APB4CLKDivider = RCC_APB4_DIV1;

    return HAL_RCC_ClockConfig(&clk, MK_FLASH_LATENCY);
}

void mk_clock_init(void)
{
    /* 🔴 전원 공급 방식을 먼저 확정한다. 이 보드는 LDO 다(데이터시트 §4 —
     *    VCAP 에 커패시터, SMPS 인덕터 없음). 여기가 틀리면 이후 전압
     *    스케일링이 아예 안 걸린다. */
    HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY);
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {
        /* 내부 레귤레이터가 준비될 때까지. 여기가 안 끝나면 코어에 전압이
         * 안 선 것이라, 폴백을 두어도 실행할 코어가 없다. */
    }

    if (start_hse_pll() == HAL_OK && select_sysclk(RCC_SYSCLKSOURCE_PLLCLK) == HAL_OK) {
        s_src = MK_CLOCK_SRC_HSE_PLL;
        return;
    }

    /* ── 폴백 ───────────────────────────────────────────────────────────
     *
     * 여기 오는 것은 크리스털이 시한 안에 안 떴거나 PLL 이 락을 못 건
     * 경우다. 둘 다 하드웨어 문제이고 재시도로 풀리지 않는다 — 그러니
     * 다시 걸지 않고 HSI 로 간다. 정확도는 두 자릿수 나쁘지만(±1 %)
     * 보드는 살아 있고, $STAT 이 그 사실을 말한다. */
    stop_hse();

    RCC_OscInitTypeDef osc = {0};
    osc.OscillatorType      = RCC_OSCILLATORTYPE_HSI;
    osc.HSIState            = RCC_HSI_DIV1;          /* HSI = 64 MHz */
    osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    osc.PLL.PLLState        = RCC_PLL_NONE;
    (void)HAL_RCC_OscConfig(&osc);
    (void)select_sysclk(RCC_SYSCLKSOURCE_HSI);
    s_src = MK_CLOCK_SRC_HSI;
}

MkClockSource mk_clock_source(void)
{
    return s_src;
}

const char *mk_clock_source_name(void)
{
    return (s_src == MK_CLOCK_SRC_HSE_PLL) ? "hse_pll" : "hsi";
}

uint32_t mk_clock_sysclk_hz(void)
{
    /* 🔴 상수를 돌려주지 않는다. HAL 이 레지스터를 읽어 계산한 값이라,
     *    폴백했거나 분주가 의도와 다르면 그 사실이 그대로 나온다 —
     *    $STAT 이 "보드가 믿는 값" 이 아니라 "실제로 선 값" 을 싣는다. */
    return HAL_RCC_GetSysClockFreq();
}
