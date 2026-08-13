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

    s_head = 0;
    s_tail = 0;
    s_overruns = 0;

    /* 🔴 HAL 의 수신 API 를 쓰지 않고 RXNE 인터럽트를 직접 켠다.
     *    HAL_UART_Receive_IT 는 정해진 개수를 받으면 멈추고 콜백에서 다시
     *    걸어야 하는데, 그 틈에 온 바이트가 사라진다. 줄 단위로 언제 올지
     *    모르는 명령을 받는 데는 맞지 않는다. */
    __HAL_UART_ENABLE_IT(&s_uart, UART_IT_RXNE);
    HAL_NVIC_SetPriority(USART3_IRQn, 5, 0);
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
