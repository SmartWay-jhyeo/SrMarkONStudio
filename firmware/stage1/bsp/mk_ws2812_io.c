#include "mk_ws2812_io.h"

#include "stm32h7xx_hal.h"

#include "mk_dma_mem.h"
#include "../app/mk_ws2812.h"

/* 🔴 이 파일이 GPIOA 핀 7 과 TIM3 을 만지는 유일한 곳이다. */
#define WS_PORT   GPIOA
#define WS_PIN    GPIO_PIN_7

/* 체인 최대치 + 리셋. */
#define WS_SLOTS  MK_WS2812_SLOTS(MK_LED_COUNT)

/* 🔴 DMA 가 닿는 곳에 둔다. 안 붙이면 링커가 DTCM 에 잡고, 전송만 조용히
 *    안 된다 (bsp/mk_dma_mem.h). 링크 뒤 check_dma_placement.py 가 잡는다. */
static uint16_t MK_DMA_BUF s_slots[WS_SLOTS];

static TIM_HandleTypeDef s_tim;
static DMA_HandleTypeDef s_dma;
static volatile int s_busy;

void mk_ws2812_io_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_TIM3_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin       = WS_PIN;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    /* 800kHz 자체는 느리지만 상승·하강이 무뎌지면 0 과 1 의 High 폭 차이가
     * 흐려진다. 여유를 둔다. */
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF2_TIM3;      /* DS13313 Rev 1 p.72 Table 8 */
    HAL_GPIO_Init(WS_PORT, &g);

    /* 🔴 분주 0 이다. TIM3 은 APB1 에 있고 이 펌웨어는 HSI 64MHz·분주 1 로
     *    돌므로 타이머 클럭이 곧 64MHz 다(main.c SystemClock_Config).
     *    ARR+1 = 80 → 1.25us = 800kHz. 클럭 설정을 바꾸면 여기가 함께
     *    틀어진다 — app/mk_ws2812.h 의 시험이 그 값을 못 박아 두고 있다. */
    s_tim.Instance               = TIM3;
    s_tim.Init.Prescaler         = 0u;
    s_tim.Init.CounterMode       = TIM_COUNTERMODE_UP;
    s_tim.Init.Period            = MK_WS2812_ARR;
    s_tim.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    s_tim.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_PWM_Init(&s_tim);

    TIM_OC_InitTypeDef oc = {0};
    oc.OCMode     = TIM_OCMODE_PWM1;
    oc.Pulse      = 0u;               /* 시작은 Low — 체인이 꺼진 채로 */
    oc.OCPolarity = TIM_OCPOLARITY_HIGH;
    oc.OCFastMode = TIM_OCFAST_DISABLE;
    HAL_TIM_PWM_ConfigChannel(&s_tim, &oc, TIM_CHANNEL_2);

    /* 🔴 Stream0·Stream1 은 SPI4(ADS1256)가 쓴다. 겹치면 둘 다 망가진다. */
    s_dma.Instance                 = DMA1_Stream2;
    s_dma.Init.Request             = DMA_REQUEST_TIM3_CH2;
    s_dma.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    s_dma.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_dma.Init.MemInc              = DMA_MINC_ENABLE;
    /* CCR2 는 32비트 레지스터지만 값은 16비트에 들어간다. 메모리도 16비트로
     * 맞춰 버퍼를 절반으로 쓴다. */
    s_dma.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
    s_dma.Init.MemDataAlignment    = DMA_MDATAALIGN_HALFWORD;
    s_dma.Init.Mode                = DMA_NORMAL;
    /* 🔴 수집(SPI4)보다 낮춘다. 램프 색이 한 프레임 늦는 것은 안 보이지만
     *    ADS1256 샘플이 밀리면 타임스탬프가 오염된다. */
    s_dma.Init.Priority            = DMA_PRIORITY_LOW;
    s_dma.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_dma);
    __HAL_LINKDMA(&s_tim, hdma[TIM_DMA_ID_CC2], s_dma);

    HAL_NVIC_SetPriority(DMA1_Stream2_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream2_IRQn);

    s_busy = 0;
}

uint16_t *mk_ws2812_io_buffer(size_t *cap)
{
    if (cap != NULL) {
        *cap = WS_SLOTS;
    }
    return s_slots;
}

int mk_ws2812_io_busy(void)
{
    return s_busy;
}

int mk_ws2812_io_send(size_t n)
{
    if (n == 0u || n > WS_SLOTS || s_busy) {
        return 0;
    }
    s_busy = 1;
    if (HAL_TIM_PWM_Start_DMA(&s_tim, TIM_CHANNEL_2,
                              (const uint32_t *)(const void *)s_slots,
                              (uint16_t)n) != HAL_OK) {
        s_busy = 0;
        return 0;
    }
    return 1;
}

void mk_ws2812_io_dma_isr(void)
{
    HAL_DMA_IRQHandler(&s_dma);
}

/* 전송이 끝나면 PWM 을 멈춘다.
 *
 * 🔴 멈추지 않으면 타이머가 계속 돌면서 **버퍼의 마지막 값을 반복**한다.
 *    마지막 슬롯이 듀티 0 이라 선은 Low 로 남지만, 다음 전송이 앞 프레임의
 *    꼬리에 이어 붙어 리셋 구간이 짧아진다. 그러면 색이 한 칸씩 밀린다. */
void HAL_TIM_PWM_PulseFinishedCallback(TIM_HandleTypeDef *htim)
{
    if (htim->Instance == TIM3) {
        HAL_TIM_PWM_Stop_DMA(htim, TIM_CHANNEL_2);
        s_busy = 0;
    }
}
