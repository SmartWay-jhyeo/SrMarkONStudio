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

#include "mk_cfgtable.h"
#include "app/mk_ads1256.h"
#include "bsp/mk_ads_io.h"
#include "bsp/mk_time.h"
#include "mk_config.h"
#include "mk_flash.h"
#include "mk_hostlink.h"
#include "mk_uart.h"

#include <stddef.h>
#include <string.h>

#define FW_VERSION   "0.1.0"
#define BOARD_REV    "2.0"
#define DEVICE_ID    "1"
/* 🔴 921600 (사용자 확정 2026-08-14).
 *
 * 115200 에서는 기본 설정이 링크의 94.8% 를 쓰고, $CFG,LIST 카탈로그
 * (8.8 KB)를 남는 600 B/s 로 흘리면 설정 화면 여는 데 16.7초가 걸린다.
 * 921600 이면 11.8% 를 쓰고 카탈로그가 0.1초에 온다.
 * (docs/measurements/2026-08-14_link_budget.md)
 *
 * H723 쪽 여유는 충분하다. APB1 = 64 MHz, USARTDIV = 64e6/921600 = 69.44
 * 이고 BRR 은 정수 69 이므로 실제 927,536 baud — 오차 +0.64% 다. UART
 * 허용 오차(보통 2~3%) 안이다. */
#define UART_BAUD    921600u

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

static MkConfig s_cfg;
static MkAds    s_ads;

/* 🔴 크기를 어림으로 잡지 않는다. 실제 항목 수에서 나온다.
 *    Flash 쪽 staging 버퍼를 512 로 어림잡았다가 실기기에서 저장이
 *    ERR,BUSY 로 떨어진 적이 있다 — 45항목 × 24바이트 = 1,080 이었다. */
static uint8_t  s_blob[sizeof(MkValue) * MK_CFG_MAX_ITEMS];

/* $CFG,SAVE 가 부른다. 값만 모아 Flash 에 남긴다.
 *
 * 🔴 구조체를 통째로 쓰지 않는다. key·label·note 가 포인터라서, 펌웨어를
 *    다시 구우면 그 주소가 달라진다. 옛 주소로 되살리면 어디를 가리킬지
 *    알 수 없다. */
static int save_config(void *ctx)
{
    (void)ctx;
    size_t n = mk_cfgtable_blob_size();
    if (n > sizeof s_blob) {
        return -1;
    }
    mk_cfgtable_pack(&s_cfg, s_blob);
    return mk_flash_save(s_blob, n);
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    led_init();
    mk_uart_init(UART_BAUD);

    /* 🔴 설정을 먼저 세운 뒤 저장본을 덮어씌운다. 저장이 없거나 깨졌으면
     *    기본값 그대로 간다 — 기본값은 전원 레일이 전부 꺼진 상태다.
     *    깨진 저장을 짐작으로 받아들이면 24V 가 켜진 채로 부팅할 수 있다. */
    mk_cfgtable_init(&s_cfg);
    {
        size_t n = mk_cfgtable_blob_size();
        if (n <= sizeof s_blob && mk_flash_load(s_blob, n) == 0) {
            mk_cfgtable_unpack(&s_cfg, s_blob);
        }
    }
    mk_cfg_mark_saved(&s_cfg);   /* 방금 읽은 것과 같으니 저장할 것이 없다 */

    MkHostlink link;
    mk_hostlink_init(&link, emit, NULL, DEVICE_ID, FW_VERSION, BOARD_REV);

    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);
    mk_hostlink_attach_config(&link, &s_cfg, fields, n_fields,
                              save_config, NULL);

    /* 🔴 수집기를 켠다. 다만 **아직 채널을 하나도 켜지 않는다.**
     *
     *    ADS1256 의 아날로그 전원과 기준전압이 5V 레일에 있다
     *    [넷리스트 확인 2026-08-14]: V5 -> FB1 -> AVDD5 -> U9 pin1(AVDD),
     *    그리고 같은 AVDD5 -> U10(VIN) -> VREF2V5 -> U9 pin4(VREFP).
     *    디지털(DVDD pin16)만 V3V3 상시다.
     *
     *    그래서 PD10(5V)이 Low 인 지금은 SPI 레지스터가 정상 응답해도
     *    변환이 되지 않고 DRDY 가 떨어지지 않는다. 채널을 켜 두면 채널마다
     *    타임아웃만 쌓이고, 그것을 배선 문제로 오해하기 딱 좋다.
     *
     *    레일을 켜는 것은 별도 결정이다 — 지금 펌웨어는 레일을 건드리지
     *    않는다는 규칙(test_firmware_safety.py)이 있고, 그 규칙을 바꾸는
     *    것은 실물로 확인할 수 있을 때 함께 한다. */
    mk_ads_io_init(&s_ads);
    mk_hostlink_attach_ads(&link, &s_ads);

    char rx[MK_RX_LINE_MAX];
    uint32_t last_blink = 0;

    for (;;) {
        /* 🔴 HAL_GetTick() 을 직접 쓰지 않는다. 32비트라 49.7일에 되감기고,
         *    그 순간 타임스탬프가 과거로 뛴다 (bsp/mk_time.h). */
        int64_t now = mk_time_ms();

        /* 받은 줄을 전부 처리한다. 한 바퀴에 하나만 처리하면 명령이 몰릴 때
         * 뒤로 밀린다. */
        size_t n;
        while ((n = mk_uart_read_line(rx, sizeof rx)) > 0u) {
            mk_hostlink_feed(&link, rx, n, now);
        }

        mk_hostlink_tick(&link, now);
        mk_ads_tick(&s_ads, now);

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
