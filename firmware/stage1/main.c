/**
 * MarkON Studio 펌웨어 1단계 — $ID 와 $HB 만.
 *
 * 목적은 기능이 아니라 **계약 확인**이다. 호스트와 보드가 같은 프레이밍,
 * 같은 체크섬, 같은 NDJSON 을 쓰는지 실기기에서 판가름한다. 여기서
 * 어긋나면 그 위에 쌓는 모든 것이 어긋난다.
 *
 * 🔴 전원 레일을 켜지 않는다.
 *
 *    참고 펌웨어(h723_sensor_read)는 PD10(5V) → PD9(14.9V) → PD8(24V) 를
 *    순서대로 올린다. 센서를 구동해야 하기 때문이다. 이 펌웨어는 $ID 와
 *    $HB 만 하므로 24V 가 필요 없다. 센서도 붙어 있지 않고 J30/J1 상태도
 *    모르는 상황에서 불필요한 인가를 하지 않는다.
 *
 *    리셋 직후 3.3V 만 살아 있고 나머지는 꺼진 상태다(데이터시트 §4).
 *    아무것도 안 하면 그대로 꺼져 있다.
 *
 * 경로: PB10/PB11 (USART3) → F103(BMP) → USB VCP → COM23
 */

#include "stm32h7xx_hal.h"

#include "mk_hostlink.h"
#include "mk_uart.h"

#include <stddef.h>

#define FW_VERSION   "0.1.0"
#define BOARD_REV    "2.0"
#define DEVICE_ID    "1"
#define UART_BAUD    115200u

/* 상태 LED. PD11 = LED5 — 참고 펌웨어와 같은 핀이다.
 * 🔴 PD8/PD9/PD10 은 건드리지 않는다. 그것들이 전원 레일이다. */
#define LED_PORT     GPIOD
#define LED_PIN      GPIO_PIN_11

static void SystemClock_Config(void);
static void led_init(void);

/* mk_hostlink 가 줄을 내보낼 때 부른다. */
static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_uart_write(line, len);
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    led_init();
    mk_uart_init(UART_BAUD);

    MkHostlink link;
    mk_hostlink_init(&link, emit, NULL, DEVICE_ID, FW_VERSION, BOARD_REV);

    char rx[MK_RX_LINE_MAX];
    uint32_t last_blink = 0;

    for (;;) {
        int64_t now = (int64_t)HAL_GetTick();

        /* 받은 줄을 전부 처리한다. 한 바퀴에 하나만 처리하면 명령이 몰릴 때
         * 뒤로 밀린다. */
        size_t n;
        while ((n = mk_uart_read_line(rx, sizeof rx)) > 0u) {
            mk_hostlink_feed(&link, rx, n, now);
        }

        mk_hostlink_tick(&link, now);

        /* 살아 있음 표시. 모드에 따라 주기를 바꿔 눈으로 구분한다.
         *   RUN    2초에 한 번 (느리게)
         *   CONFIG 0.5초에 한 번 (빠르게 — 호스트가 붙어 있다)
         *
         * 🔴 이것이 stage 1 에서 유일한 시각 피드백이다. 시리얼이 안 나올 때
         *    보드가 죽은 것인지 통신만 안 되는 것인지 가른다. */
        uint32_t period = (mk_hostlink_mode(&link, now) == MK_MODE_CONFIG)
                          ? 500u : 2000u;
        uint32_t tick = HAL_GetTick();
        if (tick - last_blink >= period) {
            last_blink = tick;
            HAL_GPIO_TogglePin(LED_PORT, LED_PIN);
        }
    }
}

static void led_init(void)
{
    __HAL_RCC_GPIOD_CLK_ENABLE();

    /* 🔴 PD11 하나만 건드린다. PD8·PD9·PD10 은 전원 레일이라 초기화
     *    대상에 넣지 않는다. GPIO_InitTypeDef 에 여러 핀을 OR 로 묶어
     *    넣는 습관 때문에 실수하기 쉬운 자리다. */
    GPIO_InitTypeDef g = {0};
    g.Pin   = LED_PIN;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(LED_PORT, &g);
    HAL_GPIO_WritePin(LED_PORT, LED_PIN, GPIO_PIN_RESET);
}

/* 참고 펌웨어(h723_sensor_read)에서 그대로 가져왔다. 이 보드에서 실증된
 * 유일한 클럭 설정이다. HSI 64 MHz, PLL 없음, 모든 분주 1 → APB1 64 MHz.
 * USART3 이 APB1 에 있으므로 115200 은 물론 921600 도 낼 수 있다. */
static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY);
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {
    }

    osc.OscillatorType      = RCC_OSCILLATORTYPE_HSI;
    osc.HSIState            = RCC_HSI_DIV1;          /* HSI = 64 MHz */
    osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    osc.PLL.PLLState        = RCC_PLL_NONE;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        for (;;) { }
    }

    clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                    RCC_CLOCKTYPE_D1PCLK1 | RCC_CLOCKTYPE_PCLK1 |
                    RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_D3PCLK1;
    clk.SYSCLKSource   = RCC_SYSCLKSOURCE_HSI;
    clk.SYSCLKDivider  = RCC_SYSCLK_DIV1;
    clk.AHBCLKDivider  = RCC_HCLK_DIV1;
    clk.APB3CLKDivider = RCC_APB3_DIV1;
    clk.APB1CLKDivider = RCC_APB1_DIV1;
    clk.APB2CLKDivider = RCC_APB2_DIV1;
    clk.APB4CLKDivider = RCC_APB4_DIV1;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_1) != HAL_OK) {
        for (;;) { }
    }
}
