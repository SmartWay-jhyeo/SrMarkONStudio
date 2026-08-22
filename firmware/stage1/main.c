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
#include "app/mk_ws2812.h"
#include "app/mk_solctl.h"
#include "app/mk_i2c.h"
#include "app/mk_lcd.h"
#include "app/mk_screen.h"
#include "app/mk_gnss.h"
#include "app/mk_gnssctl.h"
#include "app/mk_timeax.h"
#include "bsp/mk_rails.h"
#include "bsp/mk_ws2812_io.h"
#include "bsp/mk_sol.h"
#include "bsp/mk_i2c_io.h"
#include "bsp/mk_lcd_io.h"
#include "bsp/mk_gnss_io.h"
#include "bsp/mk_critsec.h"
#include "bsp/mk_clock.h"
#include "mk_config.h"
#include "mk_flash.h"
#include "mk_hostlink.h"
#include "app/mk_linkbaud.h"
#include "mk_uart.h"
#include "bsp/mk_jet.h"
#include "bsp/mk_iwdg.h"
#include "app/mk_statled.h"
#include "app/mk_cloud.h"
#include "app/mk_imu.h"

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
 * H723 쪽 여유는 충분하다. APB1 = 64 MHz(MK_USART3_KERNEL_HZ),
 * USARTDIV = 64e6/921600 = 69.44 이고 BRR 은 정수 69 이므로 실제
 * 927,536 baud — 오차 +0.64% 다. UART 허용 오차(보통 2~3%) 안이다.
 *
 * 🔴 이 오차는 클럭에 매여 있다. 클럭을 바꾸면 BRR 이 다른 정수로 떨어져
 *    오차가 달라지므로, host/tests/test_firmware_clock.py 가 커널 클럭과
 *    이 상수로 오차를 다시 계산해 2 % 안인지 본다.
 *
 * 🔴 [개정, 2026-08-20] 이제 이것은 **부팅 기본값**이다. 실제 속도는 설정
 *    항목 `link.baud` 로 바꿀 수 있고(규격 §4.2), 확정된 값만 Flash 에
 *    남는다. 921600 을 기본으로 그대로 둔 이유는 §4.2.5 에 있다 — 이 값만
 *    실기기에서 확인됐고, 더 높은 속도는 F103(BMP) 브리지가 견디는지
 *    아무도 모른다. */
#define UART_BAUD    921600u

/* 🔴 카탈로그의 기본값과 부팅 기본값이 갈리면, 저장이 없는 보드가
 *    카탈로그와 다른 속도로 말한다. 컴파일 때 못박는다. */
_Static_assert(UART_BAUD == MK_LINKBAUD_DEFAULT,
               "main.c 의 UART_BAUD 와 mk_linkbaud.h 의 기본값이 갈렸다");

/* 🔴 고를 수 있는 속도가 **이 클럭에서 실제로 나오는지** 컴파일 때 본다.
 *
 *    카탈로그(app/mk_cfgtable.c)와 이 검사가 같은 목록
 *    (MK_LINKBAUD_CHOICE_LIST)을 펼쳐 쓴다. 못 내는 값이 목록에 있으면
 *    사용자가 그것을 고르는 순간에만 드러나고, 그 순간은 이미 링크가
 *    끊긴 뒤다 — 여기서 막지 않으면 막을 곳이 없다.
 *
 *    실행 시간에도 mk_linkbaud_request() 가 같은 계산을 한 번 더 한다.
 *    이쪽은 클럭을 바꿨을 때 **빌드가 깨지게** 하는 것이 목적이다. */
#define MK_LB_CHECK(v)                                                       \
    _Static_assert(MK_LINKBAUD_BRR(MK_USART3_KERNEL_HZ, v) >= 16u,           \
                   #v ": BRR < 16 — 오버샘플 16 으로 낼 수 없는 속도다");    \
    _Static_assert(MK_LINKBAUD_ERR_PPM(MK_USART3_KERNEL_HZ, v)               \
                       <= MK_LINKBAUD_MAX_ERR_PPM,                           \
                   #v ": 보율 오차가 2 % 를 넘는다 — 목록에서 빼야 한다");
MK_LINKBAUD_CHOICE_LIST(MK_LB_CHECK)
#undef MK_LB_CHECK

/* 🔴 상태 LED(PD11)와 전원 레일(PD8·PD9·PD10)은 같은 포트에 있다.
 *    그래서 GPIOD 를 만지는 파일을 bsp/mk_rails.c 하나로 묶었다 —
 *    안전 검사(test_firmware_safety.py)가 빠짐없이 돌게 하기 위해서다.
 *    여기서는 mk_rails_led() 를 부르기만 한다. */

/* mk_hostlink 가 줄을 내보낼 때 부른다 — 명령 응답·오류·하트비트다.
 *
 * 🔴 이쪽은 링의 예약 몫까지 쓴다. 텔레메트리가 링을 채운 상태에서도
 *    `$SACK` 은 나가야 한다 — 링크를 포화시킨 것이 바로 그 설정인데
 *    되돌릴 응답이 사라지면 사람이 아무것도 못 하는 상태가 된다
 *    (app/mk_txring.h 의 예약 몫 주석). */
static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_uart_write(line, len);
}

/* mk_telem 이 줄을 내보낼 때 부른다 — 초당 수백 줄인 쪽이다.
 *
 * 🔴 예약 몫을 남기고 넣는다. 위 emit 과 이것을 가르는 것이 예약의 전부다. */
static void emit_telem(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_uart_write_bulk(line, len);
}

/* 젯슨(J29) 링크 — Cloud 계약을 말하는 mk_cloud 의 출구.
 * (2026-08-21 까지는 여기가 본선 텔레메트리의 바이트 미러였다 — 결선
 * 검증용이었고, 이제 mk_cloud 가 대체했다.) */
static void emit_cloud(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    mk_jet_write(line, len);
}

/* mk_hostlink 가 카탈로그를 **이어서** 내보낼 때 부른다.
 *
 * 🔴 [신설, 2026-08-20] 위 emit 과의 차이가 이번 결함의 고침 자리다.
 *    `$CFG,LIST` 는 103줄 ≈ 25 KB 인데 링은 8,192 B 다. 자리가 없으면
 *    0 을 돌려주고, 그 줄은 버려지지 않은 채 다음 슈퍼루프 바퀴에 다시
 *    온다. 버리면 카탈로그가 통째로 못 쓰게 되고(호스트가 cfg_end 의
 *    count 로 절단을 판정한다), 기다리면 슈퍼루프가 선다 — 어느 쪽도
 *    안 되므로 이어서 낸다. */
static int emit_stream(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    return mk_uart_write_stream(line, len);
}

static MkConfig s_cfg;
static MkAds    s_ads;
static MkRailCtl s_rails;
static MkTelem   s_telem;
static MkSolCtl  s_sol;
static MkI2c     s_i2c;
static MkGnss    s_gnss;
static MkGnssCtl s_gnssctl;
static MkTimeAx  s_timeax;
static MkLcd     s_lcd;
static MkCloud   s_cloud;
static MkImu     s_imu;
static MkScreen  s_screen;
static MkLinkBaud s_linkbaud;
static int      s_led_on;

/* 링크 속도를 실제로 바꾼다 (규격 §4.2). mk_linkbaud 가 부른다.
 *
 * 🔴 이 콜백이 불리는 시점이 이 기능의 전부다 — 응답($SACK,CFG,OK)이
 *    **옛 속도로 다 나간 뒤**여야 한다. 그 순서는 두 겹으로 지켜진다:
 *
 *      1) mk_hostlink_feed() 는 절대 apply 하지 않는다. 슈퍼루프가 그
 *         뒤에 부르는 mk_linkbaud_tick() 안에서만 일어난다.
 *      2) mk_uart_set_baud() 가 재설정 전에 TC(전송 완료)를 기다린다.
 *
 *    한 겹만으로는 부족하다. (1)만 있으면 emit 이 비동기(DMA)로 바뀌는
 *    순간 조용히 깨지고, (2)만 있으면 이 함수를 feed 안에서 부르는
 *    구조로 되돌아갔을 때 막을 것이 없다. */
static void apply_link_baud(void *ctx, uint32_t baud)
{
    (void)ctx;
    mk_uart_set_baud(baud);
}

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
 * 🔴 깊이를 어림으로 고르지 않는다 (2026-08-19, 채널당 10 ms 작업).
 *
 *    예전 값 32 는 "100 ms 주기에서 3.2초분" 이라는 근거였다. 주기를
 *    10 ms 로 내리면 같은 32 칸이 **0.32 초분**이 된다 — 근거가 통째로
 *    바뀐 것이지 값이 여전히 넉넉한 것이 아니다.
 *
 *    깊이가 견뎌야 하는 것은 **슈퍼루프가 큐를 안 비우고 지나가는 최악
 *    시간**이다. 그 값은 소스에 적혀 있다:
 *
 *      60 ms   I2C — HAL 블로킹. HAL 고정 I2C_TIMEOUT_BUSY(25 ms) +
 *              우리 타임아웃(5 ms) = xfer 한 번에 30 ms 이고, BH1750 의
 *              start 가 한 스텝에서 xfer 를 두 번 부른다
 *              (bsp/mk_i2c_io.c 머리말).
 *      27 ms   링크 완충 — 송신 링(4,096 B)이 가득 찬 상태에서 다 빠지는
 *              시간(1.5 Mbps ÷ 10 bit/B = 150,000 B/s). 그동안 새 줄은
 *              링에 못 들어가므로 표본이 큐에 쌓인다.
 *      ----
 *      87 ms   합. 10 ms 주기면 그 사이에 8 칸이 찬다.
 *
 *    🔴 [재계산, 2026-08-20 — 송신이 DMA 로 바뀐 뒤]
 *
 *    여기 있던 두 번째 항은 "134 ms 텔레메트리 — mk_uart_write 가
 *    HAL_UART_Transmit(블로킹)이다" 였고, 그것이 합의 대부분이었다.
 *    송신이 링버퍼+DMA 로 바뀌면서 그 항이 통째로 사라졌다
 *    (app/mk_txring.h, bsp/mk_uart.c). 이제 mk_telem_tick 은 줄을 만들어
 *    링에 memcpy 하고 끝난다 — 마이크로초 단위다.
 *
 *    그래서 최악 정지가 194 ms 에서 87 ms 로 줄었고, 하한은 2배를 둬도
 *    18 칸이다. 그런데 **값은 64 로 유지한다.** 이유가 바뀌었기 때문이다:
 *    큐의 역할이 "슈퍼루프 정지 흡수" 에서 "링크가 잠시 포화됐을 때의
 *    완충" 으로 옮겨 갔고, 그쪽에는 딱 떨어지는 상한이 없다. 64 칸은
 *    10 ms 주기에서 0.64 초분이고, MK_TELEM_MAX_LINES(11)가 채널 7개에
 *    한 줄씩 주고 남기는 4줄/틱으로 16틱(160 ms)이면 다 비운다 —
 *    따라잡을 수 있는 크기다. 더 키우면 비우는 데 걸리는 시간만 늘어난다.
 *
 *    비용은 7 × 64 × 16 B = 7 KB 다. DTCM 128 KB 에서 문제가 되지 않는다.
 *
 *    🔴 같은 계산이 host/tests/test_firmware_acquisition.py 에도 있다.
 *       거기서 이 값을 읽어 최소 깊이를 다시 계산하므로, 되돌리면 깨진다. */
#define SAMPLES_PER_CHANNEL  64
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

/* 🔴 여기만 임계구역이 필요하다.
 *
 *    수집의 진행은 인터럽트에 있다 — mk_ads_tick() 은 TIM7(1 kHz)이,
 *    다음 채널로 넘어가는 것은 SPI 완료 인터럽트가 한다. 그 인터럽트들은
 *    서로 같은 우선순위라 서로를 선점하지 못하지만(bsp/mk_ads_io.c 의
 *    MK_ADS_IRQ_PRIO), **슈퍼루프는 그 대열 밖**이다.
 *
 *    이 함수는 채널의 enabled·period_ms·next_due_ms 와 칩 설정(chip_dirty)
 *    을 쓴다. 그 도중에 인터럽트가 끼면 절반만 바뀐 상태로 한 바퀴가
 *    시작될 수 있다. 값이 실제로 바뀌는 일은 드물지만(설정 변경 때뿐),
 *    드문 만큼 재현이 안 되는 결함이 된다.
 *
 *    읽기 쪽(mk_telem 의 큐 배출)은 이미 mk_queue 자신이 같은 방식으로
 *    보호한다(app/mk_queue.h 의 [I3]). */
static void sync_channels(MkAds *ads, MkConfig *cfg, int64_t now_ms)
{
    /* 🔴 칩 전체 설정(증폭률·데이터율)을 밀어 넣는다.
     *
     *    이것이 없어서 화면에는 60 SPS 라고 떠 있는데 칩은 리셋 기본값인
     *    30,000 SPS 로 돌고 있었다 [2026-08-17]. 값은 나오니 아무도 눈치채지
     *    못하고, 필요 이상으로 잡음만 컸다. 설정과 칩을 잇는 선이 아예
     *    없었던 것 — ain*.enabled 때와 같은 종류의 빠짐이다. */
    MkCfgItem *pga = mk_cfg_find(cfg, "adc.pga");
    MkCfgItem *dr  = mk_cfg_find(cfg, "adc.drate");
    mk_critsec_enter();
    mk_ads_set_chip(ads,
                    pga != NULL ? pga->cur.u : 1u,
                    dr  != NULL ? dr->cur.u  : 60u);
    mk_critsec_exit();

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
        mk_critsec_enter();
        mk_ads_configure(ads, ch, (int)en->cur.u, (uint16_t)pr->cur.u, now_ms);
        mk_critsec_exit();
    }
}

/* 설정표의 led* 를 WS2812 체인으로 내보낸다.
 *
 * 🔴 매번 보내지 않는다. 한 프레임이 180us 이고 그동안 DMA1 이 물린다 —
 *    수집(SPI4)과 같은 컨트롤러다. 값이 바뀐 때와, 500ms 마다 한 번만 보낸다.
 *
 * 🔴 주기적으로도 보내는 이유: 5V 레일이 나중에 켜질 수 있다. 체인은 전원이
 *    없는 동안 받은 프레임을 기억하지 못하므로, 값이 안 바뀌었다는 이유로
 *    안 보내면 레일을 켜도 계속 깜깜하다. */
static MkStatLed s_statled;

/* 🔴 [개정 2026-08-22] LED 는 상태 표시 전용이다 (app/mk_statled.h —
 *    사용자 설계). 수동 색 항목(led{n}.r/g/b)은 카탈로그에서 걷어냈고,
 *    색은 mk_statled 가 보드 상태에서 계산한다. led.count·brightness·grb
 *    는 남는다 — 물린 스트립의 성질이지 색이 아니다. */
static void sync_leds(MkConfig *cfg, int64_t now_ms)
{
    static uint8_t last[1u + 1u + 1u + MK_STATLED_COUNT * 3u];
    static int64_t last_send;
    static int primed;

    /* 숨쉬기 패턴이 매 ms 변하므로 송신 빈도는 여기서 막는다 — 40ms(25fps)
     * 면 눈에 매끈하고 DMA·CPU 로는 공짜다. */
    if (primed && now_ms - last_send < 40) {
        return;
    }

    MkCfgItem *cnt = mk_cfg_find(cfg, "led.count");
    MkCfgItem *br  = mk_cfg_find(cfg, "led.brightness");
    MkCfgItem *grb = mk_cfg_find(cfg, "led.grb");
    /* 못 찾으면 끈 것으로 본다 — sync_rails 와 같은 이유다. */
    unsigned n      = cnt != NULL ? (unsigned)cnt->cur.u : 0u;
    uint8_t bright  = br  != NULL ? (uint8_t)br->cur.u   : 0u;
    MkWs2812Order order = (grb != NULL && grb->cur.u) ? MK_WS2812_GRB
                                                      : MK_WS2812_RGB;
    if (n > MK_LED_COUNT) {
        n = MK_LED_COUNT;
    }

    MkRgb status[MK_STATLED_COUNT];
    mk_statled_colors(&s_statled, now_ms, status);

    MkRgb lamps[MK_LED_COUNT];
    memset(lamps, 0, sizeof lamps);     /* 4번째 이후 자리는 꺼 둔다 */
    for (unsigned lamp = 0; lamp < MK_STATLED_COUNT && lamp < n; lamp++) {
        lamps[lamp] = status[lamp];
    }

    uint8_t now_state[sizeof last];
    size_t s = 0;
    now_state[s++] = (uint8_t)n;
    now_state[s++] = bright;
    now_state[s++] = (uint8_t)order;
    for (unsigned lamp = 0; lamp < MK_STATLED_COUNT; lamp++) {
        now_state[s++] = lamps[lamp].r;
        now_state[s++] = lamps[lamp].g;
        now_state[s++] = lamps[lamp].b;
    }

    int changed = !primed || memcmp(now_state, last, sizeof last) != 0;
    if (!changed && now_ms - last_send < 500) {
        return;
    }
    if (mk_ws2812_io_busy()) {
        return;                 /* 다음 바퀴에 다시 온다 */
    }

    size_t cap = 0;
    uint16_t *buf = mk_ws2812_io_buffer(&cap);
    size_t slots = mk_ws2812_encode(lamps, n, bright, order, buf, cap);
    if (slots == 0u || !mk_ws2812_io_send(slots)) {
        return;                 /* 보내지 못했으면 last 를 갱신하지 않는다 */
    }
    memcpy(last, now_state, sizeof last);
    last_send = now_ms;
    primed = 1;
}

int main(void)
{
    HAL_Init();

    /* 🔴 리셋 원인을 지금 읽는다 — RSR 플래그는 지우기 전까지 누적되므로
     *    (2026-08-22 실측 0x00fa0000 — 과거 리셋들이 다 쌓여 있었다),
     *    읽고 바로 지워야 "이번 부팅이 워치독이었나"가 다음 부팅에도
     *    참말이 된다. 상태 LED1 이 이 값으로 주황을 켠다(mk_statled). */
    int woke_from_iwdg = (RCC->RSR & RCC_RSR_IWDG1RSTF) != 0u;
    __HAL_RCC_CLEAR_RESET_FLAGS();
    /* 🔴 HAL_Init() 뒤여야 한다. 크리스털 대기 시한을 HAL_GetTick() 으로
     *    재는데, SysTick 은 HAL_Init() 이 켠다. 순서가 바뀌면 시한이
     *    영영 안 지나가고 — 벽돌을 막으려고 넣은 장치가 그 자리에서
     *    벽돌을 만든다. */
    mk_clock_init();

    /* 🔴 [검토 지적 I3] 큐를 쓰는 어떤 초기화보다도 먼저 등록한다.
     *    mk_queue_push/pop 이 이 훅으로 PRIMASK 임계구역을 얻는다 — 늦게
     *    등록하면 그 사이에 도는 ISR 이 기본(무보호) 구현으로 count 를
     *    건드릴 수 있다. */
    mk_queue_set_critical_section(mk_critsec_enter, mk_critsec_exit);

    mk_rails_init();

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

    /* 🔴 젯슨 레일(PD9)은 **부팅 최우선**으로 올린다 (사용자 결정
     *    2026-08-22). 메인 전원이 복귀하는 순간 절체 회로가 배터리를 즉시
     *    끊는데(HANDOFF §7.4b — 비교기는 차량 입력만 본다), 14.9V 가 순차
     *    기동을 기다리는 1초+ 공백에 젯슨이 브라운아웃됐다. 공백을 0 으로는
     *    못 만들지만(MCU 부팅 자체의 시간) LCD 초기화·레일 순차 대기를
     *    건너뛰어 수백 ms 로 줄인다.
     *
     *    저장 설정이 켜라고 할 때만 — 저장이 없거나 깨졌으면 기본값(꺼짐)
     *    그대로다(위 주석의 이유). */
    int early_14v9 = 0;
    {
        MkCfgItem *v14 = mk_cfg_find(&s_cfg, "pwr.14v9");
        if (v14 != NULL && v14->cur.u != 0u) {
            mk_rails_set(NULL, MK_RAIL_14V9, 1);
            early_14v9 = 1;
        }
    }

    /* 🔴 UART 를 여는 것이 설정을 읽은 **뒤**다 (규격 §4.2).
     *
     *    속도가 이제 설정 항목이라 Flash 를 읽기 전에는 무엇으로 열어야
     *    하는지 알 수 없다. 순서를 되돌리면 저장된 속도로 바꾸는 순간
     *    부팅 로그가 중간에서 끊긴다.
     *
     *    저장된 값이 이 클럭으로 못 내는 값이면 mk_linkbaud_init() 이
     *    기본값으로 대체한다 — 확정된 값만 저장되므로 정상 경로에서는
     *    일어나지 않지만, 저장이 깨졌을 때 보드가 벽돌이 되지 않게 하는
     *    마지막 방어선이다. 그 실패는 굽기로만 풀린다(CLAUDE.md §4). */
    {
        MkCfgItem *baud_item = mk_cfg_find(&s_cfg, "link.baud");
        uint32_t want = baud_item ? baud_item->cur.u : UART_BAUD;
        mk_linkbaud_init(&s_linkbaud, MK_USART3_KERNEL_HZ, want);
        if (baud_item != NULL) {
            baud_item->cur.u = mk_linkbaud_active(&s_linkbaud);
        }
        mk_uart_init(mk_linkbaud_active(&s_linkbaud));
    }

    /* 🔴 젯슨 링크(USART2 미러 + PA1 하트비트, 2026-08-21). 결선돼 있지
     *    않아도 무해하다 — TX 뿐이라 아무도 안 들으면 바이트가 사라질
     *    뿐이다(설계 원칙 3 과 같은 결). baud 는 MK_JET_BAUD 고정. */
    mk_jet_init();

    MkHostlink link;
    mk_hostlink_init(&link, emit, NULL, DEVICE_ID, FW_VERSION, BOARD_REV);

    /* 🔴 카탈로그를 이어서 내보내는 통로. 이것을 안 붙이면 `$CFG,LIST` 가
     *    25 KB 를 한 자리에서 쏟아 링(8,192 B)을 넘기고, 넘친 줄이 버려져
     *    호스트가 카탈로그를 통째로 거부한다 — GUI 가 설정 폼을 못 만든다
     *    (실기기 2026-08-20). */
    mk_hostlink_attach_stream(&link, emit_stream);

    /* 🔴 송신 링 상태를 `$STAT` 에 싣는다(규격 §7.4). 이 수가 전선에
     *    없어서 위 결함을 GDB 로 `p 'mk_uart.c'::s_tx` 를 해서야 찾았다. */
    mk_hostlink_attach_txring(&link, mk_uart_tx_ring());

    /* 🔴 무엇으로 도는지 호스트에 알린다(규격 §7.4). 크리스털이 안 떠서
     *    내부 RC 로 폴백했으면 초 안쪽 보간이 두 자릿수 나빠지는데, 그것을
     *    모르고 저장하면 카메라 정렬이 조용히 틀어진다.
     *
     *    sysclk 는 상수가 아니라 HAL 이 레지스터에서 읽어 계산한 값이다 —
     *    "보드가 믿는 값" 이 아니라 "실제로 선 값" 을 싣는다. */
    mk_hostlink_attach_clock(&link, mk_clock_source_name(),
                             mk_clock_sysclk_hz());

    /* 🔴 $BAUD,CONFIRM 을 받을 수 있게 하고(규격 §4.2.3), 확인 대기 중에는
     *    $CFG,SAVE 를 막는다(§4.2.2 규칙 5). 이것을 안 붙이면 링크 속도를
     *    바꿀 수는 있는데 확정할 방법이 없어 **반드시 10초 뒤 되돌아간다.** */
    mk_hostlink_attach_linkbaud(&link, &s_linkbaud);

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
    if (early_14v9) {
        /* 위에서 이미 올린 PD9 를 상태기계에 알린다 — 안 알리면 $STAT 이
         * 켜진 레일을 꺼졌다고 말한다(mk_railctl_prime 주석). */
        mk_railctl_prime(&s_rails, MK_RAIL_14V9);
    }
    /* 🔴 J18~J20 은 입력이다(사용자 확정 2026-08-18) — mk_solctl_init() 이
     *    큐를 먼저 비운 뒤에 mk_sol_init() 을 부른다. mk_sol_init() 은 핀을
     *    열자마자 실제 레벨을 동기적으로 읽어 mk_solctl_prime() 으로 초기
     *    상태를 세우므로, 순서가 바뀌면 prime() 이 아직 초기화 안 된
     *    구조체에 쓰게 된다.
     *
     *    `mk_sol_read` 를 여기서 등록한다 — 상태의 근거는 엣지가 아니라
     *    레벨이므로(같은 날 확정), mk_solctl_tick() 이 매 바퀴 이 콜백으로
     *    세 핀을 직접 읽어 디바운스를 건다. GPIO 는 뒤이은 mk_sol_init() 이
     *    여는데, 여기서는 함수 포인터만 저장할 뿐 아직 부르지 않으므로
     *    순서가 문제되지 않는다. */
    mk_solctl_init(&s_sol, mk_sol_read, NULL);
    mk_sol_init(&s_sol);
    mk_ws2812_io_init();

    /* 🔴 I2C 버스 셋. 카탈로그(mk_cfgtable 의 i2c*.kind·addr)에는 이미
     *    항목이 있었지만 xfer 뒤가 비어 있어 파형이 안 나갔다. */
    mk_i2c_io_init();
    /* 🔴 delay_us 를 채운다 — AM2320 이 깨우기·명령 사이에 실제로
     *    기다려야 한다(app/mk_i2c_am2320.c). 안 채우면 그 드라이버가
     *    타이밍을 못 지킨다. */
    MkI2cIo i2c_io = { mk_i2c_io_xfer, NULL, mk_i2c_io_delay_us };
    mk_i2c_init(&s_i2c, &i2c_io);

    /* 🔴 LCD(J25). 하드웨어는 늘 연다 — `lcd.enabled` 가 꺼져 있으면
     *    mk_lcd_tick() 이 SPI 로 한 바이트도 안 내보내므로, 패널이 안 물린
     *    보드에서도 아무 일이 없다(mk_i2c_io_init() 이 포트마다 kind=없음
     *    이어도 버스를 여는 것과 같은 결).
     *
     *    이 초기화 자체는 해 둬야 한다 — PD14(터치 CS)를 High 로 못박는
     *    일이 여기 있고, 그것은 화면을 쓰든 안 쓰든 필요하다. LCD 와
     *    터치가 같은 SPI 버스라 떠 있는 CS 하나가 나중에 버스를 물고
     *    늘어질 수 있다 (bsp/mk_lcd_io.h). */
    mk_lcd_io_init(&s_lcd);

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
    mk_telem_attach_i2c(&s_telem, &s_i2c);
    mk_telem_attach_sol(&s_telem, &s_sol);

    /* 🔴 젯슨(J29) 링크의 직렬화기 — mk_telem 의 병렬 소비자다. 같은
     *    수집원을 읽기만 하고 본선 출력에는 관여하지 않는다(app/mk_cloud.h,
     *    설계 2026-08-21). */
    mk_cloud_init(&s_cloud, &s_cfg, &s_ads, DEVICE_ID, FW_VERSION);
    mk_cloud_attach_i2c(&s_cloud, &s_i2c);
    mk_cloud_attach_sol(&s_cloud, &s_sol);
    mk_imu_init(&s_imu);
    mk_cloud_attach_imu(&s_cloud, &s_imu);
    mk_hostlink_attach_rails(&link, &s_rails);
    mk_hostlink_attach_sol(&link, &s_sol);

    /* 🔴 GNSS/PPS 시간축(Phase 3). 하드웨어(UART6·TIM8)는 늘 켠다 — J16 에
     *    아무것도 안 꽂혀 있으면 바이트도 펄스도 안 오므로 그냥
     *    device_clock 에 머문다(설계 원칙 3, mk_i2c_io_init() 이 포트마다
     *    kind=없음 이어도 버스를 여는 것과 같은 결). `gnss.enabled` 는
     *    지금은 화면에 보여 줄 사용자 의도 표시일 뿐, 이 초기화를 막지
     *    않는다 — 카탈로그 기본값이 꺼짐이라도 모듈을 실제로 꽂아
     *    확인할 길이 남아 있어야 한다. */
    {
        MkCfgItem *baud_item = mk_cfg_find(&s_cfg, "gnss.baud");
        uint32_t gnss_baud = baud_item ? baud_item->cur.u : 115200u;
        mk_gnss_io_init(gnss_baud);
    }
    mk_gnss_init(&s_gnss);
    mk_timeax_init(&s_timeax);
    mk_telem_attach_timeax(&s_telem, &s_timeax);
    mk_cloud_attach_gnss(&s_cloud, &s_gnss);
    mk_cloud_attach_timeax(&s_cloud, &s_timeax);

    /* 🔴 상태 LED (app/mk_statled.h) — 모든 출처가 선 뒤에 붙인다.
     *    mk_screen 과 같은 이유: 포인터만 들고 매 바퀴 그때의 값을 읽는다. */
    mk_statled_init(&s_statled, &s_cfg, &s_ads, woke_from_iwdg);
    mk_statled_attach_i2c(&s_statled, &s_i2c);
    mk_statled_attach_gnss(&s_statled, &s_gnss);
    mk_hostlink_attach_timeax(&link, &s_timeax);
    /* 🔴 $GNSS(규격 §4.1) 와 원시 문장 에코(규격 §7.7) — 둘 다 UART6 가
     *    이미 열려 있어야 뜻이 있으므로 mk_gnss_io_init() 뒤에 놓는다. */
    mk_hostlink_attach_gnss(&link, mk_gnss_io_write, NULL);
    mk_telem_attach_gnss(&s_telem, &s_gnss);
    mk_gnssctl_init(&s_gnssctl);
    mk_hostlink_attach_gnssctl(&link, &s_gnssctl);

    /* 🔴 화면 내용. **모든 출처가 선 뒤에** 붙인다 — mk_screen 은 포인터만
     *    들고 매 갱신 주기에 그때의 값을 읽으므로, 아직 초기화 안 된
     *    구조체를 붙이면 첫 주기에 쓰레기를 그린다.
     *
     * 🔴 새 큐를 만들지 않는다 (사용자 설계 2026-08-19). 화면은 마지막
     *    값만 보면 되고, 그것은 이미 수집기들이 들고 있다. 큐를 하나 더
     *    두면 표본이 두 벌 생겨 어느 쪽이 진짜인지 흐려진다. */
    {
        MkScreenSources src = { &s_cfg, &s_ads, &s_i2c, &s_sol,
                                &s_timeax, &s_rails };
        mk_screen_init(&s_screen, &src);
    }

    /* 🔴 화면 회복 계수기를 $STAT 에 싣는다 (규격 §7.4). 실기기에서
     *    "노이즈 타면 픽셀이 다 깨지는데?" 가 나온 뒤 넣은 것이라,
     *    **몇 번 깨졌고 몇 번 되살렸는지**를 밖에서 볼 수 없으면 이 문제가
     *    해결됐는지 덮였는지 알 방법이 없다. */
    mk_hostlink_attach_lcd(&link, &s_lcd);

    char rx[MK_RX_LINE_MAX];
    uint32_t last_blink = 0;

    /* 🔴 워치독은 초기화가 다 끝난 여기서 켠다 — 위의 LCD 대기(수백 ms)
     *    등이 예산을 먹지 않게. 이 아래로 루프가 5초 서면 칩이 스스로
     *    리셋한다(bsp/mk_iwdg.h — 사용자 결정 2026-08-22). */
    mk_iwdg_init();

    for (;;) {
        mk_iwdg_kick();
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

        /* 🔴 링크 속도 (규격 §4.2). **자리가 곧 안전장치다.**
         *
         *    여기는 이번 바퀴에 나갈 응답($SACK)과 하트비트($HB)가 전부
         *    전선으로 빠져나간 뒤다. 그래서 속도를 바꿔도 잘리는 바이트가
         *    없다. 위(mk_hostlink_feed)로 올리면 응답 한가운데서 속도가
         *    바뀌어 호스트가 성공했는지조차 모르게 된다.
         *
         *    되돌림도 여기서 일어난다 — 확인이 10초 안에 안 오면 옛 속도로
         *    스스로 돌아간다. 사람이 아무것도 안 해도 링크가 살아나는 것이
         *    이 한 줄의 값이다. */
        mk_linkbaud_tick(&s_linkbaud, &s_cfg, now, apply_link_baud, NULL);

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

        /* 🔴 디지털 입력도 매 바퀴 민다 — ISR 이 큐에 쌓아 둔 엣지를
         *    비우고 디바운스를 진행한다. `sol.debounce_ms` 를 여기서
         *    직접 읽으므로(mk_solctl_tick 내부) 설정이 바뀐 것을 알아챌
         *    다른 통로가 필요 없다. mk_telem_tick() 이 그 뒤에 확정된
         *    레코드를 mk_solctl_take() 로 꺼내 din 으로 내보낸다. */
        mk_solctl_tick(&s_sol, &s_cfg, now);
        sync_leds(&s_cfg, now);

        /* 🔴 I2C 도 매 바퀴 민다. 한 바퀴에 포트 하나만 나아가므로
         *    전송이 겹치지 않는다. */
        mk_i2c_tick(&s_i2c, &s_cfg, now);

        /* 🔴 여기에 mk_ads_tick() 이 **없다** (2026-08-19).
         *
         *    수집의 진행은 인터럽트로 옮겼다 — TIM7(1 kHz)이 쉬고 있던
         *    상태머신을 깨우고, SPI 완료 인터럽트 안의 finish() 가 밀린
         *    다음 채널로 곧바로 이어 간다 (bsp/mk_ads_io.c 의
         *    MK_ADS_TICK_HZ 주석에 계산이 있다).
         *
         *    여기서 부르면 수집 주기가 이 루프의 주기에 다시 묶인다. 바로
         *    아래 mk_i2c_tick() 하나가 최악 60 ms 를 잡는다 (2026-08-20
         *    까지는 mk_telem_tick() 의 HAL_UART_Transmit 도 블로킹이라 여기에
         *    더해졌다 — 지금은 링버퍼+DMA 로 걷어냈다). 7채널 × 10 ms 면 채널
         *    하나에 1.43 ms 인데 그 예산이 지켜질 수 없고, 못 뜬 표본은
         *    큐의 drops 에도 안 잡힌 채 사라진다.
         *
         *    host/tests/test_firmware_acquisition.py 가 이 자리를 지킨다. */

        /* 🔴 화면도 매 바퀴 민다. 한 바퀴에 걸음 하나뿐이고, 전송이 떠
         *    있으면 즉시 돌아온다 — 한 장(460,800바이트)을 다 그리는 동안
         *    수집이 서지 않는 것이 이 구조의 요지다 (app/mk_lcd.h). */
        mk_lcd_tick(&s_lcd, &s_cfg, now);

        /* 🔴 화면 내용도 매 바퀴 민다. 값을 다시 읽는 것은 `lcd.period_ms`
         *    마다(기본 250 ms)이고, 그때도 **바뀐 칸만** 다시 그린다 —
         *    값이 그대로면 SPI 로 한 바이트도 안 나간다.
         *
         *    순서가 뜻이 있다: mk_lcd_tick() 이 먼저 한 걸음 나아가야 그
         *    바퀴에 화면이 놀게 되고(mk_lcd_idle), 다음 칸을 맡길 수 있다.
         *    반대로 두면 언제나 한 바퀴씩 늦는다. */
        mk_screen_tick(&s_screen, &s_lcd, now);

        /* 🔴 GNSS/PPS(Phase 3). 순서가 뜻이 있다 —
         *
         *    1) UART 링을 통째로 비운다. 한 바퀴에 한 바이트만 먹이면
         *       NMEA 문장이 몰려올 때(1Hz 로 여러 문장이 뭉텅이로 옴)
         *       뒤로 밀린다.
         *    2) PPS 캡처를 timeax 에 알린다 — RMC 보다 먼저 해야 그
         *       사이 도착한 RMC 가 즉시 이 펄스와 짝지어질 수 있다
         *       (mk_timeax.h 의 짝짓기 규칙 — 펄스가 먼저, 문장이 뒤).
         *    3) 파싱된 RMC/GGA 를 timeax 에 먹인다.
         *    4) tick() 으로 신선도를 재본다 — 새 데이터가 없어도 매
         *       바퀴 불러야 등급이 스스로 내려간다. */
        uint8_t gnss_byte;
        while (mk_gnss_io_read_byte(&gnss_byte)) {
            mk_gnss_feed(&s_gnss, gnss_byte);
            /* 🔴 같은 바이트를 IMU 조립기에도 준다 — RAWIMUX('#')는 NMEA
             *    ('$')와 다른 형식이라 조립기가 따로다(app/mk_imu.h). 서로
             *    남의 문장은 무시하므로 한 흐름을 나눠 먹어도 안전하다. */
            mk_imu_feed(&s_imu, gnss_byte, now);
        }

        uint64_t gnss_now_us = mk_gnss_io_now_us();

        uint64_t pps_us;
        while (mk_gnss_io_pps_take(&pps_us)) {
            mk_timeax_on_pps(&s_timeax, pps_us);
        }

        MkGnssRmc rmc;
        while (mk_gnss_take_rmc(&s_gnss, &rmc)) {
            mk_timeax_on_rmc(&s_timeax, &rmc, gnss_now_us);
        }
        MkGnssGga gga;
        while (mk_gnss_take_gga(&s_gnss, &gga)) {
            mk_timeax_on_gga(&s_timeax, &gga);
        }
        mk_timeax_tick(&s_timeax, gnss_now_us);

        /* 🔴 켤 때 초기화 명령(규격 §4.1.1) — gnss.enabled 가 켜지면
         *    LOG 명령을 대신 보내고, 문장을 받으면 멈춘다. 못 찾으면
         *    꺼진 것으로 본다 — sync_rails() 와 같은 이유(설정표에서
         *    항목이 사라지는 것은 실수이고, 실수했을 때 계속 재시도하며
         *    UART 를 갉아먹으면 안 된다). */
        {
            MkCfgItem *en = mk_cfg_find(&s_cfg, "gnss.enabled");
            MkCfgItem *imu = mk_cfg_find(&s_cfg, "gnss.imu");
            mk_gnssctl_set_imu(&s_gnssctl, imu != NULL && imu->cur.u);
            mk_gnssctl_tick(&s_gnssctl, en != NULL && en->cur.u,
                            mk_gnss_any_sentence_seen(&s_gnss), now,
                            mk_gnss_io_write, NULL);
        }

        /* 🔴 듣는 사람이 없으면 COM23 텔레메트리를 침묵시킨다 (규격 §7.1.3,
         *    사용자 결정 2026-08-22). 판정은 CONFIG 모드와 같은 HB 신선도다
         *    — 케이블은 감지할 수 없고(CLAUDE.md §4) HB 가 곧 "저쪽에서
         *    사람이 보고 있다"이다. 아무도 안 읽는 홍수가 F103(BMP)을
         *    굳게 한 것이 이 게이트의 이유다(HANDOFF §5). */
        mk_telem_set_host_alive(
            &s_telem, mk_hostlink_mode(&link, now) == MK_MODE_CONFIG);
        mk_telem_tick(&s_telem, now, emit_telem, NULL);
        /* 🔴 젯슨(J29) 링크 — 규격 v3 가 아니라 Cloud 계약(v1.7.0)을 말한다
         *    (app/mk_cloud.h). 2026-08-21 부터 미러가 아니다. */
        mk_cloud_tick(&s_cloud, now, emit_cloud, NULL);

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

        /* 젯슨 워치독용 하트비트 — PA1 을 1 Hz 로 토글한다. 젯슨이 이
         * 엣지를 일정 시간 못 보면 NRST 로 보드를 리셋한다(사용자 설계
         * 2026-08-21, HANDOFF.md §7.4). */
        mk_jet_hb_tick();
    }
}

/* 참고 펌웨어(h723_sensor_read)에서 그대로 가져왔다. 이 보드에서 실증된
 * 유일한 클럭 설정이다. HSI 64 MHz, PLL 없음, 모든 분주 1 → APB1 64 MHz.
 * USART3 이 APB1 에 있으므로 115200 은 물론 921600 도 낼 수 있다. */
/* 🔴 클럭 설정은 bsp/mk_clock.c 로 옮겼다.
 *
 *    여기 있던 판은 HSI 를 그대로 쓰고, 실패하면 `for (;;)` 로 섰다.
 *    HSE 크리스털을 쓰기 시작하면 그 두 성질이 모두 위험해진다 — 발진기가
 *    안 뜨는 것은 실제로 일어나는 일이고, 거기서 서면 보드가 벽돌이 된다.
 *
 *    옮기면서 얻은 것: 클럭 숫자가 mk_clock.h 한 곳에 모였고, 파생 상수
 *    (프리스케일·분주비·대기 루프)가 거기서 나온다. */
