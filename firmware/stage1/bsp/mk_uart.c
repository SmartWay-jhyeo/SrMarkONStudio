#include "mk_uart.h"

#include "stm32h7xx_hal.h"

static UART_HandleTypeDef s_uart;

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

    uart_configure(baud);
    HAL_NVIC_SetPriority(USART3_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}

void mk_uart_set_baud(uint32_t baud)
{
    if (baud == s_uart.Init.BaudRate) {
        return;
    }

    /* 🔴 **보내던 바이트를 자르지 않는다** (규격 §4.2.2 규칙 1).
     *
     *    mk_uart_write() 가 쓰는 HAL_UART_Transmit 은 이미 TC 를 기다리고
     *    돌아오므로 여기 오면 보통 이미 서 있다. 그래도 확인한다 —
     *    이 한 줄이 지키는 것은 "응답이 옛 속도로 다 나갔다" 이고,
     *    그것이 깨지면 호스트는 성공했는지조차 모른 채 링크를 잃는다.
     *
     *    🔴 영영 기다리지 않는다. TC 가 안 서는 고장(클럭이 끊겼다든가)
     *       에서 여기 갇히면 보드가 통째로 멈춘다 — 되돌림 시한도 못
     *       돈다. 그러면 안전장치가 그 자리에서 벽돌을 만든다.
     *       64 MHz 에서 이 루프 한 바퀴는 몇 사이클이므로, 2,000,000
     *       바퀴면 가장 느린 115200 의 한 바이트(87 us)보다 두 자릿수
     *       길다. */
    for (uint32_t guard = 0; guard < 2000000u; guard++) {
        if ((s_uart.Instance->ISR & USART_ISR_TC) != 0u) {
            break;
        }
    }

    /* 재설정하는 동안 ISR 이 링을 건드리지 않게 막는다. 링 자체는 비우지
     * 않는다 — 아직 처리 안 된 명령이 그 안에 있을 수 있다. */
    HAL_NVIC_DisableIRQ(USART3_IRQn);
    uart_configure(baud);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
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
    if (data == 0 || len == 0u) {
        return;
    }
    HAL_UART_Transmit(&s_uart, (const uint8_t *)data, (uint16_t)len, 100u);
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
