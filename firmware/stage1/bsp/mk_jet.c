#include "mk_jet.h"

#include "mk_dma_mem.h"
#include "../app/mk_txring.h"
#include "stm32h7xx_hal.h"

/* 구조는 bsp/mk_uart.c 의 송신 절반을 그대로 옮긴 것이다 — 링버퍼 + DMA,
 * 놀고 있으면 걸고 완료 인터럽트가 다음 조각을 잇는다. 다른 점 셋:
 *
 *   1. 수신은 RTCM 통과 전용이다 [B단계 부분 개시 2026-08-28]. PA3
 *      (USART2_RX)를 열되, 이 층은 바이트 링만 채우고 해석하지 않는다 —
 *      꺼내 먹이는 곳은 main 의 배선이고, 도착지는 app/mk_rtcm 하나뿐이다.
 *      🔴 이 링크에는 인증도 줄 프레이밍도 없으므로 여기서 명령을 받으면
 *      안 된다(사용자 결정, 좁은 보안 경계). mk_hostlink 로 잇는 순간
 *      host/tests/test_firmware_safety.py 가 막는다. 젯슨 쪽 명령(시각
 *      요청 등)은 여전히 다음 단계다.
 *   2. 예약 몫이 없다. 이 링크에는 명령 응답이 없어 급이 하나뿐이다.
 *   3. baud 변경이 없다. MK_JET_BAUD 고정 — 결선 검증 단계다.
 */

/* 🔴 본선(8,192)의 절반. 미러는 best-effort 라 버텨야 할 포화 시간이
 *    없다 — 921600(≈92 KB/s)이 따라가지 못하는 설정이면 링을 키워도
 *    결국 버린다. 비용은 D2 SRAM 4 KB. */
#define MK_JET_RING_SIZE  4096u

/* 🔴 본선 송신(7)과 같은 급 — 수집(6)보다 낮다. 미러가 몇 µs 늦는 것은
 *    아무 일도 아니고, 획득 시각이 밀리는 것은 설계 위반이다(원칙 2). */
#define MK_JET_DMA_PRIO   7u

/* 🔴 수신 링 4096. 근거: 실보정 유입은 0.5~3 KB/s(1초 묶음)인데 도착은
 *    선속도(921600 ≈ 92 KB/s) 버스트로 온다. 슈퍼루프가 최악 60 ms(I2C
 *    블로킹, main.c) 동안 못 꺼내도 44 ms 선속도분을 삼킬 수 있는 크기다.
 *    넘치면 overrun 으로 세고 버린다 — 보정은 다음 초에 다시 온다(Q1 의
 *    실증 크기와 동일). CPU 인터럽트가 채우므로 D2 일 필요는 없다. */
#define MK_JET_RX_RING_SIZE  4096u

static UART_HandleTypeDef s_uart;
static DMA_HandleTypeDef  s_hdma_tx;
/* 🔴 DMA 가 읽을 곳이므로 D2(0x3000_0000)에 둔다 — DTCM 에 잡히면 전송이
 *    조용히 안 된다(bsp/mk_dma_mem.h, RM0468 p.140). */
static uint8_t MK_DMA_BUF s_tx_buf[MK_JET_RING_SIZE];
static MkTxRing           s_tx;
static volatile uint16_t  s_tx_inflight;

/* 수신 링 — mk_gnss_io.c 의 바이트 링과 같은 구조(머리는 ISR, 꼬리는 루프). */
static volatile uint8_t   s_rx[MK_JET_RX_RING_SIZE];
static volatile uint16_t  s_rx_head;
static volatile uint16_t  s_rx_tail;
static volatile uint32_t  s_rx_overruns;

static uint32_t s_hb_last_ms;
static uint8_t  s_hb_level;

/* mk_uart.c 의 tx_kick 과 같은 구조 — "놀고 있나" 확인과 걸기를 PRIMASK 로
 * 한 창에 넣는다. 완료 인터럽트가 사이에 끼면 둘 다 걸거나 둘 다 안 건다. */
static void tx_kick(void)
{
    uint32_t primask = __get_PRIMASK();
    __disable_irq();

    if (s_tx_inflight == 0u) {
        const uint8_t *p = NULL;
        size_t n = mk_txring_chunk(&s_tx, &p);
        if (n > 0u) {
            if (n > 0xFFFFu) {
                n = 0xFFFFu;          /* NDTR 은 16비트다 */
            }
            s_tx_inflight = (uint16_t)n;
            __DSB();                  /* 링 내용이 메모리에 닿은 뒤에 건다 */
            if (HAL_DMA_Start_IT(&s_hdma_tx, (uint32_t)(uintptr_t)p,
                                 (uint32_t)(uintptr_t)&s_uart.Instance->TDR,
                                 (uint32_t)n) != HAL_OK) {
                /* 못 걸었으면 링은 그대로 두고 다음 mk_jet_write() 때 다시
                 * 건다 — 텔레메트리가 주기적으로 오므로 다음 기회는 온다. */
                s_tx_inflight = 0u;
            }
        }
    }

    __set_PRIMASK(primask);
}

static void tx_complete(DMA_HandleTypeDef *h)
{
    (void)h;
    mk_txring_consume(&s_tx, s_tx_inflight);
    s_tx_inflight = 0u;
    tx_kick();
}

/* 오류에서 멈추지 않는다 — 옮기던 조각은 전선에서 이미 깨졌다. 버리고
 * 다음으로 간다(본선과 같은 정책, 젯슨은 seq 구멍으로 알아챈다). */
static void tx_error(DMA_HandleTypeDef *h)
{
    (void)h;
    mk_txring_consume(&s_tx, s_tx_inflight);
    s_tx_inflight = 0u;
    tx_kick();
}

void mk_jet_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};

    /* PA2 = USART2_TX (DS13313 Rev 1 p.72 Table 8, AF7).
     * 🔴 PA2 하나만 연다. 같은 포트의 PA4~PA6(디지털 입력)·PA7(WS2812)을
     *    건드리면 안 된다 — host/tests/test_firmware_safety.py 가 본다. */
    g.Pin       = GPIO_PIN_2;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &g);

    /* PA3 = USART2_RX (같은 표, AF7). J29 핀3 JET_RX — 젯슨 TX 가 능동
     * 구동하므로 풀 없이 둔다. 이 선으로 RTCM 보정이 내려온다. */
    g.Pin       = GPIO_PIN_3;
    g.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &g);

    /* PA1 = JET_HB. 넷리스트 확인 — J29.6 에 직결, 중간 부품 없음.
     * 젯슨 쪽(핀7)은 입력이므로 푸시풀로 몰아도 되는 유일한 방향이다. */
    g.Pin       = GPIO_PIN_1;
    g.Mode      = GPIO_MODE_OUTPUT_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_LOW;
    g.Alternate = 0;
    HAL_GPIO_Init(GPIOA, &g);
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_RESET);
    s_hb_last_ms = HAL_GetTick();
    s_hb_level   = 0u;

    /* 송신 DMA — 본선이 DMA2 Stream0 이므로 Stream1. DMA1 은 Stream0~3 이
     * SPI4·WS2812·LCD 로 차 있다(겹치면 수집이 깨진다). */
    __HAL_RCC_DMA2_CLK_ENABLE();
    __HAL_RCC_D2SRAM1_CLK_ENABLE();   /* 링 저장소가 D2 에 있다 */

    mk_txring_init(&s_tx, s_tx_buf, (uint16_t)MK_JET_RING_SIZE);
    s_tx_inflight = 0u;

    s_hdma_tx.Instance                 = DMA2_Stream1;
    s_hdma_tx.Init.Request             = DMA_REQUEST_USART2_TX;
    s_hdma_tx.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    s_hdma_tx.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_hdma_tx.Init.MemInc              = DMA_MINC_ENABLE;
    s_hdma_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    s_hdma_tx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
    s_hdma_tx.Init.Mode                = DMA_NORMAL;
    s_hdma_tx.Init.Priority            = DMA_PRIORITY_LOW;   /* 수집이 먼저다 */
    s_hdma_tx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_hdma_tx);
    s_hdma_tx.XferCpltCallback  = tx_complete;
    s_hdma_tx.XferErrorCallback = tx_error;

    s_uart.Instance                    = USART2;
    s_uart.Init.BaudRate               = MK_JET_BAUD;
    s_uart.Init.WordLength             = UART_WORDLENGTH_8B;
    s_uart.Init.StopBits               = UART_STOPBITS_1;
    s_uart.Init.Parity                 = UART_PARITY_NONE;
    s_uart.Init.Mode                   = UART_MODE_TX_RX;   /* RX = RTCM 통과 전용 */
    s_uart.Init.HwFlowCtl              = UART_HWCONTROL_NONE;
    s_uart.Init.OverSampling           = UART_OVERSAMPLING_16;
    s_uart.Init.OneBitSampling         = UART_ONE_BIT_SAMPLE_DISABLE;
    s_uart.Init.ClockPrescaler         = UART_PRESCALER_DIV1;
    s_uart.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    HAL_UART_Init(&s_uart);

    /* 🔴 DMAT 는 HAL_UART_Init() 뒤에 켠다 — Init 이 CR3 를 통째로 쓴다
     *    (본선에서 실제로 밟은 함정, bsp/mk_uart.c 의 uart_configure). */
    s_uart.Instance->CR3 |= USART_CR3_DMAT;

    HAL_NVIC_SetPriority(DMA2_Stream1_IRQn, MK_JET_DMA_PRIO, 0);
    HAL_NVIC_EnableIRQ(DMA2_Stream1_IRQn);

    /* ---- 수신 (RTCM 통과 전용) ----------------------------------------- */
    s_rx_head = 0;
    s_rx_tail = 0;
    s_rx_overruns = 0;

    /* RXNE 를 직접 켠다 — mk_gnss_io.c 와 같은 이유(HAL_UART_Receive_IT 는
     * 정해진 개수에서 멈추는데 RTCM 프레임 길이는 프레임마다 다르다). */
    __HAL_UART_ENABLE_IT(&s_uart, UART_IT_RXNE);
    HAL_NVIC_SetPriority(USART2_IRQn, 5, 0);   /* USART3/USART6 수신과 같은 급 */
    HAL_NVIC_EnableIRQ(USART2_IRQn);
}

/* ---- USART2 수신 ISR — 바이트를 링에 넣기만 한다 --------------------------- */

void mk_jet_uart_isr(void)
{
    uint32_t isr = s_uart.Instance->ISR;

    if (isr & USART_ISR_RXNE_RXFNE) {
        uint8_t b = (uint8_t)s_uart.Instance->RDR;
        uint16_t next = (uint16_t)((s_rx_head + 1u) % MK_JET_RX_RING_SIZE);
        if (next == s_rx_tail) {
            s_rx_overruns++;      /* 링이 찼다 — 새 바이트를 버린다(수신 링 공통 정책) */
        } else {
            s_rx[s_rx_head] = b;
            s_rx_head = next;
        }
    }

    if (isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) {
        /* 🔴 ORE 를 안 지우면 수신이 영영 멈춘다 — 두 선행 프로젝트가 모두
         *    밟은 함정(재무장 전 플래그 클리어). */
        s_uart.Instance->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                               USART_ICR_NECF  | USART_ICR_PECF;
    }
}

int mk_jet_read_byte(uint8_t *out)
{
    if (s_rx_tail == s_rx_head) {
        return 0;
    }
    *out = s_rx[s_rx_tail];
    s_rx_tail = (uint16_t)((s_rx_tail + 1u) % MK_JET_RX_RING_SIZE);
    return 1;
}

uint32_t mk_jet_rx_overruns(void)
{
    return s_rx_overruns;
}

void mk_jet_write(const char *data, size_t len)
{
    (void)mk_txring_push(&s_tx, data, len, 0u);
    tx_kick();
}

void mk_jet_hb_tick(void)
{
    uint32_t now = HAL_GetTick();
    if (now - s_hb_last_ms >= MK_JET_HB_HALF_MS) {
        s_hb_last_ms = now;
        s_hb_level = (uint8_t)!s_hb_level;
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1,
                          s_hb_level ? GPIO_PIN_SET : GPIO_PIN_RESET);
    }
}

uint32_t mk_jet_tx_drops(void)
{
    return mk_txring_drops(&s_tx);
}

uint32_t mk_jet_tx_dropped_bytes(void)
{
    return mk_txring_dropped_bytes(&s_tx);
}

void mk_jet_dma_isr(void)
{
    HAL_DMA_IRQHandler(&s_hdma_tx);
}
