#include "mk_gnss_io.h"

#include "stm32h7xx_hal.h"

#include "mk_clock.h"
#include "../app/mk_timeax.h"

/* ---- USART6 (GNSS NMEA) ---------------------------------------------------
 *
 * 링 구조는 mk_uart.c 와 같다. 다른 점은 여기가 **줄** 대신 **바이트**를
 * 내준다는 것 — mk_gnss.c 가 이미 바이트 단위로 문장을 조립하므로
 * (mk_gnss_feed), 이 층에서 다시 줄로 모을 필요가 없다. */
#define MK_GNSS_RX_RING_SIZE  256u

static UART_HandleTypeDef s_uart;
static volatile uint8_t   s_rx[MK_GNSS_RX_RING_SIZE];
static volatile uint16_t  s_head;
static volatile uint16_t  s_tail;
static volatile uint32_t  s_overruns;

/* ---- TIM8 CH3 (PPS 입력 캡처) ---------------------------------------------- */

static TIM_HandleTypeDef s_tim;

/* 🔴 1 MHz(1us 분해능)를 만든다. ARR=0xFFFF → 주기 65536us (65.536ms).
 *
 *    TIM8 은 APB2 에 있고, 프리스케일은 **손으로 적지 않고 클럭에서
 *    파생시킨다**(MK_TIM_PSC_1US_APB2, bsp/mk_clock.h). 이 값이 캡처
 *    레지스터의 단위를 정하므로, 클럭이 바뀌었는데 여기가 그대로면
 *    시간축이 **아무 오류 없이** 틀린 값을 낸다. 예전에는 63 이라고
 *    적혀 있었다. */
#define MK_GNSS_TIM_PERIOD  65536u    /* ARR+1 */

static volatile uint32_t s_wrap_count;
static volatile uint64_t s_pps_dev_us;
static volatile int      s_pps_pending;

void mk_gnss_io_init(uint32_t baud)
{
    /* ---- GPIO -------------------------------------------------------- */
    __HAL_RCC_GPIOC_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Mode  = GPIO_MODE_AF_PP;
    g.Pull  = GPIO_NOPULL;      /* 모듈이 양쪽 다 능동 구동한다 */
    g.Speed = GPIO_SPEED_FREQ_HIGH;

    g.Pin       = GPIO_PIN_6 | GPIO_PIN_7;      /* GNSS_TX/GNSS_RX (넷 이름) */
    g.Alternate = GPIO_AF7_USART6;              /* DS13313 Rev 1 p.72 Table 8 */
    HAL_GPIO_Init(GPIOC, &g);

    g.Pin       = GPIO_PIN_8;                   /* GNSS_PPS */
    g.Alternate = GPIO_AF3_TIM8;                /* 같은 표 — TIM3_CH3(AF2) 아님, mk_gnss_io.h 참고 */
    HAL_GPIO_Init(GPIOC, &g);

    /* ---- USART6 -------------------------------------------------------
     *
     * 🔴 SWAP 필수. mk_gnss_io.h 상단 주석 — Table 8 은 PC6 을 USART6_TX
     *    로 싣지만 넷 이름은 GNSS_TX(모듈→MCU)다. SWAP=1 로 전기적
     *    TX/RX 를 뒤집어 넷 이름과 맞춘다(RM0468 p.2116, USART_CR2.SWAP). */
    __HAL_RCC_USART6_CLK_ENABLE();

    s_uart.Instance                    = USART6;
    s_uart.Init.BaudRate               = baud;
    s_uart.Init.WordLength             = UART_WORDLENGTH_8B;
    s_uart.Init.StopBits               = UART_STOPBITS_1;
    s_uart.Init.Parity                 = UART_PARITY_NONE;
    s_uart.Init.Mode                   = UART_MODE_TX_RX;
    s_uart.Init.HwFlowCtl              = UART_HWCONTROL_NONE;
    s_uart.Init.OverSampling           = UART_OVERSAMPLING_16;
    s_uart.Init.OneBitSampling         = UART_ONE_BIT_SAMPLE_DISABLE;
    s_uart.Init.ClockPrescaler         = UART_PRESCALER_DIV1;
    s_uart.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_SWAP_INIT;
    s_uart.AdvancedInit.Swap           = UART_ADVFEATURE_SWAP_ENABLE;
    HAL_UART_Init(&s_uart);

    s_head = 0;
    s_tail = 0;
    s_overruns = 0;

    /* 🔴 mk_uart.c 와 같은 이유로 RXNE 를 직접 켠다 — HAL_UART_Receive_IT
     *    는 정해진 개수를 받으면 멈춘다. NMEA 문장 길이는 문장마다 달라
     *    맞지 않는다. */
    __HAL_UART_ENABLE_IT(&s_uart, UART_IT_RXNE);
    HAL_NVIC_SetPriority(USART6_IRQn, 5, 0);   /* mk_uart.c 의 USART3 과 같은 급 */
    HAL_NVIC_EnableIRQ(USART6_IRQn);

    /* ---- TIM8 CH3 입력 캡처 --------------------------------------------- */
    __HAL_RCC_TIM8_CLK_ENABLE();

    s_tim.Instance               = TIM8;
    s_tim.Init.Prescaler         = MK_TIM_PSC_1US_APB2;
    s_tim.Init.CounterMode       = TIM_COUNTERMODE_UP;
    s_tim.Init.Period            = MK_GNSS_TIM_PERIOD - 1u;   /* ARR */
    s_tim.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    s_tim.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    HAL_TIM_IC_Init(&s_tim);

    TIM_IC_InitTypeDef ic = {0};
    ic.ICPolarity  = TIM_ICPOLARITY_RISING;   /* PPS 는 초 경계에서 올라간다 */
    ic.ICSelection = TIM_ICSELECTION_DIRECTTI;
    ic.ICPrescaler = TIM_ICPSC_DIV1;          /* 엣지마다 캡처 — 거르지 않는다 */
    ic.ICFilter    = 0u;
    HAL_TIM_IC_ConfigChannel(&s_tim, &ic, TIM_CHANNEL_3);

    s_wrap_count = 0u;
    s_pps_pending = 0;
    s_pps_dev_us = 0u;

    /* 🔴 Update(오버플로) 인터럽트가 Capture 보다 급해야 한다 — 같은
     *    순간 둘 다 걸리면 wrap_count 갱신이 먼저 끝나 있어야
     *    mk_timeax_extend_ticks() 의 update_pending 보정이 뜻대로 맞는다.
     *    [일반 기법·실기기 미검증] mk_timeax.h 상단 주석 참고. */
    HAL_NVIC_SetPriority(TIM8_UP_TIM13_IRQn, 3, 0);
    HAL_NVIC_EnableIRQ(TIM8_UP_TIM13_IRQn);
    HAL_NVIC_SetPriority(TIM8_CC_IRQn, 4, 0);
    HAL_NVIC_EnableIRQ(TIM8_CC_IRQn);

    __HAL_TIM_ENABLE_IT(&s_tim, TIM_IT_UPDATE);
    HAL_TIM_IC_Start_IT(&s_tim, TIM_CHANNEL_3);
}

int mk_gnss_io_write(void *ctx, const char *data, size_t len)
{
    (void)ctx;
    if (data == NULL || len == 0u) {
        return 0;
    }
    HAL_StatusTypeDef st = HAL_UART_Transmit(&s_uart, (const uint8_t *)data,
                                             (uint16_t)len, 100u);
    return st == HAL_OK ? 1 : 0;
}

/* ---- USART6 ISR ------------------------------------------------------------ */

void mk_gnss_io_uart_isr(void)
{
    uint32_t isr = s_uart.Instance->ISR;

    if (isr & USART_ISR_RXNE_RXFNE) {
        uint8_t b = (uint8_t)s_uart.Instance->RDR;
        uint16_t next = (uint16_t)((s_head + 1u) % MK_GNSS_RX_RING_SIZE);
        if (next == s_tail) {
            s_overruns++;         /* 링이 찼다 — 새 바이트를 버린다(mk_uart.c 와 같은 정책) */
        } else {
            s_rx[s_head] = b;
            s_head = next;
        }
    }

    if (isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) {
        s_uart.Instance->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                               USART_ICR_NECF  | USART_ICR_PECF;
    }
}

int mk_gnss_io_read_byte(uint8_t *out)
{
    if (s_tail == s_head) {
        return 0;
    }
    *out = s_rx[s_tail];
    s_tail = (uint16_t)((s_tail + 1u) % MK_GNSS_RX_RING_SIZE);
    return 1;
}

uint32_t mk_gnss_io_rx_overruns(void)
{
    return s_overruns;
}

/* ---- TIM8 ISR --------------------------------------------------------------
 *
 * 🔴 캡처만 한다. 짝짓기·등급 판단은 app/mk_timeax.c 슈퍼루프의 몫이다
 *    (이 저장소의 관례 — mk_sol.c·mk_ads_io.c 와 같다). */

void mk_gnss_io_tim_up_isr(void)
{
    if (__HAL_TIM_GET_FLAG(&s_tim, TIM_FLAG_UPDATE) != RESET &&
        __HAL_TIM_GET_IT_SOURCE(&s_tim, TIM_IT_UPDATE) != RESET) {
        __HAL_TIM_CLEAR_FLAG(&s_tim, TIM_FLAG_UPDATE);
        s_wrap_count++;
    }
}

void mk_gnss_io_tim_cc_isr(void)
{
    if (__HAL_TIM_GET_FLAG(&s_tim, TIM_FLAG_CC3) == RESET) {
        return;
    }
    __HAL_TIM_CLEAR_FLAG(&s_tim, TIM_FLAG_CC3);

    uint16_t ccr = (uint16_t)HAL_TIM_ReadCapturedValue(&s_tim, TIM_CHANNEL_3);
    /* 🔴 이 순간 Update 가 아직 안 지워졌으면 "겹침 후보" 다 —
     *    mk_timeax_extend_ticks() 의 update_pending 인자가 이것이다. */
    int update_pending = (__HAL_TIM_GET_FLAG(&s_tim, TIM_FLAG_UPDATE) != RESET) ? 1 : 0;
    uint32_t wraps = s_wrap_count;

    s_pps_dev_us = mk_timeax_extend_ticks(wraps, ccr, MK_GNSS_TIM_PERIOD, update_pending);
    s_pps_pending = 1;
}

int mk_gnss_io_pps_take(uint64_t *dev_us)
{
    /* 🔴 두 volatile 을 임계구역 없이 읽는다 — 최악의 경우 이번 tick 에
     *    안 잡히고 다음 tick 에 잡힐 뿐, 값이 섞이지는 않는다(각각
     *    독립적으로 원자적인 32/64비트 정렬 접근이며 Cortex-M7 은 정렬된
     *    64비트 volatile 읽기가 단일 로드로 안 끝날 수 있어 이론적으로는
     *    찢어질 수 있다 — 그래도 "이번에 못 봤다" 정도의 결과이지 시각이
     *    거꾸로 가지는 않는다. 1Hz 신호라 다음 기회가 금방 온다). */
    if (!s_pps_pending) {
        return 0;
    }
    *dev_us = s_pps_dev_us;
    s_pps_pending = 0;
    return 1;
}

uint64_t mk_gnss_io_now_us(void)
{
    uint32_t primask = __get_PRIMASK();
    __disable_irq();

    uint32_t wraps = s_wrap_count;
    uint16_t cnt = (uint16_t)s_tim.Instance->CNT;

    if (!primask) {
        __enable_irq();
    }
    return (uint64_t)wraps * MK_GNSS_TIM_PERIOD + cnt;
}
