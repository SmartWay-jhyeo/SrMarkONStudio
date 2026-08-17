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
#include "app/mk_railctl.h"
#include "app/mk_telem.h"
#include "bsp/mk_rails.h"
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

/* 🔴 상태 LED(PD11)와 전원 레일(PD8·PD9·PD10)은 같은 포트에 있다.
 *    그래서 GPIOD 를 만지는 파일을 bsp/mk_rails.c 하나로 묶었다 —
 *    안전 검사(test_firmware_safety.py)가 빠짐없이 돌게 하기 위해서다.
 *    여기서는 mk_rails_led() 를 부르기만 한다. */

static void SystemClock_Config(void);

/* mk_hostlink 가 줄을 내보낼 때 부른다. */
static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_uart_write(line, len);
}

static MkConfig s_cfg;
static MkAds    s_ads;
static MkRailCtl s_rails;
static MkTelem   s_telem;
static int      s_led_on;

/* 채널별 표본 저장소.
 *
 * 🔴 DMA 가 닿을 필요가 없다. 여기에 쓰는 것은 SPI 완료 인터럽트(CPU)이고
 *    읽는 것은 슈퍼루프(CPU)다. 그래서 DTCM 에 두는 편이 오히려 빠르다 —
 *    MK_DMA_BUF 를 붙이지 않는 것이 맞다.
 *
 * 🔴 붙이는 것을 잊으면 표본이 조용히 사라진다. 실기기에서 그랬다:
 *    수집은 정상인데 $STAT 의 drops 만 올라갔다(qcount=0, qdrops=32).
 *    mk_queue 가 저장소 없는 push 도 세어 주기 때문에 원인이 바로 보였다.
 *
 * 32칸이면 100 ms 주기에서 3.2초분이다. 슈퍼루프가 그보다 훨씬 자주 돈다. */
#define SAMPLES_PER_CHANNEL  32
static MkSample s_samples[MK_ADS_CHANNELS][SAMPLES_PER_CHANNEL];

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

/* 설정표의 pwr.* 를 레일 제어기에 반영한다.
 *
 * 🔴 항목을 못 찾으면 **끈 것으로** 본다. 설정표에서 항목이 사라지는 것은
 *    실수이고, 실수했을 때 24V 가 켜지면 안 된다. 5V 도 마찬가지다 —
 *    못 찾았는데 켜 두면 왜 켜졌는지 아무도 설명할 수 없다. */
static void sync_rails(MkRailCtl *rc, MkConfig *cfg, int64_t now_ms)
{
    MkCfgItem *v5  = mk_cfg_find(cfg, "pwr.5v");
    MkCfgItem *v14 = mk_cfg_find(cfg, "pwr.14v9");
    MkCfgItem *v24 = mk_cfg_find(cfg, "pwr.24v");
    MkCfgItem *dly = mk_cfg_find(cfg, "pwr.seq_delay_ms");

    mk_railctl_tick(rc,
                    v5  != NULL && v5->cur.u,
                    v14 != NULL && v14->cur.u,
                    v24 != NULL && v24->cur.u,
                    dly != NULL ? (uint16_t)dly->cur.u : 500u,
                    now_ms);
}

/* 설정표의 ain* 항목을 수집기에 반영한다.
 *
 * 🔴 핀 번호가 아니라 커넥터 개념으로 다룬다 — 채널 n 은 J(n+3) 이고,
 *    그 대응은 설정 키 이름에만 있다(설계 원칙 1). */

static void sync_channels(MkAds *ads, MkConfig *cfg, int64_t now_ms)
{
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        char key[24];
        int n = 0;

        /* "ain<n>.enabled" / "ain<n>.period_ms" 를 손으로 만든다 —
         * app/ 과 마찬가지로 여기서도 snprintf 를 부르지 않는다. */
        key[n++] = 'a'; key[n++] = 'i'; key[n++] = 'n';
        key[n++] = (char)('0' + ch);
        const char *suffix = ".enabled";
        for (const char *p = suffix; *p; p++) { key[n++] = *p; }
        key[n] = '\0';
        MkCfgItem *en = mk_cfg_find(cfg, key);

        n = 4;                              /* "ain<n>" 뒤부터 다시 */
        suffix = ".period_ms";
        for (const char *p = suffix; *p; p++) { key[n++] = *p; }
        key[n] = '\0';
        MkCfgItem *pr = mk_cfg_find(cfg, key);

        if (en == NULL || pr == NULL) {
            continue;
        }
        mk_ads_configure(ads, ch, (int)en->cur.u, (uint16_t)pr->cur.u, now_ms);
    }
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    mk_rails_init();
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

    /* 🔴 레일 제어. 설정표의 pwr.* 를 실제 핀으로 옮긴다.
     *
     *    이것이 없으면 GUI 에서 24V 를 켜도 설정표의 숫자만 바뀐다.
     *    실제로 그 상태였다 — $STAT 이 5V 를 ON 이라고 보고하는데 PD10 은
     *    0 이었다(실기기 확인 2026-08-14). 설정값을 명령 상태인 척
     *    내보내고 있었던 것이다.
     *
     *    5V 가 올라가야 ADS1256 이 변환한다. 아날로그 전원과 기준전압이
     *    그 레일에 있다 [넷리스트 확인]: V5 -> FB1 -> AVDD5 -> U9 pin1
     *    (AVDD), 그리고 같은 AVDD5 -> U10(VIN) -> VREF2V5 -> U9 pin4
     *    (VREFP). 디지털(DVDD pin16)만 V3V3 상시다. WS2812(J21~J24)도
     *    같은 레일이다. */
    mk_railctl_init(&s_rails, mk_rails_set, NULL);

    mk_ads_io_init(&s_ads);
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        mk_ads_attach_queue(&s_ads, ch, s_samples[ch], SAMPLES_PER_CHANNEL);
    }
    mk_hostlink_attach_ads(&link, &s_ads);
    /* 🔴 수집 사슬의 마지막 조각. 이것이 없으면 큐가 차고
     *    drops 만 오르며 호스트는 한 줄도 못 받는다 —
     *    실기기에서 4초를 들어도 0건이었다. */
    mk_telem_init(&s_telem, &s_cfg, &s_ads, fields, n_fields,
                  DEVICE_ID);
    mk_hostlink_attach_rails(&link, &s_rails);

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

        /* 🔴 설정을 수집기로 밀어 넣는다.
         *
         *    이것이 없으면 GUI 에서 채널을 켜도 수집기는 영원히 모른다.
         *    실기기에서 찾았다 — ain0.enabled 를 true 로 바꿨는데 $STAT 의
         *    queues 가 계속 빈 배열이었다. 설정과 수집기를 잇는 선이
         *    아예 없었던 것이다.
         *
         *    매 바퀴 미는 이유는 설정이 바뀐 것을 알아챌 다른 통로가
         *    없기 때문이다. mk_ads_configure 는 같은 값이면 아무것도
         *    하지 않으므로(그러지 않으면 예정이 영원히 밀린다) 싸다. */
        sync_channels(&s_ads, &s_cfg, now);
        sync_rails(&s_rails, &s_cfg, now);

        mk_ads_tick(&s_ads, now);
        mk_telem_tick(&s_telem, now, emit, NULL);

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
            s_led_on = !s_led_on;
            mk_rails_led(s_led_on);
        }
    }
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
