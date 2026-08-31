#include "mk_uart.h"

#include "mk_dma_mem.h"
#include "mk_txring.h"
#include "stm32h7xx_hal.h"

static UART_HandleTypeDef s_uart;

/* ── 송신: 링버퍼 + DMA ──────────────────────────────────────────────────
 *
 * 🔴 왜 바꿨나 (실기기 30초, 2026-08-20)
 *
 *    7채널 × 10 ms, adc.drate=2000, tx.period_ms=10, 링크 1.5 Mbps:
 *    채널마다 2123개(3000 이어야 한다), 큐는 일곱 채널 모두 depth 64
 *    peak 64 drops 853~860, 총 14,863줄 = 초당 495줄.
 *
 *    495 ÷ 16(MK_TELEM_MAX_LINES) = 초당 31 틱 → 슈퍼루프 한 바퀴가
 *    32 ms 다. 그런데 링크는 67 KB/s 로 1.5 Mbps(187 KB/s)의 36 % 밖에
 *    안 썼다. 막고 있던 것은 링크가 아니라 여기 있던
 *    `HAL_UART_Transmit`(블로킹)이었다 — 16줄 × 135 B 를 밀어내는 14 ms
 *    (921600 이면 23 ms) 동안 슈퍼루프가 통째로 서 있었고, 그동안
 *    인터럽트로 계속 들어오던 표본이 큐를 넘쳤다.
 *
 * 🔴 DMA2 Stream0 을 쓴다. DMA1 은 Stream0·1 = SPI4(ADS1256),
 *    Stream2 = TIM3(WS2812), Stream3 = SPI2(LCD)로 이미 차 있다. 겹치면
 *    수집이 깨지고 그것이 이 프로젝트에서 가장 나쁜 실패다. DMA2 는
 *    통째로 비어 있어 앞으로 늘어날 자리도 남는다.
 *
 * 🔴 HAL 의 `HAL_UART_Transmit_DMA` 를 쓰지 않는다. 그 함수는 완료를
 *    `HAL_UART_IRQHandler` 안에서 마무리하는데, 이 파일은 수신을 위해
 *    USART3 인터럽트를 **직접** 다룬다(아래 mk_uart_isr). 둘을 섞으면
 *    HAL 의 상태머신이 반쪽만 돌아 전송이 한 번 나가고 멈춘다. 그래서
 *    스트림을 직접 걸고(HAL_DMA_Start_IT) USART 쪽은 CR3 의 DMAT 만 켠다.
 */

/* 🔴 크기를 어림으로 잡지 않는다.
 *
 *    한 틱에 만들어지는 양: ain 이 MK_TELEM_MAX_LINES(11, 아래 계산은
 *    app/mk_telem.h) × 실측 135 B = 1,485 B, 거기에 i2c·din 이 붙어
 *    최악 2 KB 남짓이다. 한 틱에 빠지는 양: 1.5 Mbps ÷ 10 bit/B ×
 *    10 ms = 1,500 B. 즉 링크가 포화된 상태에서 한 틱마다 500 B 씩
 *    쌓인다 — 4,096 B 면 그런 틱이 여덟 번 이어져도 버틴다. 그보다 오래
 *    이어지면 설정 자체가 링크 용량을 넘은 것이고, 그때는 버리는 것이
 *    맞다(mk_txring.h 의 판단 근거).
 *
 *    비용은 D2 SRAM 4 KB 다. 32 KB 구역에서 지금 쓰는 것이 LCD 행버퍼
 *    960 B + WS2812 + ADS 16 B 뿐이라 문제가 되지 않는다.
 *
 * 🔴 4,096 -> 8,192 [2026-08-20]. 예약 몫을 512 에서 2,048 로 올리면서
 *    함께 키웠다(아래 MK_TX_RESERVE 의 근거). 링을 그대로 두고 예약만
 *    올리면 텔레메트리가 쓸 수 있는 자리가 4,095 에서 2,047 로 **반이
 *    되어** 어제 얻은 10 ms 수집을 도로 잃는다. 8,192 로 하면 6,143 이
 *    남아 예전보다 넉넉하다.
 *
 *    비용은 D2 SRAM 4 KB 가 더. 맵을 보면 .dma_buffers 가 0x1540(5,440 B)
 *    이므로 32 KB 중 9,536 B 를 쓰게 된다 — 여전히 3분의 1 이 안 된다. */
#define MK_TX_RING_SIZE   8192u

/* 🔴 명령 응답 몫. 텔레메트리와 카탈로그는 이만큼을 남기고 넣는다.
 *
 *    링을 포화시킨 것은 설정(채널 수·주기·baud)인데, 그것을 되돌리는
 *    `$SACK` 이 나갈 자리가 없으면 사람이 아무것도 못 하는 상태가 된다.
 *
 * 🔴 512 -> 2,048 [2026-08-20]. 512 로는 **`$STAT` 한 줄이 안 들어간다.**
 *    그 줄은 7채널·din·gnss·lcd·link·tx 를 다 실어 최악 1.7 KB 이고
 *    실측으로도 900 B 를 넘는다. 텔레메트리가 링을 채운 상태에서 `$STAT`
 *    을 물으면 예약 몫이 모자라 응답이 통째로 버려졌다 — 유실을 찾으려고
 *    여는 창구가 바로 그때 닫히는 셈이다. 카탈로그 결함과 같은 부류다
 *    (한 줄이 예약보다 크다).
 *
 * 🔴 2,048 -> 2,304 [2026-08-28]. gnss.rtcm_* 다섯 필드로 최악 $STAT 이
 *    1,792 → 1,920 이 됐다(app/mk_hostlink.c 의 body[1920] — 산식은 그쪽
 *    원장 주석). 2,304 = 최악 $STAT(1,920) + 최대 길이 줄(MK_LINE_MAX+1 =
 *    193) 두 개에 여유를 더한 값. 텔레메트리 몫은 8,192 − 2,304 = 5,888 —
 *    예약을 키우기 전(2026-08-20 이전)의 4,096 보다 여전히 넉넉하다. */
#define MK_TX_RESERVE     2304u

/* 🔴 수집(MK_ADS_IRQ_PRIO = 6)보다 **덜** 급하게 둔다. Cortex-M 은 숫자가
 *    작을수록 급하다. 송신 완료가 몇 µs 늦는 것은 아무 일도 아니지만,
 *    획득 시각이 밀리는 것은 이 시스템이 존재하는 이유를 깎는다
 *    (설계 원칙 2). 수신(USART3, 5)보다도 낮다. */
#define MK_UART_TX_DMA_PRIO  7u

/* 🔴 baud 를 바꾸기 전에 링이 빠지기를 기다리는 시한.
 *
 *    가장 느린 115200 에서 링 8,192 B 가 다 빠지는 데 711 ms 다
 *    (8192 × 10 bit ÷ 115200). 1,000 ms 면 그것을 덮는다 — 링을 4,096
 *    에서 8,192 로 키우면서 500 ms 로는 모자라게 됐고, 그대로 두면 링이
 *    가득 찬 상태의 baud 변경에서 마지막 응답이 잘린다(규격 §4.2.2 규칙 1
 *    위반).
 *
 *    시한을 두는 이유는 그대로다 — TC 가 안 서는 고장에서 여기 갇히면
 *    보드가 통째로 멈추고, 되돌림 시한도 못 돌아 안전장치가 그 자리에서
 *    벽돌을 만든다. 1초를 서는 것은 baud 변경에서만, 그것도 링이 가득
 *    찼을 때만 일어난다. */
#define MK_TX_DRAIN_MS    1000u

static DMA_HandleTypeDef  s_hdma_tx;
/* 🔴 DMA 가 읽을 곳이므로 D2(0x3000_0000)에 둔다. 그냥 static 이면
 *    DTCM 에 잡히고 DMA1·DMA2 는 거기 닿지 못한다 — 컴파일도 링크도
 *    통과하고 전송만 조용히 안 된다(bsp/mk_dma_mem.h, RM0468 p.140). */
static uint8_t MK_DMA_BUF s_tx_buf[MK_TX_RING_SIZE];
static MkTxRing           s_tx;
/* 지금 DMA 가 옮기고 있는 바이트 수. 0 이면 놀고 있다. */
static volatile uint16_t  s_tx_inflight;

/* 수신 링. ISR 이 head 를, 슈퍼루프가 tail 을 움직인다.
 * 🔴 둘 다 volatile 이어야 한다. 최적화기가 루프 안에서 head 를 레지스터에
 *    담아 두면 인터럽트가 바꾼 값을 영영 못 본다. */
static volatile uint8_t  s_rx[MK_RX_RING_SIZE];
static volatile uint16_t s_head;
static volatile uint16_t s_tail;
static volatile uint32_t s_overruns;

/* 주변장치를 이 속도로 세운다. 수신 링은 건드리지 않는다 —
 * mk_uart_set_baud() 가 이것을 다시 부르기 때문이다. */
static void uart_configure(uint32_t baud)
{
    s_uart.Instance                    = USART3;
    s_uart.Init.BaudRate               = baud;
    s_uart.Init.WordLength             = UART_WORDLENGTH_8B;
    s_uart.Init.StopBits               = UART_STOPBITS_1;
    s_uart.Init.Parity                 = UART_PARITY_NONE;
    s_uart.Init.Mode                   = UART_MODE_TX_RX;
    s_uart.Init.HwFlowCtl              = UART_HWCONTROL_NONE;
    s_uart.Init.OverSampling           = UART_OVERSAMPLING_16;
    s_uart.Init.OneBitSampling         = UART_ONE_BIT_SAMPLE_DISABLE;
    s_uart.Init.ClockPrescaler         = UART_PRESCALER_DIV1;
    s_uart.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    HAL_UART_Init(&s_uart);

    /* 🔴 HAL 의 수신 API 를 쓰지 않고 RXNE 인터럽트를 직접 켠다.
     *    HAL_UART_Receive_IT 는 정해진 개수를 받으면 멈추고 콜백에서 다시
     *    걸어야 하는데, 그 틈에 온 바이트가 사라진다. 줄 단위로 언제 올지
     *    모르는 명령을 받는 데는 맞지 않는다.
     *
     * 🔴 HAL_UART_Init() 이 UE 를 껐다 켜며 CR1 을 다시 쓰므로, 이 한 줄은
     *    **매번 뒤따라야 한다.** 속도만 바꾸고 이것을 빠뜨리면 보드가
     *    말은 하는데 아무것도 못 듣는 상태가 된다 — 확인을 못 받으니
     *    반드시 되돌아간다. */
    __HAL_UART_ENABLE_IT(&s_uart, UART_IT_RXNE);

    /* 🔴 DMAT(송신 DMA 요청)도 **매번 뒤따라야 한다.** RXNE 와 정확히 같은
     *    함정이다 — HAL_UART_Init() 이 CR3 를 통째로 다시 쓰므로 여기를
     *    빠뜨리면 baud 를 한 번 바꾼 뒤 보드가 영원히 말을 못 한다.
     *    DMA 는 멀쩡히 걸리는데 USART 가 요청을 안 하니, 링만 차오르고
     *    전선은 조용하다 — 원인을 찾기 가장 어려운 종류의 고장이다. */
    s_uart.Instance->CR3 |= USART_CR3_DMAT;
}

/* 놀고 있으면 다음 조각을 건다. 이미 옮기는 중이면 아무것도 안 한다.
 *
 * 🔴 "놀고 있나" 를 보는 것과 거는 것을 쪼개면 안 된다. 그 사이에 완료
 *    인터럽트가 끼면 둘 다 걸거나(전송이 겹친다) 둘 다 안 건다(링크가
 *    영원히 선다). PRIMASK 로 그 창을 닫는다 — bsp/mk_time.c 와 같은
 *    저장/복원 방식이고, 지역변수에 담으므로 중첩해도 안전하다. */
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
            /* 🔴 링에 쓴 바이트가 메모리에 닿은 뒤에 DMA 를 건다. 쓰기
             *    버퍼에 남아 있는 채로 시작하면 DMA 가 옛 내용을 읽는다.
             *    (D-캐시는 꺼져 있으므로 클린은 필요 없다.) */
            __DSB();
            if (HAL_DMA_Start_IT(&s_hdma_tx, (uint32_t)(uintptr_t)p,
                                 (uint32_t)(uintptr_t)&s_uart.Instance->TDR,
                                 (uint32_t)n) != HAL_OK) {
                /* 못 걸었으면 링은 그대로 두고 다음 기회에 다시 건다.
                 * 다음 기회는 반드시 온다 — $HB 가 최소 1초에 한 번
                 * mk_uart_write() 를 부르고, 그것이 다시 여기로 온다. */
                s_tx_inflight = 0u;
            }
        }
    }

    __set_PRIMASK(primask);
}

/* DMA 가 한 조각을 다 옮겼다. 소비하고 다음 조각을 잇는다 — 링이 감기는
 * 자리도 여기서 이어진다(첫 조각은 링 끝까지, 나머지는 처음부터). */
static void tx_complete(DMA_HandleTypeDef *h)
{
    (void)h;
    mk_txring_consume(&s_tx, s_tx_inflight);
    s_tx_inflight = 0u;
    tx_kick();
}

/* 전송 오류(TE/FE). 옮기던 조각은 이미 깨졌으므로 버리고 다음으로 간다.
 *
 * 🔴 여기서 멈춰 있으면 링크가 통째로 죽는다 — 그것이 오류 한 번보다
 *    훨씬 나쁘다. 버린 바이트는 줄 가운데를 자르지만, 어차피 그 줄은
 *    전선에서 이미 깨졌다. 호스트는 seq 구멍으로 알아챈다(규격 §7.1). */
static void tx_error(DMA_HandleTypeDef *h)
{
    (void)h;
    mk_txring_consume(&s_tx, s_tx_inflight);
    s_tx_inflight = 0u;
    tx_kick();
}

void mk_uart_init(uint32_t baud)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_USART3_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin       = GPIO_PIN_10 | GPIO_PIN_11;   /* PB10=TX, PB11=RX */
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_PULLUP;
    g.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF7_USART3;
    HAL_GPIO_Init(GPIOB, &g);

    s_head = 0;
    s_tail = 0;
    s_overruns = 0;

    /* 🔴 송신 DMA 를 **USART 를 세우기 전에** 준비한다. uart_configure()
     *    가 DMAT 를 켜므로, 그 뒤에 첫 줄이 들어오면 곧바로 걸릴 수 있어야
     *    한다. */
    __HAL_RCC_DMA2_CLK_ENABLE();
    /* 🔴 D2 도메인 SRAM 의 클럭. 리셋 직후 꺼져 있다 — 링 저장소가
     *    0x3000_0000 에 있으므로 이것을 안 켜면 DMA 가 읽는 것이 전부
     *    0 이다(bsp/mk_ads_io.c 에 같은 주석이 있다). */
    __HAL_RCC_D2SRAM1_CLK_ENABLE();

    mk_txring_init(&s_tx, s_tx_buf, (uint16_t)MK_TX_RING_SIZE);
    s_tx_inflight = 0u;

    /* 🔴 DMA2 Stream0. DMA1 은 Stream0·1(SPI4/ADS1256) · Stream2(WS2812) ·
     *    Stream3(SPI2/LCD)로 차 있다. 겹치면 수집이 깨진다. */
    s_hdma_tx.Instance                 = DMA2_Stream0;
    s_hdma_tx.Init.Request             = DMA_REQUEST_USART3_TX;
    s_hdma_tx.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    s_hdma_tx.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_hdma_tx.Init.MemInc              = DMA_MINC_ENABLE;
    s_hdma_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    s_hdma_tx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
    s_hdma_tx.Init.Mode                = DMA_NORMAL;
    /* 🔴 채널 중재도 수집보다 낮게 둔다. ADS1256(SPI4)은 HIGH 다 —
     *    같은 순간에 둘이 버스를 원하면 수집이 먼저 가야 한다. */
    s_hdma_tx.Init.Priority            = DMA_PRIORITY_LOW;
    s_hdma_tx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_hdma_tx);
    s_hdma_tx.XferCpltCallback  = tx_complete;
    s_hdma_tx.XferErrorCallback = tx_error;

    uart_configure(baud);

    HAL_NVIC_SetPriority(USART3_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
    HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, MK_UART_TX_DMA_PRIO, 0);
    HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
}

void mk_uart_set_baud(uint32_t baud)
{
    if (baud == s_uart.Init.BaudRate) {
        return;
    }

    /* 🔴 **보내던 바이트를 자르지 않는다** (규격 §4.2.2 규칙 1).
     *
     *    송신이 DMA 로 바뀌면서 "다 보냈다" 의 뜻이 달라졌다. 예전에는
     *    mk_uart_write() 가 쓰던 HAL_UART_Transmit 이 이미 TC 를 기다리고
     *    돌아왔으므로 여기 오면 대개 이미 끝나 있었다. 지금은 셋을 다
     *    봐야 한다:
     *
     *      1) 링이 비었나        — 아직 DMA 에 걸리지도 않은 바이트
     *      2) DMA 가 놀고 있나   — 지금 옮기고 있는 조각
     *      3) TC 가 섰나         — USART 의 FIFO·시프트 레지스터에 남은 것
     *
     *    하나라도 빼면 응답의 앞부분만 옛 속도로 나가고, 호스트는
     *    성공했는지조차 모른 채 링크를 잃는다. mk_linkbaud 의 확인/되돌림
     *    절차가 통째로 이 계약 위에 서 있다.
     *
     *    🔴 기다리는 동안에도 tx_kick() 을 부른다. 완료 인터럽트가 스스로
     *       다음 조각을 잇지만, 어떤 이유로 링만 차 있고 DMA 가 놀고 있는
     *       상태라면 아무도 깨워 주지 않는다 — 그러면 여기서 시한을 다
     *       채우고 잘린다.
     *
     *    🔴 영영 기다리지 않는다. TC 가 안 서는 고장(클럭이 끊겼다든가)에서
     *       여기 갇히면 보드가 통째로 멈춘다 — 되돌림 시한도 못 돈다.
     *       그러면 안전장치가 그 자리에서 벽돌을 만든다. 시한은 가장 느린
     *       115200 에서 링 4,096 B 가 다 빠지는 356 ms 를 덮는 500 ms 다. */
    uint32_t deadline = HAL_GetTick() + MK_TX_DRAIN_MS;
    for (;;) {
        tx_kick();
        int busy = (mk_txring_used(&s_tx) > 0u) || (s_tx_inflight != 0u)
                   || ((s_uart.Instance->ISR & USART_ISR_TC) == 0u);
        if (!busy) {
            break;
        }
        if ((int32_t)(HAL_GetTick() - deadline) >= 0) {
            /* 🔴 시한이 지났으면 남은 것을 **버리고** 바꾼다. 그대로 두면
             *    옛 속도로 만든 바이트가 새 속도로 나가 호스트에서 잡음이
             *    된다 — 프레이밍 오류만 늘리고 아무도 못 읽는다. 잘린 줄은
             *    seq 구멍으로 보이고, 이 상황 자체는 10초 뒤 mk_linkbaud
             *    의 자동 되돌림이 수습한다. */
            HAL_DMA_Abort(&s_hdma_tx);
            s_tx_inflight = 0u;
            mk_txring_consume(&s_tx, mk_txring_used(&s_tx));
            break;
        }
    }

    /* 재설정하는 동안 ISR 이 링을 건드리지 않게 막는다. 수신 링 자체는
     * 비우지 않는다 — 아직 처리 안 된 명령이 그 안에 있을 수 있다. */
    HAL_NVIC_DisableIRQ(USART3_IRQn);
    uart_configure(baud);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}

void mk_uart_dma_isr(void)
{
    HAL_DMA_IRQHandler(&s_hdma_tx);
}

void mk_uart_isr(void)
{
    uint32_t isr = s_uart.Instance->ISR;

    if (isr & USART_ISR_RXNE_RXFNE) {
        uint8_t b = (uint8_t)s_uart.Instance->RDR;   /* 읽으면 플래그가 지워진다 */
        uint16_t next = (uint16_t)((s_head + 1u) % MK_RX_RING_SIZE);
        if (next == s_tail) {
            /* 링이 찼다. 새 바이트를 버린다 — 오래된 것을 밀어내면 줄
             * 가운데가 잘려 나가 엉뚱한 명령이 된다. */
            s_overruns++;
        } else {
            s_rx[s_head] = b;
            s_head = next;
        }
    }

    /* 🔴 오류 플래그를 지우지 않으면 그 인터럽트가 영원히 다시 걸려
     *    보드가 멈춘 것처럼 보인다. 잡음 한 번에 링크가 죽는다. */
    if (isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) {
        s_uart.Instance->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                               USART_ICR_NECF  | USART_ICR_PECF;
    }
}

void mk_uart_write(const char *data, size_t len)
{
    /* 예약 몫까지 쓴다 — 명령 응답·오류·하트비트는 텔레메트리에 밀려
     * 사라지면 안 된다. 자세한 이유는 mk_uart_write_bulk 아래.
     *
     * 🔴 그래도 못 넣었으면 **제어 유실**로 센다(텔레메트리와 다른 계기).
     *    명령 응답의 seq 는 항상 0 이라(규격 §5.2) 호스트가 유실을
     *    알아챌 방법이 그 수 말고는 없다 — app/mk_txring.h 의 근거. */
    (void)mk_txring_push_ctl(&s_tx, data, len);
    tx_kick();
}

void mk_uart_write_bulk(const char *data, size_t len)
{
    /* 🔴 텔레메트리는 예약 몫을 남기고 넣는다. 링을 포화시킨 것은
     *    설정(채널 수·주기·baud)인데, 그것을 되돌리는 `$SACK` 이 나갈
     *    자리가 없으면 사람이 아무것도 못 하는 상태가 된다 —
     *    mk_linkbaud 의 자동 되돌림과 같은 정신이다. */
    (void)mk_txring_push(&s_tx, data, len, MK_TX_RESERVE);
    tx_kick();
}

int mk_uart_write_stream(const char *data, size_t len)
{
    /* 🔴 못 넣어도 **세지 않는다.** 부른 쪽(카탈로그 펌프)이 다음 바퀴에
     *    같은 줄을 다시 준다 — 유실이 아니라 역압이다. 여기서 세면
     *    정상 동작에서도 제어 유실이 계속 올라 그 수가 아무 말도 못 하게
     *    된다.
     *
     * 🔴 예약 몫을 남긴다. 카탈로그(25 KB)가 링을 끝까지 채우면 그 몇 초
     *    동안 `$SACK`·`$HB` 가 나갈 자리가 없어진다 — 카탈로그를 살리려다
     *    명령 응답을 죽이는 셈이다. */
    int ok = mk_txring_offer(&s_tx, data, len, MK_TX_RESERVE);
    tx_kick();
    return ok;
}

uint32_t mk_uart_tx_drops(void)
{
    return mk_txring_drops(&s_tx);
}

uint32_t mk_uart_tx_dropped_bytes(void)
{
    return mk_txring_dropped_bytes(&s_tx);
}

uint32_t mk_uart_tx_ctl_drops(void)
{
    return mk_txring_ctl_drops(&s_tx);
}

uint32_t mk_uart_tx_ctl_dropped_bytes(void)
{
    return mk_txring_ctl_dropped_bytes(&s_tx);
}

const MkTxRing *mk_uart_tx_ring(void)
{
    return &s_tx;
}

size_t mk_uart_tx_pending(void)
{
    return mk_txring_used(&s_tx);
}

uint16_t mk_uart_tx_peak(void)
{
    return mk_txring_peak(&s_tx);
}

size_t mk_uart_read_line(char *out, size_t cap)
{
    static char  line[MK_RX_LINE_MAX];
    static size_t used;
    static int    dropping;      /* 너무 긴 줄을 만나면 줄끝까지 버린다 */

    while (s_tail != s_head) {
        char c = (char)s_rx[s_tail];
        s_tail = (uint16_t)((s_tail + 1u) % MK_RX_RING_SIZE);

        if (c == '\n') {
            size_t n = used;
            used = 0;
            if (dropping) {
                dropping = 0;
                continue;        /* 잘린 줄은 내보내지 않는다 */
            }
            if (n == 0u || n >= cap) {
                continue;
            }
            for (size_t i = 0; i < n; i++) {
                out[i] = line[i];
            }
            out[n] = '\0';
            return n;
        }

        if (dropping) {
            continue;
        }
        if (used + 1u >= sizeof line) {
            /* 🔴 잘라 담지 않는다. 앞 192 바이트만으로 완결된 명령이 될 수
             *    있다. 줄끝을 만날 때까지 통째로 버린다. */
            dropping = 1;
            used = 0;
            continue;
        }
        line[used++] = c;
    }
    return 0;
}

uint32_t mk_uart_rx_overruns(void)
{
    return s_overruns;
}
