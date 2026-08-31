/* mk_statled 단위 시험 — 보드 없이 상태→색 계약을 본다.
 *
 * 🔴 여기서 지키는 것: 설계 원칙 3(켜진 채널만 판단)·4(실측에서만 초록),
 *    그리고 패턴 규칙(안정=상시, 진행=숨쉬기, 문제=점멸). */
#include <stdio.h>
#include <string.h>
#include "../app/mk_statled.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_ads1256.h"   /* mk_ads_raw_to_ma (구 mk_telem, 2026-08-31 이사) */

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static MkConfig  CFG;
static MkAds     ADS;
static MkI2c     I2C;
static MkGnss    GNSS;
static MkStatLed SL;
static MkSample  BUF[MK_ADS_CHANNELS][8];

static void setup(int iwdg)
{
    mk_cfgtable_init(&CFG);
    memset(&ADS, 0, sizeof ADS);
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        mk_ads_attach_queue(&ADS, ch, BUF[ch], 8);
    }
    MkI2cIo io = { NULL, NULL };
    mk_i2c_init(&I2C, &io);
    mk_gnss_init(&GNSS);
    mk_statled_init(&SL, &CFG, &ADS, iwdg);
    mk_statled_attach_i2c(&SL, &I2C);
    mk_statled_attach_gnss(&SL, &GNSS);
}

static void set_flag(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

/* 20mA 근처의 원시 코드 — mk_telem_raw_to_ma 역산 대신 실측 코드 사용
 * (test_telem 의 CODES: 4026531 ≈ 20mA, 805306 ≈ 4mA). */
#define RAW_20MA 4026531
#define RAW_4MA   805306
#define RAW_0MA        0

static void feed_ain(int ch, int64_t t_ms, int32_t raw)
{
    mk_ads_configure(&ADS, ch, 1, 100, 0);
    ADS.ch[ch].last.t_ms = t_ms;
    ADS.ch[ch].last.raw = raw;
    ADS.ch[ch].has_last = 1u;
}

static void feed_gga(const char *line)
{
    for (const char *p = line; *p; p++) {
        mk_gnss_feed(&GNSS, (uint8_t)*p);
    }
}

/* 실기기에서 받은 문장 그대로 (test_cloud.c 와 동일 — 체크섬 실증).
 * fix 는 RMC 가 완성한다(mk_gnss.c push_fix) — GGA 만으로는 안 밀린다. */
#define RTK_GGA "$GNGGA,023116.00,3719.14416560,N,12720.43544199,E,4,20,0.8,100.8517,M,23.8989,M,1.2,0421*57\r\n"
#define RTK_RMC "$GNRMC,023116.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5D\r\n"

static int is_off(MkRgb c)   { return c.r == 0 && c.g == 0 && c.b == 0; }
static int is_green(MkRgb c) { return c.g > 0 && c.r == 0 && c.b == 0; }
static int is_red(MkRgb c)   { return c.r > 0 && c.g == 0 && c.b == 0; }
static int is_yellow(MkRgb c){ return c.r > 0 && c.g > 0 && c.b == 0; }

/* ---- LED1: 시스템 -------------------------------------------------------- */

static void test_system_breathes_green_when_healthy(void)
{
    setup(0);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_green(c[0]), "정상이면 초록");
    /* 숨쉬기 — 삼각파의 골(2000의 배수)과 마루(1000 홀수배)가 다르다 */
    MkRgb lo[3], hi[3];
    mk_statled_colors(&SL, 4000, lo);
    mk_statled_colors(&SL, 5000, hi);
    CHECK(lo[0].g != hi[0].g, "숨쉬기 — 밝기가 시간에 따라 변한다");
    CHECK(lo[0].g > 0, "숨쉬기 바닥에서도 꺼지지 않는다 — 점멸과 헷갈리면 안 된다");
}

static void test_system_orange_after_watchdog_reset(void)
{
    setup(1);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(c[0].r > 0 && c[0].g > 0 && c[0].g < c[0].r,
          "워치독 부활 직후는 주황 — RAM 설정이 날아갔다는 신호다");
    mk_statled_colors(&SL, 6000, c);
    CHECK(is_green(c[0]), "5초가 지나면 정상 표시로 돌아간다");
}

static void test_system_blinks_red_on_queue_drops(void)
{
    setup(0);
    mk_ads_configure(&ADS, 0, 1, 100, 0);
    MkQueue *q = mk_ads_queue(&ADS, 0);
    for (int k = 0; k < 20; k++) {          /* 용량 8 — 나머지는 드랍 */
        mk_queue_push(q, k, k);
    }
    MkRgb on[3], off[3];
    mk_statled_colors(&SL, 10000, on);      /* 10000%800=400 <500 → 켜짐 */
    mk_statled_colors(&SL, 11000, off);     /* 11000%800=600 ≥500 → 꺼짐 */
    CHECK(is_red(on[0]), "드랍이 생기면 빨강");
    CHECK(is_off(off[0]), "점멸이다 — 문제 = 점멸 규칙");
    mk_statled_colors(&SL, 17000, on);      /* 15000 이후 + 새 드랍 없음 */
    CHECK(is_green(on[0]), "드랍이 멎고 5초 지나면 초록으로 돌아온다");
}

/* ---- LED2: 측위 ---------------------------------------------------------- */

static void test_fix_led_off_when_gnss_disabled(void)
{
    setup(0);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_off(c[1]), "GNSS 비활성이면 꺼짐 — 미연결은 정상이다(원칙 3)");
}

static void test_fix_led_blinks_red_without_reception(void)
{
    setup(0);
    set_flag("gnss.enabled", 1u);
    MkRgb c[3];
    mk_statled_colors(&SL, 400, c);
    CHECK(is_red(c[1]), "켰는데 수신이 없으면 빨강 점멸 — 설정만으로 초록을 켜지 않는다(원칙 4)");
}

static void test_fix_led_green_on_rtk_fixed_then_stale(void)
{
    setup(0);
    set_flag("gnss.enabled", 1u);
    feed_gga(RTK_GGA);
    feed_gga(RTK_RMC);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_green(c[1]), "RTK fixed(4) = 초록");
    /* 수신이 끊긴다 — 3초 넘게 새 fix 없음 */
    mk_statled_colors(&SL, 5000, c);
    CHECK(is_red(c[1]) || is_off(c[1]),
          "3초 무수신이면 옛 fix 로 초록을 유지하지 않는다 — 등급은 저절로 내려간다");
}

/* ---- LED3: 센서 ---------------------------------------------------------- */

static void test_sensor_led_off_when_nothing_enabled(void)
{
    setup(0);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_off(c[2]), "켜진 채널이 없으면 꺼짐");
}

static void test_sensor_led_green_when_all_alive(void)
{
    setup(0);
    feed_ain(0, 900, RAW_20MA);
    feed_ain(1, 900, RAW_4MA);
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_green(c[2]), "켜진 채널이 전부 살아 있으면 초록 (4mA 는 정상 하한)");
}

static void test_sensor_led_yellow_on_loop_break(void)
{
    setup(0);
    feed_ain(0, 900, RAW_0MA);              /* 0mA = 단선 (NAMUR < 3.6mA) */
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_yellow(c[2]), "3.6mA 미만이면 노랑 — 단선 의심");
}

static void test_sensor_led_blinks_red_on_stale_channel(void)
{
    setup(0);
    feed_ain(0, 900, RAW_20MA);
    MkRgb c[3];
    mk_statled_colors(&SL, 900 + MK_STATLED_STALE_MS + 400, c);
    CHECK(is_red(c[2]), "값이 두절되면 빨강 점멸 — 켜진 채널이 죽었다");
}

static void test_sensor_led_ignores_disabled_channels(void)
{
    setup(0);
    feed_ain(0, 900, RAW_20MA);
    /* ch1 은 값이 낡았지만 **꺼져 있다** — 판단에서 빠져야 한다(원칙 3) */
    ADS.ch[1].last.t_ms = 0;
    ADS.ch[1].last.raw = RAW_0MA;
    ADS.ch[1].has_last = 1u;
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_green(c[2]), "꺼진 채널의 낡은 값은 판단에 안 들어간다");
}

static void test_sensor_led_blinks_red_on_i2c_failure(void)
{
    setup(0);
    set_flag("i2c12.enabled", 1u);
    set_flag("i2c12.kind", 3u);             /* MLX90614 */
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12, .quantity = "temp_object",
                                 .value = 0, .have_value = 0, .status = 1,
                                 .t_ms = 900 };
    I2C.last_valid[2][0] = 1u;
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_red(c[2]), "I2C 읽기 실패(status 1)면 빨강 점멸");
}

static void test_sensor_led_treats_unsupported_as_normal(void)
{
    setup(0);
    set_flag("i2c12.enabled", 1u);
    set_flag("i2c12.kind", 3u);
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12, .quantity = "temp_object",
                                 .value = 24.0f, .have_value = 1, .status = 0,
                                 .t_ms = 900 };
    I2C.last_valid[2][0] = 1u;
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12, .quantity = "temp_ambient",
                                 .value = 0, .have_value = 0, .status = 3,
                                 .t_ms = 900 };
    I2C.last_valid[2][1] = 1u;
    MkRgb c[3];
    mk_statled_colors(&SL, 1000, c);
    CHECK(is_green(c[2]), "미지원(status 3)은 정상 취급 — 없는 것이 정상인 슬롯이 있다");
}

int main(void)
{
    printf("mk_statled\n");
    test_system_breathes_green_when_healthy();
    test_system_orange_after_watchdog_reset();
    test_system_blinks_red_on_queue_drops();
    test_fix_led_off_when_gnss_disabled();
    test_fix_led_blinks_red_without_reception();
    test_fix_led_green_on_rtk_fixed_then_stale();
    test_sensor_led_off_when_nothing_enabled();
    test_sensor_led_green_when_all_alive();
    test_sensor_led_yellow_on_loop_break();
    test_sensor_led_blinks_red_on_stale_channel();
    test_sensor_led_ignores_disabled_channels();
    test_sensor_led_treats_unsupported_as_normal();

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all ok\n");
    return 0;
}
