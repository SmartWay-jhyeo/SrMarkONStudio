/* mk_cloud 단위 시험 — J29(젯슨) 링크로 나가는 Cloud 스키마 직렬화.
 *
 * 계약: docs/데이터 스키마 명세서 v1.7.x. 선택 필드의 켬/끔은 본선과 같은
 * 마스크(tx.fields_*)가 정한다(사용자 확정 2026-08-22 — "기존 NDJSON 필드
 * 선택 화면이 곧 이 링크의 구성이다"). 보드도 크로스 툴체인도 필요 없다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_cloud.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_gnss.h"
#include "../app/mk_i2c.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_HAS(hay, needle, msg) \
    CHECK((hay) != NULL && strstr((hay), (needle)) != NULL, msg)

/* ---- 모은 줄 ------------------------------------------------------------ */

#define CAP 32
static char LINES[CAP][512];
static int  N;

static void sink(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    if (N >= CAP) return;
    size_t n = len < sizeof LINES[0] - 1u ? len : sizeof LINES[0] - 1u;
    memcpy(LINES[N], line, n);
    LINES[N][n] = '\0';
    N++;
}

static const char *find_line(const char *type)
{
    char want[64];
    snprintf(want, sizeof want, "\"type\":\"%s\"", type);
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], want)) { return LINES[k]; }
    }
    return NULL;
}

static MkConfig CFG;
static MkAds    ADS;
static MkCloud  C;
static MkSample BUF[4][8];

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void set_f32(const char *key, float v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.f = v; }
}

/* 필드 표에서 이름으로 비트값을 얻는다 — 비트 번호를 시험에 적지 않는다
 * (test_telem.c 의 test_field_mask_selects 와 같은 이유). */
static uint32_t bit_of(const char *name)
{
    size_t n = 0;
    const MkFieldBit *f = mk_cfgtable_fields(&n);
    for (size_t i = 0; i < n; i++) {
        if (strcmp(f[i].name, name) == 0) { return 1u << f[i].bit; }
    }
    return 0u;
}

static void mask_on(const char *mask_key, const char *field)
{
    MkCfgItem *it = mk_cfg_find(&CFG, mask_key);
    if (it) { it->cur.u |= bit_of(field); }
}

static void set_last(int ch, int64_t t_ms, int32_t raw)
{
    ADS.ch[ch].last.t_ms = t_ms;
    ADS.ch[ch].last.raw = raw;
    ADS.ch[ch].has_last = 1u;
}

static void setup(void)
{
    N = 0;
    mk_cfgtable_init(&CFG);
    memset(&ADS, 0, sizeof ADS);
    for (int ch = 0; ch < 4; ch++) {
        mk_ads_attach_queue(&ADS, ch, BUF[ch], 8);
        mk_ads_configure(&ADS, ch, 1, 100, 0);
    }
    mk_cloud_init(&C, &CFG, &ADS, "1", "0.1.0");
}

static void drain_capability(void)
{
    mk_cloud_tick(&C, 1, sink, NULL);
    N = 0;
}

/* ---- ain ---------------------------------------------------------------- */

static void test_ain_pressure_paint_record(void)
{
    setup();
    drain_capability();
    set_u32("ain0.cloud", 1u);            /* 타입 = 도료 분사압 */
    set_f32("ain0.zero", 4.0f);
    set_f32("ain0.scale", 9.375f);
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("pressure_paint");
    CHECK(ln != NULL, "선택한 타입으로 나간다");
    CHECK_HAS(ln, "\"schema_ver\":1", "schema_ver 기본 1");
    CHECK_HAS(ln, "\"device_id\":\"1\"", "device_id = dev.id 기본값");
    CHECK_HAS(ln, "\"bar\":150.0", "(ma-zero)*scale, 소수 1자리");
    CHECK_HAS(ln, "\"time_source\":\"device_clock\"", "timeax 없으면 device_clock");
    CHECK(strstr(ln, "\"valve\":") == NULL, "밸브 비트 꺼짐 = 태깅 없음");
    CHECK(strstr(ln, "\"seq\":") == NULL, "계약에 seq 는 없다 — v3 방언 섞임 금지");
}

static void test_common_field_values_are_config(void)
{
    /* 🔴 사용자 확정 2026-08-22 — 공통 필드 중 값을 바꿀 수 있는 것은
     *    스키마 버전과 장치 식별자다. 장치 식별자는 기존 dev.id 를 쓴다. */
    setup();
    drain_capability();
    set_u32("tx.schema_ver", 2u);
    MkCfgItem *dev = mk_cfg_find(&CFG, "dev.id");
    if (dev) { snprintf(dev->cur.s, sizeof dev->cur.s, "car-7"); }
    set_u32("ain0.cloud", 3u);
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("flow");
    CHECK_HAS(ln, "\"schema_ver\":2", "스키마 버전이 설정을 따른다");
    CHECK_HAS(ln, "\"device_id\":\"car-7\"", "장치 식별자 = dev.id");
}

static void test_ain_optional_fields_follow_the_mask(void)
{
    /* 🔴 선택 필드는 본선과 같은 마스크가 정한다 — 화면 하나로 양쪽이
     *    같이 움직인다(사용자 확정 2026-08-22). */
    setup();
    drain_capability();
    set_u32("ain0.cloud", 1u);
    set_last(0, 1000, 4026531);
    set_u32("tx.fields_ain", 0u);
    mask_on("tx.fields_ain", "ma");
    mask_on("tx.fields_ain", "raw");

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("pressure_paint");
    CHECK_HAS(ln, "\"ma\":", "ma 비트를 켜면 전류가 실린다");
    CHECK_HAS(ln, "\"raw\":4026531", "raw 비트를 켜면 원시 카운트");
}

static void test_ain_none_is_not_published(void)
{
    setup();
    drain_capability();                    /* ain0.cloud 기본 = 없음 */
    set_last(0, 1000, 4026531);
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "없음 = 미발행 (계약 §16.6)");
}

static void test_moving_the_sensor_is_a_config_change(void)
{
    setup();
    drain_capability();
    set_u32("ain1.cloud", 1u);            /* J4 로 옮겼다 */
    set_last(1, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK(find_line("pressure_paint") != NULL, "타입이 채널을 따라간다");
}

static void test_a_sample_is_published_once(void)
{
    setup();
    set_u32("ain0.cloud", 3u);
    drain_capability();
    set_last(0, 1000, 4026531);

    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 1, "새 표본 = 1줄");
    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0, "같은 표본은 다시 안 낸다");
    set_last(0, 1100, 4000000);
    N = 0;
    CHECK(mk_cloud_tick(&C, 300, sink, NULL) == 1, "표본이 갱신되면 또 1줄");
    CHECK_HAS(find_line("flow"), "\"lpm\":", "유량은 lpm 필드");
}

/* ---- i2c ---------------------------------------------------------------- */

static MkI2c I2C;

static int fake_xfer_ok(void *ctx, uint8_t bus, uint8_t addr,
                        const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx;
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return 0;
}

static void setup_with_i2c(void)
{
    setup();
    static MkI2cIo io;
    io = (MkI2cIo){ fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_cloud_attach_i2c(&C, &I2C);
    /* 🔴 표본은 drain **뒤에** 채운다 — 앞에 채우면 drain tick 이 표본까지
     *    소비해 버려("같은 표본은 두 번 안 낸다") 본 시험이 침묵을 본다. */
    drain_capability();
    /* AM2320(J13 = 포트 3): slot0 = 온도, slot1 = 습도 */
    I2C.last[3][0] = (MkI2cOut){ .connector_id = 13u, .quantity = "temp",
                                 .value = 23.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[3][1] = (MkI2cOut){ .connector_id = 13u, .quantity = "humidity",
                                 .value = 42.6f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[3][0] = 1u;
    I2C.last_valid[3][1] = 1u;
}

static void test_i2c_values_flow_without_a_switch(void)
{
    /* 별도 발행 스위치가 없다(사용자 확정 2026-08-22) — 값이 오고 있으면
     * 나간다. */
    setup_with_i2c();
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("temp_air"), "\"degc\":23.5", "온도 → temp_air");
    CHECK_HAS(find_line("humidity"), "\"pct\":42.6", "습도 → humidity");
}

static void test_dewpoint_is_computed_when_the_bit_is_on(void)
{
    setup_with_i2c();
    mask_on("tx.fields_i2c", "dewpoint");
    mk_cloud_tick(&C, 100, sink, NULL);
    /* 23.5°C·42.6% → Magnus 10.054°C (파이썬 독립 계산 2026-08-22) */
    const char *ln = find_line("humidity");
    CHECK_HAS(ln, "\"dewpoint_degc\":10.1", "이슬점이 계산돼 붙는다");
}

static void test_ambient_rides_on_temp_road(void)
{
    setup_with_i2c();
    /* MLX90614(J12 = 포트 2): 대상 + 주변 */
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_object",
                                 .value = 42.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_ambient",
                                 .value = 24.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][0] = 1u;
    I2C.last_valid[2][1] = 1u;
    mask_on("tx.fields_i2c", "temp_ambient");

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("temp_road");
    CHECK_HAS(ln, "\"degc\":42.5", "대상 온도가 본값");
    CHECK_HAS(ln, "\"ambient_degc\":24.0", "주변 온도는 선택 필드로 붙는다");
    CHECK(find_line("temp_ambient") == NULL,
          "주변 온도가 제 레코드로 나가지는 않는다");
}

static void test_ambient_off_means_no_field(void)
{
    setup_with_i2c();
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_object",
                                 .value = 42.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_ambient",
                                 .value = 24.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][0] = 1u;
    I2C.last_valid[2][1] = 1u;

    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK(strstr(find_line("temp_road"), "ambient") == NULL,
          "비트 꺼짐 = 주변 온도 없음");
}

/* ---- valve -------------------------------------------------------------- */

static MkSolCtl SOL;

static void sol_set_confirmed(int ch, int on)
{
    SOL.confirmed_valid[ch] = 1u;
    SOL.confirmed_state[ch] = (uint8_t)(on ? 1 : 0);
}

static void setup_with_sol(void)
{
    setup();
    memset(&SOL, 0, sizeof SOL);
    mk_cloud_attach_sol(&C, &SOL);
    drain_capability();
}

static void test_valve_is_the_or_of_inputs(void)
{
    /* 🔴 좌·우 밸브가 어느 입력에서 올지 모른다(사용자 확정 2026-08-22) —
     *    지정 대신 OR. 어느 쪽이든 열리면 1. */
    setup_with_sol();
    sol_set_confirmed(0, 0);              /* 좌 닫힘 */
    sol_set_confirmed(1, 1);              /* 우 열림 */
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("valve"), "\"state\":1", "한쪽만 열려도 1");

    sol_set_confirmed(1, 0);              /* 둘 다 닫힘 */
    N = 0;
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK_HAS(find_line("valve"), "\"state\":0", "둘 다 닫혀야 0");

    N = 0;
    CHECK(mk_cloud_tick(&C, 300, sink, NULL) == 0,
          "상태가 안 바뀌면 다시 안 낸다");
}

static void test_no_confirmed_inputs_stay_silent(void)
{
    setup_with_sol();                      /* 아무 입력도 확정 안 됨 */
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0,
          "모르는 상태를 0 이라 말하지 않는다");
}

static void test_valve_mask_bit_tags_records(void)
{
    setup_with_sol();
    sol_set_confirmed(2, 1);
    set_u32("ain0.cloud", 1u);
    mask_on("tx.fields_ain", "valve");
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    CHECK_HAS(find_line("pressure_paint"), "\"valve\":1",
              "밸브 비트를 켠 레코드에 상태가 붙는다");
}

/* ---- device_capability -------------------------------------------------- */

static void test_capability_is_first_and_reflects_config(void)
{
    setup();
    set_u32("ain0.cloud", 1u);
    set_u32("i2c13.enabled", 1u);
    set_u32("i2c13.kind", 2u);            /* 온습도 */

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("device_capability");
    CHECK(ln != NULL, "첫 발행에 capability 가 있다");
    CHECK(ln == LINES[0], "capability 가 맨 앞이다 (계약 §16.1)");
    CHECK_HAS(ln, "\"pressure_paint\"", "ain 타입 선택이 sensors 에");
    CHECK_HAS(ln, "\"temp_air\"", "온습도 → temp_air");
    CHECK_HAS(ln, "\"humidity\"", "온습도 → humidity");
    CHECK_HAS(ln, "\"fw_version\":\"0.1.0\"", "펌웨어 버전 동봉");
    CHECK(strstr(ln, "\"valve\"") == NULL,
          "valve 는 sensors 에 안 넣는다 (계약 §16.3)");

    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0,
          "설정이 그대로면 재발행하지 않는다 — 부팅 1회");
}

static void test_capability_reemits_on_config_change(void)
{
    setup();
    drain_capability();
    set_u32("ain1.cloud", 3u);             /* 유량 추가 */
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK_HAS(find_line("device_capability"), "\"flow\"",
              "설정이 바뀌면 즉시 재발행 (계약 §16.4)");
}

/* ---- gnss --------------------------------------------------------------- */

static MkGnss GNSS;

static void gnss_feed_str(const char *s)
{
    for (const char *p = s; *p; p++) { mk_gnss_feed(&GNSS, (uint8_t)*p); }
}

/* test_gnss.c 의 실기기 문장 쌍 — 같은 시각이라 GGA 값이 fix 에 짝지어진다.
 * GGA 13열(보정 나이)은 비어 있다 — 실기기(단독 측위)가 실제로 그렇다. */
#define REAL_GGA "$GNGGA,023115.00,3719.14416560,N,12720.43544199,E,1,20,1.2,100.8517,M,23.8989,M,,*70\r\n"
#define REAL_RMC "$GNRMC,023115.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5E\r\n"
/* RTK 흉내 — 13열 1.2 s, 14열 기준국 0421. 체크섬은 XOR 로 계산해 박았다. */
#define RTK_GGA "$GNGGA,023116.00,3719.14416560,N,12720.43544199,E,4,20,0.8,100.8517,M,23.8989,M,1.2,0421*57\r\n"
#define RTK_RMC "$GNRMC,023116.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5D\r\n"

static void setup_with_gnss(void)
{
    setup();
    mk_gnss_init(&GNSS);
    mk_cloud_attach_gnss(&C, &GNSS);
    set_u32("gnss.enabled", 1u);
    drain_capability();
}

static void test_gnss_record_matches_the_contract(void)
{
    setup_with_gnss();
    gnss_feed_str(REAL_GGA);
    gnss_feed_str(REAL_RMC);
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("gnss");
    CHECK(ln != NULL, "유효 fix 가 gnss 레코드로 나간다");
    CHECK_HAS(ln, "\"lat_e8\":3731906943", "1e-8 도 정수 그대로");
    CHECK_HAS(ln, "\"lon_e8\":12734059070", "경도도");
    CHECK_HAS(ln, "\"hdop_x100\":120", "1.2 -> 120");
    CHECK_HAS(ln, "\"cs\":\"70\"", "원 GGA 체크섬 보존");
    CHECK_HAS(ln, "\"valve\":0", "gnss 는 valve 태깅 필수 (계약 §3)");
    /* 기본 마스크에서 alt 는 켜져 있다(규격 §7.2 기본 on) — 100.8517 m */
    CHECK_HAS(ln, "\"alt\":100.85", "alt 비트(기본 켬)가 실린다");
    CHECK(strstr(ln, "\"diff_age\"") == NULL, "빈 13열은 안 실린다");

    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0, "새 fix 없으면 침묵");
}

static void test_gnss_rtk_extras_follow_the_mask(void)
{
    setup_with_gnss();
    mask_on("tx.fields_gnss", "diff_age");
    mask_on("tx.fields_gnss", "station_id");

    gnss_feed_str(RTK_GGA);
    gnss_feed_str(RTK_RMC);
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("gnss");
    CHECK_HAS(ln, "\"diff_age\":1.2", "보정 나이 (GGA 13열)");
    CHECK_HAS(ln, "\"station_id\":421", "기준국 (GGA 14열)");
    CHECK_HAS(ln, "\"fix\":4", "RTK fixed 품질");
}

static void test_gnss_disabled_is_silent(void)
{
    setup();
    mk_gnss_init(&GNSS);
    mk_cloud_attach_gnss(&C, &GNSS);   /* gnss.enabled 기본 = 꺼짐 */
    drain_capability();
    gnss_feed_str(REAL_GGA);
    gnss_feed_str(REAL_RMC);
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "꺼져 있으면 미발행");
}

/* ---- imu ---------------------------------------------------------------- */

static MkImu IMU;

#define IMU_WIRE \
    "#RAWIMUXA,COM1,0,60.0,FINE,2261,366772.050,0,0,0;" \
    "00,64,2261,366772.050,0ac00000,16278,-70,172,-1044,-90,-200" \
    "*bffb7522\r\n"

static void feed_imu(void)
{
    for (const char *p = IMU_WIRE; *p; p++) {
        mk_imu_feed(&IMU, (uint8_t)*p, 5000);
    }
}

static void test_imu_record_from_um981(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);
    set_u32("gnss.imu", 1u);
    drain_capability();

    feed_imu();
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("imu");
    CHECK(ln != NULL, "RAWIMUX 가 imu 레코드로 나간다");
    CHECK_HAS(ln, "\"az\":0.994", "환산 2/32767 (거의 1 g)");
    CHECK_HAS(ln, "\"gz\":-7.96", "자이로 250/32767");
    CHECK(strstr(ln, "\"mx\":") == NULL, "지자기는 없다 — v1.7 완화");
    CHECK(strstr(ln, "\"degc\":") == NULL, "온도는 기본 꺼짐");
}

static void test_imu_temp_when_enabled(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);
    set_u32("gnss.imu", 1u);
    mask_on("tx.fields_imu", "imu_temp");    /* 전송 탭의 IMU 필드 카드 */
    drain_capability();

    feed_imu();
    mk_cloud_tick(&C, 100, sink, NULL);

    /* status 0x0ac00000 -> bit21~31 = 0x056 = 86 -> 86*0.125+23 = 33.75 */
    CHECK_HAS(find_line("imu"), "\"degc\":33.8",
              "status 워드의 온도 (Table 2-15, x0.125+23)");
}

static void test_imu_off_is_silent(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);     /* gnss.imu 기본 = 꺼짐 */
    drain_capability();
    IMU.last.valid = 1;
    IMU.last.seq = 1u;
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "꺼져 있으면 미발행");
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);   /* 크래시 지점을 놓치지 않게 */
    test_ain_pressure_paint_record();
    test_common_field_values_are_config();
    test_ain_optional_fields_follow_the_mask();
    test_ain_none_is_not_published();
    test_moving_the_sensor_is_a_config_change();
    test_a_sample_is_published_once();
    test_i2c_values_flow_without_a_switch();
    test_dewpoint_is_computed_when_the_bit_is_on();
    test_ambient_rides_on_temp_road();
    test_ambient_off_means_no_field();
    test_valve_is_the_or_of_inputs();
    test_no_confirmed_inputs_stay_silent();
    test_valve_mask_bit_tags_records();
    test_capability_is_first_and_reflects_config();
    test_capability_reemits_on_config_change();
    test_gnss_record_matches_the_contract();
    test_gnss_rtk_extras_follow_the_mask();
    test_gnss_disabled_is_silent();
    test_imu_record_from_um981();
    test_imu_temp_when_enabled();
    test_imu_off_is_silent();

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all ok\n");
    return 0;
}
